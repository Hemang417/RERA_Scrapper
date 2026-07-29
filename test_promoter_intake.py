"""
Tests for run_promoter_intake -- the standalone CIN-only promoter lookup
(Phase 1 of the "identifier can arrive before a RERA number" work). Mocks
the four _safe_* wrappers (network calls) to keep this offline and fast;
see the manual live check noted in the PR/commit for a real-network proof.

The one property that actually matters here, more than any individual
field: the dict keys this writes (company_profile_check,
ibbi_insolvency_check, group_companies_check, credit_rating_check) must be
IDENTICAL to the keys run_company_charter itself uses for these same four
checks (verified against company_charter.py's own "results" handling right
after its ThreadPoolExecutor block) -- that's what lets a later Charter run
for the same promoter absorb this file with a plain dict update instead of
a reshape. test_output_keys_match_run_company_charters_own_facts_keys
pins that down directly.

Run directly: python test_promoter_intake.py
"""

import json
import os
import shutil

import company_charter as cc

_SCRATCH_DIR = os.path.join("output", "_test_scratch_promoter_intake")

_FOUND_PROFILE = {
    "found": True, "cin": "U70109MH2022PLC385473", "name": "PRANAMI NEEV REALTY LIMITED",
    "url": "https://www.zaubacorp.com/company/X/U70109MH2022PLC385473",
    "sources_used": ["zaubacorp.com", "instafinancials.com"],
    "roster_conflicts": ["Director X: ZaubaCorp lists as current, InstaFinancials does not."],
}
_FOUND_IBBI = {"found_process": False, "url": "https://ibbi.gov.in/claims/inner-process/U70109MH2022PLC385473"}
_FOUND_GROUP = {
    "found": True, "companies": ["Entity A", "Entity B"],
    "url": "https://www.zaubacorp.com/company/X/U70109MH2022PLC385473",
}
_FOUND_RATING = {
    "ratings": [{"agency": "ICRA", "company_name": "Pranami Neev Realty Limited", "url": "https://icra.in/x"}],
}


def _patch(monkeypatched):
    """Replaces the 4 _safe_* wrappers with fixed return values for the
    duration of the `with` block, restoring the real functions after --
    same pattern as this repo's other tests that monkeypatch module-level
    functions directly (no mocking framework in use here)."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        originals = {
            "_safe_company_profile": cc._safe_company_profile,
            "_safe_ibbi_check": cc._safe_ibbi_check,
            "_safe_group_companies": cc._safe_group_companies,
            "_safe_credit_rating": cc._safe_credit_rating,
        }
        for name, value in monkeypatched.items():
            setattr(cc, name, value)
        try:
            yield
        finally:
            for name, value in originals.items():
                setattr(cc, name, value)

    return _ctx()


def test_output_keys_match_run_company_charters_own_facts_keys():
    with _patch({
        "_safe_company_profile": lambda identifier, promoter_name="": _FOUND_PROFILE,
        "_safe_ibbi_check": lambda identifier: _FOUND_IBBI,
        "_safe_group_companies": lambda identifier: _FOUND_GROUP,
        "_safe_credit_rating": lambda promoter_name: _FOUND_RATING,
    }):
        record = cc.run_promoter_intake("U70109MH2022PLC385473", "Pranami Neev Realty Limited", output_dir=_SCRATCH_DIR)

    # These 4 keys are exactly what run_company_charter assigns into `facts`
    # for the same 4 checks -- pinned here so a future edit to either side
    # can't silently drift the two apart.
    assert record["company_profile_check"] == _FOUND_PROFILE
    assert record["ibbi_insolvency_check"] == _FOUND_IBBI
    assert record["group_companies_check"] == _FOUND_GROUP
    assert record["credit_rating_check"] == {"promoter": _FOUND_RATING}
    print("test_output_keys_match_run_company_charters_own_facts_keys: PASS")


def test_sources_and_gaps_shaped_like_run_company_charters_own_entries():
    with _patch({
        "_safe_company_profile": lambda identifier, promoter_name="": _FOUND_PROFILE,
        "_safe_ibbi_check": lambda identifier: _FOUND_IBBI,
        "_safe_group_companies": lambda identifier: _FOUND_GROUP,
        "_safe_credit_rating": lambda promoter_name: _FOUND_RATING,
    }):
        record = cc.run_promoter_intake("U70109MH2022PLC385473", "Pranami Neev Realty Limited", output_dir=_SCRATCH_DIR)

    topics = {s["topic"] for s in record["sources"]}
    # insolvency_status IS included even though found_process is False --
    # a confirmed "no active process" is still a real, citable checked fact,
    # matching run_company_charter's own `is not None` (not truthiness) test.
    assert topics == {"company_profile", "group_companies", "credit_rating", "insolvency_status"}
    assert all(set(s.keys()) == {"label", "ref", "topic", "published_date", "accessed_date"} for s in record["sources"])
    assert record["gaps"] == ["Director X: ZaubaCorp lists as current, InstaFinancials does not."]
    print("test_sources_and_gaps_shaped_like_run_company_charters_own_entries: PASS")


def test_credit_rating_skipped_without_a_company_name():
    with _patch({
        "_safe_company_profile": lambda identifier, promoter_name="": {"found": False, "note": "not found anywhere"},
        "_safe_ibbi_check": lambda identifier: {"found_process": None, "note": "could not run"},
        "_safe_group_companies": lambda identifier: {"found": False, "note": "not found"},
        "_safe_credit_rating": lambda promoter_name: (_ for _ in ()).throw(AssertionError("must not be called without a company_name")),
    }):
        record = cc.run_promoter_intake("U70109MH2022PLC385473", output_dir=_SCRATCH_DIR)

    assert "credit_rating_check" not in record
    assert record["sources"] == []
    print("test_credit_rating_skipped_without_a_company_name: PASS")


def test_writes_to_pending_cin_keyed_directory():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    with _patch({
        "_safe_company_profile": lambda identifier, promoter_name="": _FOUND_PROFILE,
        "_safe_ibbi_check": lambda identifier: _FOUND_IBBI,
        "_safe_group_companies": lambda identifier: _FOUND_GROUP,
        "_safe_credit_rating": lambda promoter_name: _FOUND_RATING,
    }):
        record = cc.run_promoter_intake("u70109mh2022plc385473", "Pranami Neev Realty Limited", output_dir=_SCRATCH_DIR)

    # Not lowercased/reshaped on disk -- .strip()'d only, matching whatever
    # case the caller actually passed (CINs are conventionally uppercase).
    written_path = os.path.join(_SCRATCH_DIR, "_pending", "u70109mh2022plc385473", "promoter_profile.json")
    assert os.path.exists(written_path)
    with open(written_path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk == record
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_writes_to_pending_cin_keyed_directory: PASS")


if __name__ == "__main__":
    test_output_keys_match_run_company_charters_own_facts_keys()
    test_sources_and_gaps_shaped_like_run_company_charters_own_entries()
    test_credit_rating_skipped_without_a_company_name()
    test_writes_to_pending_cin_keyed_directory()
    print("\nAll tests passed.")

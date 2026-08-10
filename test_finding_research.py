"""
Tests for the per-finding deep-research stage (CLAUDE.md Section B, "Deep
research on every finding").

Every test here runs against an injected researcher, so the whole stage is
exercised without spending anything. The one thing that cannot be tested
offline is a real call's cost landing under its own label in
usage_summary.json; that needs a live run (see the module note in
run_finding_research).

The property these tests care about most is the failure behaviour. Enriching a
finding is a nice-to-have; silently DELETING one because an auth token expired
would be a serious defect, and worse than never running the stage. So the
degradation paths are pinned harder than the happy path.

Run directly: python test_finding_research.py
"""

import copy
import json
import os

import company_charter as cc
import deep_research

_PRANAMI_FACTS = os.path.join("output", "company_charters", "Company_Charter_Pranami_Bliss_P51800077150.facts.json")


def _facts() -> dict:
    with open(_PRANAMI_FACTS, encoding="utf-8") as f:
        facts = json.load(f)
    cc._normalize_misfiled_facts(facts)
    return facts


def _synthetic() -> dict:
    """A deterministic facts dict with exactly one finding per finding-field.

    Not read from the live fixture, and deliberately so. The pipeline persists
    research-ENRICHED facts back to that file, and an enriched finding is
    multi-sentence, so it splits into several clauses and the fixture's finding
    COUNT changes after any real run (3 became 9 on the first one). Tests about
    counting, capping and per-finding failure need input that cannot drift."""
    long_a = ("The development agreements permit the developer to create a mortgage over its free sale "
              "area, and no such mortgage has been taken on this land to date.")
    long_b = ("Two orders of the Deputy Registrar dated 6 March 2024 amalgamated the two societies into "
              "one, which is how the separate plots became the single plot this project is built on.")
    long_c = ("The Title Report's search found nothing against either property except one Notice of Lis "
              "Pendens dated 20 December 2017 naming an adjoining society on the same survey number.")
    return {
        "litigation_status": {"value": long_c, "source": "Title Report - RERA.pdf"},
        "land_identification": {"land_assembly": {"value": long_b, "source": "Order.pdf"}},
        "fsi_metrics": {"mortgage_area": long_a},
        "gaps": ["A gap that must never be researched or altered."],
        "rera_core_fields": {"project_name": "Test Project", "registration_number": "P51800077150"},
        "corporate_identity": {"promoter_name": {"value": "Test Promoter", "source": "x"}},
    }


def _ok(prefix="RESOLVED: "):
    return lambda clause, context="": {
        "resolved": True, "text": prefix + clause, "still_live": "no", "note": "",
    }


def _unresolved(note="deeper research could not run: no API key"):
    return lambda clause, context="": {
        "resolved": False, "text": clause, "still_live": "unknown", "note": note,
    }


# --- what counts as a finding ------------------------------------------------

def test_collects_the_real_findings_including_the_lis_pendens():
    findings = cc._collect_findings(_facts())
    paths = {f["path"] for f in findings}
    assert "litigation_status.value" in paths, paths
    joined = " ".join(f["clause"] for f in findings)
    assert "Lis Pendens" in joined, "the spec's worked example must be picked up"
    print(f"test_collects_the_real_findings_including_the_lis_pendens: PASS ({len(findings)} findings)")


def test_clean_checks_are_never_sent_for_research():
    """There is nothing to research about a nothing, and a research pass over
    an absence comes back padded or falsely certain."""
    findings = cc._collect_findings(_facts())
    for f in findings:
        assert not cc._is_clean_check_clause(f["clause"]), f["clause"]
    joined = " ".join(f["clause"] for f in findings)
    assert "No litigation is disclosed" not in joined
    print("test_clean_checks_are_never_sent_for_research: PASS")


def test_gaps_are_left_completely_alone():
    """Section B scopes this stage to findings, "not gaps, not clean checks"."""
    facts = _synthetic()
    before = list(facts.get("gaps", []))
    cc.run_finding_research(facts, researcher=_ok())
    assert facts["gaps"] == before
    print("test_gaps_are_left_completely_alone: PASS")


def test_a_stub_or_label_is_too_short_to_research():
    facts = {"litigation_status": {"value": "None."}}
    assert cc._collect_findings(facts) == []
    print("test_a_stub_or_label_is_too_short_to_research: PASS")


# --- enrichment --------------------------------------------------------------

def test_resolved_findings_are_written_back_into_their_own_field():
    facts = _synthetic()
    summary = cc.run_finding_research(facts, researcher=_ok())
    assert summary["enriched"] == summary["findings_seen"] > 0, summary
    assert "RESOLVED: " in facts["litigation_status"]["value"]
    assert facts["finding_research"]["enriched"] == summary["enriched"]
    print(f"test_resolved_findings_are_written_back_into_their_own_field: PASS ({summary['enriched']})")


def test_the_search_gets_project_context_to_disambiguate():
    """A Notice of Lis Pendens means nothing without knowing which plot it is
    being researched against."""
    seen = []

    def spy(clause, context=""):
        seen.append(context)
        return {"resolved": True, "text": clause, "still_live": "no", "note": ""}

    cc.run_finding_research(_facts(), researcher=spy)
    assert seen and "MahaRERA registration" in seen[0], seen[:1]
    assert "P51800077150" in seen[0], seen[:1]
    print("test_the_search_gets_project_context_to_disambiguate: PASS")


# --- degradation: the part that actually matters ------------------------------

def test_an_unresolved_finding_keeps_its_original_text_verbatim():
    facts = _synthetic()
    original = facts["litigation_status"]["value"]
    summary = cc.run_finding_research(facts, researcher=_unresolved())
    assert facts["litigation_status"]["value"] == original, "a finding was altered despite no research"
    assert summary["enriched"] == 0 and summary["kept_original"] == summary["findings_seen"]
    print("test_an_unresolved_finding_keeps_its_original_text_verbatim: PASS")


def test_a_raising_researcher_costs_only_its_own_finding():
    facts = _synthetic()
    original = facts["litigation_status"]["value"]
    calls = {"n": 0}

    def flaky(clause, context=""):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("rate limit")
        return {"resolved": True, "text": "RESOLVED " + clause, "still_live": "no", "note": ""}

    summary = cc.run_finding_research(facts, researcher=flaky)
    assert facts["litigation_status"]["value"] == original, "the failed finding must be untouched"
    assert summary["enriched"] >= 1, "later findings must still be researched"
    assert summary["kept_original"] >= 1
    print("test_a_raising_researcher_costs_only_its_own_finding: PASS")


def test_no_finding_is_ever_deleted_whatever_the_researcher_returns():
    """The one outcome worse than not running the stage at all."""
    for bad in (
        lambda c, x="": {"resolved": True, "text": "", "still_live": "no", "note": ""},
        lambda c, x="": {"resolved": True, "still_live": "no"},
        lambda c, x="": {},
    ):
        facts = _synthetic()
        original = facts["litigation_status"]["value"]
        cc.run_finding_research(facts, researcher=bad)
        assert facts["litigation_status"]["value"] == original, bad
        assert facts["litigation_status"]["value"].strip(), "field must never be emptied"
    print("test_no_finding_is_ever_deleted_whatever_the_researcher_returns: PASS")


def test_the_live_transport_degrades_instead_of_raising():
    """With no ANTHROPIC_API_KEY configured this exercises the real failure
    path: research_finding must hand back the original text, not raise."""
    original = "A 2017 Notice of Lis Pendens naming an adjoining society."
    out = deep_research.research_finding(original, "context")
    assert out["text"] == original or out["resolved"], out
    assert set(out) >= {"resolved", "text", "still_live", "note"}, out
    print("test_the_live_transport_degrades_instead_of_raising: PASS")


# --- budget ------------------------------------------------------------------

def test_fan_out_is_capped_and_the_excess_keeps_its_text():
    facts = _synthetic()
    findings = cc._collect_findings(facts)
    assert len(findings) >= 2, "fixture premise"

    real_cap = deep_research.MAX_FINDING_RESEARCH_CALLS
    deep_research.MAX_FINDING_RESEARCH_CALLS = 1
    try:
        before_second = copy.deepcopy(facts)[findings[1]["path"].split(".")[0]]
        summary = cc.run_finding_research(facts, researcher=_ok())
    finally:
        deep_research.MAX_FINDING_RESEARCH_CALLS = real_cap

    assert summary["enriched"] == 1, summary
    assert summary["kept_original"] == len(findings) - 1, summary
    assert facts[findings[1]["path"].split(".")[0]] == before_second, "capped finding was altered"
    print("test_fan_out_is_capped_and_the_excess_keeps_its_text: PASS")


def test_the_stage_has_its_own_usage_label():
    """Its cost has to be separable in usage_summary.json, per the spec."""
    import inspect
    src = inspect.getsource(deep_research.research_finding)
    assert 'label: str = "finding_research"' in src, "the stage needs its own label"
    summary = deep_research.usage_summary()
    assert "by_label" in summary
    print("test_the_stage_has_its_own_usage_label: PASS")


if __name__ == "__main__":
    test_collects_the_real_findings_including_the_lis_pendens()
    test_clean_checks_are_never_sent_for_research()
    test_gaps_are_left_completely_alone()
    test_a_stub_or_label_is_too_short_to_research()
    test_resolved_findings_are_written_back_into_their_own_field()
    test_the_search_gets_project_context_to_disambiguate()
    test_an_unresolved_finding_keeps_its_original_text_verbatim()
    test_a_raising_researcher_costs_only_its_own_finding()
    test_no_finding_is_ever_deleted_whatever_the_researcher_returns()
    test_the_live_transport_degrades_instead_of_raising()
    test_fan_out_is_capped_and_the_excess_keeps_its_text()
    test_the_stage_has_its_own_usage_label()
    print("\nAll tests passed.")

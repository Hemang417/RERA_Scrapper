"""
Tests for run_igr_registered_deed_check / _interactively_resolve_igr_search --
the IGR Maharashtra registered-deed corroboration check (see
igr_maharashtra_search.py, wired into run_company_charter as part of the
Promoter Profile consolidation).

Mocks igr_maharashtra_search.search_by_document_number (network + a real
browser/CAPTCHA) to keep this offline and fast, same convention as
test_cts_intake.py mocking mahabhumi.search_cts_candidates/
fetch_property_card and test_nclt_bombay_hc_checks.py mocking
nclt_search.search_party/bombay_hc_search.search_party.

Run directly: python test_igr_registered_deed_check.py
"""

import json
import os
import shutil
from unittest import mock

import igr_maharashtra_search
import company_charter as cc

_SCRATCH_DIR = os.path.join("output", "_test_scratch_igr_check")
_REG_NO = "P00000000001"


def _clean():
    shutil.rmtree(os.path.join(_SCRATCH_DIR, _REG_NO), ignore_errors=True)


def test_non_interactive_leaves_facts_unchanged():
    """The ordinary automated-run case: no igr_lookup_input.json, and no
    human at this terminal to supply one interactively -- must not block
    or guess, and must not fabricate a key that was never checked."""
    _clean()
    with mock.patch("sys.stdin.isatty", return_value=False):
        facts = cc.run_igr_registered_deed_check({}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert "igr_registered_deed_check" not in facts
    input_path = os.path.join(_SCRATCH_DIR, _REG_NO, "igr_lookup_input.json")
    assert not os.path.exists(input_path)
    _clean()
    print("test_non_interactive_leaves_facts_unchanged: PASS")


def test_interactive_resolution_completes_and_runs_the_search():
    """A human at a real terminal supplies district/SRO/year/doc-number/
    registration-type -- igr_lookup_input.json gets written AND the
    function falls straight through into the search in the SAME call."""
    _clean()
    found_result = {
        "found": True, "rows": [{"seller name": "Test Seller", "purchaser name": "Test Purchaser"}],
        "raw_text": "...", "url": "https://freesearchigrservice.maharashtra.gov.in/x", "note": "",
    }
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=["Pune", "Haveli", "2024", "100", ""]), \
         mock.patch.object(igr_maharashtra_search, "search_by_document_number", return_value=found_result) as fake_search:
        facts = cc.run_igr_registered_deed_check({}, _REG_NO, output_dir=_SCRATCH_DIR)

    input_path = os.path.join(_SCRATCH_DIR, _REG_NO, "igr_lookup_input.json")
    assert os.path.exists(input_path)
    with open(input_path, encoding="utf-8") as f:
        written = json.load(f)
    assert written == {"district": "Pune", "sro_contains": "Haveli", "year": "2024", "doc_number": "100", "registration_type": "regular"}
    fake_search.assert_called_once_with("Pune", "Haveli", "2024", "100", registration_type="regular")
    assert facts["igr_registered_deed_check"] == found_result
    assert facts["sources"][0]["topic"] == "registered_deed"
    _clean()
    print("test_interactive_resolution_completes_and_runs_the_search: PASS")


def test_interactive_resolution_aborts_on_blank_input():
    """A human declining at any prompt (blank answer) must fall back to
    leaving facts unchanged -- never guess a document number."""
    _clean()
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=[""]), \
         mock.patch.object(igr_maharashtra_search, "search_by_document_number") as fake_search:
        facts = cc.run_igr_registered_deed_check({}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert "igr_registered_deed_check" not in facts
    fake_search.assert_not_called()
    _clean()
    print("test_interactive_resolution_aborts_on_blank_input: PASS")


def test_eof_during_interactive_prompt_is_treated_as_abort():
    """Confirmed live elsewhere in this codebase: sys.stdin.isatty() can be
    True with no human actually feeding it a line, which makes input()
    raise EOFError. Must degrade the same way a blank answer does -- never
    an uncaught exception escaping run_company_charter."""
    _clean()
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=EOFError()), \
         mock.patch.object(igr_maharashtra_search, "search_by_document_number") as fake_search:
        facts = cc.run_igr_registered_deed_check({}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert "igr_registered_deed_check" not in facts
    fake_search.assert_not_called()
    _clean()
    print("test_eof_during_interactive_prompt_is_treated_as_abort: PASS")


def test_captcha_timeout_is_caught_not_raised():
    _clean()
    os.makedirs(os.path.join(_SCRATCH_DIR, _REG_NO), exist_ok=True)
    with open(os.path.join(_SCRATCH_DIR, _REG_NO, "igr_lookup_input.json"), "w", encoding="utf-8") as f:
        json.dump({"district": "Pune", "sro_contains": "Haveli", "year": "2024", "doc_number": "100", "registration_type": "regular"}, f)

    with mock.patch.object(
        igr_maharashtra_search, "search_by_document_number",
        side_effect=igr_maharashtra_search.CaptchaTimeoutError("timed out waiting for CAPTCHA"),
    ):
        facts = cc.run_igr_registered_deed_check({}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert facts["igr_registered_deed_check"]["found"] is False
    assert "did not complete" in facts["igr_registered_deed_check"]["note"]
    _clean()
    print("test_captcha_timeout_is_caught_not_raised: PASS")


def test_non_maharashtra_state_is_never_asked_igr_questions():
    """IGR Maharashtra e-Search only exists for Maharashtra -- unlike CTS,
    which gets the same effect implicitly via its district-hint extraction,
    IGR's interactive prompt asks for a district directly, so this must be
    checked explicitly or a Gujarat/Karnataka/etc. run would ask a human
    Maharashtra-specific questions that could never resolve to anything."""
    _clean()
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input") as fake_input, \
         mock.patch.object(igr_maharashtra_search, "search_by_document_number") as fake_search:
        facts = cc.run_igr_registered_deed_check({"state": {"code": "GJ"}}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert "igr_registered_deed_check" not in facts
    fake_input.assert_not_called()
    fake_search.assert_not_called()
    input_path = os.path.join(_SCRATCH_DIR, _REG_NO, "igr_lookup_input.json")
    assert not os.path.exists(input_path)
    _clean()
    print("test_non_maharashtra_state_is_never_asked_igr_questions: PASS")


def test_malformed_input_file_is_a_clean_not_found_not_a_crash():
    _clean()
    os.makedirs(os.path.join(_SCRATCH_DIR, _REG_NO), exist_ok=True)
    with open(os.path.join(_SCRATCH_DIR, _REG_NO, "igr_lookup_input.json"), "w", encoding="utf-8") as f:
        json.dump({"district": "Pune"}, f)  # missing sro_contains/year/doc_number

    facts = cc.run_igr_registered_deed_check({}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert facts["igr_registered_deed_check"]["found"] is False
    assert "missing required field" in facts["igr_registered_deed_check"]["note"]
    _clean()
    print("test_malformed_input_file_is_a_clean_not_found_not_a_crash: PASS")


if __name__ == "__main__":
    test_non_interactive_leaves_facts_unchanged()
    test_interactive_resolution_completes_and_runs_the_search()
    test_interactive_resolution_aborts_on_blank_input()
    test_eof_during_interactive_prompt_is_treated_as_abort()
    test_non_maharashtra_state_is_never_asked_igr_questions()
    test_captcha_timeout_is_caught_not_raised()
    test_malformed_input_file_is_a_clean_not_found_not_a_crash()
    print("\nAll tests passed.")

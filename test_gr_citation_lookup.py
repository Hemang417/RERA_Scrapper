"""
Tests for run_gr_citation_lookup / _interactively_resolve_gr_citation / the
_looks_like_gr_citation auto-detection inside run_cts_land_lookup -- the
Maharashtra Government Resolutions portal lookup (see gr_maharashtra_
search.py), wired into run_company_charter right after the CTS Property
Card check.

Mocks gr_maharashtra_search.search_gr (network + a real browser/CAPTCHA) to
keep this offline and fast, same convention as test_igr_registered_deed_
check.py mocking igr_maharashtra_search.search_by_document_number and
test_nclt_bombay_hc_checks.py mocking nclt_search.search_party.

Run directly: python test_gr_citation_lookup.py
"""

import json
import os
import shutil
from unittest import mock

import gr_maharashtra_search
import company_charter as cc

_SCRATCH_DIR = os.path.join("output", "_test_scratch_gr_citation")
_REG_NO = "P00000000002"

_GR_SHAPED_FACTS = {"land_identification": {"survey_cts_plot_numbers": {"value": "CID-1812/CR-274/UD-10"}}}
_CTS_SHAPED_FACTS = {"land_identification": {"survey_cts_plot_numbers": {"value": "602"}}}


def _clean():
    shutil.rmtree(os.path.join(_SCRATCH_DIR, _REG_NO), ignore_errors=True)


def test_looks_like_gr_citation_shape_detection():
    """Code-computed, deterministic classification -- never a guess about
    content, just format recognition. Must not false-positive on a real
    CTS/survey/gat number."""
    assert gr_maharashtra_search._looks_like_gr_citation("CID-1812/CR-274/UD-10")
    assert gr_maharashtra_search._looks_like_gr_citation("CID-1812/C.R.274/UD-10")
    assert not gr_maharashtra_search._looks_like_gr_citation("602")
    assert not gr_maharashtra_search._looks_like_gr_citation("88/1")
    assert not gr_maharashtra_search._looks_like_gr_citation("P51700012345")
    print("test_looks_like_gr_citation_shape_detection: PASS")


def test_cts_land_lookup_skips_cts_flow_for_a_gr_shaped_value():
    """run_cts_land_lookup's own guard: a GR-shaped value must short-circuit
    straight to a 'not a CTS number' note, never attempt office/village
    resolution against it."""
    _clean()
    facts = cc.run_cts_land_lookup(dict(_GR_SHAPED_FACTS), _REG_NO, output_dir=_SCRATCH_DIR)
    assert facts["cts_land_record_check"]["found"] is False
    assert "Government Resolution" in facts["cts_land_record_check"]["note"]
    _clean()
    print("test_cts_land_lookup_skips_cts_flow_for_a_gr_shaped_value: PASS")


def test_cts_land_lookup_unaffected_for_a_real_cts_value():
    """A genuine CTS-shaped value must NOT trip the GR-citation guard --
    falls through into the ordinary CTS flow (which here just reports no
    input file, the normal unattended-run case)."""
    _clean()
    with mock.patch("sys.stdin.isatty", return_value=False):
        facts = cc.run_cts_land_lookup(dict(_CTS_SHAPED_FACTS), _REG_NO, output_dir=_SCRATCH_DIR)
    assert "cts_land_record_check" not in facts or "Government Resolution" not in facts.get("cts_land_record_check", {}).get("note", "")
    _clean()
    print("test_cts_land_lookup_unaffected_for_a_real_cts_value: PASS")


def test_non_interactive_no_gr_shape_leaves_facts_unchanged():
    """Ordinary automated run, nothing GR-shaped on record: must not block,
    guess, or add a gap about a citation that was never seen."""
    _clean()
    with mock.patch("sys.stdin.isatty", return_value=False):
        facts = cc.run_gr_citation_lookup({}, _REG_NO, output_dir=_SCRATCH_DIR)
    assert "gr_citation_check" not in facts
    assert not facts.get("gaps")
    _clean()
    print("test_non_interactive_no_gr_shape_leaves_facts_unchanged: PASS")


def test_non_interactive_gr_shape_adds_an_honest_gap():
    """Automated run, but the recorded value IS GR-shaped: no human to ask,
    so this must surface as an explicit, actionable gap -- never silently
    dropped, never guessed at."""
    _clean()
    with mock.patch("sys.stdin.isatty", return_value=False):
        facts = cc.run_gr_citation_lookup(dict(_GR_SHAPED_FACTS), _REG_NO, output_dir=_SCRATCH_DIR)
    assert "gr_citation_check" not in facts
    assert any("CID-1812/CR-274/UD-10" in g for g in facts.get("gaps", []))
    _clean()
    print("test_non_interactive_gr_shape_adds_an_honest_gap: PASS")


def test_interactive_resolution_completes_and_runs_the_search():
    """A human at a real terminal supplies department/from_date/to_date/
    keyword -- gr_citation_lookup_input.json gets written AND the function
    falls straight through into the search in the SAME call."""
    _clean()
    found_result = {
        "found": True,
        "rows": [{
            "department": "नगर विकास विभाग", "title": "...", "gr_code": "201709221737589325",
            "gr_date": "22-09-2017", "size_kb": "3364", "pdf_url": "https://gr.maharashtra.gov.in/x.pdf",
        }],
        "raw_text": "...", "url": "https://gr.maharashtra.gov.in/1145/Government-Resolutions", "note": "",
    }
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=["Urban Development", "2017-09-22", "", ""]), \
         mock.patch.object(gr_maharashtra_search, "search_gr", return_value=found_result) as fake_search, \
         mock.patch.object(gr_maharashtra_search, "download_gr_pdf", return_value=False):
        facts = cc.run_gr_citation_lookup(dict(_GR_SHAPED_FACTS), _REG_NO, output_dir=_SCRATCH_DIR)

    input_path = os.path.join(_SCRATCH_DIR, _REG_NO, "gr_citation_lookup_input.json")
    assert os.path.exists(input_path)
    with open(input_path, encoding="utf-8") as f:
        written = json.load(f)
    assert written == {"department": "Urban Development", "from_date": "2017-09-22", "to_date": "2017-09-22", "keyword": ""}
    fake_search.assert_called_once_with("", "Urban Development", "2017-09-22", "2017-09-22")
    assert facts["gr_citation_check"] == found_result
    assert facts["sources"][0]["topic"] == "gr_citation"
    _clean()
    print("test_interactive_resolution_completes_and_runs_the_search: PASS")


def test_interactive_resolution_aborts_on_blank_from_date():
    """A human declining at the (required) From-date prompt must fall back
    to leaving facts unchanged -- never guess a date."""
    _clean()
    with mock.patch("sys.stdin.isatty", return_value=True), \
         mock.patch("builtins.input", side_effect=["Urban Development", ""]), \
         mock.patch.object(gr_maharashtra_search, "search_gr") as fake_search:
        facts = cc.run_gr_citation_lookup(dict(_GR_SHAPED_FACTS), _REG_NO, output_dir=_SCRATCH_DIR)

    assert "gr_citation_check" not in facts
    fake_search.assert_not_called()
    _clean()
    print("test_interactive_resolution_aborts_on_blank_from_date: PASS")


def test_carryover_reuses_prior_result_without_a_new_search():
    """Same convention as run_cts_land_lookup's own land_record_carryover
    .json: an already-confirmed result is reused directly, no new browser/
    CAPTCHA needed."""
    _clean()
    os.makedirs(os.path.join(_SCRATCH_DIR, _REG_NO), exist_ok=True)
    carryover = {
        "gr_citation_check": {"found": True, "rows": [{"gr_code": "201709221737589325"}], "raw_text": "", "url": "", "note": ""},
        "sources": [{"label": "carried over", "topic": "gr_citation"}],
        "gaps": [],
    }
    with open(os.path.join(_SCRATCH_DIR, _REG_NO, "gr_citation_carryover.json"), "w", encoding="utf-8") as f:
        json.dump(carryover, f)

    with mock.patch.object(gr_maharashtra_search, "search_gr") as fake_search:
        facts = cc.run_gr_citation_lookup({}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert facts["gr_citation_check"]["found"] is True
    assert facts["sources"][0]["label"] == "carried over"
    fake_search.assert_not_called()
    _clean()
    print("test_carryover_reuses_prior_result_without_a_new_search: PASS")


def test_captcha_timeout_is_caught_not_raised():
    _clean()
    os.makedirs(os.path.join(_SCRATCH_DIR, _REG_NO), exist_ok=True)
    with open(os.path.join(_SCRATCH_DIR, _REG_NO, "gr_citation_lookup_input.json"), "w", encoding="utf-8") as f:
        json.dump({"department": "Urban Development", "from_date": "2017-09-22", "to_date": "2017-09-22", "keyword": ""}, f)

    with mock.patch.object(
        gr_maharashtra_search, "search_gr",
        side_effect=gr_maharashtra_search.CaptchaTimeoutError("timed out waiting for CAPTCHA"),
    ):
        facts = cc.run_gr_citation_lookup({}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert facts["gr_citation_check"]["found"] is False
    assert "did not complete" in facts["gr_citation_check"]["note"]
    _clean()
    print("test_captcha_timeout_is_caught_not_raised: PASS")


def test_generic_failure_never_leaks_raw_exception_text():
    """rules.md Section B: no file path or raw exception string into either
    document. Same leak class as every other browser-driven check here."""
    _clean()
    os.makedirs(os.path.join(_SCRATCH_DIR, _REG_NO), exist_ok=True)
    with open(os.path.join(_SCRATCH_DIR, _REG_NO, "gr_citation_lookup_input.json"), "w", encoding="utf-8") as f:
        json.dump({"department": "Urban Development", "from_date": "2017-09-22", "to_date": "2017-09-22", "keyword": ""}, f)

    leaky_message = (
        "BrowserType.launch: spawn UNKNOWN\nCall log:\n"
        "  - <launching> C:\\Users\\SomeUser\\AppData\\Local\\ms-playwright\\chromium-1234\\chrome-win64\\chrome.exe "
        "--user-data-dir=C:\\Users\\SomeUser\\AppData\\Local\\Temp\\playwright_chromiumdev_profile-abc123"
    )
    with mock.patch.object(gr_maharashtra_search, "search_gr", side_effect=RuntimeError(leaky_message)):
        facts = cc.run_gr_citation_lookup({}, _REG_NO, output_dir=_SCRATCH_DIR)

    note = facts["gr_citation_check"]["note"]
    assert "C:\\Users" not in note
    assert "chrome.exe" not in note
    assert "RuntimeError" in note
    _clean()
    print("test_generic_failure_never_leaks_raw_exception_text: PASS")


def test_unknown_department_is_caught_not_raised():
    _clean()
    os.makedirs(os.path.join(_SCRATCH_DIR, _REG_NO), exist_ok=True)
    with open(os.path.join(_SCRATCH_DIR, _REG_NO, "gr_citation_lookup_input.json"), "w", encoding="utf-8") as f:
        json.dump({"department": "Not A Real Department", "from_date": "2017-09-22", "to_date": "2017-09-22", "keyword": ""}, f)

    with mock.patch.object(
        gr_maharashtra_search, "search_gr",
        side_effect=gr_maharashtra_search.UnknownDepartmentError("Unknown department 'Not A Real Department'"),
    ):
        facts = cc.run_gr_citation_lookup({}, _REG_NO, output_dir=_SCRATCH_DIR)

    assert facts["gr_citation_check"]["found"] is False
    assert "Unknown department" in facts["gr_citation_check"]["note"]
    _clean()
    print("test_unknown_department_is_caught_not_raised: PASS")


if __name__ == "__main__":
    test_looks_like_gr_citation_shape_detection()
    test_cts_land_lookup_skips_cts_flow_for_a_gr_shaped_value()
    test_cts_land_lookup_unaffected_for_a_real_cts_value()
    test_non_interactive_no_gr_shape_leaves_facts_unchanged()
    test_non_interactive_gr_shape_adds_an_honest_gap()
    test_interactive_resolution_completes_and_runs_the_search()
    test_interactive_resolution_aborts_on_blank_from_date()
    test_carryover_reuses_prior_result_without_a_new_search()
    test_captcha_timeout_is_caught_not_raised()
    test_generic_failure_never_leaks_raw_exception_text()
    test_unknown_department_is_caught_not_raised()
    print("\nAll tests passed.")

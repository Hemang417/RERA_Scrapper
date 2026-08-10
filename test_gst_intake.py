"""
Tests for gst_intake.run_intake -- the shared core behind both `python
gst_intake.py <PAN_or_GSTIN> <reg_no>` and main.py's --gstin/--pan pipeline
step -- plus main._run_gst_intake_step's never-fatal contract.

gst_portal.search_gstins_by_pan / fetch_gstin_filing_table are real network
calls behind a human-solved CAPTCHA, so every test here patches them. The
filing payloads use the portal's REAL raw_text shape (tab-separated
"FY\tMonth\tdd/mm/yyyy\tFiled" rows under a "Filing details for <FORM>"
header), because run_intake parses that text via gst_portal.parse_filing_table
and treats a payload it cannot parse as "no scoreable data" -- a mock in the
wrong shape passes nothing through and tests nothing.

Run directly: python test_gst_intake.py
"""

import contextlib
import json
import os
import shutil

import gst_intake
import gst_portal
import main as main_mod

_SCRATCH_DIR = os.path.join("output", "_test_scratch_gst_intake")
_REG_NO = "P51800077150"
_REAL_GSTIN = "27AANCM5273D1ZA"
_REAL_PAN = "AANCM5273D"


def _filing_payload(*rows: tuple) -> dict:
    """Builds a fetch_gstin_filing_table-shaped result whose raw_text matches
    the portal's own layout. Each row is (financial_year, month, filing_date)."""
    body = "\n".join(f"{fy}\t{month}\t{filed}\tFiled" for fy, month, filed in rows)
    return {
        "found": True,
        "by_year": {rows[0][0]: {"raw_text": f"Filing details for GSTR3B\n{body}"}},
        "raw_text": "", "ocr_text": "", "url": "x", "note": "",
    }


@contextlib.contextmanager
def _patch_gst_portal(pan_result, filing_results_by_gstin):
    """Patches both portal calls and records every call made, so a test can
    assert the portal was never touched at all."""
    real_search = gst_portal.search_gstins_by_pan
    real_fetch = gst_portal.fetch_gstin_filing_table
    calls = []

    def _fake_search(pan, **kwargs):
        calls.append(("search", pan))
        return pan_result

    def _fake_fetch(gstin, **kwargs):
        calls.append(("fetch", gstin))
        return filing_results_by_gstin[gstin]

    gst_portal.search_gstins_by_pan = _fake_search
    gst_portal.fetch_gstin_filing_table = _fake_fetch
    try:
        yield calls
    finally:
        gst_portal.search_gstins_by_pan = real_search
        gst_portal.fetch_gstin_filing_table = real_fetch


def _raw_dir():
    return os.path.join(_SCRATCH_DIR, _REG_NO, "gst_portal_raw")


def test_rejects_a_malformed_identifier_before_touching_the_portal():
    """A typo must be caught on format alone. Every portal lookup costs a
    human a fresh CAPTCHA solve, so reaching the browser at all is the bug."""
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    with _patch_gst_portal({"found": False, "gstins": []}, {}) as calls:
        try:
            gst_intake.run_intake("not-a-gstin", _REG_NO, _SCRATCH_DIR)
            raise AssertionError("expected GstIntakeError for a malformed identifier")
        except gst_intake.GstIntakeError as e:
            assert "neither" in str(e), e
    assert calls == [], f"portal must not be touched at all, got: {calls}"
    assert not os.path.isdir(os.path.join(_SCRATCH_DIR, _REG_NO))
    print("test_rejects_a_malformed_identifier_before_touching_the_portal: PASS")


def test_discovers_multiple_gstins_and_writes_the_one_with_most_periods():
    """A promoter can hold one GSTIN per state. All of them get fetched and
    written to gst_portal_raw/, but gst_filing_input.json's schema is
    single-GSTIN, so the one with the most scoreable periods becomes primary."""
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    second = "07AANCM5273D1ZB"
    pan_result = {
        "found": True, "gstins": [_REAL_GSTIN, second],
        "raw_text": "...", "ocr_text": "", "url": "x", "note": "",
    }
    filing_results = {
        _REAL_GSTIN: _filing_payload(
            ("2024-2025", "April", "20/05/2024"),
            ("2024-2025", "May", "20/06/2024"),
            ("2024-2025", "June", "20/07/2024"),
        ),
        second: _filing_payload(("2024-2025", "April", "22/05/2024")),
    }
    with _patch_gst_portal(pan_result, filing_results) as calls:
        result = gst_intake.run_intake(_REAL_GSTIN, _REG_NO, _SCRATCH_DIR)

    assert [c[0] for c in calls] == ["search", "fetch", "fetch"], calls
    assert result["primary_gstin"] == _REAL_GSTIN, result
    assert result["period_count"] == 3, result
    assert result["scored_gstin_count"] == 2, result

    # Both GSTINs' raw responses survive for a human to review, not just the primary's.
    for g in (_REAL_GSTIN, second):
        with open(os.path.join(_raw_dir(), f"{g}_filing.json"), encoding="utf-8") as f:
            assert json.load(f)["found"] is True

    with open(os.path.join(_SCRATCH_DIR, _REG_NO, "gst_filing_input.json"), encoding="utf-8") as f:
        written = json.load(f)
    assert written["gstin"] == _REAL_GSTIN
    assert len(written["records"]) == 3
    assert written["records"][0] == {
        "form": "GSTR-3B", "period_start": "2024-04-01",
        "period_end": "2024-04-30", "filing_date": "2024-05-20",
    }, written["records"][0]

    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_discovers_multiple_gstins_and_writes_the_one_with_most_periods: PASS")


def test_accepts_a_bare_pan_and_searches_on_it_directly():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    pan_result = {
        "found": True, "gstins": [_REAL_GSTIN],
        "raw_text": "...", "ocr_text": "", "url": "x", "note": "",
    }
    filing_results = {_REAL_GSTIN: _filing_payload(("2024-2025", "April", "20/05/2024"))}
    with _patch_gst_portal(pan_result, filing_results) as calls:
        result = gst_intake.run_intake(_REAL_PAN, _REG_NO, _SCRATCH_DIR)

    assert calls[0] == ("search", _REAL_PAN), calls
    assert result["pan"] == _REAL_PAN, result
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_accepts_a_bare_pan_and_searches_on_it_directly: PASS")


def test_falls_back_to_supplied_gstin_when_pan_search_finds_nothing():
    """The PAN search is the discovery step, not the data step. If it comes
    back empty but the caller handed us a real GSTIN, that GSTIN is still
    worth fetching -- only a bare PAN leaves us with nothing to try."""
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    pan_result = {"found": False, "gstins": [], "raw_text": "", "ocr_text": "", "url": "x", "note": "no results"}
    filing_results = {_REAL_GSTIN: _filing_payload(("2024-2025", "April", "20/05/2024"))}
    with _patch_gst_portal(pan_result, filing_results):
        result = gst_intake.run_intake(_REAL_GSTIN, _REG_NO, _SCRATCH_DIR)

    assert result["primary_gstin"] == _REAL_GSTIN, result
    assert result["period_count"] == 1, result
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_falls_back_to_supplied_gstin_when_pan_search_finds_nothing: PASS")


def test_a_bare_pan_with_no_results_raises_rather_than_guessing():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    pan_result = {"found": False, "gstins": [], "raw_text": "", "ocr_text": "", "url": "x", "note": "no results"}
    with _patch_gst_portal(pan_result, {}):
        try:
            gst_intake.run_intake(_REAL_PAN, _REG_NO, _SCRATCH_DIR)
            raise AssertionError("expected GstIntakeError when a bare PAN yields no GSTINs")
        except gst_intake.GstIntakeError as e:
            assert "No GSTINs found" in str(e), e
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_a_bare_pan_with_no_results_raises_rather_than_guessing: PASS")


def test_registration_found_but_no_scoreable_periods_raises_and_keeps_raw():
    """A registration that exists but files nothing scoreable (e.g. only
    annual/composition returns) must not write an empty gst_filing_input.json
    -- but its raw response still has to survive for a human to look at."""
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    pan_result = {
        "found": True, "gstins": [_REAL_GSTIN],
        "raw_text": "...", "ocr_text": "", "url": "x", "note": "",
    }
    unscoreable = {
        "found": True,
        "by_year": {"2024-2025": {"raw_text": "Filing details for GSTR9\n2024-2025\tAnnual\t31/12/2025\tFiled"}},
        "raw_text": "", "ocr_text": "", "url": "x", "note": "",
    }
    with _patch_gst_portal(pan_result, {_REAL_GSTIN: unscoreable}):
        try:
            gst_intake.run_intake(_REAL_GSTIN, _REG_NO, _SCRATCH_DIR)
            raise AssertionError("expected GstIntakeError when nothing scoreable was parsed")
        except gst_intake.GstIntakeError as e:
            assert "scoreable filing records" in str(e), e

    assert not os.path.exists(os.path.join(_SCRATCH_DIR, _REG_NO, "gst_filing_input.json"))
    assert os.path.exists(os.path.join(_raw_dir(), f"{_REAL_GSTIN}_filing.json"))
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_registration_found_but_no_scoreable_periods_raises_and_keeps_raw: PASS")


# --- main.py's pipeline step: the never-fatal contract ----------------------

def test_pipeline_step_skips_cleanly_when_no_identifier_supplied():
    status = main_mod._run_gst_intake_step(None, _REG_NO, _SCRATCH_DIR)
    assert "not requested" in status, status
    print("test_pipeline_step_skips_cleanly_when_no_identifier_supplied: PASS")


def test_pipeline_step_never_raises_whatever_the_intake_does():
    """The whole point of wiring GST into the pipeline: a portal outage, an
    unsolved CAPTCHA, or a crash inside Playwright costs one unscored
    sub-metric, never the entire scrape."""
    real = gst_intake.run_intake
    for boom in (
        gst_intake.GstIntakeError("no GSTINs found"),
        RuntimeError("playwright browser closed unexpectedly"),
        KeyError("found"),
    ):
        def _fake(*a, _e=boom, **k):
            raise _e
        gst_intake.run_intake = _fake
        try:
            status = main_mod._run_gst_intake_step(_REAL_PAN, _REG_NO, _SCRATCH_DIR)
        finally:
            gst_intake.run_intake = real
        assert status.startswith("FAILED this run"), status
    print("test_pipeline_step_never_raises_whatever_the_intake_does: PASS")


def test_pipeline_step_reports_periods_on_success():
    real = gst_intake.run_intake
    gst_intake.run_intake = lambda *a, **k: {
        "primary_gstin": _REAL_GSTIN, "period_count": 76,
        "pan": _REAL_PAN, "gstins": [_REAL_GSTIN], "scored_gstin_count": 1,
        "input_path": "x", "raw_dir": "y",
    }
    try:
        status = main_mod._run_gst_intake_step(_REAL_PAN, _REG_NO, _SCRATCH_DIR)
    finally:
        gst_intake.run_intake = real
    assert "76 period(s)" in status and _REAL_GSTIN in status, status
    print("test_pipeline_step_reports_periods_on_success: PASS")


if __name__ == "__main__":
    test_rejects_a_malformed_identifier_before_touching_the_portal()
    test_discovers_multiple_gstins_and_writes_the_one_with_most_periods()
    test_accepts_a_bare_pan_and_searches_on_it_directly()
    test_falls_back_to_supplied_gstin_when_pan_search_finds_nothing()
    test_a_bare_pan_with_no_results_raises_rather_than_guessing()
    test_registration_found_but_no_scoreable_periods_raises_and_keeps_raw()
    test_pipeline_step_skips_cleanly_when_no_identifier_supplied()
    test_pipeline_step_never_raises_whatever_the_intake_does()
    test_pipeline_step_reports_periods_on_success()
    print("\nAll tests passed.")

"""
Tests for gst_intake.py's orchestration -- mocks gst_portal.search_gstins_by_pan
/fetch_gstin_filing_table (real network + CAPTCHA-gated browser calls) to
keep this offline.

Run directly: python test_gst_intake.py
"""

import contextlib
import json
import os
import shutil

import gst_intake
import gst_portal

_SCRATCH_DIR = os.path.join("output", "_test_scratch_gst_intake")
_REAL_GSTIN = "27AANCM5273D1ZA"


@contextlib.contextmanager
def _patch_gst_portal(pan_result, filing_results_by_gstin):
    real_search = gst_portal.search_gstins_by_pan
    real_fetch = gst_portal.fetch_gstin_filing_table

    def _fake_search(pan, **kwargs):
        return pan_result

    def _fake_fetch(gstin, **kwargs):
        return filing_results_by_gstin[gstin]

    gst_portal.search_gstins_by_pan = _fake_search
    gst_portal.fetch_gstin_filing_table = _fake_fetch
    try:
        yield
    finally:
        gst_portal.search_gstins_by_pan = real_search
        gst_portal.fetch_gstin_filing_table = real_fetch


def _run_main(args):
    import sys
    real_argv = sys.argv
    sys.argv = ["gst_intake.py"] + args
    try:
        return gst_intake.main()
    finally:
        sys.argv = real_argv


def test_rejects_invalid_gstin_before_touching_the_portal():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    exit_code = _run_main(["not-a-gstin", "P51800077150", "--output-dir", _SCRATCH_DIR])
    assert exit_code == 1
    assert not os.path.isdir(os.path.join(_SCRATCH_DIR, "P51800077150"))
    print("test_rejects_invalid_gstin_before_touching_the_portal: PASS")


def test_discovers_multiple_gstins_and_fetches_each():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    pan_result = {
        "found": True, "gstins": ["27AANCM5273D1ZA", "07AANCM5273D1ZB"],
        "raw_text": "...", "ocr_text": "", "url": "x", "note": "",
    }
    filing_results = {
        "27AANCM5273D1ZA": {"found": True, "raw_text": "MH filing table", "ocr_text": "", "url": "x", "note": ""},
        "07AANCM5273D1ZB": {"found": True, "raw_text": "DL filing table", "ocr_text": "", "url": "x", "note": ""},
    }
    with _patch_gst_portal(pan_result, filing_results):
        exit_code = _run_main([_REAL_GSTIN, "P51800077150", "--output-dir", _SCRATCH_DIR])
    assert exit_code == 0

    out_dir = os.path.join(_SCRATCH_DIR, "P51800077150", "gst_portal_raw")
    with open(os.path.join(out_dir, "pan_AANCM5273D_search.json"), encoding="utf-8") as f:
        assert json.load(f) == pan_result
    with open(os.path.join(out_dir, "27AANCM5273D1ZA_filing.json"), encoding="utf-8") as f:
        assert json.load(f)["raw_text"] == "MH filing table"
    with open(os.path.join(out_dir, "07AANCM5273D1ZB_filing.json"), encoding="utf-8") as f:
        assert json.load(f)["raw_text"] == "DL filing table"
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_discovers_multiple_gstins_and_fetches_each: PASS")


def test_falls_back_to_supplied_gstin_when_pan_search_finds_nothing():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    pan_result = {"found": False, "gstins": [], "raw_text": "", "ocr_text": "", "url": "x", "note": "no results"}
    filing_results = {_REAL_GSTIN: {"found": True, "raw_text": "fallback filing table", "ocr_text": "", "url": "x", "note": ""}}
    with _patch_gst_portal(pan_result, filing_results):
        exit_code = _run_main([_REAL_GSTIN, "P51800077150", "--output-dir", _SCRATCH_DIR])
    assert exit_code == 0

    out_dir = os.path.join(_SCRATCH_DIR, "P51800077150", "gst_portal_raw")
    with open(os.path.join(out_dir, f"{_REAL_GSTIN}_filing.json"), encoding="utf-8") as f:
        assert json.load(f)["raw_text"] == "fallback filing table"
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_falls_back_to_supplied_gstin_when_pan_search_finds_nothing: PASS")


if __name__ == "__main__":
    test_rejects_invalid_gstin_before_touching_the_portal()
    test_discovers_multiple_gstins_and_fetches_each()
    test_falls_back_to_supplied_gstin_when_pan_search_finds_nothing()
    print("\nAll tests passed.")

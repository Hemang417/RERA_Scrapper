"""
Tests for company_charter.run_gst_compliance_check (the human-supplied
GST filing intake -- gst_filing_input.json -> facts["gst_compliance_check"]
-> gst_compliance.py's pure math) and the "ask the developer" flag logic
in _classify_flags that reads its output. _score_gst_compliance itself is
covered in test_developer_score.py.

Run directly: python test_gst_compliance_check.py
"""

import json
import os
import shutil

import company_charter as cc

_SCRATCH_DIR = os.path.join("output", "_test_scratch_gst_check")
_REAL_GSTIN = "27AANCM5273D1ZA"


def _write_input(reg_no: str, payload: dict) -> str:
    project_dir = os.path.join(_SCRATCH_DIR, reg_no)
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, "gst_filing_input.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def test_returns_facts_unchanged_when_no_input_file():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    facts = {"existing": "untouched"}
    result = cc.run_gst_compliance_check(facts, "P51800077150", output_dir=_SCRATCH_DIR)
    assert result == {"existing": "untouched"}
    assert "gst_compliance_check" not in result
    print("test_returns_facts_unchanged_when_no_input_file: PASS")


def test_valid_input_populates_gst_compliance_check_and_a_source():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    _write_input("P51800077150", {
        "gstin": _REAL_GSTIN,
        "records": [
            {"form": "GSTR-3B", "period_start": "2025-04-01", "period_end": "2025-04-30", "filing_date": "2025-05-15"},
            {"form": "GSTR-3B", "period_start": "2025-05-01", "period_end": "2025-05-31", "filing_date": "2025-06-25"},
        ],
    })
    facts = {}
    result = cc.run_gst_compliance_check(facts, "P51800077150", output_dir=_SCRATCH_DIR)

    check = result["gst_compliance_check"]
    assert check["found"] is True
    assert check["gstin"] == _REAL_GSTIN
    assert check["summary"]["total_periods"] == 2
    assert check["summary"]["on_time"] == 1
    assert check["summary"]["late"] == 1

    assert len(result["sources"]) == 1
    assert result["sources"][0]["topic"] == "gst_compliance"
    assert "--" not in result["sources"][0]["ref"] and " -- " not in result["sources"][0]["ref"]
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_valid_input_populates_gst_compliance_check_and_a_source: PASS")


def test_invalid_gstin_is_an_honest_not_found_not_a_crash():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    _write_input("P51800077150", {"gstin": "not-a-real-gstin", "records": []})
    facts = {}
    result = cc.run_gst_compliance_check(facts, "P51800077150", output_dir=_SCRATCH_DIR)
    assert result["gst_compliance_check"]["found"] is False
    assert "not a validly-formatted GSTIN" in result["gst_compliance_check"]["note"]
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_invalid_gstin_is_an_honest_not_found_not_a_crash: PASS")


def test_malformed_record_is_an_honest_not_found_not_a_crash():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    _write_input("P51800077150", {
        "gstin": _REAL_GSTIN,
        "records": [{"form": "GSTR-3B", "period_start": "not-a-date", "period_end": "2025-04-30", "filing_date": None}],
    })
    facts = {}
    result = cc.run_gst_compliance_check(facts, "P51800077150", output_dir=_SCRATCH_DIR)
    assert result["gst_compliance_check"]["found"] is False
    assert "record[0] is malformed" in result["gst_compliance_check"]["note"]
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_malformed_record_is_an_honest_not_found_not_a_crash: PASS")


def test_null_filing_date_means_not_yet_filed_not_a_crash():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    _write_input("P51800077150", {
        "gstin": _REAL_GSTIN,
        "records": [{"form": "GSTR-3B", "period_start": "2020-04-01", "period_end": "2020-04-30", "filing_date": None}],
    })
    facts = {}
    result = cc.run_gst_compliance_check(facts, "P51800077150", output_dir=_SCRATCH_DIR)
    assert result["gst_compliance_check"]["found"] is True
    assert result["gst_compliance_check"]["summary"]["not_filed_yet"] == 1
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_null_filing_date_means_not_yet_filed_not_a_crash: PASS")


def _classify(summary: dict) -> dict:
    facts = {"gst_compliance_check": {"found": True, "gstin": _REAL_GSTIN, "summary": summary}}
    return cc._classify_flags(facts)


def test_classify_flags_stays_quiet_on_a_clean_pattern():
    flags = _classify({"late_pct": 0.0, "delays_last_12_months": 0})
    all_texts = flags["imminent"] + flags["structural"] + flags["monitor"]
    assert not any("GST filing pattern" in item["text"] for item in all_texts)
    print("test_classify_flags_stays_quiet_on_a_clean_pattern: PASS")


def test_classify_flags_monitor_for_a_single_old_late_filing():
    flags = _classify({"late_pct": 10.0, "delays_last_12_months": 0})
    assert any("GST filing pattern" in item["text"] for item in flags["monitor"])
    assert not any("GST filing pattern" in item["text"] for item in flags["imminent"] + flags["structural"])
    print("test_classify_flags_monitor_for_a_single_old_late_filing: PASS")


def test_classify_flags_structural_above_monitor_threshold():
    flags = _classify({"late_pct": 25.0, "delays_last_12_months": 2})
    assert any("GST filing pattern" in item["text"] for item in flags["structural"])
    assert not any("GST filing pattern" in item["text"] for item in flags["imminent"]), flags["imminent"]
    print("test_classify_flags_structural_above_monitor_threshold: PASS")


def test_classify_flags_imminent_above_imminent_threshold():
    """Recent delay count crossing the imminent threshold, per the original
    ask: a bad RECENT filing pattern is exactly what should prompt raising
    this directly with the developer."""
    flags = _classify({"late_pct": 50.0, "delays_last_12_months": 5})
    assert any("GST filing pattern" in item["text"] and "raising directly with the developer" in item["text"] for item in flags["imminent"])
    print("test_classify_flags_imminent_above_imminent_threshold: PASS")


def test_classify_flags_ignores_gst_check_when_not_found():
    facts = {"gst_compliance_check": {"found": False, "note": "invalid GSTIN"}}
    flags = cc._classify_flags(facts)
    all_texts = flags["imminent"] + flags["structural"] + flags["monitor"]
    assert not any("GST filing pattern" in item["text"] for item in all_texts)
    print("test_classify_flags_ignores_gst_check_when_not_found: PASS")


if __name__ == "__main__":
    test_returns_facts_unchanged_when_no_input_file()
    test_valid_input_populates_gst_compliance_check_and_a_source()
    test_invalid_gstin_is_an_honest_not_found_not_a_crash()
    test_malformed_record_is_an_honest_not_found_not_a_crash()
    test_null_filing_date_means_not_yet_filed_not_a_crash()
    test_classify_flags_stays_quiet_on_a_clean_pattern()
    test_classify_flags_monitor_for_a_single_old_late_filing()
    test_classify_flags_structural_above_monitor_threshold()
    test_classify_flags_imminent_above_imminent_threshold()
    test_classify_flags_ignores_gst_check_when_not_found()
    print("\nAll tests passed.")

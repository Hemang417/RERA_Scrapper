"""
Tests for Phase 3 of the "identifier can arrive before a RERA number" work:
attach_rera_number (copies a pending CIN/CTS case into a reg_no-keyed
project), _load_promoter_carryover (reuses an attached promoter profile
instead of re-running the live CIN checks), and run_cts_land_lookup's new
carryover path (reuses an attached land record and, on a genuine CTS
mismatch, sets facts["cts_mismatch_note"] -- which _classify_flags
promotes to an imminent flag, per the explicit ask: a developer-supplied
CTS not matching RERA's own record is something the investment team
should be able to question the developer about directly).

Run directly: python test_attach_rera_number.py
"""

import json
import os
import shutil

import company_charter as cc

_SCRATCH_DIR = os.path.join("output", "_test_scratch_attach")


def _write_pending_case(case_id: str, promoter_profile: dict | None = None, land_record: dict | None = None) -> str:
    pending_dir = os.path.join(_SCRATCH_DIR, "_pending", case_id)
    os.makedirs(pending_dir, exist_ok=True)
    if promoter_profile is not None:
        with open(os.path.join(pending_dir, "promoter_profile.json"), "w", encoding="utf-8") as f:
            json.dump(promoter_profile, f)
    if land_record is not None:
        with open(os.path.join(pending_dir, "land_record.json"), "w", encoding="utf-8") as f:
            json.dump(land_record, f)
    return pending_dir


def test_attach_copies_both_files_when_both_exist():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    _write_pending_case("U70109MH2022PLC385473", promoter_profile={"cin": "U70109MH2022PLC385473"}, land_record={"cts_number": "183"})

    result = cc.attach_rera_number("U70109MH2022PLC385473", "P51800077150", output_dir=_SCRATCH_DIR)
    assert result["attached"] is True
    assert result["had_promoter_profile"] is True
    assert result["had_land_record"] is True

    project_dir = os.path.join(_SCRATCH_DIR, "P51800077150")
    assert os.path.exists(os.path.join(project_dir, "promoter_profile_carryover.json"))
    assert os.path.exists(os.path.join(project_dir, "land_record_carryover.json"))

    # Copies, not moves -- the same CIN case can legitimately attach to
    # more than one of a promoter's projects.
    assert os.path.exists(os.path.join(_SCRATCH_DIR, "_pending", "U70109MH2022PLC385473", "promoter_profile.json"))
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_attach_copies_both_files_when_both_exist: PASS")


def test_attach_copies_only_whichever_file_exists():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    _write_pending_case("Pune_Wagholi_100", land_record={"cts_number": "100"})

    result = cc.attach_rera_number("Pune_Wagholi_100", "P51800011111", output_dir=_SCRATCH_DIR)
    assert result["attached"] is True
    assert result["had_promoter_profile"] is False
    assert result["had_land_record"] is True

    project_dir = os.path.join(_SCRATCH_DIR, "P51800011111")
    assert not os.path.exists(os.path.join(project_dir, "promoter_profile_carryover.json"))
    assert os.path.exists(os.path.join(project_dir, "land_record_carryover.json"))
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_attach_copies_only_whichever_file_exists: PASS")


def test_attach_reports_honest_failure_for_missing_case():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    result = cc.attach_rera_number("does_not_exist", "P51800077150", output_dir=_SCRATCH_DIR)
    assert result["attached"] is False
    assert "No pending case directory found" in result["note"]
    print("test_attach_reports_honest_failure_for_missing_case: PASS")


def test_attach_reports_honest_failure_for_empty_case_dir():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    os.makedirs(os.path.join(_SCRATCH_DIR, "_pending", "empty_case"), exist_ok=True)
    result = cc.attach_rera_number("empty_case", "P51800077150", output_dir=_SCRATCH_DIR)
    assert result["attached"] is False
    assert "neither promoter_profile.json nor land_record.json" in result["note"]
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_attach_reports_honest_failure_for_empty_case_dir: PASS")


def test_load_promoter_carryover_assigns_facts_and_returns_true():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    carryover = {
        "company_profile_check": {"found": True, "name": "PRANAMI NEEV REALTY LIMITED"},
        "ibbi_insolvency_check": {"found_process": False},
        "group_companies_check": {"found": True, "companies": ["A", "B"]},
        "credit_rating_check": {"promoter": {"ratings": []}},
        "sources": [{"label": "test source", "topic": "company_profile"}],
        "gaps": ["a carried-over gap"],
    }
    project_dir = os.path.join(_SCRATCH_DIR, "P51800077150")
    os.makedirs(project_dir, exist_ok=True)
    with open(os.path.join(project_dir, "promoter_profile_carryover.json"), "w", encoding="utf-8") as f:
        json.dump(carryover, f)

    facts = {}
    used = cc._load_promoter_carryover(facts, _SCRATCH_DIR, "P51800077150")
    assert used is True
    assert facts["company_profile_check"] == carryover["company_profile_check"]
    assert facts["ibbi_insolvency_check"] == carryover["ibbi_insolvency_check"]
    assert facts["group_companies_check"] == carryover["group_companies_check"]
    assert facts["credit_rating_check"] == carryover["credit_rating_check"]
    assert facts["sources"] == carryover["sources"]
    assert facts["gaps"] == carryover["gaps"]
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_load_promoter_carryover_assigns_facts_and_returns_true: PASS")


def test_load_promoter_carryover_returns_false_when_absent():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    facts = {"existing": "untouched"}
    used = cc._load_promoter_carryover(facts, _SCRATCH_DIR, "P51800077150")
    assert used is False
    assert facts == {"existing": "untouched"}
    print("test_load_promoter_carryover_returns_false_when_absent: PASS")


def test_cts_land_lookup_reuses_carryover_with_no_mismatch():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    project_dir = os.path.join(_SCRATCH_DIR, "P51800077150")
    os.makedirs(project_dir, exist_ok=True)
    carryover = {
        "district": "Mumbai Suburban", "village": "Aambivali", "cts_number": "183",
        "cts_land_record_check": {"found": True, "fields": {}, "raw_text": "...", "ocr_text": "...", "url": "https://bhulekh.mahabhumi.gov.in/x"},
        "sources": [{"label": "Maha Bhulekh Property Card", "topic": "land_record"}],
        "gaps": [],
    }
    with open(os.path.join(project_dir, "land_record_carryover.json"), "w", encoding="utf-8") as f:
        json.dump(carryover, f)

    facts = {"land_identification": {"survey_cts_plot_numbers": {"value": "CTS No. 183(pt), Village Aambivali"}}}
    result = cc.run_cts_land_lookup(facts, "P51800077150", output_dir=_SCRATCH_DIR)

    assert result["cts_land_record_check"] == carryover["cts_land_record_check"]
    assert "cts_mismatch_note" not in result
    assert result["sources"] == carryover["sources"]
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_cts_land_lookup_reuses_carryover_with_no_mismatch: PASS")


def test_cts_land_lookup_flags_a_genuine_cts_mismatch():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    project_dir = os.path.join(_SCRATCH_DIR, "P51800077150")
    os.makedirs(project_dir, exist_ok=True)
    carryover = {
        "district": "Mumbai Suburban", "village": "Aambivali", "cts_number": "999",  # does NOT match RERA's own record below
        "cts_land_record_check": {"found": True, "fields": {}, "raw_text": "", "ocr_text": "", "url": "https://bhulekh.mahabhumi.gov.in/x"},
        "sources": [], "gaps": [],
    }
    with open(os.path.join(project_dir, "land_record_carryover.json"), "w", encoding="utf-8") as f:
        json.dump(carryover, f)

    facts = {"land_identification": {"survey_cts_plot_numbers": {"value": "CTS No. 183(pt), Village Aambivali"}}}
    result = cc.run_cts_land_lookup(facts, "P51800077150", output_dir=_SCRATCH_DIR)

    assert "cts_mismatch_note" in result
    assert "999" in result["cts_mismatch_note"] and "183" in result["cts_mismatch_note"]

    flags = cc._classify_flags(result)
    assert any("999" in item["text"] for item in flags["imminent"]), flags["imminent"]
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_cts_land_lookup_flags_a_genuine_cts_mismatch: PASS")


def test_cts_land_lookup_carryover_takes_priority_over_input_json():
    """If both a carryover file AND a manually-dropped cts_lookup_input.json
    exist for the same project, the carryover -- already-fetched, no new
    CAPTCHA needed -- wins; the input.json path (which WOULD open a new
    browser) must not also run."""
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    project_dir = os.path.join(_SCRATCH_DIR, "P51800077150")
    os.makedirs(project_dir, exist_ok=True)
    carryover_check = {"found": True, "fields": {}, "raw_text": "carryover", "ocr_text": "", "url": "x"}
    with open(os.path.join(project_dir, "land_record_carryover.json"), "w", encoding="utf-8") as f:
        json.dump({"district": "Pune", "village": "Wagholi", "cts_number": "100", "cts_land_record_check": carryover_check, "sources": [], "gaps": []}, f)
    with open(os.path.join(project_dir, "cts_lookup_input.json"), "w", encoding="utf-8") as f:
        json.dump({"district": "Pune", "office": "x", "village": "Wagholi", "cts_number": "100", "mobile": "9999999999"}, f)

    facts = {}
    result = cc.run_cts_land_lookup(facts, "P51800077150", output_dir=_SCRATCH_DIR)
    assert result["cts_land_record_check"] == carryover_check
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_cts_land_lookup_carryover_takes_priority_over_input_json: PASS")


if __name__ == "__main__":
    test_attach_copies_both_files_when_both_exist()
    test_attach_copies_only_whichever_file_exists()
    test_attach_reports_honest_failure_for_missing_case()
    test_attach_reports_honest_failure_for_empty_case_dir()
    test_load_promoter_carryover_assigns_facts_and_returns_true()
    test_load_promoter_carryover_returns_false_when_absent()
    test_cts_land_lookup_reuses_carryover_with_no_mismatch()
    test_cts_land_lookup_flags_a_genuine_cts_mismatch()
    test_cts_land_lookup_carryover_takes_priority_over_input_json()
    print("\nAll tests passed.")

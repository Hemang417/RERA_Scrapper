"""
Guards on the TG-RERA adapter.

TG-RERA CAPTCHA-gates its own search, so `acquire()` can never run in a test
-- it needs a human at a browser. That is exactly why the mapping is
factored out of it: `map_detail_to_category_data` and `project_notes` are
PURE functions, and this file replays the real CONSTELLA capture
(output/CONSTELLA_TS/raw/ts_rera_project.json) through them with no network
and no CAPTCHA. Without that split there would be no way to regression-test
this adapter at all.

What these pin, and why each matters:

  * TELANGANA DECLARES NO CAPABILITIES, AND THAT IS A COMPLETE ADAPTER.
    Not a stub. The test asserts the empty capability set explicitly so
    nobody later "fixes" it by declaring something the portal cannot do --
    the failure mode Gujarat already hit once.

  * A DERIVED KEY MUST BE DISCLOSED AS DERIVED. TS-RERA's public record does
    not carry its own registration number, so the output folder is keyed on
    a slug. `registration_number` stays None and a note says so, because a
    pipeline-assigned key presented as a regulator-issued one would be a
    fabrication in the finished document.

  * ABSENCES MUST NOT READ AS CLEAN CHECKS. Seven categories come back None.
    Each is listed in categories_not_published so the run summary says "not
    published by TG-RERA" rather than "FAILED", and the authority-level
    limitations reach the reader through notes.

Run directly: python test_telangana_adapter.py
"""

import json
import os

import states
from states.adapter_telangana import (
    _AUTHORITY_NOTES,
    _slug,
    map_detail_to_category_data,
    project_notes,
)

# The one real TG-RERA capture this repo has: a live, human-CAPTCHA-solved
# fetch of the CONSTELLA project record from August 2026.
_CAPTURE = os.path.join("output", "CONSTELLA_TS", "raw", "ts_rera_project.json")


def _capture():
    with open(_CAPTURE, "r", encoding="utf-8") as f:
        return json.load(f)


def _parser_shaped_detail():
    """The capture was hand-curated into a nested shape for the one-off
    Charter run; ts_rera_client._parse_print_preview emits a FLAT dict.
    This rebuilds the flat shape from the capture so the mapper is tested
    against what the parser actually produces, not against a convenience
    format that no code path emits."""
    c = _capture()
    org = c.get("promoter_organization") or {}
    proj = c.get("project_information") or {}
    bank = (c.get("bank_details") or {}).get("collection_account_100pct") or {}
    return {
        "promoter_org_name": org.get("name"),
        "promoter_pan": org.get("pan"),
        "organization_type": org.get("organization_type"),
        "gstin": org.get("gstin"),
        "authority_name": proj.get("authority_name"),
        "plan_approval_number": proj.get("plan_approval_number"),
        "project_name": proj.get("project_name"),
        "project_status": proj.get("project_status"),
        "approved_date": proj.get("approved_date"),
        "proposed_date_of_completion": proj.get("proposed_date_of_completion"),
        "litigations_related_to_project": "No" if proj.get("litigations_related_to_project_declared") is False else "Yes",
        "project_type": proj.get("project_type"),
        "collection_bank_name": bank.get("bank_name"),
        "collection_ifsc": bank.get("ifsc_code"),
        "mortgage_area_sqmt": "4500.00",
        "members": c.get("members") or [],
        "land_owner_investor_promoters": c.get("land_owner_investor_promoters") or [],
        "has_zero_progress_signal": False,
        "raw_text": "Separate Account of the Project (70%)\nBank Name\n\nTransaction Account",
    }


def test_the_capture_confirms_no_registration_number_is_published():
    """The load-bearing fact behind this whole adapter's design. If TG-RERA
    ever starts publishing one, CAP_LOOKUP_BY_REG_NO becomes possible and
    the derived-key machinery can go."""
    c = _capture()
    assert c.get("official_ts_rera_registration_certificate_number") is None, c.get(
        "official_ts_rera_registration_certificate_number"
    )
    assert "_gap_registration_number" in c, sorted(c)
    print("test_the_capture_confirms_no_registration_number_is_published: PASS")


def test_telangana_declares_no_capabilities_and_that_is_valid():
    tg = states.PROFILES["TG"]
    assert tg.capabilities == frozenset(), tg.capabilities
    for capability in states.ALL_CAPABILITIES:
        assert not tg.can(capability), capability
    # ...and it still resolves to a real adapter rather than raising.
    assert states.get_adapter("TG").profile.code == "TG"
    print("test_telangana_declares_no_capabilities_and_that_is_valid: PASS")


def test_the_mapper_fills_the_categories_telangana_actually_publishes():
    data = map_detail_to_category_data(_parser_shaped_detail())

    project = data["projects"]
    assert project["projectName"] == "CONSTELLA", project
    assert project["authorityName"] == "HMDA", project
    assert project["planApprovalNumber"] == "40/LO/Plg/HMDA/2022", project

    promoter = data["partners"]["promoterDetails"]
    assert promoter["promoterName"] == "SPEED INFRA DEVELOPERS LLP", promoter
    assert promoter["panNumber"] == "AELFS2377H", promoter
    assert promoter["gstin"] == "36AELFS2377H1ZM", promoter
    # Four members on the real record: three partners and an authorised signatory.
    assert len(promoter["members"]) == 4, promoter["members"]
    print("test_the_mapper_fills_the_categories_telangana_actually_publishes: PASS")


def test_unpublished_categories_are_none_not_empty_collections():
    """None means "this authority does not publish it". An empty list would
    read downstream as "published, and there were none" -- which for a
    complaint register is the false-clean-record failure again."""
    data = map_detail_to_category_data(_parser_shaped_detail())
    for category in ("professionals", "spocs", "sro_details",
                     "past_experiences", "documents", "complaints", "appeals"):
        assert data[category] is None, (category, data[category])
    print("test_unpublished_categories_are_none_not_empty_collections: PASS")


def test_the_mapper_survives_a_completely_empty_parse():
    """A PrintPreview that parsed to nothing must not crash the run -- the
    Charter can still be built from whatever else the pipeline has."""
    data = map_detail_to_category_data({})
    assert data["projects"]["projectName"] is None
    assert data["partners"]["promoterDetails"]["promoterName"] == ""
    assert map_detail_to_category_data(None)["complaints"] is None
    print("test_the_mapper_survives_a_completely_empty_parse: PASS")


def test_the_derived_key_is_stable_and_marked_as_derived():
    """Two runs of the same project must land in one folder, and the reader
    must be told the key is ours, not the authority's."""
    assert _slug("CONSTELLA") == "CONSTELLA_TG"
    assert _slug("CONSTELLA") == _slug("  constella  ".upper().strip())
    assert _slug("Green Field Phase-II") == "GREEN_FIELD_PHASE_II_TG"
    assert _slug("") == "UNKNOWN_TG"
    disclosure = [n for n in _AUTHORITY_NOTES if "assigned by this pipeline" in n]
    assert disclosure, "the derived key is never disclosed to the reader"
    print("test_the_derived_key_is_stable_and_marked_as_derived: PASS")


def test_project_notes_flag_the_escrow_gap_and_the_unnamed_mortgage():
    """Two real findings from the CONSTELLA record, both of which a reader
    would want and neither of which the portal states outright."""
    notes = project_notes(_parser_shaped_detail())
    joined = " ".join(notes)
    assert "70% Separate Account" in joined, notes
    assert "4500.00" in joined and "names no lender" in joined, notes
    # The litigation declaration must be attributed, never asserted as fact.
    assert "promoter's own declaration" in joined, notes
    print("test_project_notes_flag_the_escrow_gap_and_the_unnamed_mortgage: PASS")


def test_a_clean_record_produces_no_spurious_findings():
    """The notes must be findings, not boilerplate -- so a record with
    nothing notable produces none of them."""
    notes = project_notes({
        "raw_text": "",
        "collection_bank_name": None,
        "mortgage_area_sqmt": "0",
        "has_zero_progress_signal": False,
        "litigations_related_to_project": "Yes",
    })
    assert notes == [], notes
    print("test_a_clean_record_produces_no_spurious_findings: PASS")


def test_authority_notes_never_claim_a_clean_check():
    """Wording guard. "No complaints found" and "this authority publishes no
    complaint register" are different claims, and only one of them is true
    here."""
    joined = " ".join(_AUTHORITY_NOTES).lower()
    assert "not published by this authority" in joined, _AUTHORITY_NOTES
    assert "none exist" in joined, "the note must say what the absence does NOT mean"
    print("test_authority_notes_never_claim_a_clean_check: PASS")


if __name__ == "__main__":
    test_the_capture_confirms_no_registration_number_is_published()
    test_telangana_declares_no_capabilities_and_that_is_valid()
    test_the_mapper_fills_the_categories_telangana_actually_publishes()
    test_unpublished_categories_are_none_not_empty_collections()
    test_the_mapper_survives_a_completely_empty_parse()
    test_the_derived_key_is_stable_and_marked_as_derived()
    test_project_notes_flag_the_escrow_gap_and_the_unnamed_mortgage()
    test_a_clean_record_produces_no_spurious_findings()
    test_authority_notes_never_claim_a_clean_check()
    print("\nAll tests passed.")

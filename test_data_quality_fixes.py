"""
Regression tests for three real data-quality bugs found by reading the
Pranami Bliss Charter's own assembled facts, all fixed in company_charter.py:

1. `local_planning.professionals_of_record` was MODEL-authored from the
   downloaded documents and reported "Engineer and CA firms of record were
   listed in MahaRERA's professionals category but not individually named"
   -- while that very category, already fetched and handed to the model,
   named all five with their registration numbers. Now code-computed via
   summarize_professionals, same policy as document_library.

2. ZaubaCorp's free tier gates its Subsidiaries/Associates table and puts
   its upsell copy in the cells where the values belong -- INCLUDING the
   company-name cell. The Charter rendered
   "subsidiary/associate/JV (This information is part of the paid company
   report.Purchase Report shares held)" as if it were data, and counted 5
   advert rows as named related entities. Now detected by _looks_paywalled,
   excluded from the named list, and reported as a count instead.

3. Names arrived in inconsistent shapes in the same table -- "SUNDEEP
   PODDAR" (double space, ALL CAPS) beside "Vimanam Realty LLP" (title
   case) and "SAHEJTA REALITY LLP" (ALL CAPS). Now normalised by
   _normalise_entity_name wherever a name reaches a table.

Run directly: python test_data_quality_fixes.py
"""

from bs4 import BeautifulSoup

import company_charter as cc


# --- 3. name normalisation ------------------------------------------------

def test_normalise_entity_name():
    """Collapses whitespace always; title-cases ONLY input that arrived
    effectively ALL CAPS, so a deliberately mixed-case name is never
    re-styled into something its owner doesn't use."""
    assert cc._normalise_entity_name("SUNDEEP  PODDAR") == "Sundeep Poddar"
    assert cc._normalise_entity_name("BIJAY KUMAR AGARWAL") == "Bijay Kumar Agarwal"
    assert cc._normalise_entity_name("PRANAMI NEEV REALTY LIMITED") == "Pranami Neev Realty Limited"
    # Corporate-form tokens must survive title-casing intact.
    assert cc._normalise_entity_name("SAHEJTA REALITY LLP") == "Sahejta Reality LLP"
    assert cc._normalise_entity_name("ACME BUILDERS PVT LTD") == "Acme Builders Pvt Ltd"
    # Already mixed case -> untouched apart from whitespace.
    assert cc._normalise_entity_name("A plus Architects plus Planners") == "A plus Architects plus Planners"
    assert cc._normalise_entity_name("Vimanam Realty LLP") == "Vimanam Realty LLP"
    assert cc._normalise_entity_name("  Vinod   Gowadia  ") == "Vinod Gowadia"
    # Missing name must not become the string "None".
    assert cc._normalise_entity_name(None) == ""
    assert cc._normalise_entity_name("") == ""
    print("test_normalise_entity_name: PASS")


def test_normalised_display_name_never_changes_roster_identity():
    """_merge_director_rosters keys on DIN (or a normalised-for-compare
    name) BEFORE display normalisation, so normalising a display name can
    never merge or split two directors. Same DIN across two sources with
    differently-cased names must stay ONE director, confirmed by both."""
    merged, conflicts = cc._merge_director_rosters([
        ("zaubacorp.com", [{"DIN": "00448678", "Director Name": "BIJAY KUMAR AGARWAL", "Designation": "Director"}]),
        ("instafinancials.com", [{"DIN": "00448678", "Director Name": "Bijay Kumar Agarwal", "Designation": "Director"}]),
    ])
    assert len(merged) == 1, merged
    assert merged[0]["Director Name"] == "Bijay Kumar Agarwal", merged
    assert conflicts == [], conflicts
    print("test_normalised_display_name_never_changes_roster_identity: PASS")


# --- 2. paywall detection -------------------------------------------------

def test_looks_paywalled():
    assert cc._looks_paywalled(cc._ZAUBACORP_GATED_NOTE) is True
    # The real failing case: the placeholder concatenated with surrounding
    # text, which the older exact-match check never caught.
    assert cc._looks_paywalled("This information is part of the paid company report.Purchase Report shares held") is True
    assert cc._looks_paywalled("Subscribe to view") is True
    assert cc._looks_paywalled("51%") is False
    assert cc._looks_paywalled("100") is False
    assert cc._looks_paywalled("") is False
    assert cc._looks_paywalled(None) is False
    print("test_looks_paywalled: PASS")


def test_zaubacorp_clean_delegates_to_the_broader_check():
    """One definition of "paywalled" in the file: _zaubacorp_clean must now
    also reject the concatenated form, not just the exact placeholder."""
    assert cc._zaubacorp_clean(cc._ZAUBACORP_GATED_NOTE) is None
    assert cc._zaubacorp_clean("This information is part of the paid company report.Purchase Report shares held") is None
    assert cc._zaubacorp_clean("Rs 1,00,000") == "Rs 1,00,000"
    assert cc._zaubacorp_clean(None) is None
    print("test_zaubacorp_clean_delegates_to_the_broader_check: PASS")


def _fake_zaubacorp_group_page():
    """Mimics the real ZaubaCorp structure: a gated Subsidiaries table whose
    Name AND Percentage cells both carry the upsell copy, plus a genuine
    Other-Directorships table."""
    gated = cc._ZAUBACORP_GATED_NOTE
    html = f"""
    <html><body>
      <h3>Other Directorships of VIJAY KUMAR MOHTA</h3>
      <table>
        <tr><th>CIN</th><th>Company Name</th><th>Designation</th></tr>
        <tr><td>U11111MH2020PTC111111</td><td>SAHEJTA REALITY LLP</td><td>Designated Partner</td></tr>
      </table>
      <h3>Subsidiaries, Associate Companies and Joint Ventures</h3>
      <table>
        <tr><th>Name</th><th>Company Identifier</th><th>Percentage of Shares Held</th></tr>
        <tr><td>{gated}</td><td>{gated}</td><td>{gated}</td></tr>
        <tr><td>{gated}</td><td>{gated}</td><td>{gated}</td></tr>
        <tr><td>REAL SUBSIDIARY LIMITED</td><td>U22222MH2021PTC222222</td><td>51%</td></tr>
      </table>
    </body></html>
    """
    return BeautifulSoup(html, "html.parser")


def test_paywalled_rows_are_counted_not_listed_as_named_entities():
    """A row whose IDENTITY is paywalled must never appear as a named
    entity -- listing a row named after an advert is worse than reporting
    that the relationship exists but is undisclosed. A row with a real name
    on the same table must still come through, with its shareholding."""
    real_fetch = cc._zaubacorp_fetch
    real_corroborate = cc._corroborate_group_companies
    cc._zaubacorp_fetch = lambda cin: (_fake_zaubacorp_group_page(), "https://zaubacorp.test/x")
    cc._corroborate_group_companies = lambda cin, companies: {"agrees": None}  # network-free
    try:
        result = cc.find_group_companies_by_cin("U70109MH2022PLC385473")
    finally:
        cc._zaubacorp_fetch = real_fetch
        cc._corroborate_group_companies = real_corroborate

    names = {c["name"] for c in result["companies"]}
    assert "Real Subsidiary Limited" in names, names          # real row kept, and title-cased
    assert "Sahejta Reality LLP" in names, names              # ALL CAPS normalised
    assert not any(cc._looks_paywalled(n) for n in names), names
    # Both gated rows counted, neither named.
    assert result["undisclosed_relationship_counts"] == {"subsidiary/associate/JV": 2}, result["undisclosed_relationship_counts"]
    # The genuine shareholding figure must survive on the real row.
    real_row = next(c for c in result["companies"] if c["name"] == "Real Subsidiary Limited")
    assert any("51%" in b for b in real_row["basis"]), real_row
    print("test_paywalled_rows_are_counted_not_listed_as_named_entities: PASS")


def test_corroboration_distinguishes_no_check_from_disagreement():
    """"The second source couldn't be read" is a different finding from "the
    two sources disagree" -- agrees must be None, never False, in the former
    case, the same distinction _verify_one_field already draws."""
    real = cc._instafinancials_directorship_count
    cc._instafinancials_directorship_count = lambda cin: None
    try:
        out = cc._corroborate_group_companies("U1", [{"basis": ["shared director: X"]}])
    finally:
        cc._instafinancials_directorship_count = real
    assert out["agrees"] is None, out
    assert "uncorroborated, not contradicted" in out["note"], out

    cc._instafinancials_directorship_count = lambda cin: 26
    try:
        out = cc._corroborate_group_companies("U1", [{"basis": ["shared director: X"]}] * 37)
    finally:
        cc._instafinancials_directorship_count = real
    assert out["independent_directorship_count"] == 26, out
    assert out["zaubacorp_shared_director_entities"] == 37, out
    assert out["agrees"] is True, out  # same order of magnitude
    print("test_corroboration_distinguishes_no_check_from_disagreement: PASS")


# --- 1. code-computed professionals ---------------------------------------

_REAL_PROFESSIONALS = [
    {"professionalTypeName": "Architect", "entityCompanyName": "A plus Architects plus Planners",
     "professionalPersonalTypeName": "Person / Individual", "architectCoARegistrationNo": "a"},
    {"professionalTypeName": "Engineer", "entityCompanyName": "Nexus Project Solutions Pvt Ltd",
     "professionalPersonalTypeName": None, "engineerLicenseNo": None},
    {"professionalTypeName": "Chartered Accountant", "entityCompanyName": "Vinod Gowadia",
     "professionalPersonalTypeName": "Person / Individual", "caIcaiMembershipNo": "039352"},
    {"professionalTypeName": "Other", "entityCompanyName": "Jai Bhawani Estate Pvt Ltd"},
    {"professionalTypeName": "Other", "entityCompanyName": "Induslaw"},
]


def test_summarize_professionals_names_every_role_from_structured_data():
    """The exact regression: all five must be named with their roles, where
    the model's own prose claimed the engineer and CA "were not individually
    named"."""
    team = cc.summarize_professionals({"professionals": _REAL_PROFESSIONALS})
    assert len(team) == 5, team
    by_role = {t["role"]: t for t in team}
    assert by_role["Engineer"]["name"] == "Nexus Project Solutions Pvt Ltd"
    assert by_role["Chartered Accountant"]["name"] == "Vinod Gowadia"
    assert by_role["Chartered Accountant"]["registration_number"] == "039352"
    assert by_role["Chartered Accountant"]["registration_label"] == "ICAI membership"
    assert by_role["Chartered Accountant"]["is_individual"] is True
    assert by_role["Engineer"]["is_individual"] is False
    print("test_summarize_professionals_names_every_role_from_structured_data: PASS")


def test_summarize_professionals_reports_a_junk_registration_as_filed():
    """One real project's architect CoA number is literally "a". That's a
    filing-quality signal and must reach the reader as filed, not be
    silently cleaned away for looking wrong."""
    team = cc.summarize_professionals({"professionals": _REAL_PROFESSIONALS})
    architect = next(t for t in team if t["role"] == "Architect")
    assert architect["registration_number"] == "a", architect
    print("test_summarize_professionals_reports_a_junk_registration_as_filed: PASS")


def test_summarize_professionals_empty_when_category_absent():
    """No professionals on record -> [] so a caller can render an honest
    "none on record" line, never a fabricated entry."""
    assert cc.summarize_professionals({}) == []
    assert cc.summarize_professionals({"professionals": None}) == []
    assert cc.summarize_professionals({"professionals": []}) == []
    # An entry with no usable name at all is skipped rather than rendered blank.
    assert cc.summarize_professionals({"professionals": [{"professionalTypeName": "Architect"}]}) == []
    print("test_summarize_professionals_empty_when_category_absent: PASS")


if __name__ == "__main__":
    test_normalise_entity_name()
    test_normalised_display_name_never_changes_roster_identity()
    test_looks_paywalled()
    test_zaubacorp_clean_delegates_to_the_broader_check()
    test_paywalled_rows_are_counted_not_listed_as_named_entities()
    test_corroboration_distinguishes_no_check_from_disagreement()
    test_summarize_professionals_names_every_role_from_structured_data()
    test_summarize_professionals_reports_a_junk_registration_as_filed()
    test_summarize_professionals_empty_when_category_absent()
    print("\nAll tests passed.")

"""
Guards on the WBRERA adapter.

TWO BUGS THIS FILE EXISTS FOR, both found live and both about documents.

  1. THE PROJECT'S FILINGS ARE ON A DIFFERENT HOST, OVER PLAIN HTTP.
     WBRERA serves them from doc.repository.semtwb.in. The adapter's first
     version fetched them through the legacy-TLS urllib3 pool that
     rera.wb.gov.in requires -- and urllib3 rejects `assert_hostname` on a
     NON-TLS connection with a TypeError. Every http:// document failed.
     267 of 275 filings were reported unretrievable for that reason alone,
     which reads in the Charter as a promoter who filed almost nothing.

  2. THE PAGE ALSO LINKS THE PORTAL'S OWN FURNITURE. The West Bengal Real
     Estate Rules, four user manuals, and authority orders under /scrol/
     are linked from every project page. Counted as project documents they
     inflate the library with files the promoter never filed -- so a reader
     counting filings counts the portal's own paperwork, and a reader
     looking for a missing filing believes it is present.

The classification is therefore explicit: a document belongs to this
project only if it is on the document host AND under one of the
project-scoped path segments. is_project_document is pure so that rule is
testable without the portal.

A THIRD THING PINNED HERE IS AN ABSENCE OF A CAPABILITY. WBRERA publishes
no promoter search and its state index does not name the promoter, so a
promoter portfolio is not derivable without opening all 4,721 project
pages. The profile must not claim CAP_PROMOTER_PORTFOLIO -- Gujarat shipped
a declared-but-undelivered capability once already.

Network tests are opt-in: WBRERA_LIVE=1.

Run directly: python test_westbengal_adapter.py
"""

import os

import states
from states.adapter_westbengal import (
    document_entries,
    is_project_document,
    parse_project_detail,
    parse_state_index,
    project_notes,
)
from states.base import storage_key

_LIVE = os.environ.get("WBRERA_LIVE") == "1"
_REG = "WBRERA/P/NOR/2024/002162"

_REPO = "http://doc.repository.semtwb.in/attachments/GridAttach/rera"

_DOC_HTML = f"""
<a href="{_REPO}/nproj/11835000000008/axp_gridattach_1/Pan Card.pdf">Pan Card.pdf</a>
<a href="{_REPO}/nproj/11835000000008/axp_gridattach_2/Balance Sheet FY 2022-23.pdf">Balance Sheet</a>
<a href="{_REPO}/aproj/11835000000008/axp_gridattach_1/Location Plan.pdf">Download</a>
<a href="{_REPO}/upcer/nproj/11835000000008.pdf">VIEW CERTIFICATE</a>
<a href="{_REPO}/scrol/12854000000128/axp_gridattach_2/QPR_User_Manual.pdf">QPR manual</a>
<a href="img/pdf/West-Bengal-Real-Estate-Rules.pdf">Rules</a>
<a href="./assets/pdf/sample.pdf">Sample</a>
"""


def _detail(litigation_html):
    return f"""
    <table><tr><th>Sl No.</th><th>Promoter Name</th><th>Firm Name</th>
      <th>Establishment Year</th><th>Contact</th><th>Email ID</th><th>Address</th></tr>
      <tr><td>1</td><td>Srijan Residency LLP</td><td>Srijan Residency LLP</td>
          <td>2016</td><td>NA</td><td>x@y.in</td><td>Kolkata</td></tr>
    </table>
    <table><tr><th>Sl No.</th><th>Consultant Name</th><th>Consultant Address</th>
      <th>Consultant Type</th></tr>
      <tr><td>1</td><td>Maheshwari &amp; Associates</td><td>Kolkata</td><td>Architect</td></tr>
    </table>
    {litigation_html}
    """


_LITIGATION_NA = """<table><tr><th>Sl No.</th><th>Litigations</th></tr>
<tr><td>1</td><td>NA</td></tr></table>"""
_LITIGATION_REAL = """<table><tr><th>Sl No.</th><th>Litigations</th></tr>
<tr><td>1</td><td>WP 1234 of 2023 pending before the High Court</td></tr></table>"""


# --- the documents bugs ---------------------------------------------------

def test_only_this_projects_own_filings_count_as_documents():
    """The portal's Rules, user manuals and authority orders are linked from
    every project page. Counting them would inflate every document library
    on the portal with files no promoter ever filed."""
    entries = document_entries(_DOC_HTML)
    labels = [e["label"] for e in entries]
    assert len(entries) == 4, labels
    assert "QPR manual" not in labels, labels
    assert "Rules" not in labels, labels
    assert "Sample" not in labels, labels
    assert "Pan Card.pdf" in labels, labels
    print("test_only_this_projects_own_filings_count_as_documents: PASS")


def test_the_classification_rule_is_explicit_about_each_case():
    assert is_project_document(f"{_REPO}/nproj/1/x/Pan Card.pdf") is True
    assert is_project_document(f"{_REPO}/aproj/1/x/Plan.pdf") is True
    assert is_project_document(f"{_REPO}/upcer/nproj/1.pdf") is True
    # Site-wide scrolling-notice area, same for every project.
    assert is_project_document(f"{_REPO}/scrol/1/x/QPR_User_Manual.pdf") is False
    # Served by the portal itself: chrome, not filings.
    assert is_project_document("img/pdf/West-Bengal-Real-Estate-Rules.pdf") is False
    # Not a document at all.
    assert is_project_document(f"{_REPO}/nproj/1/x/index.html") is False
    print("test_the_classification_rule_is_explicit_about_each_case: PASS")


def test_backslashed_hrefs_are_normalised():
    """The portal emits Windows separators in some hrefs, which break both
    the suffix match and the URL."""
    entries = document_entries(
        f'<a href="{_REPO}\\\\nproj\\\\1\\\\Pan Card.pdf">Pan Card</a>'
    )
    assert len(entries) == 1, entries
    assert "\\\\" not in entries[0]["url"], entries[0]["url"]
    print("test_backslashed_hrefs_are_normalised: PASS")


def test_the_pan_card_label_is_one_promoter_identity_recognises():
    import promoter_identity as pi

    labels = [e["label"] for e in document_entries(_DOC_HTML)]
    assert any(any(h in f" {l.lower()} " for h in pi._PAN_DOC_HINTS) for l in labels), labels
    print("test_the_pan_card_label_is_one_promoter_identity_recognises: PASS")


# --- litigation -----------------------------------------------------------

def test_a_declared_nil_litigation_return_is_not_the_same_as_no_field():
    """WBRERA writes the literal "NA" when a promoter declares none. That is
    an answer. A page with no litigation field at all is not."""
    declared = parse_project_detail(_detail(_LITIGATION_NA))
    assert declared["litigation"] == [], declared["litigation"]
    assert declared["litigation_declared_none"] is True
    assert "stated nil return" in " ".join(project_notes(declared))

    missing = parse_project_detail(_detail(""))
    assert missing["litigation_table_present"] is False
    notes = " ".join(project_notes(missing))
    assert "UNKNOWN" in notes and "must not be read as a clean litigation record" in notes, notes
    print("test_a_declared_nil_litigation_return_is_not_the_same_as_no_field: PASS")


def test_a_real_litigation_row_survives_the_na_filter():
    """The NA filter must not eat genuine entries."""
    parsed = parse_project_detail(_detail(_LITIGATION_REAL))
    assert len(parsed["litigation"]) == 1, parsed["litigation"]
    assert parsed["litigation_declared_none"] is False
    print("test_a_real_litigation_row_survives_the_na_filter: PASS")


# --- the state index ------------------------------------------------------

def test_the_state_index_needs_a_procode_to_be_usable():
    html = """<table><tr><th>Sl No.</th><th>Project ID</th><th>Project Name</th>
      <th>Completion Date</th><th>Registration No</th><th>Registration Date</th></tr>
      <tr><td>1</td><td>WBRERA/NPR-003009</td><td>Optima Phase 1</td><td>31-12-2034</td>
          <td>WBRERA/P/NOR/2024/002162</td><td>18-11-2024</td>
          <td><a href="project_details.php?procode=11835000000008">view</a></td></tr>
      <tr><td>2</td><td>WBRERA/NPR-000001</td><td>No Link</td><td>x</td><td>y</td><td>z</td></tr>
    </table>"""
    index = parse_state_index(html)
    assert len(index) == 1, index
    assert index[0]["procode"] == "11835000000008"
    assert index[0]["reg_no"] == "WBRERA/P/NOR/2024/002162"
    assert parse_state_index("<html></html>") == []
    print("test_the_state_index_needs_a_procode_to_be_usable: PASS")


# --- profile honesty ------------------------------------------------------

def test_west_bengal_does_not_claim_a_promoter_portfolio():
    """The index does not name the promoter and there is no promoter search,
    so the capability cannot be delivered. Gujarat declared one it could not
    deliver once; this pins that West Bengal does not repeat it."""
    profile = states.PROFILES["WB"]
    assert not profile.can(states.CAP_PROMOTER_PORTFOLIO)
    assert not profile.can(states.CAP_ORDERS_SEARCH)
    assert not profile.can(states.CAP_LAND_RECORDS)
    assert profile.can(states.CAP_LOOKUP_BY_REG_NO)
    assert profile.can(states.CAP_DOCUMENTS)
    print("test_west_bengal_does_not_claim_a_promoter_portfolio: PASS")


def test_the_hira_history_is_stated_because_absence_here_is_ambiguous():
    """West Bengal ran its own statute until the Supreme Court struck it
    down in May 2021, so a genuine pre-2021 project can be missing from this
    register entirely. A reader must be told that before reading an absence
    as a finding."""
    from states.adapter_westbengal import _AUTHORITY_NOTES

    joined = " ".join(_AUTHORITY_NOTES)
    assert "Housing Industry Regulation Act" in joined, joined
    assert "may legitimately have no record" in joined, joined
    print("test_the_hira_history_is_stated_because_absence_here_is_ambiguous: PASS")


def test_registration_format_resolves_to_west_bengal():
    profiles, _ = states.candidate_profiles(_REG)
    assert [p.code for p in profiles] == ["WB"], [p.code for p in profiles]
    key = storage_key(_REG)
    assert "/" not in key, key
    print("test_registration_format_resolves_to_west_bengal: PASS")


# --- live ------------------------------------------------------------------

def test_live_state_index_and_detail():
    if not _LIVE:
        print("test_live_state_index_and_detail: SKIPPED (set WBRERA_LIVE=1)")
        return
    import main
    import tempfile

    ctx = states.AcquisitionContext(output_dir=tempfile.mkdtemp(), reporter=main.CliReporter())
    result = states.get_adapter("WB").acquire(_REG, ctx)
    assert result.registration_number == _REG
    assert result.promoter_name, result.promoter_name
    assert result.promoter_portfolio is None, "WBRERA cannot deliver a portfolio"
    assert "complaints" in result.categories_not_published
    print("test_live_state_index_and_detail: PASS")


# --- opening one project -------------------------------------------------
#
# West Bengal cannot be SEARCHED by promoter: its index does not name the
# promoter, which is exactly why opening the project matters more here than
# anywhere else. The promoter's name is on the project page and NOWHERE
# else, so the open is the only way to attach a WB registration to a name.

_WB_INDEX = [{
    "project_id": "WBRERA/NPR-003432",
    "project_name": "Crown",
    "completion_date": "31-12-2034",
    "reg_no": "WBRERA/P/NOR/2025/002592",
    "registration_date": "11-03-2025",
    "procode": "16792000000025",
}]

_WB_DETAIL_HTML = """
<table><tr><th>Promoter Name</th><th>Firm Name</th><th>Email ID</th></tr>
       <tr><td>AARIKA CONSTRUCTION LLP</td><td>AARIKA CONSTRUCTION LLP</td>
           <td>x@y.com</td></tr></table>
<table><tr><th>Consultant Name</th><th>Consultant Type</th><th>Consultant Address</th></tr>
       <tr><td>An Architect</td><td>Architect</td><td>Kolkata</td></tr></table>
<table><tr><th>Litigations</th></tr><tr><td>NA</td></tr></table>
"""


def _wb_open(ref, index=None, detail_html=None):
    from states import adapter_westbengal as wb

    saved = (wb._get, wb._pool, list(wb._INDEX_CACHE))
    wb._pool = lambda: None
    wb._INDEX_CACHE[:] = _WB_INDEX if index is None else index
    wb._get = lambda pool, url, ctx, what="page": (
        _WB_DETAIL_HTML if detail_html is None else detail_html)
    try:
        return wb.fetch_project_summary(ref)
    finally:
        wb._get, wb._pool = saved[0], saved[1]
        wb._INDEX_CACHE[:] = saved[2]


def test_westbengal_opens_a_project_and_names_its_promoter():
    """THE POINT OF THIS ONE. WBRERA's index carries no promoter, so before
    this a West Bengal registration could be listed and never attached to a
    name."""
    summary = _wb_open("WBRERA/P/NOR/2025/002592")
    assert summary["opened"] is True, summary
    assert summary["promoter_name"] == "AARIKA CONSTRUCTION LLP", summary
    assert summary["project_name"] == "Crown", summary
    assert len(summary["professionals"]) == 1, summary["professionals"]
    print("test_westbengal_opens_a_project_and_names_its_promoter: PASS")


def test_westbengal_opens_by_any_of_the_three_identifiers_a_row_carries():
    for ref in ("WBRERA/P/NOR/2025/002592", "WBRERA/NPR-003432", "16792000000025"):
        assert _wb_open(ref)["opened"] is True, ref
    print("test_westbengal_opens_by_any_of_the_three_identifiers_a_row_carries: PASS")


def test_westbengal_a_stated_nil_litigation_is_not_a_missing_one():
    """WBRERA writes the literal 'NA' for none, which is an ANSWER. A page
    with no litigation table at all is not, and the two must never collapse
    into the same output."""
    summary = _wb_open("WBRERA/P/NOR/2025/002592")
    assert summary["litigation"] == [], summary["litigation"]
    assert any("stated nil return" in n for n in summary["notes"]), summary["notes"]

    silent = _WB_DETAIL_HTML.replace(
        "<table><tr><th>Litigations</th></tr><tr><td>NA</td></tr></table>", "")
    summary = _wb_open("WBRERA/P/NOR/2025/002592", detail_html=silent)
    assert any("UNKNOWN" in n for n in summary["notes"]), summary["notes"]
    assert any("must not be read as a clean litigation record" in n
               for n in summary["notes"]), summary["notes"]
    print("test_westbengal_a_stated_nil_litigation_is_not_a_missing_one: PASS")


def test_westbengal_an_absence_carries_the_2021_statute_caveat():
    """West Bengal ran its own HIRA statute until the Supreme Court struck it
    down in May 2021, so a genuine pre-2021 project may have no WBRERA record
    at all. An absence here must never read as 'unregistered'."""
    summary = _wb_open("WBRERA/P/NOR/1999/000001")
    assert summary["opened"] is False, summary
    assert "HIRA" in summary["note"], summary["note"]
    print("test_westbengal_an_absence_carries_the_2021_statute_caveat: PASS")


def test_westbengal_an_unreadable_register_is_not_an_absence():
    summary = _wb_open("WBRERA/P/NOR/2025/002592", index=[])
    assert summary["opened"] is False, summary
    assert "could not be read rather than" in summary["note"], summary["note"]
    assert _wb_open("")["note"].startswith("No WBRERA project reference")
    print("test_westbengal_an_unreadable_register_is_not_an_absence: PASS")


if __name__ == "__main__":
    test_westbengal_opens_a_project_and_names_its_promoter()
    test_westbengal_opens_by_any_of_the_three_identifiers_a_row_carries()
    test_westbengal_a_stated_nil_litigation_is_not_a_missing_one()
    test_westbengal_an_absence_carries_the_2021_statute_caveat()
    test_westbengal_an_unreadable_register_is_not_an_absence()
    test_only_this_projects_own_filings_count_as_documents()
    test_the_classification_rule_is_explicit_about_each_case()
    test_backslashed_hrefs_are_normalised()
    test_the_pan_card_label_is_one_promoter_identity_recognises()
    test_a_declared_nil_litigation_return_is_not_the_same_as_no_field()
    test_a_real_litigation_row_survives_the_na_filter()
    test_the_state_index_needs_a_procode_to_be_usable()
    test_west_bengal_does_not_claim_a_promoter_portfolio()
    test_the_hira_history_is_stated_because_absence_here_is_ambiguous()
    test_registration_format_resolves_to_west_bengal()
    test_live_state_index_and_detail()
    print("\nAll tests passed.")

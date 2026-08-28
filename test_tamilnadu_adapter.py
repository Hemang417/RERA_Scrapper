"""
Guards on the TNRERA adapter.

THE FINDING THIS FILE IS BUILT ON. Tamil Nadu's project register is served
by two different applications, and their coverage does not meet. Counted
live on 24 August 2026, the 2024 Building register:

    static per-year register   96 rows   serials   1..96
    current application       313 rows   serials 301..613
    overlap                     0 rows
    on neither                204 numbers, serials 97..300

Every other year is whole. 2024 is the year the authority changed
applications and 204 registrations fell down the seam.

That produces two distinct failures, and only the tests below separate them:

  1. An adapter reading ONE register reports "not registered" for a project
     the state has registered -- for most of 2024 and all of 2025 if it read
     the static one, for everything before 2024 if it read the other.
  2. An adapter reading BOTH still hits 204 numbers that are on neither, and
     an absence there is a hole in what TNRERA publishes, NOT evidence about
     the project. `coverage_note` has to tell that apart from a serial past
     the end of the year's numbering, which really is unissued.

The second theme is the PAN. TNRERA masks it -- XXXXXX230D -- and the
promoter view was observed carrying a full, unmasked PAN in the adjacent
`Company Registration No` field, where a promoter had typed it into the
wrong box. Harvesting that would mean attributing a PAN to a company on the
strength of someone else's data-entry error, so the parser refuses it. If
that guard is ever removed, gst_group starts keying group entities on PANs
recovered from a portal bug.

Network tests are opt-in: TNRERA_LIVE=1.

Run directly: python test_tamilnadu_adapter.py
"""

import os

import group_sweep as gs
from states.adapter_tamilnadu import (
    coverage_note,
    document_extension,
    fetch_penalty_notices,
    field_value,
    looks_like_a_document,
    parse_order_register,
    parse_penalty_register,
    parse_public_view,
    parse_register,
    parse_registration_number,
    project_name,
    promoter_name,
    search_enforcement_lists_by_name,
    search_orders_by_promoter,
)

_LIVE = os.environ.get("TNRERA_LIVE") == "1"

# The static register's shape: eight columns, the annexed PDFs in 'Other
# Details' and 'Current Status', and no detail view anywhere.
_LEGACY_HTML = """
<table>
 <tr><th>S.No.</th><th>Project Registration No.</th><th>Name and Address of the Promoter</th>
     <th>Project Details and Address</th><th>Approval Details</th>
     <th>Project Completion Date</th><th>Other Details</th>
     <th>Current Status of the Project</th></tr>
 <tr><td>1</td><td>TN/16/Building/0001/2024 dated 03/01/2024</td>
     <td>Thiru. M.Anand, Managing Partner, M/s. Rohini Colours, No.7, Kulumani Main Road,
         Worriyur, Tiruchirappalli &#8211; 620003.</td>
     <td>Project Name: &#8220;Rohini Colours&#8221; - Construction of Stilt Floor + 5 Floors
         Residential Building with 20 dwelling units at Plot Nos.4 &amp; 5.</td>
     <td>Planning Permission issued by The Joint Director.</td>
     <td>17.05.2031</td>
     <td><a href="https://rera.tn.gov.in/cms/Other_Details/Building/Approval_Details/2024/1-2024.pdf">Approval Details</a>
         <a href="https://rera.tn.gov.in/cms/Other_Details/Building/Carpet_Area/2024/1-2024.pdf">Carpet Area</a></td>
     <td><a href="https://rera.tn.gov.in/cms/Current_Status_Project/Building/2024/1(D25)-2024.pdf">Progress of Work as on December 2025</a></td></tr>
</table>
"""

# The current application's shape. NOTE THE EXTRA 'Form-c' COLUMN: it sits
# before the last one, so every positional index after 'Other Details'
# shifts by one against the static register.
_ONLINE_HTML = """
<table>
 <tr><th>S. No</th><th>Project Registration No.</th><th>Name and Address of the Promoter</th>
     <th>Project Details and Address</th><th>Approval Details</th>
     <th>Project Completion Date</th><th>Other Details</th><th>Form-c</th>
     <th>Current Status of the Project</th></tr>
 <tr><td>1</td><td>TN/29/Building/0302/2024 dated 05-02-2024</td>
     <td>M/s. CASA GRANDE AXIOM PRIVATE LIMITED, New No.111, Old No. 59, L B Road, Chennai.</td>
     <td>Project Name: Casagrand Axiom Registration for promoter.</td>
     <td>CMDA issued planning permission.</td><td>30.06.2027</td>
     <td><a href="https://rera.tn.gov.in/public-view1/building/pfirm/aaa-111">Promoter Details</a>
         <a href="https://rera.tn.gov.in/public-view2/building/pfirm/bbb-222">Project Details</a></td>
     <td><a href="https://rera.tn.gov.in/formcqr/ccc-333"></a></td>
     <td></td></tr>
</table>
"""

# Both label/value shapes, both heading kinds, and the PAN leak.
_VIEW_HTML = """
<div class="tabcontent">
 <div class="card-body">
  <h6 class="fw-bold text-dark mb-3">Promoter Detail</h6>
  <label class="text-muted">Firm Name :</label><p class="mb-0 fw-semibold">TNUHDB</p>
  <label class="text-muted">PAN Card No :</label><p class="mb-0 fw-semibold">XXXXXX230D</p>
  <label class="text-muted">Company Registration No :</label>
  <p class="mb-0 fw-semibold">ADWPG4230D</p>

  <h6 class="fw-bold text-dark mb-3">Financial Details</h6>
  <small class="text-muted">Total Project Cost :</small>
  <div class="fw-semibold text-primary">600499000</div>

  <div class="card-header bg-light fw-bold">Project Structural Engineer</div>
  <small class="text-muted">Engineer Name :</small><div class="fw-semibold">R Venkatesan</div>
  <small class="text-muted">Mobile No. 1 :</small><div class="fw-semibold">9854348523</div>

  <div class="card-header bg-light fw-bold">Project Architect</div>
  <small class="text-muted">Architect Name :</small><div class="fw-semibold">B Sathiya praba</div>
  <small class="text-muted">Mobile No. 1 :</small><div class="fw-semibold">9677107545</div>

  <h6 class="fw-bold text-dark mb-3">Documents</h6>
  <a href="https://rera.tn.gov.in/public/storage/upload/abc.pdf">View Document</a>
 </div>
</div>
"""

_ORDERS_HTML = """
<table>
 <tr><th>S.No.</th><th>Complaint No.</th><th>Complainant</th><th>Respondent</th>
     <th>Project</th><th>Date of Final Order</th><th>Order</th></tr>
 <tr><td>1</td><td>77/2023</td><td>Venkatakrishnan Ramaswamy</td>
     <td>Green Avenue Homes &amp; Gardens</td><td>Project "Dakshin Avenue-IV"</td>
     <td>09.01.2025</td><td><a href="/cms/tnrera_judgements/2025/77.pdf">Order</a></td></tr>
 <tr><td>2</td><td>31/2024</td><td>N. Muthukumaran</td>
     <td>Casa Grande Smart Value Homes</td><td>Project "Casagrand Utopia"</td>
     <td>09.01.2025</td><td></td></tr>
</table>
"""


# --- the registration number ---------------------------------------------

def test_the_four_ways_the_old_pattern_was_wrong():
    """Each of these is a shape the pattern this repo used to carry rejected,
    and each is a live issued number."""
    # 1. single-digit district
    assert parse_registration_number("TN/1/Building/0306/2024")["district_code"] == "1"
    # 2. three-digit serial, unpadded, beside a four-digit one on one page
    assert parse_registration_number("TN/29/Building/002/2024")["serial"] == 2
    assert parse_registration_number("TN/16/Building/0001/2024")["serial"] == 1
    # 3. the TNRERA/ prefix and its abbreviated type tokens
    current = parse_registration_number("TNRERA/29/BLG/0001/2026")
    assert current["era"] == "current" and current["kind"] == "building"
    assert parse_registration_number("TNRERA/11/LO/0001/2026")["kind"] == "layout"
    # 4. type tokens beyond Building/Layout, including one with its own slash
    assert parse_registration_number("TN/01/Regularisation-Layout/0028/2022") is not None
    assert parse_registration_number("TN/01/Layout/Offline/0028/2022") is not None
    print("test_the_four_ways_the_old_pattern_was_wrong: PASS")


def test_an_agent_registration_is_not_a_project():
    """TN/Agent/0249/2020 is four segments with the literal word Agent where
    a project carries district digits. Requiring digits there is what keeps
    an agent out of the project register."""
    assert parse_registration_number("TN/Agent/0249/2020") is None
    print("test_an_agent_registration_is_not_a_project: PASS")


def test_the_number_names_the_page_that_holds_it():
    """Which is why a lookup here costs no search: the type and year in the
    number pick the register slice directly."""
    parsed = parse_registration_number("TN/01/Regularisation-Layout/0028/2022")
    assert parsed["legacy_path"] == "Regularisation_Layout" and parsed["year"] == 2022
    assert parse_registration_number("TN/16/Building/0001/2024")["legacy_path"] == "Building"
    print("test_the_number_names_the_page_that_holds_it: PASS")


# --- the two-register seam ------------------------------------------------

def test_a_missing_number_inside_the_gap_is_not_an_absence():
    """THE CENTRAL GUARD. Serial 150 of 2024 sits between the static
    register's last (96) and the current application's first (301). It is on
    neither, and reporting that as 'not registered' is a false clean
    record."""
    rows = ([{"reg_no": f"TN/29/Building/{n:04d}/2024"} for n in range(1, 97)]
            + [{"reg_no": f"TN/29/Building/{n:04d}/2024"} for n in range(301, 614)])
    parsed = parse_registration_number("TN/29/Building/0150/2024")

    note = coverage_note(parsed, rows)
    assert "INSIDE a gap" in note, note
    assert "NOT evidence that the project was never registered" in note, note
    assert "204" in note, note
    print("test_a_missing_number_inside_the_gap_is_not_an_absence: PASS")


def test_a_number_past_the_end_of_the_year_is_a_different_answer():
    """And this one really is 'no such registration' -- the two must never
    collapse into one sentence."""
    rows = [{"reg_no": f"TN/29/Building/{n:04d}/2024"} for n in range(1, 97)]
    parsed = parse_registration_number("TN/29/Building/0900/2024")
    note = coverage_note(parsed, rows)
    assert "beyond the numbers published" in note, note
    assert "gap" not in note, note
    print("test_a_number_past_the_end_of_the_year_is_a_different_answer: PASS")


def test_no_register_read_at_all_is_not_an_absence_either():
    parsed = parse_registration_number("TN/29/Building/0150/2024")
    note = coverage_note(parsed, [])
    assert "nothing was established" in note, note
    print("test_no_register_read_at_all_is_not_an_absence_either: PASS")


# --- reading a register ---------------------------------------------------

def test_both_applications_parse_through_one_column_map():
    """The current application inserts a Form-c column before the last one.
    Matching columns by header content is what stops 'Current Status' being
    read out of the Form-c cell."""
    legacy = parse_register(_LEGACY_HTML, "static")
    assert len(legacy) == 1
    row = legacy[0]
    assert row["reg_no"] == "TN/16/Building/0001/2024"
    assert row["registered_on"] == "03/01/2024"
    assert row["completion_date"] == "17.05.2031"
    assert row["current_status"].startswith("Progress of Work"), row["current_status"]
    assert len(row["documents"]) == 3, row["documents"]
    assert row["project_view_url"] == "", "the static register has no detail view"

    online = parse_register(_ONLINE_HTML, "current")
    row = online[0]
    assert row["reg_no"] == "TN/29/Building/0302/2024"
    assert row["completion_date"] == "30.06.2027", row["completion_date"]
    assert row["current_status"] == "", row["current_status"]
    assert row["promoter_view_url"].endswith("aaa-111"), row["promoter_view_url"]
    assert row["project_view_url"].endswith("bbb-222"), row["project_view_url"]
    # The detail-view links are navigation, not filings.
    assert all("public-view" not in d["url"] for d in row["documents"]), row["documents"]
    print("test_both_applications_parse_through_one_column_map: PASS")


def test_the_promoter_is_extracted_but_the_cell_is_kept():
    """The register prints name and address in one cell, so the name is an
    extraction from prose. It is best-effort by nature -- which is why the
    full cell travels beside it and the matching is done on the cell."""
    assert promoter_name(
        "Thiru. M.Anand, Managing Partner, M/s. Rohini Colours, No.7, Kulumani Main Road"
    ) == "M/s. Rohini Colours"
    # An address line must not be swallowed into the name.
    assert promoter_name(
        "M/s. CASA GRANDE AXIOM PRIVATE LIMITED, New No.111, Old No. 59, L B Road"
    ) == "M/s. CASA GRANDE AXIOM PRIVATE LIMITED"
    # An enumerated row.
    assert promoter_name(
        "1) M/s. Spyka Homes Private Limited (Developer), Kols Square, 4th Floor"
    ) == "M/s. Spyka Homes Private Limited (Developer)"
    # No firm named at all -- the individual is the promoter.
    assert promoter_name("Thiru. T.Ivans, Door No.4, 4th Avenue East") == "Thiru. T.Ivans"

    assert parse_register(_LEGACY_HTML)[0]["promoter_block"].startswith("Thiru. M.Anand")
    print("test_the_promoter_is_extracted_but_the_cell_is_kept: PASS")


def test_the_project_name_survives_typographic_quotes():
    assert project_name('Project Name: “Rohini Colours” - Construction of Stilt Floor') \
        == "Rohini Colours"
    # The current application's rows are unquoted prose, so this is a
    # best-effort prefix and the block is what carries the truth.
    assert project_name("Project Name: Casagrand Axiom Registration for promoter.")
    assert project_name("no name here") == ""
    print("test_the_project_name_survives_typographic_quotes: PASS")


# --- the detail views -----------------------------------------------------

def test_a_pan_in_the_wrong_box_is_refused():
    """THE GUARD THAT MATTERS MOST HERE. The promoter view shows a masked
    PAN and, on the same record, a full one in `Company Registration No`
    where somebody typed it into the wrong box. Harvesting that would key a
    group entity on another portal's data-entry error."""
    view = parse_public_view(_VIEW_HTML)
    assert view["pan_masked"] is True
    # The masked value is not returned as a PAN.
    assert field_value(view, "PAN Card No") == ""
    # And the leaked one is withheld rather than passed on.
    leaked = field_value(view, "Company Registration No")
    assert "ADWPG4230D" not in leaked, leaked
    assert "withheld" in leaked, leaked
    print("test_a_pan_in_the_wrong_box_is_refused: PASS")


def test_sections_come_from_both_heading_kinds():
    """The professionals' blocks are headed by a div.card-header, everything
    else by an h6. Reading only the h6 leaves the architect and the
    structural engineer in the same nameless section -- and then their two
    'Mobile No. 1 :' fields are indistinguishable."""
    view = parse_public_view(_VIEW_HTML)
    assert field_value(view, "Engineer Name", "Structural Engineer") == "R Venkatesan"
    assert field_value(view, "Architect Name", "Architect") == "B Sathiya praba"
    assert field_value(view, "Mobile No. 1", "Structural Engineer") == "9854348523"
    assert field_value(view, "Mobile No. 1", "Architect") == "9677107545"
    assert field_value(view, "Total Project Cost") == "600499000"
    print("test_sections_come_from_both_heading_kinds: PASS")


def test_only_filed_documents_are_collected_not_the_menu():
    """The views carry the authority's whole mega-menu. Collecting anchors by
    link TEXT gathered 21 'documents' of which 8 were navigation; the filed
    ones live under the app's storage path."""
    view = parse_public_view(_VIEW_HTML)
    assert len(view["documents"]) == 1, view["documents"]
    assert view["documents"][0]["url"].endswith("/storage/upload/abc.pdf")
    assert view["documents"][0]["label"].startswith("Documents"), view["documents"][0]["label"]
    print("test_only_filed_documents_are_collected_not_the_menu: PASS")


# --- orders ---------------------------------------------------------------

def test_orders_are_matched_on_the_respondent():
    """A complaint is filed AGAINST the promoter, so the respondent is the
    promoter. Matching the complainant column would return the promoter's
    own customers' names."""
    rows = parse_order_register(_ORDERS_HTML)
    assert len(rows) == 2, rows
    assert rows[0]["respondent"] == "Green Avenue Homes & Gardens"
    assert rows[0]["order_url"].endswith("/cms/tnrera_judgements/2025/77.pdf")
    assert rows[1]["order_url"] == "", "a row with no published order must not invent one"

    # The injected fetcher answers every register-year with the same page,
    # so the hit appears once per year read -- what is being asserted is that
    # the respondent column is what matched, and that all three registers
    # were walked rather than one.
    hits, coverage = search_orders_by_promoter(
        "Casa Grande", fetcher=lambda url: _ORDERS_HTML
    )
    assert hits, hits
    assert {h["case_no"] for h in hits} == {"31/2024"}, sorted({h["case_no"] for h in hits})
    assert {h["register"] for h in hits} == {
        "TNRERA Authority orders",
        "TNRERA single-member-bench orders",
        "TNRERA Adjudicating Officer orders",
    }, sorted({h["register"] for h in hits})
    assert coverage and all(c["years_read"] for c in coverage), coverage
    print("test_orders_are_matched_on_the_respondent: PASS")


def test_all_three_registers_are_read_and_an_unread_one_is_named():
    """K-RERA taught this: five registers existed and one was being read.
    TNRERA has three, and a search over two of them is not a clean
    litigation record."""
    from states.adapter_tamilnadu import _ORDER_REGISTERS, order_register_coverage

    assert len(_ORDER_REGISTERS) == 3, _ORDER_REGISTERS
    slugs = {slug for slug, _, _ in _ORDER_REGISTERS}
    assert slugs == {"tnrera_judgements", "smb_judgements", "adjudicating_judgements"}, slugs

    sentence = order_register_coverage([
        {"register": "TNRERA Authority orders", "years_read": 9, "years_total": 9,
         "years_failed": []},
        {"register": "TNRERA single-member-bench orders", "years_read": 3, "years_total": 5,
         "years_failed": ["2025 (ReadTimeout)", "2024 (ReadTimeout)"]},
        {"register": "TNRERA Adjudicating Officer orders", "years_read": 9, "years_total": 9,
         "years_failed": []},
    ])
    assert "did NOT load" in sentence, sentence
    assert "2025 (ReadTimeout)" in sentence, sentence
    assert "would not appear here" in sentence, sentence
    print("test_all_three_registers_are_read_and_an_unread_one_is_named: PASS")


# --- penalty register and enforcement PDFs (group_enforcement's join keys) -

_PENALTY_HTML = """
<table>
 <tr><th>Sl.No</th><th>Application Number</th><th>Promoter Details</th>
     <th>Project Details</th><th>Date of Penalty Notice Issued</th>
     <th>Penalty Amount</th></tr>
 <tr><td>1</td><td>TNRERA/PLI/2288/2024</td>
     <td>M/s. CASA GRANDE LIMITED. Door No. 27, Some Road</td>
     <td>Green Avenue Homes & Gardens</td><td>12-Mar-2025</td><td>50000</td></tr>
 <tr><td>2</td><td></td><td>No application number</td>
     <td>Some Project</td><td>01-Jan-2026</td><td>10000</td></tr>
</table>
"""


def test_the_penalty_register_is_found_by_header_and_keeps_the_promoter_block_whole():
    """promoter_block is deliberately NOT run through promoter_name(): that
    helper truncates at 'Door No.', which this register's cells run
    straight into with a period rather than a comma."""
    rows = parse_penalty_register(_PENALTY_HTML)
    assert len(rows) == 1, rows
    assert rows[0]["application_no"] == "TNRERA/PLI/2288/2024", rows[0]
    assert "Door No." in rows[0]["promoter_block"], rows[0]
    assert rows[0]["penalty_amount"] == "50000", rows[0]
    print("test_the_penalty_register_is_found_by_header_and_keeps_the_promoter_block_whole: PASS")


def test_fetch_penalty_notices_reads_both_kinds_through_one_parser():
    rows = fetch_penalty_notices(kind="building", fetcher=lambda url: _PENALTY_HTML)
    assert len(rows) == 1 and rows[0]["application_no"] == "TNRERA/PLI/2288/2024", rows
    print("test_fetch_penalty_notices_reads_both_kinds_through_one_parser: PASS")


def test_enforcement_lists_are_searched_by_name_across_both_pdfs():
    """search_enforcement_lists_by_name reads two PDFs via
    fetch_enforcement_pdf_rows -- injected here rather than fabricating real
    PDF bytes, to pin the search/match logic (normalised substring over
    prose, both lists walked, a miss on one list does not short-circuit the
    other) independent of pdfplumber's own extraction."""
    import states.adapter_tamilnadu as tn

    original = tn.fetch_enforcement_pdf_rows
    calls = []

    def fake_fetch(url, session=None):
        calls.append(url)
        if url == tn.SCN_PENALTY_PDF:
            return [{"sl_no": "1", "party_detail": "Thiru. Casa Grande, Managing Partner",
                      "site_address": "Chennai", "extra": ""}]
        return [{"sl_no": "1", "party_detail": "Someone Unrelated",
                  "site_address": "Coimbatore", "extra": ""}]

    tn.fetch_enforcement_pdf_rows = fake_fetch
    try:
        hits = search_enforcement_lists_by_name("Casa Grande")
    finally:
        tn.fetch_enforcement_pdf_rows = original

    assert len(calls) == 2, "both enforcement PDFs must be checked, not just one"
    assert len(hits) == 1, hits
    assert "Casa Grande" in hits[0]["party_detail"], hits[0]
    print("test_enforcement_lists_are_searched_by_name_across_both_pdfs: PASS")


# --- documents ------------------------------------------------------------

def test_a_missing_file_answers_200_and_is_not_saved():
    """TNRERA answers a file it does not hold with 14 bytes of HTML at
    HTTP 200. And the test is 'is this a document', not 'is this a PDF':
    the carpet-area statement is served as .xlsx."""
    class _Response:
        def __init__(self, content, ctype):
            self.content, self.headers = content, {"Content-Type": ctype}

    assert not looks_like_a_document(_Response(b"Page not found", "text/html; charset=UTF-8"))
    assert not looks_like_a_document(_Response(b"", "application/pdf"))
    assert looks_like_a_document(_Response(b"%PDF-1.4 ...", "application/pdf"))
    assert looks_like_a_document(
        _Response(b"PK\x03\x04 ...", "application/vnd.openxmlformats-officedocument"
                                    ".spreadsheetml.sheet")
    ), "an .xlsx carpet-area statement is a real filing"
    print("test_a_missing_file_answers_200_and_is_not_saved: PASS")


def test_a_spreadsheet_is_not_saved_under_a_pdf_name():
    """FOUND BY THE FIRST LIVE RUN, not by any of the tests above.

    TNRERA serves the carpet-area statement as .xlsx, and the downloader's
    default extension wrote it to disk as
    `Carpet_Area_Statement_-_View_File.pdf` -- a ZIP archive whose name
    claims to be a PDF. Nothing crashed; anything opening it by extension
    would simply have got nothing out of it, which is how every serious bug
    in this pipeline has presented itself."""
    assert document_extension(
        "https://rera.tn.gov.in/public/storage/upload/gZHsn2vTIEII.xlsx") == ".xlsx"
    assert document_extension(
        "https://rera.tn.gov.in/public/storage/upload/abc.pdf") == ".pdf"
    # An opaque URL with no extension still has to produce something.
    assert document_extension("https://rera.tn.gov.in/formcqr/ccc-333") == ".pdf"
    print("test_a_spreadsheet_is_not_saved_under_a_pdf_name: PASS")


def test_the_form_c_link_has_no_text_and_still_gets_a_name():
    """Also from the live run. The Form-C anchor carries no link text, so its
    document was saved as `document.pdf` -- and a second such link would have
    collided with the first. The column names it instead."""
    row = parse_register(_ONLINE_HTML, "current")[0]
    labels = [d["label"] for d in row["documents"]]
    assert "" not in labels, labels
    assert "Form-C" in labels, labels
    print("test_the_form_c_link_has_no_text_and_still_gets_a_name: PASS")


# --- the sweep seam -------------------------------------------------------

def test_tamil_nadu_is_searchable_and_every_hit_is_a_candidate():
    import states.adapter_tamilnadu as tn

    assert hasattr(tn, "search_promoter_projects")
    assert "TN" in gs.searchable_states(), gs.searchable_states()
    assert hasattr(tn, "fetch_project_summary"), (
        "a swept hit that cannot be opened can be neither confirmed nor refuted"
    )
    print("test_tamil_nadu_is_searchable_and_every_hit_is_a_candidate: PASS")


# --- live -----------------------------------------------------------------

def test_live_the_two_registers_do_not_overlap():
    """The finding, checked against the portal so it cannot quietly stop
    being true -- in either direction. If the authority ever backfills the
    gap, this fails and the notes should be rewritten."""
    if not _LIVE:
        print("test_live_the_two_registers_do_not_overlap: SKIPPED (set TNRERA_LIVE=1)")
        return
    from states.adapter_tamilnadu import _legacy_register, _online_register, _session

    session = _session()
    legacy = {r["reg_no"] for r in _legacy_register(session, "Building", 2024) or []}
    online = {r["reg_no"] for r in _online_register(session, "building", 2024) or []}
    assert legacy and online, (len(legacy), len(online))
    assert not (legacy & online), sorted(legacy & online)[:5]
    print(f"test_live_the_two_registers_do_not_overlap: PASS "
          f"({len(legacy)} static, {len(online)} current, 0 shared)")


def test_live_a_known_project_resolves_from_the_number_alone():
    if not _LIVE:
        print("test_live_a_known_project_resolves_from_the_number_alone: "
              "SKIPPED (set TNRERA_LIVE=1)")
        return
    from states.adapter_tamilnadu import fetch_project_summary

    summary = fetch_project_summary("TN/16/Building/0001/2024")
    assert summary["opened"] is True, summary
    assert summary["promoter_name"], summary
    print("test_live_a_known_project_resolves_from_the_number_alone: PASS")


if __name__ == "__main__":
    test_the_four_ways_the_old_pattern_was_wrong()
    test_an_agent_registration_is_not_a_project()
    test_the_number_names_the_page_that_holds_it()
    test_a_missing_number_inside_the_gap_is_not_an_absence()
    test_a_number_past_the_end_of_the_year_is_a_different_answer()
    test_no_register_read_at_all_is_not_an_absence_either()
    test_both_applications_parse_through_one_column_map()
    test_the_promoter_is_extracted_but_the_cell_is_kept()
    test_the_project_name_survives_typographic_quotes()
    test_a_pan_in_the_wrong_box_is_refused()
    test_sections_come_from_both_heading_kinds()
    test_only_filed_documents_are_collected_not_the_menu()
    test_orders_are_matched_on_the_respondent()
    test_all_three_registers_are_read_and_an_unread_one_is_named()
    test_the_penalty_register_is_found_by_header_and_keeps_the_promoter_block_whole()
    test_fetch_penalty_notices_reads_both_kinds_through_one_parser()
    test_enforcement_lists_are_searched_by_name_across_both_pdfs()
    test_a_missing_file_answers_200_and_is_not_saved()
    test_a_spreadsheet_is_not_saved_under_a_pdf_name()
    test_the_form_c_link_has_no_text_and_still_gets_a_name()
    test_tamil_nadu_is_searchable_and_every_hit_is_a_candidate()
    test_live_the_two_registers_do_not_overlap()
    test_live_a_known_project_resolves_from_the_number_alone()
    print("\nAll tests passed.")

"""
Guards on the UP-RERA adapter.

THE BUG THIS FILE EXISTS FOR is not a parsing bug. It is that

    https://www.up-rera.in/Frm_View_Project_Details.aspx?id=378870

returns HTTP 200. So does ?id=30000, and ?id=420000, and every other id the
authority has never issued. Each serves a 48.7 KB page carrying the site's
masthead, menu and footer and no project record whatsoever -- no 404, no
error text, nothing a status check or a length check written for a different
portal would catch.

Run through a field parser, that page yields a project with no promoter, no
land parcels, no bank account, no professionals and no documents. On a
Charter it renders as a promoter who filed almost nothing. It is the same
species as the Maha Bhulekh bug, the K-RERA complaint bug and the MahaRERA
orders bug: SOMETHING UNREADABLE PRESENTING ITSELF AS SOMETHING ABSENT.

The defence is that resolution asks a different question. Not "did the fetch
work" but "did the page serve the registration number I asked for" -- and
the two tests below are the ones that would fail if that check were ever
softened.

The second theme is UP-RERA's three-way document state. Seven of the 31 rows
on a real record carry the promoter's own 'NA' where a file name belongs,
including the CA, architect and engineer certificates. Filed as documents
they inflate the library; filed as failures they invite a retry that can
never succeed; dropped, they hide that the certificates were never filed.

Network tests are opt-in: UPRERA_LIVE=1.

Run directly: python test_uttarpradesh_adapter.py
"""

import os

import group_sweep as gs
import states
from states.adapter_uttarpradesh import (
    UttarPradeshAdapter,
    control_values,
    document_entries,
    document_extension,
    fetch_project_summary,
    land_parcels,
    parse_project_detail,
    parse_registration_number,
    project_notes,
    served_registration_number,
)

_LIVE = os.environ.get("UPRERA_LIVE") == "1"
_REG = "UPRERAPRJ14636"
_ID = "14636"

# The shell, reproduced in the shape that matters: chrome, and no
# lblProjectNameWithID anywhere in it. The real one is 48.7 KB of menu.
_SHELL_HTML = """
<html><body>
  <div class="menu"><a href="index.aspx">Home</a><a href="#">ABOUT RERA</a></div>
  <div class="container"><h3>UP RERA</h3></div>
  <div class="footer">Uttar Pradesh Real Estate Regulatory Authority</div>
</body></html>
"""

# The three node kinds a real record spreads its values across. Reading only
# one of them is how a record comes back two-thirds empty.
_DETAIL_HTML = """
<html><body>
  <label id="ctl00_ContentPlaceHolder1_messageTop">Following details are pending for
     project BALAJI GREENS</label>
  <label id="ctl00_ContentPlaceHolder1_lblProjectNameHeading">Project Name: BALAJI GREENS</label>
  <label id="ctl00_ContentPlaceHolder1_lblProjectNameWithID">Project Id: (UPRERAPRJ14636)</label>
  <label id="ctl00_ContentPlaceHolder1_lblregisdate">Registration Date: 17-12-2017</label>
  <label id="ctl00_ContentPlaceHolder1_lblPromoterNameHeading">Promoter Name:
     BALAJIMAHIMA INFRATECH PRIVATE LIMITED</label>
  <label id="ctl00_ContentPlaceHolder1_lblPromoterNameWithID">Promoter Id: (UPRERAPRM31688)</label>
  <span id="ctl00_ContentPlaceHolder1_lblProjectType">New</span>
  <span id="ctl00_ContentPlaceHolder1_lblProjectCategory">Residential</span>
  <select id="ctl00_ContentPlaceHolder1_ddlDistrict">
     <option>--Select--</option><option selected="selected">Barabanki</option></select>
  <label id="ctl00_ContentPlaceHolder1_ddlTehsil_old">Nawabganj</label>
  <input id="ctl00_ContentPlaceHolder1_lblTotalArea" value="6500" />
  <input id="ctl00_ContentPlaceHolder1_lblProjectCost" value="118" />
  <input id="ctl00_ContentPlaceHolder1_lblAccNo" value="0314102000005067" />
  <input id="ctl00_ContentPlaceHolder1_lblBankName" value="IDBI BANK " />
  <input id="ctl00_ContentPlaceHolder1_lblArchName" value="NA" />
  <table id="ctl00_ContentPlaceHolder1_grdKhasra">
     <tr><th>Sr. No.</th><th>Khasra/Plot Number *</th><th>Area *</th><th>Type *</th></tr>
     <tr><td>1</td><td>2018 2021</td><td>974</td><td></td></tr>
     <tr><td>2</td><td>2015g</td><td>1500</td><td></td></tr>
  </table>
  <table id="ctl00_ContentPlaceHolder1_grvdocumentdetails">
     <tr><th>SNo.</th><th>Document Name</th><th>Uploaded File Name</th>
         <th>Uploaded Date</th><th>Upload Doc Type</th><th>Download</th></tr>
     <tr><td>1</td><td>CA CERTIFICATE</td>
         <td>Promoter select NA for this document</td><td></td><td></td><td>NA</td></tr>
     <tr><td>2</td><td>Details of Encumbrances</td>
         <td>PRJ84-2499VVIP Addresses Phase-III.pdf</td><td>03-07-2018</td>
         <td>New</td><td><a href="javascript:__doPostBack('x','')">Download</a></td></tr>
  </table>
</body></html>
"""


# --- the 200-OK shell -----------------------------------------------------

def test_a_page_with_no_record_is_not_a_record_with_no_fields():
    """THE CENTRAL GUARD.

    An id UP-RERA never issued is answered with 200 and a page shell. Parsed
    for fields it looks exactly like a project whose promoter filed nothing,
    so the only safe question is whether the page served a registration
    number at all."""
    assert served_registration_number(_SHELL_HTML) == "", "the shell must yield NO number"
    assert served_registration_number(_DETAIL_HTML) == _REG

    parsed = parse_project_detail(_SHELL_HTML)
    assert parsed["registration_number"] == ""
    assert parsed["promoter_name"] == ""
    assert parsed["documents"] == []
    print("test_a_page_with_no_record_is_not_a_record_with_no_fields: PASS")


def test_a_shell_is_refused_by_acquire_rather_than_returned_empty():
    """And the refusal must reach the caller as a resolution failure with
    the reason in it -- not as an AcquisitionResult full of blanks."""
    adapter = UttarPradeshAdapter()

    class _Ctx:
        output_dir = "output"
        headed = False
        explicit_token = None
        no_auto_auth = False
        captcha_timeout = 300
        project_id_override = None
        prior = None
        on_resolved = None

        class reporter:
            @staticmethod
            def info(m): pass
            @staticmethod
            def warn(m): pass
            @staticmethod
            def ok(m): pass
            @staticmethod
            def choose(p, o): return None

    adapter._fetch_detail = lambda session, parsed, ctx: (_SHELL_HTML, "http://x")
    try:
        adapter.acquire(_REG, _Ctx())
    except states.StateResolutionError as e:
        message = str(e)
        assert "no project record" in message, message
        # It must NOT read as a finding about the promoter.
        assert "NOT a project whose promoter filed nothing" in message, message
    else:
        raise AssertionError("a page shell was accepted as a project record")
    print("test_a_shell_is_refused_by_acquire_rather_than_returned_empty: PASS")


def test_a_page_serving_a_different_project_is_discarded():
    """The mis-attribution guard. If ?id=14636 ever served another project,
    accepting it would file one company's land, bank account and documents
    under another company's name."""
    other = _DETAIL_HTML.replace("UPRERAPRJ14636", "UPRERAPRJ99999")
    adapter = UttarPradeshAdapter()
    adapter._fetch_detail = lambda session, parsed, ctx: (other, "http://x")

    class _Ctx:
        output_dir = "output"
        on_resolved = None

        class reporter:
            @staticmethod
            def info(m): pass
            @staticmethod
            def warn(m): pass
            @staticmethod
            def ok(m): pass
            @staticmethod
            def choose(p, o): return None

    try:
        adapter.acquire(_REG, _Ctx())
    except states.StateResolutionError as e:
        assert "UPRERAPRJ99999" in str(e) and "discarded" in str(e), str(e)
    else:
        raise AssertionError("a record for another project was accepted")
    print("test_a_page_serving_a_different_project_is_discarded: PASS")


# --- the registration number ---------------------------------------------

def test_both_numbering_schemes_parse_and_only_one_can_be_resolved():
    legacy = parse_registration_number("UPRERAPRJ14636")
    assert legacy["scheme"] == "legacy" and legacy["project_id"] == "14636"

    dated = parse_registration_number("UPRERAPRJ378870/03/2025")
    assert dated["scheme"] == "dated", dated
    # The arithmetic that makes a legacy number free does NOT hold here:
    # ?id=378870 serves the shell. A guessed id lands on another project.
    assert dated["project_id"] is None, dated
    print("test_both_numbering_schemes_parse_and_only_one_can_be_resolved: PASS")


def test_a_promoter_id_is_not_a_project_id():
    """One character apart, and completely different things. A pattern
    loosened to UPRERAPR\\w+ would resolve a promoter as a project."""
    assert parse_registration_number("UPRERAPRM31688") is None
    assert parse_registration_number("UPRERAPRJ31688") is not None
    print("test_a_promoter_id_is_not_a_project_id: PASS")


def test_a_dated_number_is_refused_with_the_reason_stated():
    """It is a VALID registration number this adapter cannot serve, and the
    refusal has to say which of those two things it is."""
    adapter = UttarPradeshAdapter()

    class _Ctx:
        output_dir = "output"
        on_resolved = None

        class reporter:
            @staticmethod
            def info(m): pass
            @staticmethod
            def warn(m): pass
            @staticmethod
            def ok(m): pass
            @staticmethod
            def choose(p, o): return None

    try:
        adapter.acquire("UPRERAPRJ378870/03/2025", _Ctx())
    except states.StateResolutionError as e:
        message = str(e)
        assert "valid UP-RERA registration number" in message, message
        assert "not a finding about the project" in message, message
    else:
        raise AssertionError("a post-2024 number was resolved somehow")
    print("test_a_dated_number_is_refused_with_the_reason_stated: PASS")


# --- reading the record ---------------------------------------------------

def test_values_are_read_from_all_three_node_kinds():
    """UP-RERA renders the promoter's own form read-only, so its values sit
    in <input value>, in <span>/<label> text, and in a <select>'s selected
    option. An adapter reading only inputs gets the areas and the bank
    account but no promoter name, no district and no project name -- a
    record two-thirds empty, which is the shell failure again in slower
    motion."""
    values = control_values(_DETAIL_HTML)
    assert values["lblTotalArea"] == "6500"                     # <input value>
    assert values["lblProjectType"] == "New"                    # <span> text
    assert values["ddlDistrict"] == "Barabanki"                 # <select selected>

    parsed = parse_project_detail(_DETAIL_HTML)
    assert parsed["project_name"] == "BALAJI GREENS"
    assert parsed["promoter_name"] == "BALAJIMAHIMA INFRATECH PRIVATE LIMITED"
    assert parsed["promoter_id"] == "UPRERAPRM31688"
    assert parsed["district"] == "Barabanki"
    assert parsed["bank_account_no"] == "0314102000005067"
    print("test_values_are_read_from_all_three_node_kinds: PASS")


def test_land_parcels_come_off_the_khasra_grid():
    parcels = land_parcels(_DETAIL_HTML)
    assert len(parcels) == 2, parcels
    assert parcels[0] == {"khasra_or_plot": "2018 2021", "area": "974", "land_type": ""}
    print("test_land_parcels_come_off_the_khasra_grid: PASS")


# --- the third document state --------------------------------------------

def test_a_document_the_promoter_declared_NA_is_neither_filed_nor_failed():
    """THE THREE-WAY STATE. 'Promoter select NA for this document' in the
    file-name column is the promoter answering the question, not a document
    and not a failed fetch. Seven of a real record's 31 rows are in it,
    including the CA, ARCHITECT and ENGINEERS certificates -- which is a
    finding worth surfacing, and is lost if the row is dropped."""
    entries = document_entries(_DETAIL_HTML)
    assert len(entries) == 2, entries

    not_filed = [e for e in entries if e["status"] == "not filed"]
    listed = [e for e in entries if e["status"] == "listed"]
    assert [e["label"] for e in not_filed] == ["CA CERTIFICATE"], not_filed
    assert not_filed[0].get("url") is None, "an unfiled slot must carry no URL to fetch"
    assert listed[0]["filename"] == "PRJ84-2499VVIP Addresses Phase-III.pdf"

    # And the URL is built from the grid's own file name -- no postback.
    assert "ViewDocument?Param=" in listed[0]["url"], listed[0]["url"]
    assert "PRJ84-2499VVIP%20Addresses%20Phase-III.pdf" in listed[0]["url"], listed[0]["url"]
    print("test_a_document_the_promoter_declared_NA_is_neither_filed_nor_failed: PASS")


def test_the_unfiled_certificates_reach_the_reader():
    """A promoter who never filed the CA certificate is a diligence fact.
    It must leave the adapter as a sentence, not only as a status code
    nobody renders."""
    notes = project_notes(parse_project_detail(_DETAIL_HTML))
    joined = " ".join(notes)
    assert "CA CERTIFICATE" in joined, notes
    assert "not documents this run failed to retrieve" in joined, notes
    # And UP-RERA's own pending-details banner, which is the authority
    # saying the filing is incomplete.
    assert "pending-details alert" in joined, notes
    print("test_the_unfiled_certificates_reach_the_reader: PASS")


# --- the sweep seam -------------------------------------------------------

def test_a_document_keeps_the_extension_the_portal_gave_it():
    """A file saved under an extension it is not reads as empty to anything
    that opens it by type -- and reads as a promoter who filed nothing."""
    assert document_extension("PRJ84-2499VVIP Addresses Phase-III.pdf") == ".pdf"
    assert document_extension("PRJ2499-statement.xlsx") == ".xlsx"
    assert document_extension("") == ".pdf"
    print("test_a_document_keeps_the_extension_the_portal_gave_it: PASS")


def test_uttar_pradesh_is_not_searchable_and_says_why():
    """UP-RERA's register is CAPTCHA-gated AND wants a district before a
    promoter. A searcher posting that form would get back the form with an
    empty results panel -- indistinguishable from 'this promoter has no
    projects', for every promoter ever swept. So the module deliberately
    does NOT define search_promoter_projects, and the sweep must carry a
    written reason instead of a zero."""
    import states.adapter_uttarpradesh as up

    assert not hasattr(up, "search_promoter_projects"), (
        "defining this would put UP in the sweep's searched column, where a "
        "CAPTCHA-blocked empty panel becomes 'no projects found'."
    )
    assert "UP" not in gs.searchable_states(), gs.searchable_states()
    assert "UP" in gs._CANNOT_SEARCH
    reason = gs._CANNOT_SEARCH["UP"]
    assert "CAPTCHA" in reason and "not searched" in reason.lower(), reason
    print("test_uttar_pradesh_is_not_searchable_and_says_why: PASS")


def test_a_project_can_still_be_opened_even_though_the_state_is_not_searchable():
    """Not searchable is not the same as not readable: a UP number arriving
    from anywhere else can be opened and confirmed."""
    assert callable(fetch_project_summary)
    out = fetch_project_summary("UPRERAPRM31688")
    assert out["opened"] is False and "not a UP-RERA registration number" in out["note"]

    out = fetch_project_summary("UPRERAPRJ378870/03/2025")
    assert out["opened"] is False
    assert "NOT opened" in out["note"], out["note"]
    print("test_a_project_can_still_be_opened_even_though_the_state_is_not_searchable: PASS")


# --- live -----------------------------------------------------------------

def test_live_the_id_route_serves_the_number_it_was_asked_for():
    if not _LIVE:
        print("test_live_the_id_route_serves_the_number_it_was_asked_for: "
              "SKIPPED (set UPRERA_LIVE=1)")
        return
    from states.adapter_uttarpradesh import _get, _session, detail_url

    html = _get(_session(), detail_url(_ID), what="detail")
    assert served_registration_number(html) == _REG
    detail = parse_project_detail(html)
    assert detail["promoter_name"], detail
    assert detail["district"], detail
    print("test_live_the_id_route_serves_the_number_it_was_asked_for: PASS")


def test_live_an_unissued_id_answers_200_with_no_record():
    """The finding this file is built on, checked against the real portal so
    it cannot quietly stop being true."""
    if not _LIVE:
        print("test_live_an_unissued_id_answers_200_with_no_record: "
              "SKIPPED (set UPRERA_LIVE=1)")
        return
    import requests

    from states.adapter_uttarpradesh import _UA, detail_url

    response = requests.get(detail_url("378870"), headers={"User-Agent": _UA},
                            timeout=180, verify=False)
    assert response.status_code == 200, response.status_code
    assert served_registration_number(response.text) == "", "the shell served a number?"
    print("test_live_an_unissued_id_answers_200_with_no_record: PASS")


def test_live_a_document_the_portal_does_not_hold_is_not_saved():
    """ViewDocument answers a missing file with the HTML shell at 200, so a
    downloader trusting the status code writes a web page as a PDF."""
    if not _LIVE:
        print("test_live_a_document_the_portal_does_not_hold_is_not_saved: "
              "SKIPPED (set UPRERA_LIVE=1)")
        return
    import requests

    from states.adapter_uttarpradesh import BASE_URL, _UA

    response = requests.get(f"{BASE_URL}/ViewDocument?Param=NOSUCHFILE-9999.pdf",
                            headers={"User-Agent": _UA}, timeout=180, verify=False)
    assert response.status_code == 200, response.status_code
    assert not response.content.startswith(b"%PDF"), "expected the shell, not a PDF"
    print("test_live_a_document_the_portal_does_not_hold_is_not_saved: PASS")


if __name__ == "__main__":
    test_a_page_with_no_record_is_not_a_record_with_no_fields()
    test_a_shell_is_refused_by_acquire_rather_than_returned_empty()
    test_a_page_serving_a_different_project_is_discarded()
    test_both_numbering_schemes_parse_and_only_one_can_be_resolved()
    test_a_promoter_id_is_not_a_project_id()
    test_a_dated_number_is_refused_with_the_reason_stated()
    test_values_are_read_from_all_three_node_kinds()
    test_land_parcels_come_off_the_khasra_grid()
    test_a_document_the_promoter_declared_NA_is_neither_filed_nor_failed()
    test_the_unfiled_certificates_reach_the_reader()
    test_a_document_keeps_the_extension_the_portal_gave_it()
    test_uttar_pradesh_is_not_searchable_and_says_why()
    test_a_project_can_still_be_opened_even_though_the_state_is_not_searchable()
    test_live_the_id_route_serves_the_number_it_was_asked_for()
    test_live_an_unissued_id_answers_200_with_no_record()
    test_live_a_document_the_portal_does_not_hold_is_not_saved()
    print("\nAll tests passed.")

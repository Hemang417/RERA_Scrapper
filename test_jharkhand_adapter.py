"""
Guards on the JHARERA adapter.

THE BUG THIS FILE EXISTS FOR, and it is a documents bug rather than a
parsing one:

    EVERY document link on a JHARERA project page is labelled "View".
    All 67 of them, on a real record. The first version of this adapter
    matched links by filename suffix, found TWO, and reported that as the
    project's document library -- for a project whose page actually links
    the promoter's PAN card, audited balance sheet and three years of
    income-tax returns.

    Worse than the undercount: with every link reading "View", naming files
    from the link text would have saved all 67 over one filename, and
    promoter_identity would never have recognised the PAN card among them,
    which is the entire reason the PAN extraction was built.

So the label is derived from the table around the link -- the column header
where the row is a set of slots, the sibling cell where the row is one
document -- and document_label/document_entries are pure functions so that
derivation is testable without the portal.

The second guard is the false-clean-record rule, in the form it takes here.
JHARERA renders its litigation table with headers and zero rows for a
project with no litigation, which is a REAL clean check. A page with no
litigation table at all is NOT. Those must not collapse into the same
output, and Karnataka already shipped that exact confusion once.

Network tests are opt-in: JHARERA_LIVE=1.

Run directly: python test_jharkhand_adapter.py
"""

import os
import shutil
import tempfile

import states
from states.adapter_jharkhand import (
    document_entries,
    document_label,
    download_single_document,
    fetch_project_summary,
    fetch_rejected_projects,
    fetch_surrendered_projects,
    find_table,
    labelled_rows,
    parse_project_detail,
    parse_rejected_or_surrendered_register,
    parse_search_rows,
    project_notes,
)
from states.base import storage_key

_LIVE = os.environ.get("JHARERA_LIVE") == "1"
_REG = "JHARERA/PROJECT/35/2023"

# The two document-table shapes seen on a real record, reproduced exactly.
# Shape 1: one row of slots, each cell a bare "View", names in the headers.
# Shape 2: one row per document, the name in a sibling cell.
_DOC_HTML = """
<table>
  <tr><th>Company Pan Card</th><th>Balance Sheet</th><th>Income Tax Preceeding Year 1</th></tr>
  <tr><td><a href="/FirstLevel/ViewDocument/106945">View</a></td>
      <td><a href="/FirstLevel/ViewDocument/106946">View</a></td>
      <td><a href="/FirstLevel/ViewDocument/106947">View</a></td></tr>
</table>
<table>
  <tr><td>Non Encumbrance Certificate</td><td><a href="/FirstLevel/ViewDocument/106948">View</a></td></tr>
</table>
"""

_LITIGATION_PRESENT_EMPTY = """
<table><tr><th>Project Name</th><th>caseNo</th><th>Name of Authority</th>
<th>Petitioner</th><th>Respondant</th><th>fact Of Case</th></tr></table>
"""


def _detail(extra=""):
    return f"""
    <table><tr><th>Name</th><th>Designation</th><th>Emaild</th><th>Photo</th></tr>
      <tr><td>Bijay Kumar Agarwal</td><td>Director</td><td>projects@pranamigroup.com</td><td>x</td></tr>
    </table>
    <table><tr><th>Account Type</th><th>Bank Name</th><th>Account Number</th>
      <th>Account Holder Name</th><th>IFSC Code</th></tr>
      <tr><td></td><td>STATE BANK OF INDIA</td><td>42749949035</td>
          <td>PBPL PRANAMI CREST RERA PRIVATE LIMITED</td><td>SBIN0009620</td></tr>
    </table>
    <table><tr><th>Project Name</th><th>Rera Registration Number</th><th>Permit Number</th></tr>
      <tr><td>PRANAMI BLUE SAPPHIRE</td><td>JRERA/PROJECT/08/2018</td><td>826/2014/A</td></tr>
    </table>
    <table><tr><th>Contractor Name</th><th>Address</th><th>PAN No.</th><th>Mobile</th><th>Email Id</th></tr>
      <tr><td>PRANAMI BUILDERS PVT.LTD</td><td>Ranchi</td><td>AAECP0371L</td>
          <td>9608016395</td><td>projects@pranamigroup.com</td></tr>
    </table>
    {extra}
    """


# --- the documents bug ----------------------------------------------------

def test_every_document_link_is_found_not_just_the_ones_ending_in_pdf():
    """JHARERA serves documents from /FirstLevel/ViewDocument/<id> with no
    file extension anywhere in the URL. Matching on a .pdf suffix found 2 of
    69 on a real record."""
    entries = document_entries(_DOC_HTML)
    assert len(entries) == 4, entries
    assert all(e["document_id"] for e in entries), entries
    print("test_every_document_link_is_found_not_just_the_ones_ending_in_pdf: PASS")


def test_a_documents_label_comes_from_its_table_not_its_link_text():
    """The link text is always "View". Both real table shapes must yield the
    document's actual name."""
    entries = {e["document_id"]: e["label"] for e in document_entries(_DOC_HTML)}
    assert entries["106945"] == "Company Pan Card", entries
    assert entries["106946"] == "Balance Sheet", entries
    assert entries["106947"] == "Income Tax Preceeding Year 1", entries
    # ...and the sibling-cell shape.
    assert entries["106948"] == "Non Encumbrance Certificate", entries
    print("test_a_documents_label_comes_from_its_table_not_its_link_text: PASS")


def test_the_pan_card_label_is_one_promoter_identity_recognises():
    """The link between this adapter and the PAN extraction. If the derived
    label stops matching promoter_identity's hints, the PAN silently stops
    being read and nothing else fails."""
    import promoter_identity as pi

    labels = [e["label"] for e in document_entries(_DOC_HTML)]
    pan_labels = [l for l in labels if any(h in f" {l.lower()} " for h in pi._PAN_DOC_HINTS)]
    assert pan_labels == ["Company Pan Card"], (labels, pan_labels)
    print("test_the_pan_card_label_is_one_promoter_identity_recognises: PASS")


def test_document_label_never_returns_the_word_view():
    """Anti-regression on the collision: if a label ever comes back as the
    link text, every document on the page shares one filename."""
    for entry in document_entries(_DOC_HTML):
        assert entry["label"].strip().lower() not in ("view", "download", ""), entry
    print("test_document_label_never_returns_the_word_view: PASS")


class _FakeResponse:
    def __init__(self, content=b"%PDF-1.4 x", content_type="application/pdf", status_code=200):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.status_code = status_code


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        return self.response


def test_download_single_document_writes_the_bytes_and_reports_the_path():
    """The seam group_financial_disclosure.py uses to pull one specific
    document -- a balance sheet -- without a full acquire()."""
    directory = tempfile.mkdtemp(prefix="jharkhand_docs_")
    try:
        dest = os.path.join(directory, "balance_sheet")
        result = download_single_document(
            {"label": "Balance Sheet", "url": "/FirstLevel/ViewDocument/106946"},
            dest, session=_FakeSession(_FakeResponse()),
        )
        assert result["status"] == "downloaded", result
        assert result["saved_path"] == dest
        with open(dest, "rb") as f:
            assert f.read() == b"%PDF-1.4 x"
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    print("test_download_single_document_writes_the_bytes_and_reports_the_path: PASS")


def test_download_single_document_refuses_an_html_error_page():
    """JHARERA's error page for a missing document is still HTTP 200 -- the
    content type, not the status code, is what proves it is not the file."""
    directory = tempfile.mkdtemp(prefix="jharkhand_docs_")
    try:
        dest = os.path.join(directory, "missing")
        result = download_single_document(
            {"label": "Missing Doc", "url": "/FirstLevel/ViewDocument/999999"},
            dest, session=_FakeSession(_FakeResponse(b"<html>nope</html>", "text/html")),
        )
        assert result["status"] == "failed (portal served a web page, not a document)", result
        assert not os.path.exists(dest)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    print("test_download_single_document_refuses_an_html_error_page: PASS")


def test_live_fetch_project_summary_exposes_the_documents_list():
    if not _LIVE:
        print("test_live_fetch_project_summary_exposes_the_documents_list: SKIPPED (set JHARERA_LIVE=1)")
        return
    summary = fetch_project_summary(_REG)
    assert summary.get("opened") is True, summary
    assert summary.get("documents"), "fetch_project_summary must expose the documents it already parsed"
    assert summary["documents_on_page"] == len(summary["documents"])
    print("test_live_fetch_project_summary_exposes_the_documents_list: PASS")


def test_a_filename_can_never_overflow_the_platform_path_limit():
    """REGRESSION, and it aborted a real run halfway through.

    JHARERA derives some document labels from a full sentence -- "The names
    and addresses of the contractors, archiect, structural engineer, if
    any..." -- and the earlier fixed 80-character cap produced a
    262-character path under a deep output directory. Windows refuses to
    create a path over 260 unless long paths are enabled, and reports it as
    FileNotFoundError, which reads like a missing directory rather than an
    over-long name. The acquisition died after a dozen documents had
    already been written.

    The budget is computed from the CALLER'S directory, because how much
    room is left depends entirely on how deep --output-dir is: the same
    label is fine under output/ and overflows under a temp path.
    """
    from states.base import safe_document_filename

    deep = "C:/Users/x/AppData/Local/Temp/claude/a-very-long-session-id-here/scratchpad/out/JHARERA-PROJECT-35-2023/documents"
    label = ("The names and addresses of the contractors, archiect, structural engineer, "
             "if any and other details thereof")
    used = set()
    name = safe_document_filename(deep, label, used, suffix="_106999")
    assert len(os.path.join(deep, name)) <= 255, len(os.path.join(deep, name))
    # The id is what makes the name unique, so truncation must never eat it.
    assert name.endswith("_106999.pdf"), name
    # A second document with the same label still gets a distinct name.
    second = safe_document_filename(deep, label, used, suffix="_106999")
    assert second != name, (name, second)
    # ...and a shallow directory keeps the label readable rather than
    # truncating everything to the shortest common case.
    short = safe_document_filename("output/P1/documents", "Company Pan Card", set(), suffix="_1")
    assert short == "Company_Pan_Card_1.pdf", short
    print("test_a_filename_can_never_overflow_the_platform_path_limit: PASS")


# --- the false-clean-record rule -----------------------------------------

def test_an_empty_litigation_table_is_a_clean_check_but_a_missing_one_is_not():
    """The distinction that keeps this adapter honest. Present and empty is
    a real nil return; absent means nobody looked."""
    present = parse_project_detail(_detail(_LITIGATION_PRESENT_EMPTY))
    assert present["litigation_table_present"] is True
    assert present["litigation"] == []
    notes = " ".join(project_notes(present))
    assert "genuine clean result" in notes, notes

    missing = parse_project_detail(_detail())
    assert missing["litigation_table_present"] is False
    missing_notes = " ".join(project_notes(missing))
    assert "UNKNOWN" in missing_notes, missing_notes
    assert "must not be read as a clean litigation record" in missing_notes, missing_notes
    print("test_an_empty_litigation_table_is_a_clean_check_but_a_missing_one_is_not: PASS")


# --- parsing --------------------------------------------------------------

def test_tables_are_found_by_header_not_by_index():
    """A JHARERA project page emits 17 to 20 tables depending on how many
    blocks, accounts and contractors the promoter filed, so the same logical
    table sits at different positions on different projects."""
    parsed = parse_project_detail("<table><tr><th>Junk</th></tr><tr><td>x</td></tr></table>" + _detail())
    assert parsed["promoter_name"] == "PBPL PRANAMI CREST RERA PRIVATE LIMITED", parsed
    assert parsed["past_projects"][0]["Rera Registration Number"] == "JRERA/PROJECT/08/2018"
    print("test_tables_are_found_by_header_not_by_index: PASS")


def test_the_declared_past_project_survives_parsing():
    """This is the whole reason Jharkhand was built: the promoter's earlier
    registrations are the track record a single-state read never sees."""
    parsed = parse_project_detail(_detail())
    assert len(parsed["past_projects"]) == 1, parsed["past_projects"]
    notes = " ".join(project_notes(parsed))
    assert "earlier JHARERA registration" in notes, notes
    print("test_the_declared_past_project_survives_parsing: PASS")


def test_contractor_pans_are_captured_as_professional_records():
    """JHARERA is the first built state to publish a PAN as a plain FIELD."""
    parsed = parse_project_detail(_detail())
    contractors = [p for p in parsed["professionals"] if p["professionalTypeName"] == "Contractor"]
    assert contractors and contractors[0]["panNumber"] == "AAECP0371L", parsed["professionals"]
    assert "AAECP0371L" in parsed["pans_on_page"], parsed["pans_on_page"]
    print("test_contractor_pans_are_captured_as_professional_records: PASS")


def test_search_rows_need_a_profile_link_to_count():
    """A results row with no ViewProjectProfile link cannot be opened, so it
    is not a usable match and must not be offered as one."""
    html = """<table><tr><th>Sl.No.</th><th>Reg No. & Date</th><th>Project Name</th><th>Address</th></tr>
      <tr><td>1</td><td>JHARERA/PROJECT/35/2023</td><td>PRANAMI CREST</td><td>Ranchi</td>
          <td><a href="/Home/ViewProjectProfile/2625">View Profile</a></td></tr>
      <tr><td>2</td><td>JHARERA/PROJECT/99/2023</td><td>NO LINK</td><td>x</td><td>-</td></tr>
    </table>"""
    rows = parse_search_rows(html)
    assert len(rows) == 1, rows
    assert rows[0]["reg_no"] == "JHARERA/PROJECT/35/2023"
    assert rows[0]["project_id"] == "2625"
    assert parse_search_rows("<html></html>") == []
    print("test_search_rows_need_a_profile_link_to_count: PASS")


def test_labelled_rows_drops_a_header_echo_row():
    html = """<table><tr><th>Name</th><th>Designation</th></tr>
      <tr><td>Name</td><td>Designation</td></tr>
      <tr><td>Bijay Kumar Agarwal</td><td>Director</td></tr></table>"""
    from bs4 import BeautifulSoup
    rows = labelled_rows(BeautifulSoup(html, "html.parser").find("table"))
    assert len(rows) == 1, rows
    assert labelled_rows(None) is None
    print("test_labelled_rows_drops_a_header_echo_row: PASS")


def test_a_brand_search_returning_a_crowd_is_discarded():
    """JHARERA does not tokenise a query, so a full legal name returns
    nothing and only a single brand word matches. That fallback is
    necessary -- without it a group with real Jharkhand projects reads as
    searched-and-empty -- but it is also how noise gets in.

    A real group brand returns a handful of projects. A word that is not
    really a brand returns the register. Above the threshold the result is
    discarded entirely, because there is no way to tell which of thirty
    hits belong to this promoter and presenting them all would bury the
    real ones."""
    from states import adapter_jharkhand as jh

    assert jh._MAX_BRAND_MATCHES < 10, jh._MAX_BRAND_MATCHES
    calls = []

    def _fake_get(session, url, ctx, params=None, what="page"):
        calls.append(params["SearchBy"])
        # The full legal name finds nothing; the brand finds a crowd.
        if params["SearchBy"] == "Shree Hanuman Oil Mills Ltd":
            return "<html></html>"
        rows = "".join(
            f'<tr><td>{i}</td><td>JHARERA/PROJECT/{i}/2024</td><td>P{i}</td><td>a</td>'
            f'<td><a href="/Home/ViewProjectProfile/{i}">v</a></td></tr>'
            for i in range(1, 12)
        )
        return ("<table><tr><th>Sl.No.</th><th>Reg No. &amp; Date</th><th>Project Name</th>"
                f"<th>Address</th></tr>{rows}</table>")

    original = jh._get
    jh._get = _fake_get
    try:
        result = jh.search_promoter_projects("Shree Hanuman Oil Mills Ltd")
    finally:
        jh._get = original

    assert calls == ["Shree Hanuman Oil Mills Ltd", "HANUMAN"], calls
    assert result == [], f"a crowd of {len(result)} was presented as group projects"
    print("test_a_brand_search_returning_a_crowd_is_discarded: PASS")


# --- profile ---------------------------------------------------------------

def test_both_registration_spellings_resolve_to_jharkhand():
    """JHARERA/ is current and JRERA/ is what pre-2019 registrations carry.
    A pattern accepting only the longer one would fail to resolve every
    older project -- including PRANAMI BLUE SAPPHIRE, which is the declared
    past project of the very project this adapter was built for."""
    for reg in ("JHARERA/PROJECT/35/2023", "JRERA/PROJECT/08/2018", "JHARERA/PROJECT/144/2024"):
        profiles, _ = states.candidate_profiles(reg)
        assert [p.code for p in profiles] == ["JH"], (reg, [p.code for p in profiles])
    print("test_both_registration_spellings_resolve_to_jharkhand: PASS")


def test_the_storage_key_flattens_the_slashes():
    key = storage_key(_REG)
    assert "/" not in key and "\\" not in key, key
    assert key == "JHARERA-PROJECT-35-2023", key
    print("test_the_storage_key_flattens_the_slashes: PASS")


def test_capabilities_match_what_the_portal_actually_offers():
    profile = states.PROFILES["JH"]
    assert profile.can(states.CAP_LOOKUP_BY_REG_NO)
    assert profile.can(states.CAP_DOCUMENTS)
    assert profile.can(states.CAP_PROMOTER_PORTFOLIO)
    # These two gate MahaRERA-only work; declaring either sends a Jharkhand
    # run at a Maharashtra portal. Karnataka did exactly that once.
    assert not profile.can(states.CAP_ORDERS_SEARCH)
    assert not profile.can(states.CAP_LAND_RECORDS)
    print("test_capabilities_match_what_the_portal_actually_offers: PASS")


# --- rejected / surrendered registers ---------------------------------

_REJECTED_HTML = """
<table>
 <tr><th>Sl.No.</th><th>Promoter Name</th><th>Project Name</th><th>Project Address</th></tr>
 <tr><td>1</td><td>R.S. Agrawal Infratech Pvt. Ltd., Ranchi</td><td>Fulchand Gardenia</td>
     <td>Powerhouse Road, Chutia, Ranchi</td></tr>
</table>
"""

# The real live shape: a SECOND "Project Name" header whose cells actually
# carry the JHARERA registration number, not another project name.
_SURRENDERED_HTML = """
<table>
 <tr><th>Sl.No.</th><th>Promoter Name</th><th>Project Name</th><th>Project Name</th>
     <th>Project Address</th></tr>
 <tr><td>1</td><td>B T HIRANI</td><td>SUNSHINE HEIGHTS</td>
     <td>JHARERA/PROJECT/229/2023</td><td>Kanke Road, Ranchi</td></tr>
</table>
"""


def test_rejected_register_is_found_by_header_and_carries_no_reg_no():
    """A rejected APPLICATION never reached registration -- there is no
    number to carry, and the parser must not invent one from the wrong
    column."""
    rows = parse_rejected_or_surrendered_register(_REJECTED_HTML)
    assert len(rows) == 1, rows
    assert rows[0]["promoter_name"] == "R.S. Agrawal Infratech Pvt. Ltd., Ranchi", rows[0]
    assert rows[0]["project_name"] == "Fulchand Gardenia", rows[0]
    assert rows[0]["reg_no"] == "", rows[0]
    assert rows[0]["address"] == "Powerhouse Road, Chutia, Ranchi", rows[0]
    print("test_rejected_register_is_found_by_header_and_carries_no_reg_no: PASS")


def test_surrendered_registers_mislabelled_second_column_becomes_reg_no():
    """THE REAL BUG SHAPE. The surrendered table's own markup repeats
    'Project Name' as a header for what is actually the registration
    number column -- confirmed live 2026-09-09. Reading it positionally by
    header text alone would either drop the number or overwrite the real
    project name with it; this pins that the second occurrence lands in
    `reg_no`, and the first stays the project name."""
    rows = parse_rejected_or_surrendered_register(_SURRENDERED_HTML)
    assert len(rows) == 1, rows
    assert rows[0]["promoter_name"] == "B T HIRANI", rows[0]
    assert rows[0]["project_name"] == "SUNSHINE HEIGHTS", rows[0]
    assert rows[0]["reg_no"] == "JHARERA/PROJECT/229/2023", rows[0]
    print("test_surrendered_registers_mislabelled_second_column_becomes_reg_no: PASS")


def test_fetch_rejected_and_surrendered_use_the_injected_fetcher():
    """Both fetch_* functions take a `fetcher` override -- the offline seam
    group_enforcement.py's own fetchers dict relies on."""
    assert fetch_rejected_projects(fetcher=lambda: _REJECTED_HTML) == \
        parse_rejected_or_surrendered_register(_REJECTED_HTML)
    assert fetch_surrendered_projects(fetcher=lambda: _SURRENDERED_HTML) == \
        parse_rejected_or_surrendered_register(_SURRENDERED_HTML)
    print("test_fetch_rejected_and_surrendered_use_the_injected_fetcher: PASS")


def test_live_rejected_and_surrendered_registers_are_readable():
    if not _LIVE:
        print("test_live_rejected_and_surrendered_registers_are_readable: SKIPPED (set JHARERA_LIVE=1)")
        return
    rejected = fetch_rejected_projects()
    surrendered = fetch_surrendered_projects()
    assert rejected, "expected at least one rejected application"
    assert surrendered, "expected at least one surrendered registration"
    assert all(r["promoter_name"] for r in rejected), rejected
    assert all(r["promoter_name"] for r in surrendered), surrendered
    print("test_live_rejected_and_surrendered_registers_are_readable: PASS")


# --- live ------------------------------------------------------------------

def test_live_pranami_crest_end_to_end():
    if not _LIVE:
        print("test_live_pranami_crest_end_to_end: SKIPPED (set JHARERA_LIVE=1)")
        return
    import main
    import tempfile

    ctx = states.AcquisitionContext(output_dir=tempfile.mkdtemp(), reporter=main.CliReporter())
    result = states.get_adapter("JH").acquire(_REG, ctx)
    assert result.registration_number == _REG, result.registration_number
    assert "PRANAMI" in (result.promoter_name or "").upper(), result.promoter_name
    assert len(result.documents_manifest) > 20, len(result.documents_manifest)
    labels = {r["label"] for r in result.documents_manifest}
    assert any("Pan Card" in l for l in labels), sorted(labels)[:10]
    print("test_live_pranami_crest_end_to_end: PASS")


if __name__ == "__main__":
    test_every_document_link_is_found_not_just_the_ones_ending_in_pdf()
    test_a_documents_label_comes_from_its_table_not_its_link_text()
    test_the_pan_card_label_is_one_promoter_identity_recognises()
    test_document_label_never_returns_the_word_view()
    test_download_single_document_writes_the_bytes_and_reports_the_path()
    test_download_single_document_refuses_an_html_error_page()
    test_live_fetch_project_summary_exposes_the_documents_list()
    test_a_filename_can_never_overflow_the_platform_path_limit()
    test_an_empty_litigation_table_is_a_clean_check_but_a_missing_one_is_not()
    test_tables_are_found_by_header_not_by_index()
    test_the_declared_past_project_survives_parsing()
    test_contractor_pans_are_captured_as_professional_records()
    test_search_rows_need_a_profile_link_to_count()
    test_labelled_rows_drops_a_header_echo_row()
    test_a_brand_search_returning_a_crowd_is_discarded()
    test_both_registration_spellings_resolve_to_jharkhand()
    test_the_storage_key_flattens_the_slashes()
    test_capabilities_match_what_the_portal_actually_offers()
    test_rejected_register_is_found_by_header_and_carries_no_reg_no()
    test_surrendered_registers_mislabelled_second_column_becomes_reg_no()
    test_fetch_rejected_and_surrendered_use_the_injected_fetcher()
    test_live_rejected_and_surrendered_registers_are_readable()
    test_live_pranami_crest_end_to_end()
    print("\nAll tests passed.")

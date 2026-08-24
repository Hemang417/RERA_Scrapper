"""
Guards on the HARERA adapter.

THE BUG THIS FILE EXISTS FOR KILLED EVERY HARYANA RUN, and nothing caught it
because nothing exercised it. `_download_documents` called

    safe_document_filename(entry.get("label") or "document", used)

against a helper whose signature is (documents_dir, label, used_names). That
is a TypeError on the FIRST document -- and the call sat outside the try/
except wrapping the fetch, so it did not degrade into a per-document
"failed" row. It propagated out of `acquire()` as a bare TypeError, which is
not a StateAcquisitionError, so main.py did not catch it either. A real
Gurugram record (RERA-GRG-741-2020, 102 EDEN ESTATE) links SIXTY documents,
so the crash was certain rather than conditional.

Two things follow from that, and both are tested here:

  1. The directory argument is not a formality. The length budget
     safe_document_filename applies is measured against the CALLER'S output
     directory, and HARERA's labels are long -- "AMSTORIA 26-2-2015.DWG
     STREET LIGHTING-MODEL", "COPY OF LICENSE ALONG WITH SCHEDULE OF LAND".
     An over-long path is what Windows reports as FileNotFoundError, mid-run,
     after files have already been written. That is the JHARERA bug.
  2. A failure while naming a file must still be a manifest row, not an
     escaped exception. The fetch is inside the try; so is the naming now.

THE SECOND BUG, found while fixing the first: a document id HARERA does not
hold answers HTTP 200 with 25 KB of HTML. The downloader trusted the status
code, so it would write that error page to disk under the document's name
and report it "downloaded" -- a Charter document library listing a licence,
a jamabandi and a demarcation plan that are all the same web page. Same
species as UP-RERA's 200-OK shell and TNRERA's "Page not found" body.

THE THIRD BUG WAS A KEY COLLISION, found by counting the live register
rather than by reading the code. 52 of the 2,161 rows across both benches
print the literal string "NA" in the Project ID column -- BPTP NEST 83-A/B/C,
NINEX RESIDENCY, several 2017 affordable-housing schemes -- and the Project
ID is the primary key of `output/<reg_no>/`. All fifty-two would have been
filed in one directory, overwriting each other: the Gujarat nested-path bug
the other way round. Every one of them carries a distinct certificate
number, so that is the fallback key now.

The rest of the file covers what the adapter reads: the register (found by
three header needles, because one selects a decoy search form), the lapsed
flag that concatenates onto a certificate number, the CIN -- which no other
authority in this pipeline publishes -- validated by shape rather than
position, the masked PAN that must never be harvested, and HARERA's
litigation question, whose own text is rendered into the answer cell.

Network tests are opt-in: HARERA_LIVE=1.

Run directly: python test_haryana_adapter.py
"""

import os
import shutil
import tempfile

import group_sweep as gs
import states
import states.adapter_haryana as hr
from states.adapter_haryana import (
    HaryanaAdapter,
    document_extension,
    fetch_project_summary,
    looks_like_a_document,
    parse_project_detail,
    parse_register,
    project_key,
)
from states.base import storage_key

_LIVE = os.environ.get("HARERA_LIVE") == "1"
_REG = "RERA-GRG-741-2020"


class _Reporter:
    def __init__(self):
        self.messages = []

    def info(self, m): self.messages.append(m)
    def warn(self, m): self.messages.append(m)
    def ok(self, m): self.messages.append(m)
    def choose(self, p, o): return None


class _Ctx:
    def __init__(self, reporter):
        self.reporter = reporter
        self.output_dir = "output"
        self.on_resolved = None


class _Response:
    def __init__(self, content=b"%PDF-1.4 x", ctype="application/pdf"):
        self.content = content
        self.headers = {"Content-Type": ctype}
        self.status_code = 200

    def raise_for_status(self):
        return None


class _Session:
    def __init__(self, response=None):
        self.response = response or _Response()
        self.asked = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        return self.response


def test_downloading_a_document_does_not_crash_the_run():
    """THE REGRESSION. One document with an ordinary label used to raise
    TypeError before a single byte was fetched."""
    directory = tempfile.mkdtemp(prefix="harera_docs_")
    try:
        reporter = _Reporter()
        manifest = HaryanaAdapter()._download_documents(
            _Session(),
            [{"label": "COPY OF LICENSE ALONG WITH SCHEDULE OF LAND",
              "url": "/project/view_uploaded_Document_open/abc123"}],
            directory, _Ctx(reporter),
        )
        assert len(manifest) == 1, manifest
        assert manifest[0]["status"] == "downloaded", manifest
        assert os.path.isfile(manifest[0]["path"]), manifest
        # And the relative URL was made absolute against the portal.
        assert manifest[0]["url"].startswith("https://haryanarera.gov.in/"), manifest
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    print("test_downloading_a_document_does_not_crash_the_run: PASS")


def test_sixty_documents_produce_sixty_files():
    """The count HARERA actually links on one real Gurugram record, and the
    collision check that goes with it: manifest length and file count must
    agree, or promoters filing different slots under one label have silently
    overwritten each other."""
    directory = tempfile.mkdtemp(prefix="harera_docs_")
    try:
        entries = [{"label": "JAMABANDI", "url": f"/project/view_uploaded_Document_open/{i}"}
                   for i in range(60)]
        manifest = HaryanaAdapter()._download_documents(
            _Session(), entries, directory, _Ctx(_Reporter())
        )
        downloaded = [row for row in manifest if row["status"] == "downloaded"]
        assert len(downloaded) == 60, len(downloaded)
        assert len(os.listdir(directory)) == 60, os.listdir(directory)
        assert len({row["path"] for row in downloaded}) == 60, "two rows share a path"
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    print("test_sixty_documents_produce_sixty_files: PASS")


def test_a_long_label_is_budgeted_against_the_real_output_directory():
    """Why the directory argument exists. The same label is safe under
    `output/` and overflows under a deep path, and Windows reports the
    overflow as FileNotFoundError -- which reads as a missing directory
    rather than an over-long name."""
    deep = tempfile.mkdtemp(prefix="harera_" + ("d" * 60) + "_")
    deep = os.path.join(deep, "x" * 60, "y" * 60)
    os.makedirs(deep, exist_ok=True)
    try:
        manifest = HaryanaAdapter()._download_documents(
            _Session(),
            [{"label": "AMSTORIA 26-2-2015.DWG STREET LIGHTING-MODEL LAYOUT FOR BLOCK C "
                       "AND THE ADJOINING SECTOR ROAD AS SANCTIONED",
              "url": "/project/view_uploaded_Document_open/abc"}],
            deep, _Ctx(_Reporter()),
        )
        assert manifest[0]["status"] == "downloaded", manifest
        assert os.path.isfile(manifest[0]["path"])
        assert len(manifest[0]["path"]) <= 260, len(manifest[0]["path"])
    finally:
        shutil.rmtree(deep, ignore_errors=True)
    print("test_a_long_label_is_budgeted_against_the_real_output_directory: PASS")


def test_a_naming_or_write_failure_is_a_manifest_row_not_an_exception():
    """The structural half of the fix. Everything that can fail per document
    is inside the try, so one bad document costs one row -- it does not
    abandon the fifty-nine that follow."""
    class _Boom(_Session):
        def get(self, url, **kwargs):
            raise OSError("disk went away")

    manifest = HaryanaAdapter()._download_documents(
        _Boom(),
        [{"label": "JAMABANDI", "url": "/project/x"},
         {"label": "LOI-18.606", "url": "/project/y"}],
        tempfile.mkdtemp(prefix="harera_docs_"), _Ctx(_Reporter()),
    )
    assert len(manifest) == 2, manifest
    assert all(row["status"].startswith("failed: OSError") for row in manifest), manifest
    print("test_a_naming_or_write_failure_is_a_manifest_row_not_an_exception: PASS")


def test_an_error_page_is_not_saved_as_a_document():
    """THE SECOND BUG. HARERA answers a document id it does not hold with
    25 KB of HTML at HTTP 200, so the status code proves nothing."""
    directory = tempfile.mkdtemp(prefix="harera_docs_")
    try:
        manifest = HaryanaAdapter()._download_documents(
            _Session(_Response(b"<!DOCTYPE html><html>...", "text/html; charset=UTF-8")),
            [{"label": "JAMABANDI", "url": "/project/view_uploaded_Document_open/nope"}],
            directory, _Ctx(_Reporter()),
        )
        assert manifest[0]["status"] == "not held by the portal", manifest
        assert os.listdir(directory) == [], os.listdir(directory)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    print("test_an_error_page_is_not_saved_as_a_document: PASS")


def test_the_served_type_names_the_file_not_the_url():
    """HARERA's document URLs are opaque -- a hash, no file name -- so the
    Content-Type is the only evidence of what the bytes are."""
    assert document_extension(_Response(ctype="application/pdf")) == ".pdf"
    assert document_extension(_Response(ctype="image/jpeg")) == ".jpg"
    assert document_extension(
        _Response(ctype="application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet")) == ".xlsx"
    # Unknown types still have to produce a usable name.
    assert document_extension(_Response(ctype="application/octet-stream")) == ".pdf"

    assert looks_like_a_document(_Response(b"%PDF-1.4", "application/pdf"))
    assert not looks_like_a_document(_Response(b"", "application/pdf"))
    assert not looks_like_a_document(_Response(b"<html>", "text/html"))
    print("test_the_served_type_names_the_file_not_the_url: PASS")


# --- the register ---------------------------------------------------------

# The register, with the three shapes that bite: the decoy search form, a
# lapsed row whose flag text runs into the certificate number, and a row
# whose Project ID is the literal string "NA".
_REGISTER_HTML = """
<table>
 <tr><th>Registration Certificate Number</th></tr>
 <tr><td><input name="search"/></td></tr>
</table>
<table>
 <tr><th>Sr</th><th>Registration Certificate Number</th><th>Project ID</th>
     <th>Project Name</th><th>Builder</th><th>Project Location</th>
     <th>Project District</th><th>Registered With</th><th>View</th></tr>
 <tr><td>1</td><td>GGM/415/147/2020/31 DATED 09.10.2020</td><td>RERA-GRG-741-2020</td>
     <td>102 EDEN ESTATE</td><td>COUNTRYWIDE PROMOTERS PRIVATE LIMITED</td>
     <td>Sector 102</td><td>Gurugram</td><td>DTCP</td>
     <td><a href="/view_project/project_preview_open/1444">View</a></td></tr>
 <tr><td>2</td><td>GGM/486/218/2021/54 DATED 21.09.2021
        <a href="#">Lapsed Project</a></td><td>RERA-GRG-800-2021</td>
     <td>SOME LAPSED SCHEME</td><td>COUNTRYWIDE PROMOTERS PRIVATE LIMITED</td>
     <td>Sector 99</td><td>Gurugram</td><td>DTCP</td>
     <td><a href="/view_project/project_preview_open/1500">View</a></td></tr>
 <tr><td>3</td><td>HRERA-PKL-FBD-155-2019</td><td>NA</td>
     <td>BPTP NEST 83-A</td><td>COUNTRYWIDE PROMOTERS PVT LTD</td>
     <td>Sector 83</td><td>Faridabad</td><td>DTCP</td><td></td></tr>
</table>
"""


def test_the_register_is_selected_by_three_needles_not_one():
    """The page serves five tables and one of them is a SEARCH FORM whose
    only header cell reads "Registration Certificate Number". Matching on
    that phrase alone selects the form and finds no projects at all."""
    rows = parse_register(_REGISTER_HTML)
    assert len(rows) == 3, [r["project_id"] for r in rows]
    assert rows[0]["project_id"] == "RERA-GRG-741-2020", rows[0]
    assert rows[0]["builder"] == "COUNTRYWIDE PROMOTERS PRIVATE LIMITED"
    print("test_the_register_is_selected_by_three_needles_not_one: PASS")


def test_the_lapsed_flag_does_not_run_into_the_certificate_number():
    """The flag is an anchor INSIDE the certificate cell, so its text
    concatenates onto the number -- "GGM/486/218/2021/54 DATED 21.09.2021
    Lapsed Project". Left there, a reader pasting the certificate number
    would never match it."""
    rows = {r["project_id"]: r for r in parse_register(_REGISTER_HTML)}
    lapsed = rows["RERA-GRG-800-2021"]
    assert lapsed["certificate_no"] == "GGM/486/218/2021/54 DATED 21.09.2021", lapsed
    # And the flag itself is kept, because a lapsed registration is a finding.
    assert lapsed["lapsed"] is True
    assert rows["RERA-GRG-741-2020"]["lapsed"] is False
    print("test_the_lapsed_flag_does_not_run_into_the_certificate_number: PASS")


def test_the_detail_id_belongs_to_its_own_row():
    """A table-wide anchor scan would attach the first row's detail id to
    every other row -- and then every project on the bench would open as
    102 EDEN ESTATE."""
    rows = {r["project_id"]: r for r in parse_register(_REGISTER_HTML)}
    assert rows["RERA-GRG-741-2020"]["detail_id"] == "1444"
    assert rows["RERA-GRG-800-2021"]["detail_id"] == "1500"
    assert rows["NA"]["detail_id"] is None, rows["NA"]
    print("test_the_detail_id_belongs_to_its_own_row: PASS")


def test_a_project_with_no_project_id_is_not_filed_under_NA():
    """THE KEY COLLISION. 52 of the 2,161 rows across both benches print the
    literal string "NA" in the Project ID column -- mostly older, mostly
    lapsed. Used as the primary key of output/<reg_no>/ all fifty-two land
    in one directory and overwrite each other: the Gujarat nested-path bug
    the other way round. All 52 carry a certificate number and all 52 of
    those are distinct."""
    rows = {r["project_name"]: r for r in parse_register(_REGISTER_HTML)}
    na_row = rows["BPTP NEST 83-A"]
    assert na_row["project_id"] == "NA", na_row
    assert project_key(na_row) == "HRERA-PKL-FBD-155-2019", project_key(na_row)
    assert storage_key(project_key(na_row)) != "NA"

    # A normal row still keys on its Project ID.
    assert project_key(rows["102 EDEN ESTATE"]) == "RERA-GRG-741-2020"
    # Neither available: still not "NA", and never empty.
    assert project_key({"project_id": "NA", "certificate_no": "NA",
                        "project_name": "ORPHAN SCHEME"}) == "ORPHAN SCHEME"
    assert project_key({}) == "UNKNOWN"
    print("test_a_project_with_no_project_id_is_not_filed_under_NA: PASS")


def test_the_gurugram_certificate_slashes_survive_as_a_directory_name():
    """A Gurugram certificate used unchanged as a directory name produces a
    five-level nested tree -- the bug Gujarat shipped once. It matters here
    because the certificate is now the fallback key."""
    key = storage_key("GGM/415/147/2020/31 DATED 09.10.2020")
    assert "/" not in key and "\\" not in key, key
    print("test_the_gurugram_certificate_slashes_survive_as_a_directory_name: PASS")


# --- the detail record ----------------------------------------------------

_DETAIL_HTML = """
<table>
 <tr><td>1. Name and registered address of the company : COUNTRYWIDE PROMOTERS PRIVATE
     LIMITED (Annex a copy in Folder A) OT-14, 3RD FLOOR, FARIDABAD HR 121004 IN</td></tr>
 <tr><td>CIN No. (Annex a copy in Folder A) U70101DL1996PTC075865</td></tr>
 <tr><td>PAN No. XXXX280H</td></tr>
 <tr><td>Email ID : rera.gurugram@bptp.com</td></tr>
 <tr><td>2. Managing Director/HOD/CEO :</td></tr>
 <tr><td>Name : JAWAHAR CHAWLA</td></tr>
 <tr><td>3. Director 1 :</td></tr>
 <tr><td>Name : ANJALI GUPTA</td></tr>
 <tr><td>9. Whether any litigation is pending against the Project: Yes/No
     (If yes-give Annex details in folder G) No</td></tr>
</table>
<table>
 <tr><th>Sr</th><th>Document Description</th><th>View</th></tr>
 <tr><td>1</td><td>JAMABANDI</td>
     <td><a href="/project/view_uploaded_Document_open/abc">View</a></td></tr>
</table>
"""


def test_the_cin_is_the_join_key_no_other_authority_publishes():
    """Every other authority in this pipeline publishes neither CIN nor DIN,
    which forces the RERA-to-MCA link onto names and the false positives
    that implies. Haryana states it outright."""
    parsed = parse_project_detail(_DETAIL_HTML)
    assert parsed["corporate_identity_key"] == "U70101DL1996PTC075865", parsed
    print("test_the_cin_is_the_join_key_no_other_authority_publishes: PASS")


def test_a_malformed_cin_is_refused_rather_than_passed_on():
    """Validated against the CIN shape, not accepted as whatever sits after
    the label. A malformed one joined against MCA records pulls a different
    company's charges and litigation into this promoter's record."""
    broken = _DETAIL_HTML.replace("U70101DL1996PTC075865", "NOT-A-CIN-AT-ALL")
    assert parse_project_detail(broken)["corporate_identity_key"] == ""
    print("test_a_malformed_cin_is_refused_rather_than_passed_on: PASS")


def test_the_masked_pan_is_recorded_as_masked_and_never_as_a_pan():
    """It renders as XXXX280H -- last four characters only. A four-character
    tail is not a PAN, and gst_group must never receive one from Haryana."""
    parsed = parse_project_detail(_DETAIL_HTML)
    assert parsed["pan_masked"] is True
    assert "pan" not in {k.casefold() for k in parsed if k != "pan_masked"}, list(parsed)
    assert "280H" not in str(parsed.get("corporate_identity_key", ""))
    print("test_the_masked_pan_is_recorded_as_masked_and_never_as_a_pan: PASS")


def test_the_litigation_answer_is_the_promoters_not_the_question():
    """HARERA renders its own question text INTO the answer cell, so the raw
    value reads "Yes/No (If yes-give Annex details in folder G) No". Stored
    raw it puts the words "Yes/No" into a Charter as though the promoter had
    declared both."""
    assert parse_project_detail(_DETAIL_HTML)["litigation_declared"] == "No"
    yes = _DETAIL_HTML.replace("in folder G) No", "in folder G) Yes")
    assert parse_project_detail(yes)["litigation_declared"] == "Yes"
    print("test_the_litigation_answer_is_the_promoters_not_the_question: PASS")


def test_a_record_with_no_litigation_answer_is_unknown_not_clean():
    """The false-clean-record rule in the form it takes here: a missing
    answer must reach the reader as UNKNOWN, not as a silent blank."""
    silent = _DETAIL_HTML.replace(
        "9. Whether any litigation is pending against the Project: Yes/No",
        "9. Something else entirely:")
    original = hr._get
    hr._get = lambda session, url, what="page": silent
    try:
        summary = fetch_project_summary("1444")
    finally:
        hr._get = original
    assert summary["opened"] is True, summary
    assert summary["litigation_declared"] == "", summary
    assert any("UNKNOWN" in n for n in summary["notes"]), summary["notes"]
    assert any("not be read as a clean litigation record" in n
               for n in summary["notes"]), summary["notes"]
    print("test_a_record_with_no_litigation_answer_is_unknown_not_clean: PASS")


def test_an_unreachable_detail_page_is_not_an_empty_record():
    """One unreachable project must not sink a sweep, and must not look like
    a project with nothing filed."""
    original = hr._get

    def _boom(session, url, what="page"):
        raise states.StateFetchError("portal down")

    hr._get = _boom
    try:
        summary = fetch_project_summary("1444")
    finally:
        hr._get = original
    assert summary["opened"] is False, summary
    assert "could not be opened" in summary["note"], summary
    print("test_an_unreachable_detail_page_is_not_an_empty_record: PASS")


def test_directors_are_read_with_their_roles():
    parsed = parse_project_detail(_DETAIL_HTML)
    assert parsed["directors"] == [
        {"role": "Managing Director/HOD/CEO", "name": "JAWAHAR CHAWLA"},
        {"role": "Director", "name": "ANJALI GUPTA"},
    ], parsed["directors"]
    print("test_directors_are_read_with_their_roles: PASS")


# --- resolution and the sweep seam ---------------------------------------

def test_a_promoter_filing_under_two_spellings_stays_apart_and_says_so():
    """COUNTRYWIDE PROMOTERS PRIVATE LIMITED and COUNTRYWIDE PROMOTERS PVT
    LTD are the same company on the real register, and the exact-match
    portfolio keeps them apart. That is the deliberate trade -- a loose
    match folds unrelated builders in -- so the limitation is stated in the
    portfolio's own notes rather than left for the reader to discover."""
    index = [{**r, "bench": "Gurugram"} for r in parse_register(_REGISTER_HTML)]
    chosen = next(r for r in index if r["project_id"] == "RERA-GRG-741-2020")
    portfolio = HaryanaAdapter()._promoter_portfolio(index, chosen, _Ctx(_Reporter()))
    assert portfolio["totals"]["total_projects"] == 2, portfolio
    assert all("BPTP NEST" not in p["project_name"] for p in portfolio["projects"]), portfolio
    assert any("differently spelled name" in n for n in portfolio["notes"]), portfolio["notes"]
    print("test_a_promoter_filing_under_two_spellings_stays_apart_and_says_so: PASS")


def test_a_lapsed_registration_is_counted_not_dropped():
    """A lapsed registration is diligence material."""
    index = [{**r, "bench": "Gurugram"} for r in parse_register(_REGISTER_HTML)]
    chosen = next(r for r in index if r["project_id"] == "RERA-GRG-741-2020")
    portfolio = HaryanaAdapter()._promoter_portfolio(index, chosen, _Ctx(_Reporter()))
    assert portfolio["totals"]["lapsed_registrations"] == 1, portfolio
    print("test_a_lapsed_registration_is_counted_not_dropped: PASS")


def test_haryana_is_searchable_and_openable():
    assert hasattr(hr, "search_promoter_projects")
    assert "HR" in gs.searchable_states(), gs.searchable_states()
    assert hasattr(hr, "fetch_project_summary"), (
        "a swept hit that cannot be opened can be neither confirmed nor refuted"
    )
    print("test_haryana_is_searchable_and_openable: PASS")


def test_complaints_are_unknown_rather_than_zero():
    """HARERA's case search takes a case number and a year behind a CAPTCHA,
    so there is no browsable name-bearing index. Complaints are None, never
    0 -- nothing downstream may read the absence as a clean record."""
    assert not states.PROFILES["HR"].can(states.CAP_ORDERS_SEARCH)
    assert any("UNKNOWN" in n for n in hr._AUTHORITY_NOTES), hr._AUTHORITY_NOTES
    print("test_complaints_are_unknown_rather_than_zero: PASS")


# --- live -----------------------------------------------------------------

def test_live_a_real_record_downloads_its_documents():
    if not _LIVE:
        print("test_live_a_real_record_downloads_its_documents: SKIPPED (set HARERA_LIVE=1)")
        return
    from states.adapter_haryana import (
        _all_projects, _get, _session, parse_project_detail,
    )
    from states.haryana import PROJECT_DETAIL

    session = _session()
    index = _all_projects(session)
    row = next(r for r in index if r["project_id"] == _REG)
    detail = parse_project_detail(
        _get(session, PROJECT_DETAIL.format(row["detail_id"]), what="detail")
    )
    assert len(detail["documents"]) > 50, len(detail["documents"])

    directory = tempfile.mkdtemp(prefix="harera_live_")
    try:
        manifest = HaryanaAdapter()._download_documents(
            session, detail["documents"][:5], directory, _Ctx(_Reporter())
        )
        downloaded = [row for row in manifest if row["status"] == "downloaded"]
        assert downloaded, manifest
        assert len(os.listdir(directory)) == len(downloaded)
        for row in downloaded:
            with open(row["path"], "rb") as f:
                assert f.read(4) == b"%PDF", row["path"]
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    print(f"test_live_a_real_record_downloads_its_documents: PASS "
          f"({len(detail['documents'])} linked)")


def test_live_a_document_the_portal_does_not_hold_answers_200_with_html():
    """The finding, checked against the portal so it cannot quietly stop
    being true."""
    if not _LIVE:
        print("test_live_a_document_the_portal_does_not_hold_answers_200_with_html: "
              "SKIPPED (set HARERA_LIVE=1)")
        return
    from states.adapter_haryana import _session

    response = _session().get(
        "https://haryanarera.gov.in/project/view_uploaded_Document_open/deadbeef00MTk5OTk5OQ==",
        timeout=180, verify=False,
    )
    assert response.status_code == 200, response.status_code
    assert not looks_like_a_document(response), "expected the error page, not a document"
    print("test_live_a_document_the_portal_does_not_hold_answers_200_with_html: PASS")


if __name__ == "__main__":
    test_the_register_is_selected_by_three_needles_not_one()
    test_the_lapsed_flag_does_not_run_into_the_certificate_number()
    test_the_detail_id_belongs_to_its_own_row()
    test_a_project_with_no_project_id_is_not_filed_under_NA()
    test_the_gurugram_certificate_slashes_survive_as_a_directory_name()
    test_the_cin_is_the_join_key_no_other_authority_publishes()
    test_a_malformed_cin_is_refused_rather_than_passed_on()
    test_the_masked_pan_is_recorded_as_masked_and_never_as_a_pan()
    test_the_litigation_answer_is_the_promoters_not_the_question()
    test_a_record_with_no_litigation_answer_is_unknown_not_clean()
    test_an_unreachable_detail_page_is_not_an_empty_record()
    test_directors_are_read_with_their_roles()
    test_a_promoter_filing_under_two_spellings_stays_apart_and_says_so()
    test_a_lapsed_registration_is_counted_not_dropped()
    test_haryana_is_searchable_and_openable()
    test_complaints_are_unknown_rather_than_zero()
    test_downloading_a_document_does_not_crash_the_run()
    test_sixty_documents_produce_sixty_files()
    test_a_long_label_is_budgeted_against_the_real_output_directory()
    test_a_naming_or_write_failure_is_a_manifest_row_not_an_exception()
    test_an_error_page_is_not_saved_as_a_document()
    test_the_served_type_names_the_file_not_the_url()
    test_live_a_real_record_downloads_its_documents()
    test_live_a_document_the_portal_does_not_hold_answers_200_with_html()
    print("\nAll tests passed.")

"""
StateAdapter for Uttar Pradesh / UP-RERA.

See states/uttarpradesh.py for the two numbering schemes and why the pattern
this repo used to carry failed every post-2024 registration. Everything below
was established live on 24 August 2026.

A MISSING PROJECT ON THIS PORTAL IS HTTP 200. That is the finding this
adapter is built around, and it is the same species as every other
false-clean-record bug in this pipeline. `Frm_View_Project_Details.aspx` does
not 404 an id it has never issued -- it serves a 48.7 KB page shell, with a
200, carrying the site chrome and no record at all:

    ?id=14636  -> 453 KB, "Project Id: (UPRERAPRJ14636)"
    ?id=378870 -> 48.7 KB, no project-id label anywhere
    ?id=30000  -> 48.7 KB, identical

Parsed for fields, that shell yields no promoter, no land, no bank account
and no documents -- which on a Charter reads as a project that filed none of
them. So resolution here does not ask "did the fetch succeed"; it asks
whether the page SERVED the registration number that was requested, and
refuses anything else. `served_registration_number()` is that check, and
`test_uttarpradesh_adapter.py` pins it against the real shell.

RESOLVING A LEGACY NUMBER COSTS NO SEARCH. The detail page's `?id=` is the
registration number's own numeric suffix -- UPRERAPRJ14636 is served by
`?id=14636`, verified across three records. That matters more than it
sounds, because THE SEARCH IS CAPTCHA-GATED and the profile did not know it:
`View_projects.aspx` ships

    function validate() {
        var txtcap = $('#ctl00_ContentPlaceHolder1_txtcap').val();
        if (txtcap.trim() == "") { alert("Please Enter Captcha"); return false; }
    }

alongside a `CaptchaImage.axd` image, and a postback without a solved
CAPTCHA returns the form with its results panel empty -- which is exactly
what "this promoter has no projects" would look like. So this adapter never
posts that form. It resolves by number, and where it cannot it says so
rather than searching and reporting the empty panel as an answer.

POST-2024 NUMBERS CANNOT BE RESOLVED HERE AND ARE REFUSED, NOT GUESSED.
UPRERAPRJ378870/03/2025 is a valid registration number whose `?id=378870`
serves the shell. Whatever key the current application uses for those is not
published anywhere this pipeline can read, so `acquire()` raises with that
stated. A guessed id would land on another promoter's project.

DOCUMENTS NEED NO POSTBACK, BECAUSE THE PAGE ALREADY NAMES THEM. Every
document is a LinkButton whose postback answers with
`<script>window.open('ViewDocument?Param=<uploaded file name>')</script>` --
and that file name is already sitting in the grid's own 'Uploaded File Name'
column. So the grid is read and the file fetched directly, no VIEWSTATE
round-trip. A file the portal does not hold answers 200 with the HTML shell
again, so downloads are judged on magic bytes.

'NA' IN THE FILE-NAME COLUMN IS A THIRD THING, AND IT IS A FINDING. Seven of
the 31 document rows on a real record read 'Promoter select NA for this
document' -- among them the CA, ARCHITECT and ENGINEERS certificates. That
is neither a document nor a failed download: it is the promoter declaring
the document not applicable. Counted as a document it inflates the library;
counted as a failure it invites a retry; dropped silently it hides that the
professional certificates were never filed. It is carried as
`status: not filed`.
"""

import json
import os
import re
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .base import (
    AcquisitionResult,
    StateFetchError,
    StateResolutionError,
    fetch_with_retry,
    safe_document_filename,
    storage_key,
)
from .uttarpradesh import BASE_URL, DOCUMENT_URL, MIRROR_URL, PROFILE, PROJECT_DETAIL

_TIMEOUT = 180
# ASP.NET WebForms behind what looks like a filtering front end: the
# research-tool UA this repo uses elsewhere gets served the same pages, but a
# browser-shaped one is what these endpoints were verified with.
_UA = "Mozilla/5.0 (compatible; RERA-Scrapper-DueDiligence/1.0; research tool, low-volume)"

# UPRERAPRJ14636 and UPRERAPRJ378870/03/2025. PRJ spelled out: promoters are
# UPRERAPRM and a looser pattern would resolve a promoter id as a project.
_REG_RE = re.compile(r"^UPRERAPRJ(\d{3,7})(?:/(\d{2})/(\d{4}))?$", re.I)
# Every field on the page hangs off this prefix.
_CONTROL_PREFIX = "ctl00_ContentPlaceHolder1_"
# 'Project Id: (UPRERAPRJ14636)'
_SERVED_RE = re.compile(r"(UPRERAPRJ[\d/]+)", re.I)
# What the promoter wrote where a file name belongs, when nothing was filed.
_NOT_FILED_RE = re.compile(r"promoter\s+select\s+na|^na$|^-$", re.I)

_AUTHORITY_NOTES = [
    "UP-RERA gates its project search behind a CAPTCHA and requires a district to be chosen "
    "before a promoter can be selected, so this pipeline does not search it: this project was "
    "opened directly by its registration number. No promoter portfolio was obtained from "
    "Uttar Pradesh, and none should be read as absent -- the search was not run, rather than "
    "run and returned empty.",
    "UP-RERA publishes no promoter-keyed order or complaint register. Its complaint-status "
    "page accepts a complaint number only, behind a CAPTCHA. Complaints and orders against "
    "this promoter are therefore UNKNOWN from this authority, which must not be read as a "
    "clean complaint record.",
    "UP-RERA publishes no PAN as readable text on its project record, so no PAN was obtained "
    "for this promoter from Uttar Pradesh.",
]


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _get(session, url, what="page", binary=False):
    def _fetch():
        response = session.get(url, timeout=_TIMEOUT, verify=False)
        response.raise_for_status()
        return response.content if binary else response.text

    return fetch_with_retry(_fetch, what=what)


# --- the registration number ----------------------------------------------


def parse_registration_number(reg_no):
    """{scheme, suffix, month, year, project_id} or None.

    `project_id` is the detail page's `?id=`, and is None for the post-2024
    scheme -- the arithmetic that makes a legacy number free to resolve does
    not hold there, and a guessed id resolves someone else's project.
    """
    text = " ".join(str(reg_no or "").split())
    match = _REG_RE.match(text)
    if not match:
        return None
    suffix, month, year = match.groups()
    dated = month is not None
    return {
        "scheme": "dated" if dated else "legacy",
        "suffix": suffix,
        "month": month,
        "year": year,
        "project_id": None if dated else suffix,
        "normalised": text.upper(),
    }


def detail_url(project_id, mirror=False):
    return PROJECT_DETAIL.format(base=MIRROR_URL if mirror else BASE_URL, id=project_id)


# --- the page -------------------------------------------------------------


def served_registration_number(html):
    """The registration number the page ACTUALLY served, or ''.

    The whole defence against the 200-OK shell. An id the portal has never
    issued returns a page with no project-id label at all, so an empty
    return here means 'no record was served', never 'this project has no
    number'.
    """
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(id=_CONTROL_PREFIX + "lblProjectNameWithID")
    if node is None:
        return ""
    match = _SERVED_RE.search(node.get_text(" ", strip=True))
    return match.group(1).upper() if match else ""


def control_values(html):
    """Every field on the record, keyed by its ASP.NET control id.

    UP-RERA renders the promoter's own registration form read-only, so the
    values sit in three different kinds of node -- `<input value=...>` for
    most of them, `<span>`/`<label>` text for the headings, and a `<select>`
    with one option marked selected for district and tehsil. Reading only one
    kind returns a record that is two-thirds empty and looks like a promoter
    who filed almost nothing.

    Keys have the `ctl00_ContentPlaceHolder1_` prefix stripped.
    """
    soup = BeautifulSoup(html, "html.parser")
    values = {}

    def put(control_id, value):
        if not control_id or not control_id.startswith(_CONTROL_PREFIX):
            return
        key = control_id[len(_CONTROL_PREFIX):]
        value = " ".join(str(value or "").split())
        if value and not values.get(key):
            values[key] = value

    for node in soup.find_all("input"):
        if (node.get("type") or "text").lower() in ("hidden", "submit", "button", "image"):
            continue
        put(node.get("id"), node.get("value"))
    for node in soup.find_all(["span", "label"]):
        if node.find(["span", "label", "div", "table"]):
            continue
        put(node.get("id"), node.get_text(" ", strip=True))
    for node in soup.find_all("select"):
        option = node.find("option", selected=True)
        if option is not None:
            put(node.get("id"), option.get_text(" ", strip=True))
    return values


def _after_colon(text):
    """'Project Name: BALAJI GREENS' -> 'BALAJI GREENS'."""
    return (str(text or "").split(":", 1)[-1]).strip().strip("()")


def _grid_rows(html, control_id):
    """One GridView's data rows as lists of cell text."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id=_CONTROL_PREFIX + control_id)
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if any(cells):
            rows.append(cells)
    return rows[1:] if rows else []


def document_entries(html):
    """The document library as [{label, filename, uploaded_on, era, status}].

    THREE STATES, NOT TWO. A row whose file-name cell reads 'Promoter select
    NA for this document' is the promoter declaring it not applicable -- it
    is `not filed`, which is neither a document to fetch nor a fetch that
    failed. Seven of a real record's 31 rows are in that state, including
    the CA, architect and engineer certificates, and flattening them into
    either of the other two states misreports the filing.
    """
    out = []
    for row in _grid_rows(html, "grvdocumentdetails"):
        if len(row) < 3:
            continue
        label, filename = row[1].strip(), row[2].strip()
        uploaded_on = row[3].strip() if len(row) > 3 else ""
        era = row[4].strip() if len(row) > 4 else ""
        if not label:
            continue
        if not filename or _NOT_FILED_RE.search(filename):
            out.append({"label": label, "filename": "", "uploaded_on": "", "era": era,
                        "status": "not filed"})
            continue
        out.append({
            "label": label,
            "filename": filename,
            "uploaded_on": uploaded_on,
            "era": era,
            "status": "listed",
            "url": DOCUMENT_URL.format(base=BASE_URL, name=quote(filename)),
        })
    return out


_KNOWN_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".doc", ".docx", ".jpg", ".jpeg", ".png")


def document_extension(filename):
    """The extension the portal's own file name declares, defaulting to
    .pdf. A file saved under an extension it is not breaks anything
    downstream that opens it by type -- and does so silently."""
    tail = str(filename or "").casefold()
    for extension in _KNOWN_EXTENSIONS:
        if tail.endswith(extension):
            return extension
    return ".pdf"


def looks_like_a_document(content):
    """Whether a 200 from ViewDocument actually carried a file.

    `ViewDocument?Param=NOSUCHFILE.pdf` answers 200 with the site's 48.7 KB
    page shell, so the status code proves nothing. Checked on the body, and
    not against `%PDF` specifically, so a filing served as something other
    than a PDF is not written off as missing.
    """
    content = content or b""
    if not content:
        return False
    return not content.lstrip()[:9].lower().startswith((b"<!doctype", b"<html"))


def land_parcels(html):
    """The KHASRA/PLOT grid -- the promoter's declared land, with areas."""
    out = []
    for row in _grid_rows(html, "grdKhasra"):
        if len(row) < 3 or not row[1].strip():
            continue
        out.append({"khasra_or_plot": row[1].strip(), "area": row[2].strip(),
                    "land_type": row[3].strip() if len(row) > 3 else ""})
    return out


def registry_documents(html):
    """The registry/agreement grid -- how the land was acquired."""
    out = []
    for row in _grid_rows(html, "grdRegistryAgreementDetails"):
        if len(row) < 3 or not row[1].strip():
            continue
        out.append({"registry_or_agreement_no": row[1].strip(), "date": row[2].strip(),
                    "land_type": row[3].strip() if len(row) > 3 else "",
                    "area": row[4].strip() if len(row) > 4 else ""})
    return out


def parse_project_detail(html):
    """The whole record, as the fields a Charter consumes."""
    values = control_values(html)
    documents = document_entries(html)
    return {
        "registration_number": served_registration_number(html),
        "project_name": _after_colon(values.get("lblProjectNameHeading", "")),
        "promoter_name": _after_colon(values.get("lblPromoterNameHeading", "")),
        "promoter_id": _after_colon(values.get("lblPromoterNameWithID", "")),
        "registration_date": _after_colon(values.get("lblregisdate", "")),
        "project_type": values.get("lblProjectType", ""),
        "project_category": values.get("lblProjectCategory", ""),
        "state": values.get("lblState", ""),
        "district": values.get("ddlDistrict", ""),
        "tehsil": values.get("ddlTehsil_old", "") or values.get("ddlTehsil", ""),
        "total_area": values.get("lblTotalArea", ""),
        "project_cost": values.get("lblProjectCost", ""),
        "project_duration_months": values.get("lblProjectDuration", ""),
        "start_date": values.get("lblStartDate", ""),
        "end_date": values.get("lblEndDate", ""),
        "permit_number": values.get("lblPermitNo", ""),
        "permit_date": values.get("lblPermitDate", ""),
        "registration_fee": values.get("lblRegistrationFee", ""),
        "contractor_name": values.get("lblContractorName", ""),
        "architect_name": values.get("lblArchName", ""),
        "architect_licence": values.get("lblArchLicNo", ""),
        "engineer_name": values.get("lblEnggName", ""),
        "bank_account_no": values.get("lblAccNo", ""),
        "bank_account_name": values.get("lblAccName", ""),
        "bank_name": values.get("lblBankName", ""),
        "bank_branch": values.get("lblBranchName", ""),
        "pending_details": values.get("messageTop", ""),
        "land_parcels": land_parcels(html),
        "registry_documents": registry_documents(html),
        "documents": documents,
        "controls": values,
    }


def project_notes(parsed):
    """The honest sentences this record earns, as opposed to its fields.

    The alert banner is the one worth surfacing: UP-RERA prints what a
    promoter has NOT filed at the top of its own page, and that is a
    diligence finding rather than page furniture.
    """
    notes = []
    pending = parsed.get("pending_details") or ""
    if pending and "pending" in pending.casefold():
        notes.append(
            f"UP-RERA's own record carries a pending-details alert for this project: "
            f"\"{pending.strip()}\" -- the authority stating that the promoter's filing is "
            f"incomplete."
        )
    not_filed = [d["label"] for d in parsed.get("documents", []) if d["status"] == "not filed"]
    if not_filed:
        notes.append(
            f"{len(not_filed)} document slot(s) on UP-RERA's record carry the promoter's own "
            f"declaration that the document is not applicable rather than a filed document: "
            f"{', '.join(not_filed[:6])}"
            f"{' and others' if len(not_filed) > 6 else ''}. These were not filed; they are "
            f"not documents this run failed to retrieve."
        )
    if not parsed.get("land_parcels"):
        notes.append(
            "UP-RERA's record for this project lists no khasra or plot details. Note that the "
            "khasra numbers on a RERA record are the promoter's declaration in any case -- "
            "Uttar Pradesh's own land records are not wired into this pipeline."
        )
    return notes


# --- the group-sweep seam -------------------------------------------------
#
# DELIBERATELY NO `search_promoter_projects`. group_sweep resolves a state's
# searcher by looking for that name on this module, and its absence is what
# puts Uttar Pradesh in the sweep's "not searchable" column WITH a stated
# reason instead of a zero. Adding one that posts the CAPTCHA-gated form
# would report the empty results panel as "no projects found" for every
# promoter ever swept. See group_sweep._CANNOT_SEARCH["UP"].


class _NullReporter:
    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def ok(self, *a, **k): pass
    def choose(self, *a, **k): return None


def fetch_project_summary(project_ref, reporter=None):
    """Open ONE Uttar Pradesh project by its registration number or id.

    Reachable even though the state is not searchable: a sweep hit carrying a
    UP-RERA number from some other source can still be opened and confirmed.
    """
    reporter = reporter or _NullReporter()
    parsed = parse_registration_number(project_ref)
    project_id = parsed["project_id"] if parsed else (
        str(project_ref).strip() if str(project_ref).strip().isdigit() else None
    )
    if project_id is None:
        if parsed and parsed["scheme"] == "dated":
            return {"opened": False,
                    "note": (f"'{project_ref}' is a post-2024 UP-RERA registration number, "
                             f"whose detail page cannot be reached from the number alone. It "
                             f"was NOT opened, and nothing was established about it.")}
        return {"opened": False,
                "note": f"'{project_ref}' is not a UP-RERA registration number."}

    try:
        html = _get(_session(), detail_url(project_id), what="UP-RERA project detail")
    except StateFetchError as e:
        return {"opened": False,
                "note": f"UP-RERA's project page could not be read ({type(e).__name__})."}

    served = served_registration_number(html)
    if not served:
        return {"opened": False,
                "note": (f"UP-RERA served a page for id {project_id} carrying no project "
                         f"record at all. The portal answers an unknown id with a normal-"
                         f"looking page rather than an error, so this is 'no such record', "
                         f"not a project with nothing filed.")}
    if parsed and served.upper() != parsed["normalised"]:
        return {"opened": False,
                "note": (f"UP-RERA served {served} for a request for "
                         f"{parsed['normalised']}. The record was discarded rather than "
                         f"attributed to the wrong project.")}

    detail = parse_project_detail(html)
    return {
        "opened": True,
        "promoter_name": detail["promoter_name"],
        "promoter_id": detail["promoter_id"],
        "project_name": detail["project_name"],
        "reg_no": served,
        "district": detail["district"],
        "project_cost": detail["project_cost"],
        "registration_date": detail["registration_date"],
        "notes": project_notes(detail),
    }


# --- the de-registered / defaulter register --------------------------------
#
# DELIBERATELY NOT WIRED INTO acquire(). Written and verified live on
# 2026-08-26 -- mirrors the westbengal.fetch_defaulters() precedent: a small,
# directly useful register that names the party outright, kept as a standalone
# module-level function until something actually calls it.
#
# NOT A FETCHABLE URL ON ITS OWN. The homepage's 'DE-REGISTERED PROJECTS' menu
# item is not a link -- it is `javascript:__doPostBack('...$lnkDefaulter','')`,
# an ASP.NET WebForms postback. A plain GET on the page it redirects to
# (`/DefaulterList`) serves the same 48.7 KB chrome-only shell as an unissued
# project id: the grid is populated server-side during the postback itself and
# is not there for a fresh request to read. So this fetches the homepage first
# for a live __VIEWSTATE/__EVENTVALIDATION pair, then POSTs the postback that
# names `lnkDefaulter`, and parses the grid out of THAT response.
#
# ONE REGISTER, TWO STATUSES. Confirmed live: 72 rows under
# `ctl00_ContentPlaceHolder1_grd_black`, each a project registration number,
# project name, district and promoter name -- with the project name itself
# carrying the suffix `(De-Registered Project)` or `(Defaulter Project)`.
# UP-RERA does not split these into two registers or a separate status
# column, so neither does this parser.


def fetch_defaulters(session=None):
    """UP-RERA's de-registered/defaulter project register, by promoter NAME.

    [{'sno', 'project_registration_no', 'project_name', 'project_district',
      'promoter_name', 'promoter_photo', 'rera_order', 'view_details'}, ...],
    keyed by the grid's own header row (lower-cased, spaces to underscores) so
    a header UP-RERA reorders or renames does not silently misalign the
    values -- the same defensive shape as westbengal.fetch_defaulters().
    """
    session = session or _session()
    home_url = BASE_URL + "/index.aspx"
    home_html = _get(session, home_url, what="UP-RERA homepage")
    soup = BeautifulSoup(home_html, "html.parser")

    def _field(name):
        node = soup.find(id=name) or soup.find(attrs={"name": name})
        return node.get("value", "") if node else ""

    payload = {
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$lnkDefaulter",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": _field("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _field("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _field("__EVENTVALIDATION"),
    }

    def _post():
        response = session.post(home_url, data=payload, timeout=_TIMEOUT, verify=False)
        response.raise_for_status()
        return response.text

    result_html = fetch_with_retry(_post, what="UP-RERA de-registered/defaulter list")
    table = BeautifulSoup(result_html, "html.parser").find(id=_CONTROL_PREFIX + "grd_black")
    if table is None:
        return []
    trs = table.find_all("tr")
    if not trs:
        return []
    headers = [" ".join(c.get_text(" ", strip=True).split()).lower().replace(" ", "_")
               for c in trs[0].find_all(["th", "td"])]
    out = []
    for tr in trs[1:]:
        cells = [" ".join(c.get_text(" ", strip=True).split()) for c in tr.find_all("td")]
        if not any(cells):
            continue
        out.append({headers[i]: cells[i] for i in range(min(len(headers), len(cells)))})
    return out


# --- the adapter ----------------------------------------------------------


class UttarPradeshAdapter:
    """StateAdapter for UP-RERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        session = _session()
        parsed = parse_registration_number(query)

        if parsed is None:
            raise StateResolutionError(
                f"'{query}' is not a UP-RERA registration number, and UP-RERA cannot be "
                f"searched by project name through this pipeline: its register gates the "
                f"search behind a CAPTCHA and requires a district before a promoter can be "
                f"chosen. Nothing was searched, so this says nothing about whether such a "
                f"project exists. Supply the UPRERAPRJ number instead."
            )
        if parsed["scheme"] == "dated":
            raise StateResolutionError(
                f"'{query}' is a valid UP-RERA registration number in the scheme used since "
                f"about 2024, and this pipeline cannot resolve those. Legacy numbers are "
                f"reachable because the detail page's id is the number's own numeric suffix; "
                f"that does not hold here (id {parsed['suffix']} serves no record), and the "
                f"key the current application uses is not published. The project was NOT "
                f"looked up -- this is a limit of this adapter, not a finding about the "
                f"project."
            )

        ctx.reporter.info(
            f"Opening UP-RERA project {parsed['normalised']} directly "
            f"(id {parsed['project_id']}; UP-RERA's search is CAPTCHA-gated and is not used)..."
        )
        html, source_url = self._fetch_detail(session, parsed, ctx)

        served = served_registration_number(html)
        if not served:
            raise StateResolutionError(
                f"UP-RERA served a page for '{query}' that carries no project record at all. "
                f"The portal answers an id it has never issued with a normal-looking 200 "
                f"page, so this is 'no such registration', NOT a project whose promoter filed "
                f"nothing. Verify the number: promoter ids look almost identical to project "
                f"ids (UPRERAPRM against UPRERAPRJ) and are not projects."
            )
        if served.upper() != parsed["normalised"]:
            raise StateResolutionError(
                f"UP-RERA served {served} in answer to a request for {parsed['normalised']}. "
                f"The record was discarded rather than attributed to the wrong project."
            )

        detail = parse_project_detail(html)
        registration_number = served
        reg_no = storage_key(registration_number)
        ctx.reporter.ok(f"Resolved: {registration_number} | {detail['project_name']}")

        project_out_dir = os.path.join(ctx.output_dir, reg_no)
        raw_dir = os.path.join(project_out_dir, "raw")
        documents_dir = os.path.join(project_out_dir, "documents")
        os.makedirs(raw_dir, exist_ok=True)
        if ctx.on_resolved is not None:
            ctx.on_resolved(reg_no)
            os.makedirs(raw_dir, exist_ok=True)

        category_data = {
            "projects": {
                "projectName": detail["project_name"],
                "projectRegistartionNo": registration_number,
                "registrationDate": detail["registration_date"],
                "projectTypeName": detail["project_type"],
                "projectCategory": detail["project_category"],
                "district": detail["district"],
                "tehsil": detail["tehsil"],
                "totalArea": detail["total_area"],
                "projectCost": detail["project_cost"],
                "projectDurationMonths": detail["project_duration_months"],
                "startDate": detail["start_date"],
                "endDate": detail["end_date"],
                "permitNumber": detail["permit_number"],
                "permitDate": detail["permit_date"],
                "landParcels": detail["land_parcels"],
                "registryDocuments": detail["registry_documents"],
                "pendingDetailsAlert": detail["pending_details"],
            },
            "partners": {"promoterDetails": {
                "promoterName": detail["promoter_name"],
                # UP-RERA's own promoter key. Not a CIN and not a PAN, but it
                # is stable across a promoter's projects on this authority.
                "promoterId": detail["promoter_id"],
                "bankAccountNo": detail["bank_account_no"],
                "bankAccountName": detail["bank_account_name"],
                "bankName": detail["bank_name"],
                "bankBranch": detail["bank_branch"],
            }},
            "professionals": self._professionals(detail),
            "spocs": None,
            "sro_details": None,
            "past_experiences": None,
            "documents": {"entries": detail["documents"]},
            # No promoter-keyed complaint register exists on this authority --
            # None, never 0, so nothing downstream reads it as a clean record.
            "complaints": None,
            "appeals": None,
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        documents_manifest = self._download_documents(
            session, detail["documents"], documents_dir, ctx
        )

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=registration_number,
            project_id=str(parsed["project_id"]),
            detail_url=source_url,
            category_data=category_data,
            documents_manifest=documents_manifest,
            documents_dir=documents_dir,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=detail["promoter_name"],
            # Declared absent rather than empty: UP-RERA's promoter dropdown
            # is real, but reaching it means solving a CAPTCHA and then
            # asking once per district. See _AUTHORITY_NOTES.
            promoter_portfolio=None,
            raw_record=detail,
            auth_source="none",
            categories_not_published={
                "spocs", "sro_details", "past_experiences", "complaints", "appeals",
            },
            notes=list(_AUTHORITY_NOTES) + project_notes(detail),
        )

    # -- fetching ----------------------------------------------------------
    def _fetch_detail(self, session, parsed, ctx):
        """The primary host, then the mirror.

        Both run the same application over the same data; the mirror exists
        because the primary throws an intermittent 500. Falling back to it is
        not reading a second source, and a record only ever comes from one of
        them.
        """
        url = detail_url(parsed["project_id"])
        try:
            return _get(session, url, what="UP-RERA project detail"), url
        except StateFetchError as e:
            mirror = detail_url(parsed["project_id"], mirror=True)
            ctx.reporter.warn(
                f"UP-RERA's primary host did not answer ({e}); trying the authority's "
                f"mirror at {MIRROR_URL}."
            )
            return _get(session, mirror, what="UP-RERA project detail (mirror)"), mirror

    # -- professionals -----------------------------------------------------
    def _professionals(self, detail):
        """Architect, engineer and contractor as filed.

        UP-RERA lets a promoter enter the literal string 'NA' in these
        fields, and a great many do. 'NA' is kept verbatim rather than
        blanked: an empty field reads as 'not published by the authority',
        while NA is the promoter answering the question.
        """
        blocks = {
            "architect": {"name": detail["architect_name"],
                          "licence": detail["architect_licence"]},
            "structural_engineer": {"name": detail["engineer_name"]},
            "contractor": {"name": detail["contractor_name"]},
        }
        blocks = {k: v for k, v in blocks.items() if any(v.values())}
        return blocks or None

    # -- documents ---------------------------------------------------------
    def _download_documents(self, session, entries, documents_dir, ctx):
        """Fetch each listed document straight from ViewDocument.

        A FILE THE PORTAL DOES NOT HOLD ANSWERS 200 WITH THE PAGE SHELL --
        48.7 KB of HTML where a PDF was asked for. Checked on the body,
        because the status code says nothing here.

        Promoters file different slots under one file name (three rows of a
        real record share PRJ2499SERVICE-PLAN.pdf), so names are
        de-duplicated or the library silently collapses.
        """
        listed = [e for e in entries if e.get("status") == "listed"]
        manifest = [{"label": e["label"], "status": "not filed by the promoter"}
                    for e in entries if e.get("status") == "not filed"]
        if not listed:
            ctx.reporter.info("UP-RERA lists no filed documents for this project.")
            return manifest

        os.makedirs(documents_dir, exist_ok=True)
        used = set()
        for entry in listed:
            name = safe_document_filename(documents_dir, entry["label"], used,
                                          extension=document_extension(entry["filename"]))
            path = os.path.join(documents_dir, name)
            try:
                content = _get(session, entry["url"], what="UP-RERA document", binary=True)
                if not looks_like_a_document(content):
                    manifest.append({"label": entry["label"], "url": entry["url"],
                                     "filename": entry["filename"],
                                     "status": "not held by the portal"})
                    continue
                with open(path, "wb") as f:
                    f.write(content)
                manifest.append({"label": entry["label"], "url": entry["url"],
                                 "filename": entry["filename"], "path": path,
                                 "uploaded_on": entry.get("uploaded_on", ""),
                                 "status": "downloaded"})
            except Exception as e:  # noqa: BLE001 -- recorded per document
                manifest.append({"label": entry["label"], "url": entry["url"],
                                 "status": f"failed: {type(e).__name__}"})
        downloaded = sum(1 for d in manifest if d.get("status") == "downloaded")
        ctx.reporter.ok(
            f"{downloaded}/{len(listed)} UP-RERA document(s) retrieved"
            + (f"; {len(manifest) - len(listed)} slot(s) were never filed."
               if len(manifest) > len(listed) else ".")
        )
        return manifest


ADAPTER = UttarPradeshAdapter()

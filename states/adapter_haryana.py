"""
StateAdapter for Haryana / HARERA.

See states/haryana.py for why this state was refused once, why that refusal
was wrong, and which of its three registration-number formats is the one to
resolve on.

TWO BENCHES, ONE REGISTER, ONE GET EACH. `/admincontrol/registered_projects/1`
is Panchkula (1,087 rows) and `/2` is Gurugram (1,074 rows), verified live
2026-08-24. Each row carries the Project ID, the certificate number, project
name, BUILDER, location, district and -- the thing that makes this adapter
cheap -- a link to the detail view whose id is a plain integer. So opening a
project needs no search step, and neither does a promoter portfolio.

THE TABLE IS FOUND BY HEADER, AND ONE NEEDLE IS NOT ENOUGH. The page serves
five tables, and one of them is a SEARCH FORM whose only header cell reads
"Registration Certificate Number" -- matching on that phrase alone selects
the form and finds no projects. Three needles together (certificate number,
project id, builder) select the register unambiguously. This is the same
match-by-content rule the K-RERA and JHARERA adapters follow, with the extra
wrinkle that a decoy header exists.

WHAT THIS AUTHORITY GIVES THAT NO OTHER ONE HERE DOES: THE CIN. Confirmed
live on RERA-GRG-741-2020 -- the record states
`CIN No. (Annex a copy in Folder A) U70101DL1996PTC075865`. Every other
authority in this pipeline publishes neither CIN nor DIN, which forces the
RERA-to-MCA join to run on names and carry that false-positive risk. Here
the join key is stated outright, so `corporate_identity_key` is returned for
callers that can use it, validated against the CIN format rather than
accepted as any string sitting after the label.

THE PAN IS MASKED AND IS NOT COLLECTED. It renders as `XXXX280H` -- last
four characters only, for the company and every director. A masked tail is
not a PAN, gst_group's harvest must never see it, and this adapter therefore
does not put it anywhere a PAN is expected. It is recorded as
`pan_masked: True` so a reader knows one was filed rather than absent.
"""

import json
import os
import re

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
from .haryana import (
    BENCHES,
    CANCELLED_PROJECTS,
    LAPSED_PROJECTS,
    PROFILE,
    PROJECT_DETAIL,
    REGISTERED_PROJECTS,
)

_TIMEOUT = 120
_UA = "RERA-Scrapper-DueDiligence/1.0 (research tool, low-volume)"
_DETAIL_ID_RE = re.compile(r"/project_preview_open/(\d+)", re.I)
# The real CIN/LLPIN shapes, so a value sitting after the label is only
# accepted if it actually IS one. A malformed CIN would be joined against
# MCA records and pull a different company's filings.
_CIN_RE = re.compile(r"\b([LUu]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b")
_LLPIN_RE = re.compile(r"\b([A-Z]{3}-\d{4})\b")

_AUTHORITY_NOTES = [
    "HARERA runs two benches -- Panchkula and Gurugram -- off one register, and they number "
    "their registration certificates differently. This project is keyed on its Project ID "
    "(the RERA-PKL-/RERA-GRG- form), which is uniform across both benches and every era; the "
    "certificate number is recorded as filed and is not used as an identifier, because "
    "pre-2018 registrations carry forms as loose as '211 OF 2017 DATED 18.09.2017'.",
    "HARERA masks the PAN on its public record, printing only the last four characters (for "
    "example XXXX280H) for the company and for each director. So no PAN was obtained for this "
    "promoter from Haryana, and none should be inferred from the visible tail.",
    "HARERA publishes no browsable register that names the parties to its complaints: the "
    "case search accepts a case number and year behind a CAPTCHA. Complaints and orders "
    "against this promoter are therefore UNKNOWN from this authority, which must not be read "
    "as a clean complaint record.",
]


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _get(session, url, what="page"):
    def _fetch():
        response = session.get(url, timeout=_TIMEOUT, verify=False)
        response.raise_for_status()
        return response.text

    return fetch_with_retry(_fetch, what=what)


_CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def looks_like_a_document(response):
    """Whether a 200 actually carried a document.

    A DOCUMENT ID HARERA DOES NOT HOLD ANSWERS 200 WITH 25 KB OF HTML --
    verified against a fabricated id on the real portal. A downloader
    trusting the status code writes that web page to disk under the
    document's name and reports it retrieved, so a Charter's document
    library lists a licence, a jamabandi and a demarcation plan that are all
    the same error page. The body is what decides.

    Not tested against `%PDF` specifically: this authority's records link
    drawings and spreadsheets as well, and a PDF-only test would file a real
    filing as missing.
    """
    content = response.content or b""
    if not content:
        return False
    if "text/html" in (response.headers.get("Content-Type") or "").lower():
        return False
    return not content.lstrip()[:9].lower().startswith((b"<!doctype", b"<html"))


def document_extension(response):
    """The extension the response's own Content-Type declares.

    HARERA's document URLs are opaque -- `view_uploaded_Document_open/` plus
    a hash -- so there is no file name to read an extension off, and the
    served type is the only evidence of what the bytes are. Defaults to
    .pdf, which is what this portal serves for everything checked.
    """
    ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXTENSIONS.get(ctype, ".pdf")


def _rows_of(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        if any(texts):
            rows.append((texts, cells))
    return rows


def parse_register(html):
    """One bench's register as [{project_id, certificate_no, project_name,
    builder, location, district, registered_with, detail_id, lapsed}].

    `detail_id` is the integer the detail view takes, lifted from the row's
    own link -- so nothing has to be searched or guessed to open a project.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if len(rows) < 2:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        # THREE needles. "registration certificate number" alone also
        # matches a search form on this same page.
        if not ("registration certificate number" in joined
                and "project id" in joined and "builder" in joined):
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        idx = {
            "certificate_no": _column("registration certificate number"),
            "project_id": _column("project id"),
            "project_name": _column("project name"),
            "builder": _column("builder"),
            "location": _column("project location"),
            "district": _column("project district"),
            "registered_with": _column("registered with"),
        }

        out = []
        for texts, cells in rows[1:]:
            def _cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            project_id = _cell("project_id").strip()
            if not project_id:
                continue
            # Scoped to THIS row. A table-wide anchor scan would attach one
            # row's detail id to every other row -- and on a 1,000-row
            # register it is also quadratic.
            detail_id = None
            row_element = cells[0].find_parent("tr")
            for anchor in (row_element.find_all("a", href=True) if row_element else []):
                match = _DETAIL_ID_RE.search(anchor["href"])
                if match:
                    detail_id = match.group(1)
                    break
            row_text = " ".join(texts).casefold()
            # The lapsed-project FLAG is rendered as an anchor inside the
            # certificate cell, so its text concatenates onto the number --
            # "GGM/486/218/2021/54 DATED 21.09.2021 Lapsed Project",
            # confirmed live. Strip it: the flag is captured separately
            # below, and a certificate number carrying it would never match
            # a reader's paste.
            certificate_no = re.sub(
                r"\s*lapsed\s+project\s*$", "", _cell("certificate_no"), flags=re.I
            ).strip()
            out.append({
                "project_id": project_id,
                "certificate_no": certificate_no,
                "project_name": _cell("project_name"),
                "builder": _cell("builder"),
                "location": _cell("location"),
                "district": _cell("district"),
                "registered_with": _cell("registered_with"),
                "detail_id": detail_id,
                # The register flags these in the row itself, and a lapsed
                # registration is diligence material rather than a detail.
                "lapsed": "lapsed" in row_text,
            })
        if out:
            return out
    return []


def project_key(row):
    """The identifier to file a HARERA project under.

    NORMALLY THE PROJECT ID, BUT FIFTY-TWO ROWS DO NOT HAVE ONE. Counted
    live across both benches: 52 of 2,161 rows print the literal string
    "NA" in the Project ID column -- mostly older, mostly lapsed
    registrations (BPTP NEST 83-A/B/C, NINEX RESIDENCY, several 2017
    affordable-housing schemes), and none of them carries a detail-view
    link either.

    Used unchanged, "NA" becomes the primary key of `output/<reg_no>/` for
    all fifty-two, so they overwrite each other in one directory -- the
    Gujarat nested-path bug the other way round. Every one of the 52 does
    carry a certificate number, and all 52 of those are distinct, so the
    certificate is the fallback key. It is a poor identifier to resolve ON
    (see states/haryana.py on the pre-2018 "211 OF 2017 DATED 18.09.2017"
    form), but it is a perfectly good one to file under, and
    `storage_key()` flattens the slashes in the Gurugram form.
    """
    project_id = " ".join((row.get("project_id") or "").split())
    if project_id and project_id.upper() != "NA":
        return project_id
    certificate = " ".join((row.get("certificate_no") or "").split())
    if certificate and certificate.upper() != "NA":
        return certificate
    return " ".join((row.get("project_name") or "").split()) or "UNKNOWN"


def _labelled(rows, *needles):
    """First row text beginning with a label, minus the label.

    HARERA's promoter block is a one-column form rendering -- "CIN No.
    (Annex a copy in Folder A) U70101DL1996PTC075865" is one cell -- so the
    value is what follows the label, not a neighbouring column.
    """
    for text in rows:
        low = text.casefold()
        for needle in needles:
            if low.startswith(needle.casefold()):
                return text[len(needle):].strip(" : ")
    return ""


def parse_project_detail(html):
    """The promoter and project facts off a HARERA detail page.

    Pure: HTML in, dicts out, no network, so it is testable against a saved
    capture rather than only against a live portal.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    page_text = soup.get_text(" ", strip=True)

    flat = []
    for table in tables:
        for tr in table.find_all("tr"):
            text = tr.get_text(" ", strip=True)
            if text:
                flat.append(" ".join(text.split()))

    company = _labelled(flat, "1. Name and registered address of the company")
    cin_match = _CIN_RE.search(page_text) or _LLPIN_RE.search(page_text)

    directors = []
    for index, text in enumerate(flat):
        low = text.casefold()
        if not (low.startswith("2. managing director") or re.match(r"^\d+\.\s*director\s*\d*\s*:", low)):
            continue
        role = "Managing Director/HOD/CEO" if "managing director" in low else "Director"
        name = ""
        for follow in flat[index + 1:index + 4]:
            if follow.casefold().startswith("name"):
                name = follow[4:].strip(" : ")
                break
        if name:
            directors.append({"role": role, "name": name})

    documents = []
    for table in tables:
        rows = _rows_of(table)
        if len(rows) < 2:
            continue
        header = " | ".join(h.casefold() for h in rows[0][0])
        if "document description" not in header:
            continue
        for texts, cells in rows[1:]:
            label = next((t for t in texts if t and not t.isdigit()), "")
            href = ""
            row_element = cells[0].find_parent("tr")
            for anchor in (row_element.find_all("a", href=True) if row_element else []):
                href = anchor["href"]
                break
            if label:
                documents.append({"label": label, "url": href})

    # HARERA renders its own question text INTO the answer cell -- the raw
    # value reads "Yes/No (If yes-give Annex details in folder G) No",
    # confirmed live. The promoter's actual answer is the trailing token, so
    # the boilerplate is stripped and only a real Yes or No is kept. Storing
    # the raw string would put the words "Yes/No" into a Charter as though
    # the promoter had declared both.
    litigation = ""
    match = re.search(
        r"litigation is pending against the Project.{0,160}?(?:folder\s+G\s*\))?\s*\b(Yes|No)\b\s*$",
        page_text, re.I | re.S,
    )
    raw = _labelled(flat, "9. Whether any litigation is pending against the Project:")
    if raw:
        tail = re.sub(r"^.*\)", "", raw).strip() or raw
        answer = re.findall(r"\b(Yes|No)\b", tail, re.I)
        if answer:
            litigation = answer[-1].capitalize()
    if not litigation:
        match = re.search(
            r"litigation is pending against the Project[^.]{0,160}?\)\s*(Yes|No)\b",
            page_text, re.I | re.S,
        )
        litigation = match.group(1).capitalize() if match else ""

    return {
        "promoter_name": company.split("(Annex")[0].strip() if company else "",
        "registered_address": _labelled(flat, "(Annex a copy in Folder A)"),
        "corporate_identity_key": cin_match.group(1) if cin_match else "",
        "email": _labelled(flat, "Email ID"),
        "website": _labelled(flat, "Website"),
        "phone": _labelled(flat, "Phone(Landline)", "Phone (landline)"),
        "directors": directors,
        # Recorded as masked rather than dropped: a reader should know a PAN
        # was filed, and nothing should mistake the visible tail for one.
        "pan_masked": bool(re.search(r"\bX{3,}\w{3,4}\b", page_text)),
        "litigation_declared": litigation,
        "documents": documents,
    }


def parse_lapsed_or_defaulter_register(html):
    """Rows off EITHER `/admincontrol/lapsed_projects/{bench}` or
    `/admincontrol/cancelled_projects/{bench}` -- same shape, same three
    header needles as `parse_register`, verified live 2026-08-26.

    THESE ARE TWO DIFFERENT FINDINGS, NOT ONE. Lapsed = the registration
    certificate's own validity window (Approval From/To) has ended -- 320
    Panchkula + 235 Gurugram rows. Cancelled = the menu itself labels this
    "Defaulter/ Cancelled/ Suspended/ Abeyance Projects", HARERA's own
    action against the promoter -- 23 Panchkula + 5 Gurugram rows, far
    fewer, and the two lists barely overlap. A caller must not read a
    lapsed listing as a defaulter finding or vice versa.

    Pure: HTML in, dicts out, no network -- like `parse_register`. NOT
    wired into `acquire()`; nothing here calls `fetch_lapsed_projects` or
    `fetch_defaulter_projects` below.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if len(rows) < 2:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if not ("registration certificate number" in joined
                and "project id" in joined and "builder" in joined):
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        idx = {
            "certificate_no": _column("registration certificate number"),
            "project_id": _column("project id"),
            "project_name": _column("project name"),
            "builder": _column("builder"),
            "location": _column("project location"),
            "district": _column("project district"),
            # Present on the lapsed register only; absent (None) on the
            # cancelled/defaulter one, where _cell() below just yields "".
            "approval_from": _column("approval from"),
            "approval_to": _column("approval to"),
        }

        out = []
        for texts, cells in rows[1:]:
            def _cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            builder = _cell("builder").strip()
            project_name = _cell("project_name").strip()
            if not builder and not project_name:
                continue
            out.append({
                "certificate_no": _cell("certificate_no"),
                "project_id": _cell("project_id"),
                "project_name": project_name,
                "builder": builder,
                "location": _cell("location"),
                "district": _cell("district"),
                "approval_from": _cell("approval_from"),
                "approval_to": _cell("approval_to"),
            })
        if out:
            return out
    return []


def fetch_lapsed_projects(bench, session=None):
    """One bench's list of registrations whose validity window has ended.

    NOT a defaulter list -- see `parse_lapsed_or_defaulter_register`.
    Unwired: `acquire()` never calls this.
    """
    html = _get(session or _session(), LAPSED_PROJECTS.format(bench),
                what=f"HARERA {BENCHES.get(bench, bench)} lapsed-projects register")
    return parse_lapsed_or_defaulter_register(html)


def fetch_defaulter_projects(bench, session=None):
    """One bench's "Defaulter/ Cancelled/ Suspended/ Abeyance Projects"
    list -- HARERA's own action against the promoter, and a much smaller,
    different set of rows than `fetch_lapsed_projects`.

    Unwired: `acquire()` never calls this.
    """
    html = _get(session or _session(), CANCELLED_PROJECTS.format(bench),
                what=f"HARERA {BENCHES.get(bench, bench)} defaulter/cancelled-projects register")
    return parse_lapsed_or_defaulter_register(html)


_REGISTER_CACHE = {}


def _register(bench, session=None):
    if bench not in _REGISTER_CACHE:
        _REGISTER_CACHE[bench] = parse_register(_get(
            session or _session(), REGISTERED_PROJECTS.format(bench),
            what=f"HARERA {BENCHES.get(bench, bench)} register",
        ))
    return _REGISTER_CACHE[bench]


def _all_projects(session=None):
    """Both benches, in one list, each row tagged with its bench."""
    combined = []
    for bench in sorted(BENCHES):
        try:
            for row in _register(bench, session):
                combined.append({**row, "bench": BENCHES[bench], "bench_code": bench})
        except StateFetchError:
            continue
    return combined


class _NullReporter:
    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def ok(self, *a, **k): pass
    def choose(self, *a, **k): return None


def search_promoter_projects(name, reporter=None):
    """Projects on either HARERA bench whose Builder matches `name`.

    Two GETs for the whole state, then a local substring match -- the same
    shape K-RERA's sweep search has. Every hit is a CANDIDATE: the match is
    on the builder's printed name, not an identifier.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return []
    return [
        {"reg_no": project_key(row), "project_name": row["project_name"],
         "promoter_name": row["builder"], "project_id": row["detail_id"] or project_key(row),
         "bench": row["bench"]}
        for row in _all_projects()
        if needle in " ".join((row["builder"] or "").split()).casefold()
    ]


def fetch_project_summary(project_ref, reporter=None):
    """The diligence-relevant fields of ONE HARERA project.

    Takes the detail view's integer id, which the register row carries
    directly -- so no search happens here. Never raises: one unreachable
    project must not sink a sweep.
    """
    reporter = reporter or _NullReporter()
    if not project_ref:
        return {"opened": False, "note": "No HARERA project id was carried on this row."}
    try:
        html = _get(_session(), PROJECT_DETAIL.format(project_ref), what="HARERA project detail")
    except StateFetchError as e:
        return {"opened": False,
                "note": f"This project's HARERA record could not be opened ({type(e).__name__})."}

    parsed = parse_project_detail(html)
    notes = []
    if parsed.get("corporate_identity_key"):
        notes.append(
            f"HARERA states this promoter's corporate identity number "
            f"({parsed['corporate_identity_key']}) on the project record. No other authority "
            f"in this pipeline publishes one, so this is a hard link to the MCA record rather "
            f"than a name match."
        )
    if parsed.get("pan_masked"):
        notes.append(
            "The PAN on this record is masked to its last four characters, so no PAN was "
            "obtained from Haryana for this promoter."
        )
    if not parsed.get("litigation_declared"):
        notes.append(
            "This record carried no answer to HARERA's own 'whether any litigation is pending "
            "against the Project' question, so litigation disclosed here is UNKNOWN for it. "
            "That must not be read as a clean litigation record."
        )
    return {
        "opened": True,
        "promoter_name": parsed.get("promoter_name") or "",
        "corporate_identity_key": parsed.get("corporate_identity_key") or "",
        "registered_address": parsed.get("registered_address") or "",
        "directors": parsed.get("directors") or [],
        "email": parsed.get("email") or "",
        "website": parsed.get("website") or "",
        "litigation_declared": parsed.get("litigation_declared") or "",
        "pan_masked": bool(parsed.get("pan_masked")),
        "documents_on_page": len(parsed.get("documents") or []),
        "notes": notes,
    }


class HaryanaAdapter:
    """StateAdapter for HARERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        session = _session()
        ctx.reporter.info("Fetching both HARERA bench registers (Panchkula and Gurugram)...")
        index = _all_projects(session)
        if not index:
            raise StateFetchError(
                "HARERA's registers returned no projects at all this run, which means they "
                "could not be read rather than that Haryana has no registered projects."
            )
        ctx.reporter.ok(f"{len(index)} HARERA projects indexed across both benches.")

        needle = query.strip().casefold()
        exact = [r for r in index
                 if needle in (r["project_id"].casefold(),
                               r["certificate_no"].casefold())]
        if not exact:
            # A certificate number is printed with a trailing " DATED
            # dd.mm.yyyy" (and occasionally a stray character after that),
            # so a reader pasting just the number must still resolve.
            exact = [r for r in index if r["certificate_no"]
                     and r["certificate_no"].casefold().startswith(needle)]
        matches = exact or [r for r in index if needle in r["project_name"].casefold()]
        if not matches:
            raise StateResolutionError(
                f"No HARERA project found matching '{query}' across {len(index)} projects on "
                f"both benches. HARERA is keyed on the Project ID (RERA-PKL-.../RERA-GRG-...); "
                f"a pre-2018 certificate number such as '211 OF 2017' cannot be resolved on its "
                f"own and needs the Project ID instead."
            )
        chosen = matches[0]
        if len(matches) > 1:
            ctx.reporter.warn(
                f"{len(matches)} HARERA projects matched {query!r}; using "
                f"{chosen['project_id']} ({chosen['project_name']})."
            )

        # project_key, not project_id: 52 rows print "NA" there and would
        # otherwise all be filed under output/NA/.
        registration_number = project_key(chosen)
        reg_no = storage_key(registration_number)
        ctx.reporter.ok(
            f"Resolved: {registration_number} | {chosen['project_name']} "
            f"({chosen['bench']} bench)"
        )

        project_out_dir = os.path.join(ctx.output_dir, reg_no)
        raw_dir = os.path.join(project_out_dir, "raw")
        documents_dir = os.path.join(project_out_dir, "documents")
        os.makedirs(raw_dir, exist_ok=True)
        if ctx.on_resolved is not None:
            ctx.on_resolved(reg_no)
            os.makedirs(raw_dir, exist_ok=True)

        parsed, detail_html = {}, ""
        if chosen.get("detail_id"):
            ctx.reporter.info("Fetching HARERA project record...")
            try:
                detail_html = _get(session, PROJECT_DETAIL.format(chosen["detail_id"]),
                                   what="HARERA project detail")
                parsed = parse_project_detail(detail_html)
            except StateFetchError as e:
                ctx.reporter.warn(f"HARERA detail page unreadable ({e}) -- register row only.")

        promoter_name = parsed.get("promoter_name") or chosen["builder"]
        notes = list(_AUTHORITY_NOTES)
        if (chosen.get("project_id") or "").strip().upper() == "NA":
            notes.append(
                f"HARERA's register prints no Project ID for this registration -- the column "
                f"reads 'NA', as it does for 52 of the 2,161 projects on the two benches, "
                f"almost all of them older or lapsed. It is filed here under its registration "
                f"certificate number ({registration_number}) instead. The missing Project ID "
                f"is a gap in the authority's own register, not in this project's filing."
            )
        if not chosen.get("detail_id"):
            notes.append(
                "This project's row carried no link to a detail record, so only what the "
                "register itself states was read for it."
            )
        if chosen.get("lapsed"):
            notes.append("HARERA's register flags this project's registration as LAPSED.")
        if parsed.get("corporate_identity_key"):
            notes.append(
                f"HARERA states this promoter's corporate identity number "
                f"({parsed['corporate_identity_key']}) directly on the project record -- the "
                f"only authority in this pipeline that publishes one, making the link to the "
                f"MCA record a hard identifier rather than a name match."
            )
        if parsed.get("litigation_declared"):
            notes.append(
                f"HARERA asks the promoter whether litigation is pending against the project; "
                f"the filed answer on this record is '{parsed['litigation_declared']}'."
            )

        category_data = {
            "projects": {
                "projectName": chosen["project_name"],
                "projectRegistartionNo": registration_number,
                "registrationCertificateNumber": chosen["certificate_no"],
                "bench": chosen["bench"],
                "district": chosen["district"],
                "address": chosen["location"],
                "registeredWith": chosen["registered_with"],
                "registrationLapsed": chosen["lapsed"],
                "litigationDeclared": parsed.get("litigation_declared") or "",
            },
            "partners": {"promoterDetails": {
                "promoterName": promoter_name,
                "address": parsed.get("registered_address") or "",
                "emailId": parsed.get("email") or "",
                "website": parsed.get("website") or "",
                # The join key nothing else here publishes.
                "corporateIdentityKey": parsed.get("corporate_identity_key") or "",
                "directors": parsed.get("directors") or [],
                "panMasked": bool(parsed.get("pan_masked")),
            }},
            "professionals": None,
            "spocs": None,
            "sro_details": None,
            "past_experiences": None,
            "documents": {"entries": parsed.get("documents") or []},
            # No browsable name-keyed complaint register exists -- None,
            # never 0, so nothing downstream reads an absence as clean.
            "complaints": None,
            "appeals": None,
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        documents_manifest = self._download_documents(
            session, parsed.get("documents") or [], documents_dir, ctx
        )

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=registration_number,
            project_id=str(chosen.get("detail_id") or registration_number),
            detail_url=PROJECT_DETAIL.format(chosen["detail_id"]) if chosen.get("detail_id") else None,
            category_data=category_data,
            documents_manifest=documents_manifest,
            documents_dir=documents_dir,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=promoter_name,
            promoter_portfolio=self._promoter_portfolio(index, chosen, ctx),
            raw_record=chosen,
            auth_source="none",
            categories_not_published={
                "professionals", "spocs", "sro_details", "past_experiences",
                "complaints", "appeals",
            },
            notes=notes,
        )

    # -- documents ---------------------------------------------------------
    def _download_documents(self, session, entries, documents_dir, ctx):
        """Fetch every document the project record links.

        THIS USED TO CRASH THE WHOLE RUN ON THE FIRST DOCUMENT.
        `safe_document_filename` takes (documents_dir, label, used_names) --
        the directory first, because the length budget it applies depends on
        how deep the caller's output directory is. It was being called as
        (label, used), which raises TypeError, and the call sat OUTSIDE the
        try below, so the error escaped `acquire()` as a bare traceback past
        main.py rather than as a StateAcquisitionError. A real Gurugram
        record links 60 documents, so every Haryana run died.

        Passing the directory is not a formality: HARERA's labels are long
        ("AMSTORIA 26-2-2015.DWG STREET LIGHTING-MODEL", "COPY OF LICENSE
        ALONG WITH SCHEDULE OF LAND"), and an over-long path is the failure
        Windows reports as FileNotFoundError -- which reads as a missing
        directory, mid-download, after files have already been written.
        """
        if not entries:
            return []
        os.makedirs(documents_dir, exist_ok=True)
        manifest, used = [], set()
        for entry in entries:
            label = entry.get("label") or "document"
            url = entry.get("url") or ""
            if not url:
                manifest.append({"label": entry.get("label", ""), "status": "no link published"})
                continue
            if not url.startswith("http"):
                url = "https://haryanarera.gov.in/" + url.lstrip("/")
            try:
                response = session.get(url, timeout=_TIMEOUT, verify=False)
                response.raise_for_status()
                if not looks_like_a_document(response):
                    manifest.append({"label": entry.get("label", ""), "url": url,
                                     "status": "not held by the portal"})
                    continue
                # Promoters file different slots under one filename;
                # de-duplicate or they overwrite each other, the bug both
                # new-state adapters hit at 15 of 42 documents. The set is
                # passed in and updated by the helper itself.
                name = safe_document_filename(documents_dir, label, used,
                                              extension=document_extension(response))
                path = os.path.join(documents_dir, name)
                with open(path, "wb") as f:
                    f.write(response.content)
                manifest.append({"label": entry.get("label", ""), "url": url,
                                 "path": path, "status": "downloaded"})
            except Exception as e:  # noqa: BLE001 -- recorded per document
                manifest.append({"label": entry.get("label", ""), "url": url,
                                 "status": f"failed: {type(e).__name__}"})
        downloaded = sum(1 for d in manifest if d.get("status") == "downloaded")
        ctx.reporter.ok(f"{downloaded}/{len(manifest)} HARERA document(s) retrieved.")
        return manifest

    # -- portfolio ---------------------------------------------------------
    def _promoter_portfolio(self, index, chosen, ctx):
        target = " ".join((chosen["builder"] or "").split()).casefold()
        if not target:
            return None
        others = [
            {"reg_no": project_key(r), "project_name": r["project_name"],
             "district": r["district"], "bench": r["bench"], "lapsed": r["lapsed"]}
            for r in index
            if " ".join((r["builder"] or "").split()).casefold() == target
        ]
        lapsed = sum(1 for r in others if r["lapsed"])
        ctx.reporter.info(
            f"HARERA portfolio: {len(others)} project(s) under this builder "
            f"({lapsed} lapsed)."
        )
        return {
            "promoter_name": chosen["builder"],
            "projects": others,
            "totals": {"total_projects": len(others), "lapsed_registrations": lapsed},
            "source": "HARERA registered-projects registers, both benches",
            "notes": [
                "Matched on the builder's name exactly as HARERA's register prints it. The "
                "register publishes no builder identifier, so a promoter who filed under a "
                "differently spelled name would not be joined to these."
            ],
        }


ADAPTER = HaryanaAdapter()

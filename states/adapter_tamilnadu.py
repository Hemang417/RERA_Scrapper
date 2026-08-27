"""
StateAdapter for Tamil Nadu / TNRERA.

See states/tamilnadu.py for the four ways this repo's old TN pattern was
wrong. Everything below was established live on 24 August 2026, against the
authority's own pages.

THE REGISTER IS TWO APPLICATIONS AND THEIR VOLUMES DO NOT MEET. That is the
single most important fact about this state, and it is the reason a lookup
here fetches two pages rather than one:

    Building, 2024 -- legacy static register:  96 rows, serials 1..96
    Building, 2024 -- current application:    313 rows, serials 301..613
    overlap between them: ZERO. Serials 97..300 are on NEITHER.

Every other year is whole (2017-2023 and 2025-2026 each run 1..max with at
most a handful of holes). 2024 is the year the authority changed
applications, and 204 registrations fell down the seam between them. So an
adapter that read only one register would report "not registered" for a
project the state HAS registered, and one that read both without noticing
the hole would still do it for 204 of them -- silently, with a clean-looking
absence. `coverage_note()` computes the observed serial range and says
outright when a missing number falls inside a hole rather than past the end.

RESOLVING NEEDS NO SEARCH, BECAUSE THE NUMBER CARRIES ITS OWN ADDRESS.
TN/16/Building/0001/2024 names the type and the year, and each register is
sliced exactly that way -- one type, one year, one request, whole. So a
registration number costs two GETs and no search at all, on a portal that
has no project search to speak of.

A PROMOTER PORTFOLIO IS A NAME MATCH ACROSS YEAR TABLES AND NOTHING MORE.
There is no promoter id and no promoter search; the detail routes are keyed
by an opaque per-project UUID that only a year listing hands out. So every
portfolio row is a CANDIDATE, labelled as one, with the same exposure
guardrails.md records for K-RERA.

THREE ORDER REGISTERS, NOT ONE. The profile knew about `tnrera_judgements`;
the authority also publishes `smb_judgements` (single-member bench, 2022-)
and `adjudicating_judgements` (adjudicating officer, 2018-). All three name
the COMPLAINANT and the RESPONDENT in their own columns, which is what makes
TNRERA promoter-searchable for orders -- most authorities here are not.
Reading one of the three and calling it the litigation record is the K-RERA
five-registers mistake; `order_register_coverage()` names the ones that did
not load.

THE PAN IS MASKED AND THE PORTAL'S OWN LEAK IS NOT A SOURCE. `PAN Card No`
renders as XXXXXX230D. The adjacent `Company Registration No` field was
observed carrying a full, unmasked PAN (ADWPG4230D on the same record) --
a promoter typing a PAN into the wrong box, not a published identifier.
Harvesting it would mean relying on someone else's data-entry mistake and
attributing a PAN to a company on the strength of it, so `parse_public_view`
actively REFUSES to return a PAN-shaped value from that field and records
`pan_masked` instead. gst_group must never see one from Tamil Nadu.
"""

import io
import json
import os
import re

import pdfplumber
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
from .tamilnadu import (
    BASE_URL,
    JUDGEMENTS_YEAR,
    LEGACY_INDEX,
    ONLINE_BUILDING_INDEX,
    ONLINE_LAYOUT_INDEX,
    PROFILE,
)

_TIMEOUT = 180
_UA = "RERA-Scrapper-DueDiligence/1.0 (research tool, low-volume)"

# TN/16/Building/0001/2024 and TNRERA/29/BLG/0001/2026. Kept separate from
# the profile's own pattern, which answers "is this ours?"; this one answers
# "which page holds it?" and so has to capture the parts.
_LEGACY_RE = re.compile(
    r"^TN/(\d{1,2})/(Building|Layout/Offline|Layout|Regularisation-Layout)/(\d{3,4})/(\d{4})$",
    re.I,
)
_CURRENT_RE = re.compile(r"^TNRERA/(\d{1,2})/(BLG|LO)/(\d{3,4})/(\d{4})$", re.I)

# The legacy static register spells the type one way in the URL and another
# in the number: `Normal_Layout` on disk is `Layout` in TN/01/Layout/...
_LEGACY_PATH = {
    "building": "Building",
    "layout": "Normal_Layout",
    "layout/offline": "Normal_Layout",
    "regularisation-layout": "Regularisation_Layout",
}
# Which of the two current-application routes serves a type, and the word
# the public-view URLs use for it.
_ONLINE_ROUTE = {"building": ONLINE_BUILDING_INDEX, "layout": ONLINE_LAYOUT_INDEX}

# Years each source actually offers, confirmed from the authority's own year
# menus rather than assumed. Asking for a year outside these is a request the
# portal answers with an error page, which would look like an empty register.
_LEGACY_YEARS = {
    "Building": range(2017, 2026),
    "Normal_Layout": range(2017, 2027),
    "Regularisation_Layout": range(2022, 2024),
}
_ONLINE_YEARS = range(2024, 2027)

_ORDER_REGISTERS = (
    ("tnrera_judgements", "TNRERA Authority orders", range(2018, 2027)),
    ("smb_judgements", "TNRERA single-member-bench orders", range(2022, 2027)),
    ("adjudicating_judgements", "TNRERA Adjudicating Officer orders", range(2018, 2027)),
)

# A PAN, so the Company-Registration-No leak can be recognised and refused.
_PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")

# Where a promoter's NAME stops and their ADDRESS starts, in a register cell
# that prints both in one breath. Conservative on purpose: a marker that
# fires too early truncates a company name, and a name that is only half a
# name matches the wrong company in a portfolio search.
_ADDRESS_MARKER_RE = re.compile(
    r"^(?:no\.?\s*\d|door\s|(?:old|new)\s+(?:door|no)|plot\s+no|survey|s\.?f\.?\s*no|"
    r"t\.?s\.?\s*no|r\.?s\.?\s*no|\d+[/-]\d+|\d+\s*,|flat\s|block\s+no)",
    re.I,
)
# A firm name can run across a comma -- 'M/s. X, Rep. by Thiru. Y' and
# 'M/s. X, M/s. Y' are both one promoter -- so a segment is only kept past
# the first if it reads like more of the name.
_NAME_CONTINUES_RE = re.compile(
    r"(?:m/s|rep\.?\s+by|represented|pvt|private|ltd|limited|llp|&|and\s+co|division|trust)",
    re.I,
)
# 'Kols Square', '4th Floor', 'New No.111' -- a short segment carrying a
# digit is an address line, not a company name.
_LOOKS_LIKE_ADDRESS_LINE_RE = re.compile(r"^(?=.*\d).{1,30}$")

_AUTHORITY_NOTES = [
    "TNRERA's project register is served by two separate applications -- a static per-year "
    "register for the older registrations and a newer application for recent ones -- and their "
    "coverage does not meet. In the 2024 Building register the older source holds serial "
    "numbers 1 to 96 and the newer one 301 to 613, and the 204 registrations numbered in "
    "between appear on neither. Both sources were read for this project. An absence from "
    "TNRERA's registers is therefore not by itself evidence that a project was never "
    "registered.",
    "TNRERA publishes no promoter identifier and no promoter search. Any other Tamil Nadu "
    "project attributed to this promoter was matched on the name as the register prints it, "
    "and is a candidate to confirm rather than a confirmed project of this entity.",
    "TNRERA masks the PAN on its public record, showing only the last four characters. No PAN "
    "was obtained for this promoter from Tamil Nadu, and none should be inferred.",
]


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _get(session, url, what="page", data=None):
    def _fetch():
        if data is None:
            response = session.get(url, timeout=_TIMEOUT, verify=False)
        else:
            response = session.post(url, data=data, timeout=_TIMEOUT, verify=False)
        response.raise_for_status()
        return response.text

    return fetch_with_retry(_fetch, what=what)


# --- the registration number ----------------------------------------------


def parse_registration_number(reg_no):
    """{era, district_code, type_token, serial, year, kind} or None.

    `kind` is the word the current application's detail URLs use --
    'building' or 'layout' -- and is what tells the caller which of the two
    online routes can serve the number.
    """
    text = " ".join(str(reg_no or "").split())
    match = _LEGACY_RE.match(text)
    if match:
        district, token, serial, year = match.groups()
        token = token.lower()
        return {
            "era": "legacy",
            "district_code": district,
            "type_token": token,
            "serial": int(serial),
            "year": int(year),
            "kind": "building" if token == "building" else "layout",
            "legacy_path": _LEGACY_PATH[token],
        }
    match = _CURRENT_RE.match(text)
    if match:
        district, token, serial, year = match.groups()
        token = token.upper()
        kind = "building" if token == "BLG" else "layout"
        return {
            "era": "current",
            "district_code": district,
            "type_token": token,
            "serial": int(serial),
            "year": int(year),
            "kind": kind,
            "legacy_path": "Building" if kind == "building" else "Normal_Layout",
        }
    return None


# --- the registers --------------------------------------------------------


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


def _columns(header):
    """Column indices by HEADER CONTENT, never by position.

    The two applications serve the same register with different headers --
    'S.No.' against 'S. No', and the newer one inserts a 'Form-c' column
    before the last. Positional indexing works on one and quietly reads the
    wrong column on the other.
    """
    lowered = [h.casefold() for h in header]

    def find(*needles):
        for index, name in enumerate(lowered):
            if all(n in name for n in needles):
                return index
        return None

    return {
        "reg_no": find("registration no"),
        "promoter": find("promoter"),
        "project": find("project details"),
        "approval": find("approval"),
        "completion": find("completion"),
        "other": find("other details"),
        "formc": find("form-c"),
        "status": find("current status"),
    }


def promoter_name(cell_text):
    """The promoter's name out of a cell that prints name AND address.

    'Thiru. M.Anand, Managing Partner, M/s. Rohini Colours, No.7, Kulumani
    Main Road, Worriyur, Tiruchirappalli - 620003.' is one cell. The firm is
    what a portfolio search and a group crosswalk need, so where the cell
    names one ('M/s. ...') that wins over the individual named first; where
    it does not, the leading segments up to the first address marker are
    taken.

    Deliberately conservative -- the full cell is always kept beside this as
    `promoter_block`, because this is an extraction from prose and the prose
    is the record.
    """
    text = " ".join(str(cell_text or "").split())
    if not text:
        return ""
    segments = [s.strip() for s in text.split(",")]

    # Where the firm is named, that is the promoter -- an individual partner
    # is often named first ('Thiru. M.Anand, Managing Partner, M/s. ...').
    # The marker is searched for anywhere in the segment because rows are
    # sometimes enumerated ('1) M/s. Spyka Homes ...').
    start = 0
    for index, segment in enumerate(segments):
        if re.search(r"\bm/s\.?", segment, re.I):
            start = index
            break

    kept = []
    for segment in segments[start:]:
        if kept and (_ADDRESS_MARKER_RE.match(segment)
                     or _LOOKS_LIKE_ADDRESS_LINE_RE.match(segment)
                     or not _NAME_CONTINUES_RE.search(segment)):
            break
        kept.append(segment)
        if len(kept) >= 3:
            break
    name = re.sub(r"^\d+\)\s*", "", ", ".join(kept)).strip(" .")
    return re.sub(r"^m/s\.?\s*", "M/s. ", name, flags=re.I) if name.lower().startswith("m/s") else name


def project_name(cell_text):
    """'Rohini Colours' out of 'Project Name: "Rohini Colours" - Construction
    of ... at ...'.

    The quotes are typographic and the dash separating name from description
    is an en dash on some rows and a hyphen on others, so both are handled.
    """
    text = " ".join(str(cell_text or "").split())
    match = re.search(r"project\s*name\s*[:\-]?\s*(.+)", text, re.I)
    if not match:
        return ""
    rest = match.group(1)
    quoted = re.search(r"[“”\"‘’']([^“”\"‘’']{2,120})", rest)
    if quoted:
        return quoted.group(1).strip()
    return re.split(r"\s+[–—-]\s+", rest)[0].strip()[:120]


_KNOWN_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".doc", ".docx", ".jpg", ".jpeg", ".png")


def document_extension(url):
    """The extension the URL declares, defaulting to .pdf.

    Not cosmetic. TNRERA serves the carpet-area statement as .xlsx, and the
    first live run saved it as `Carpet_Area_Statement_-_View_File.pdf` -- a
    spreadsheet whose name says PDF. Anything downstream that opens it by
    extension gets nothing back, and 'nothing back' is how this pipeline's
    worst bugs have always looked (the PAN card that OCR'd to the empty
    string, for one).
    """
    tail = (str(url or "").split("?")[0].rsplit("/", 1)[-1] or "").casefold()
    for extension in _KNOWN_EXTENSIONS:
        if tail.endswith(extension):
            return extension
    return ".pdf"


def looks_like_a_document(response):
    """Whether a 200 actually carried a document.

    TNRERA answers a file it does not hold with its own HTML -- 14 bytes of
    "Page not found" for a missing register annexe -- at HTTP 200. So the
    body is what decides, not the status. Any non-HTML body counts, because
    this authority serves PDFs and .xlsx alike and a PDF-only test would
    report a real carpet-area statement as missing.
    """
    content = response.content or b""
    if not content:
        return False
    if "text/html" in (response.headers.get("Content-Type") or "").lower():
        return False
    return not content.lstrip()[:9].lower().startswith((b"<!doctype", b"<html", b"page not"))


def _links_in(cells, index):
    if index is None or index >= len(cells):
        return []
    out = []
    for anchor in cells[index].find_all("a"):
        href = anchor.get("href")
        if not href:
            continue
        if not href.startswith("http"):
            href = BASE_URL + "/" + href.lstrip("/")
        out.append({"label": anchor.get_text(" ", strip=True), "url": href})
    return out


def parse_register(html, source=""):
    """One register page as a list of project rows.

    Handles both applications: the legacy static per-year page and the
    current app's year POST, which differ in their headers and in whether
    they carry detail-view links at all.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if len(rows) < 2:
            continue
        columns = _columns(rows[0][0])
        if columns["reg_no"] is None or columns["promoter"] is None:
            continue

        out = []
        for texts, cells in rows[1:]:
            def cell(key):
                index = columns.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            raw_reg = cell("reg_no")
            if "/" not in raw_reg:
                continue
            reg_no, _, dated = raw_reg.partition(" dated ")
            promoter_block = cell("promoter")

            documents = []
            # The Form-C anchor has NO link text at all, so a document taken
            # straight from it is saved as "document.pdf" -- and a second
            # one would collide with it. The column it came from is the
            # label when the cell does not supply one.
            for key, fallback in (("other", "Other Details"), ("formc", "Form-C"),
                                  ("status", "Current Status")):
                for link in _links_in(cells, columns.get(key)):
                    if "public-view" in link["url"]:
                        continue
                    link["label"] = link["label"] or fallback
                    documents.append(link)

            promoter_view = project_view = ""
            for link in _links_in(cells, columns.get("other")):
                if "public-view1" in link["url"]:
                    promoter_view = link["url"]
                elif "public-view2" in link["url"]:
                    project_view = link["url"]

            out.append({
                "reg_no": reg_no.strip(),
                "registered_on": dated.strip(),
                "promoter_name": promoter_name(promoter_block),
                "promoter_block": promoter_block,
                "project_name": project_name(cell("project")),
                "project_block": cell("project"),
                "approval_details": cell("approval"),
                "completion_date": cell("completion"),
                "current_status": cell("status"),
                "documents": documents,
                "promoter_view_url": promoter_view,
                "project_view_url": project_view,
                "source": source,
            })
        if out:
            return out
    return []


def _legacy_register(session, legacy_path, year):
    if year not in _LEGACY_YEARS.get(legacy_path, ()):
        return None
    url = LEGACY_INDEX.format(type=legacy_path, year=year)
    return parse_register(
        _get(session, url, what=f"TNRERA {legacy_path} {year} register"),
        source=f"TNRERA static register, {legacy_path} {year}",
    )


def _online_register(session, kind, year):
    """The current application's register for one year.

    Laravel: the year is a POST and the form carries a CSRF `_token`, so the
    page has to be opened before it can be asked a question.
    """
    if year not in _ONLINE_YEARS:
        return None
    url = _ONLINE_ROUTE[kind]
    first = _get(session, url, what=f"TNRERA {kind} register")
    token = BeautifulSoup(first, "html.parser").find("input", {"name": "_token"})
    if token is None or not token.get("value"):
        # No token means the form did not render -- which is a page that
        # could not be read, not a year with no projects in it.
        raise StateFetchError(
            f"TNRERA's {kind} register did not serve its form token, so the {year} register "
            f"could not be requested. This is a portal problem, not an empty year."
        )
    return parse_register(
        _get(session, url, what=f"TNRERA {kind} {year} register",
             data={"_token": token["value"], "year": str(year)}),
        source=f"TNRERA current register, {kind} {year}",
    )


def registers_for(parsed, session=None):
    """Every register that could hold `parsed`, with per-source coverage.

    Returns (rows, coverage). A source that failed is named in coverage with
    its reason: a lookup that read one of two registers and found nothing has
    NOT established an absence, and the caller has to be able to say so.
    """
    session = session or _session()
    rows, coverage = [], []
    for label, call in (
        ("static register", lambda: _legacy_register(session, parsed["legacy_path"], parsed["year"])),
        ("current register", lambda: _online_register(session, parsed["kind"], parsed["year"])),
    ):
        try:
            got = call()
        except StateFetchError as e:
            coverage.append({"source": label, "status": "could not be read", "detail": str(e)})
            continue
        if got is None:
            coverage.append({
                "source": label,
                "status": "does not cover this year",
                "detail": f"TNRERA's {label} publishes no {parsed['year']} page for this "
                          f"project type.",
            })
            continue
        coverage.append({"source": label, "status": "read", "rows": len(got)})
        rows.extend(got)
    return rows, coverage


def coverage_note(parsed, rows):
    """Why a registration number that should exist was not found.

    The distinction that matters: a serial PAST the end of the year's
    numbering is probably not issued, while one INSIDE the range with
    neighbours on both sides is a hole in what the two applications publish
    between them -- 204 of them in the 2024 Building register alone. Only the
    second is a coverage failure, and reporting it as an absence would be a
    false clean record.
    """
    serials = []
    for row in rows:
        other = parse_registration_number(row["reg_no"])
        if other and other["year"] == parsed["year"]:
            serials.append(other["serial"])
    if not serials:
        return ("No register page for this type and year could be read at all, so nothing "
                "was established about this registration number.")
    low, high = min(serials), max(serials)
    wanted = parsed["serial"]
    if wanted > high:
        return (f"TNRERA's {parsed['year']} register runs to serial {high} across both "
                f"applications, and this number is {wanted}. It is beyond the numbers "
                f"published for that year.")
    if wanted < low:
        return (f"TNRERA's {parsed['year']} register starts at serial {low} across both "
                f"applications, and this number is {wanted}.")
    missing = sorted(set(range(low, high + 1)) - set(serials))
    if wanted in missing:
        return (f"Serial {wanted} of {parsed['year']} sits INSIDE a gap in what TNRERA "
                f"publishes: {len(missing)} of the {high - low + 1} numbers in that year's "
                f"range appear on neither the static register nor the current one. This is a "
                f"hole in the published registers, NOT evidence that the project was never "
                f"registered.")
    return (f"The {parsed['year']} registers were read ({len(serials)} numbers) and this "
            f"registration number is not among them.")


# --- the per-project record ----------------------------------------------


def parse_public_view(html):
    """The current application's promoter or project view as
    {sections, fields, documents, pan_masked}.

    Both views are Bootstrap label/value pairs rather than tables: the
    promoter view writes `<label class="text-muted">X :</label>` followed by
    `<p class="fw-semibold">`, the project view `<small class="text-muted">`
    followed by `<div class="fw-semibold">`. Section headings are `<h6>`.

    Labels REPEAT -- every professional has a 'Mobile No. 1 :' and every
    block a 'Block Details :' -- so this returns an ordered list of
    (section, label, value) and never a flat dict, which would keep the last
    architect's phone number and drop the engineer's.
    """
    soup = BeautifulSoup(html, "html.parser")
    # SCOPED TO THE RECORD, NOT THE PAGE. Both views carry the authority's
    # standard masthead and mega-menu, whose own <h6> headings would
    # otherwise become the section every field is filed under -- every
    # document on the first parse came back labelled "[TAMIL NADU AND
    # ANDAMAN NICOBAR ISLANDS]".
    root = soup.select_one(".tabcontent") or soup

    fields, documents = [], []
    section = ""
    pan_masked = False

    for node in root.find_all(["h6", "div", "small", "label", "a"]):
        classes = node.get("class") or []
        # Two kinds of heading: an <h6 class="fw-bold"> for most sections and
        # a <div class="card-header"> for the professionals' blocks. Reading
        # only the first leaves the architect and the structural engineer
        # with no section, and then no way to tell their phone numbers apart.
        if (node.name == "h6" and "fw-bold" in classes) or "card-header" in classes:
            text = node.get_text(" ", strip=True)
            if text and len(text) < 120:
                section = text
            continue
        if node.name == "div":
            continue
        if node.name == "a":
            href = node.get("href") or ""
            text = node.get_text(" ", strip=True)
            # The filed documents live under the app's storage path. Matching
            # on link TEXT instead would collect every "view" in the menu.
            if "/storage/" in href:
                documents.append({"label": f"{section} - {text}".strip(" -"), "url": href})
            continue

        if "text-muted" not in classes:
            continue
        label = node.get_text(" ", strip=True).rstrip(":").strip()
        if not label:
            continue
        value_node = node.find_next(
            lambda t: t.name in ("div", "p", "span") and "fw-semibold" in (t.get("class") or [])
        )
        value = value_node.get_text(" ", strip=True) if value_node else ""

        # The leak. A full PAN sitting in `Company Registration No` is a
        # promoter's data-entry error, not a published identifier, and
        # returning it would attribute a PAN to a company on the strength of
        # someone else's mistake.
        if _PAN_RE.match(value.upper()) and "registration no" in label.casefold():
            value = "[withheld: a PAN-shaped value in a non-PAN field]"
        # `\bpan\b`, NOT `"pan" in label`: 'Company' contains 'pan', so the
        # loose test blanked `Company Registration No` -- including the
        # withholding note that was the whole point of the line above it.
        if re.search(r"\bpan\b", label, re.I):
            if value and "X" in value.upper():
                pan_masked = True
            value = ""

        fields.append({"section": section, "label": label, "value": value})

    return {
        "fields": fields,
        "documents": documents,
        "pan_masked": pan_masked,
        "sections": sorted({f["section"] for f in fields if f["section"]}),
    }


def field_value(parsed_view, label_needle, section_needle=None):
    """The first value whose label contains `label_needle`, optionally
    inside a section. Returns '' rather than None so callers can treat a
    missing field and an empty one the same way -- neither is a finding."""
    for field in parsed_view.get("fields", []):
        if label_needle.casefold() not in field["label"].casefold():
            continue
        if section_needle and section_needle.casefold() not in field["section"].casefold():
            continue
        return field["value"]
    return ""


# --- order registers ------------------------------------------------------


def parse_order_register(html):
    """One year of one order register as
    [{case_no, complainant, respondent, project, decided_on, order_url}].

    All three registers share this shape, and all three name both parties --
    which is what makes TNRERA searchable by promoter for orders. A complaint
    is filed against the promoter, so the RESPONDENT is the side to match on.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if len(rows) < 2:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if "respondent" not in joined:
            continue

        def find(*needles):
            for index, name in enumerate(header):
                if any(n in name for n in needles):
                    return index
            return None

        idx = {
            "case_no": find("complaint", "petition"),
            "complainant": find("complainant"),
            "respondent": find("respondent"),
            "project": find("project"),
            "decided_on": find("final order", "date"),
        }
        out = []
        for texts, cells in rows[1:]:
            def cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            if not cell("respondent").strip():
                continue
            order_url = ""
            for anchor in cells[-1].find_all("a") if cells else []:
                href = anchor.get("href") or ""
                if href:
                    order_url = href if href.startswith("http") else BASE_URL + "/" + href.lstrip("/")
                    break
            out.append({
                "case_no": cell("case_no"),
                "complainant": cell("complainant"),
                "respondent": cell("respondent"),
                "project": cell("project"),
                "decided_on": cell("decided_on"),
                "order_url": order_url,
            })
        if out:
            return out
    return []


_ORDER_CACHE = {}


def fetch_order_registers(fetcher=None, session=None):
    """All three registers, every year, cached for the process.

    Returns (rows, coverage). Coverage names each register-year that did not
    load, because a promoter search over two of three registers is not a
    clean litigation record and must never be rendered as one.
    """
    if _ORDER_CACHE and fetcher is None:
        return _ORDER_CACHE["rows"], _ORDER_CACHE["coverage"]

    session = session or _session()
    rows, coverage = [], []
    for slug, label, years in _ORDER_REGISTERS:
        read, failed = 0, []
        for year in years:
            url = JUDGEMENTS_YEAR.format(year=year).replace("tnrera_judgements", slug)
            try:
                html = fetcher(url) if fetcher is not None else _get(
                    session, url, what=f"{label} {year}"
                )
                parsed = parse_order_register(html)
            except Exception as e:  # noqa: BLE001 -- recorded, never raised
                failed.append(f"{year} ({type(e).__name__})")
                continue
            for row in parsed:
                row["register"] = label
                row["year"] = year
            rows.extend(parsed)
            read += 1
        coverage.append({
            "register": label,
            "years_read": read,
            "years_total": len(list(years)),
            "years_failed": failed,
        })
    if fetcher is None:
        _ORDER_CACHE["rows"], _ORDER_CACHE["coverage"] = rows, coverage
    return rows, coverage


def order_register_coverage(coverage):
    """A reader-facing sentence about which registers were actually read."""
    unread = [c for c in coverage if c["years_failed"]]
    total = sum(c["years_read"] for c in coverage)
    sentence = (f"TNRERA publishes three order registers -- Authority, single-member bench and "
                f"Adjudicating Officer -- and {total} register-years were read.")
    if unread:
        detail = "; ".join(f"{c['register']}: {', '.join(c['years_failed'])}" for c in unread)
        sentence += (f" These did NOT load and are not covered by this search: {detail}. An "
                     f"order in an unread year would not appear here.")
    return sentence


def search_orders_by_promoter(name, fetcher=None):
    """Orders whose RESPONDENT names `name`, across all three registers.

    A normalised substring match, so every hit is a CANDIDATE: the registers
    are keyed on names, not identifiers, and Indian firm names repeat.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return [], []
    rows, coverage = fetch_order_registers(fetcher)
    hits = [r for r in rows
            if needle in " ".join((r.get("respondent") or "").split()).casefold()]
    return hits, coverage


# --- the penalty register (unwired) ---------------------------------------
#
# NOT called from acquire() or any test -- written and verified against a
# live page (2026-08-26), same precedent as adapter_westbengal.fetch_defaulters().
#
# This is the closest thing TNRERA publishes to a "defaulters list": one live
# page per project type, no year selector and no search box, naming the
# promoter, their address, the project and the penalty levied. Confirmed live
# with real rows -- 1 on the Building page (TNRERA/PBF/0092/2025, M/S. VIKAS
# MANTRA PROPERTIES & INFRASTRUCTURE PRIVATE LIMITED, Rs 20,10,940) and 146 on
# the Layout page (TNRERA/PLI/2288/2024 among them). It is titled "Penalty",
# not "Defaulters" or "Black List" -- the authority publishes no register
# under either of those names, and no separate revoked/cancelled-registration
# list was found anywhere on the site.
PENALTY_URLS = {
    "building": BASE_URL + "/building/online/penalty",
    "layout": BASE_URL + "/layout/online/penalty",
}


def parse_penalty_register(html):
    """One penalty page as [{application_no, promoter_block, project_block,
    penalty_notice_date, penalty_amount}].

    Same header-by-content approach as `_columns` -- this page has no year
    selector so there is exactly one table to find, but matching by header
    text rather than position costs nothing and survives a column reorder.

    `promoter_block` is left whole, deliberately NOT run through
    `promoter_name()`: that helper assumes a comma before the address starts
    ('M/s. X, No.7, ... Road'), and this register's cells instead run the
    firm name straight into 'Door No.' with a period ('LIMITED. Door No.
    NO.27, ...'). Applying it here truncated a real promoter's name at
    'Door No.' on the first live row checked -- a bug in reuse, not a
    pre-existing one in `promoter_name()` itself.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if len(rows) < 2:
            continue
        header = [h.casefold() for h in rows[0][0]]

        def find(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        idx = {
            "application_no": find("application"),
            "promoter_block": find("promoter"),
            "project_block": find("project"),
            "penalty_notice_date": find("penalty", "issued"),
            "penalty_amount": find("penalty", "amount"),
        }
        if idx["application_no"] is None or idx["promoter_block"] is None:
            continue
        out = []
        for texts, _cells in rows[1:]:
            def cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            application_no = cell("application_no")
            if not application_no:
                continue
            out.append({
                "application_no": application_no,
                "promoter_block": cell("promoter_block"),
                "project_block": cell("project_block"),
                "penalty_notice_date": cell("penalty_notice_date"),
                "penalty_amount": cell("penalty_amount"),
            })
        if out:
            return out
    return []


def fetch_penalty_notices(kind="building", fetcher=None, session=None):
    """TNRERA's live penalty register for one project type ('building' or
    'layout'). Returns a plain list, never raises -- a caller that wires this
    in decides what "could not be read" should mean for its own use."""
    url = PENALTY_URLS[kind]
    html = fetcher(url) if fetcher is not None else _get(
        session or _session(), url, what=f"TNRERA {kind} penalty register"
    )
    return parse_penalty_register(html)


# --- unregistered-project enforcement PDFs ---------------------------------
#
# NOT WIRED INTO acquire(). "Projects under investigation" was Partial in the
# 2026-08-26 coverage audit -- two static PDFs, linked from the homepage,
# enumerate projects TNRERA is enforcing against for never having registered
# at all. Confirmed live these are NATIVE-TEXT PDFs (real tables, not scans)
# -- no OCR needed, unlike Delhi-RERA's REAT orders. pdfplumber's own table
# extraction reads the grid cleanly; a raw text dump does not reliably keep
# a row's three columns apart.
#
# THE CEILING IS REAL, NOT A SCRAPING GAP. Both lists are project-level
# enforcement for UNREGISTERED sites -- "Show Cause Notice issued for levy
# of penalty for non registration" and a caution list of promoters selling
# without registering at all. Neither is a "projects under investigation"
# register for something already REGISTERED, and TNRERA publishes no such
# register found this session. So this closes the "static, non-searchable"
# half of the earlier Partial verdict, not the "doesn't cover registered
# projects" half -- that would need a different finding, not better code.

SCN_PENALTY_PDF = BASE_URL + "/homePageFiles/SCN_issued_levy_of_penalty.pdf"
PERSONAL_USE_CAUTION_PDF = BASE_URL + "/homePageFiles/Personal_Use_Not_For_Sale.pdf"


def parse_enforcement_pdf(pdf_bytes):
    """Either enforcement PDF's table, across every page, as
    [{sl_no, party_detail, site_address, extra}]. `extra` is whichever
    fourth column that PDF has (an approval-letter number and date on the
    SCN list; nothing on the caution list, an empty string there).

    Both PDFs share the same three-column skeleton (serial / party+address /
    site address) with a title row and a repeated header row per page, which
    `_looks_like_header` filters out rather than counting as data.
    """

    def _looks_like_header(row):
        first = (row[0] or "").strip().casefold()
        return first in ("sl.no.", "sl.no", "s.no.", "s.no") or first == ""

    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    cells = [(" ".join((c or "").split())) for c in row]
                    if not cells or not cells[0] or not cells[0][:1].isdigit():
                        continue
                    if _looks_like_header(cells):
                        continue
                    out.append({
                        "sl_no": cells[0],
                        "party_detail": cells[1] if len(cells) > 1 else "",
                        "site_address": cells[2] if len(cells) > 2 else "",
                        "extra": cells[3] if len(cells) > 3 else "",
                    })
    return out


def fetch_enforcement_pdf_rows(url, session=None):
    """Downloads and parses one of the two enforcement PDFs. Returns a plain
    list; never raises -- these are large (up to 118 pages) and a caller
    sweeping both should not have one failure sink the other.

    NOT `_get()`: that helper returns `response.text`, which decodes a
    binary PDF as if it were text and corrupts it. This fetches bytes
    directly, with the same retry-on-transient-failure discipline.
    """
    session = session or _session()

    def _fetch():
        response = session.get(url, timeout=_TIMEOUT, verify=False)
        response.raise_for_status()
        return response.content

    try:
        content = fetch_with_retry(_fetch, what="TNRERA enforcement PDF")
    except StateFetchError:
        return []
    return parse_enforcement_pdf(content)


def search_enforcement_lists_by_name(name, session=None):
    """Rows from BOTH enforcement PDFs whose party detail matches `name`.
    A normalised substring match over OCR-free native text, so hits are
    still candidates rather than confirmed matches -- these are promoter
    names embedded in prose ('Thiru. X ... Managing Partner, M/s. Y...'),
    not a clean single-column name field.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return []
    session = session or _session()
    hits = []
    for url, source in (
        (SCN_PENALTY_PDF, "TNRERA show-cause (non-registration penalty) list"),
        (PERSONAL_USE_CAUTION_PDF, "TNRERA personal-use / not-for-sale caution list"),
    ):
        for row in fetch_enforcement_pdf_rows(url, session=session):
            if needle in row["party_detail"].casefold():
                hits.append({**row, "source": source})
    return hits


# --- the group-sweep seam -------------------------------------------------


class _NullReporter:
    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def ok(self, *a, **k): pass
    def choose(self, *a, **k): return None


_INDEX_CACHE = {}


def _whole_register(session=None, reporter=None):
    """Every year of every type, cached for the process, with coverage.

    Twenty-one legacy pages plus six from the current application, around
    20,000 rows. Expensive, and the only way to answer 'what else has this
    promoter registered in Tamil Nadu' on a portal with no promoter search --
    so it is done once and reported honestly, never sampled quietly.
    """
    if _INDEX_CACHE:
        return _INDEX_CACHE["rows"], _INDEX_CACHE["coverage"]

    session = session or _session()
    reporter = reporter or _NullReporter()
    rows, coverage = [], []
    for legacy_path, years in _LEGACY_YEARS.items():
        for year in years:
            try:
                got = _legacy_register(session, legacy_path, year) or []
            except StateFetchError as e:
                coverage.append({"source": f"static {legacy_path} {year}",
                                 "status": "could not be read", "detail": str(e)})
                continue
            coverage.append({"source": f"static {legacy_path} {year}",
                             "status": "read", "rows": len(got)})
            rows.extend(got)
    for kind in ("building", "layout"):
        for year in _ONLINE_YEARS:
            try:
                got = _online_register(session, kind, year) or []
            except StateFetchError as e:
                coverage.append({"source": f"current {kind} {year}",
                                 "status": "could not be read", "detail": str(e)})
                continue
            coverage.append({"source": f"current {kind} {year}",
                             "status": "read", "rows": len(got)})
            rows.extend(got)

    unread = [c for c in coverage if c["status"] != "read"]
    reporter.info(f"TNRERA: {len(rows)} projects indexed across "
                  f"{len(coverage) - len(unread)} register-years"
                  + (f", {len(unread)} unreadable" if unread else "."))
    _INDEX_CACHE["rows"], _INDEX_CACHE["coverage"] = rows, coverage
    return rows, coverage


def search_promoter_projects(name, reporter=None):
    """Tamil Nadu projects whose promoter cell names `name`.

    Matched against BOTH the extracted promoter name and the full register
    cell, because the cell prints name and address together and a firm can
    be named mid-sentence after an individual partner.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return []
    try:
        rows, _ = _whole_register(reporter=reporter)
    except StateFetchError:
        return []
    out = []
    for row in rows:
        haystack = " ".join((row["promoter_block"] or "").split()).casefold()
        if needle not in haystack:
            continue
        out.append({
            "reg_no": row["reg_no"],
            "project_name": row["project_name"],
            "promoter_name": row["promoter_name"],
            "project_id": row["reg_no"],
        })
    return out


def fetch_project_summary(project_ref, reporter=None):
    """Open ONE Tamil Nadu project by its registration number.

    What group_sweep confirms or refutes a candidate on is the promoter name,
    and both registers carry it, so a TN hit can be confirmed even for the
    older years whose rows have no detail view to open.
    """
    reporter = reporter or _NullReporter()
    parsed = parse_registration_number(project_ref)
    if parsed is None:
        return {"opened": False,
                "note": f"'{project_ref}' is not a TNRERA registration number."}
    try:
        rows, coverage = registers_for(parsed)
    except StateFetchError as e:
        return {"opened": False,
                "note": f"TNRERA's registers could not be read ({type(e).__name__})."}

    wanted = " ".join(str(project_ref).split()).casefold()
    entry = next((r for r in rows if r["reg_no"].casefold() == wanted), None)
    if entry is None:
        return {"opened": False, "note": coverage_note(parsed, rows), "coverage": coverage}

    summary = {
        "opened": True,
        "promoter_name": entry["promoter_name"],
        "promoter_block": entry["promoter_block"],
        "project_name": entry["project_name"],
        "reg_no": entry["reg_no"],
        "registered_on": entry["registered_on"],
        "completion_date": entry["completion_date"],
        "current_status": entry["current_status"],
        "source": entry["source"],
        "notes": [],
    }
    if entry.get("project_view_url"):
        try:
            view = parse_public_view(
                _get(_session(), entry["project_view_url"], what="TNRERA project view")
            )
            summary["district"] = field_value(view, "District")
            summary["total_project_cost"] = field_value(view, "Total Project Cost")
            summary["documents_on_page"] = len(view["documents"])
        except StateFetchError:
            summary["notes"].append(
                "The project's detail view did not load, so only what the register states "
                "was read for it."
            )
    else:
        summary["notes"].append(
            "TNRERA publishes no detail view for this registration -- the older register is a "
            "static table -- so this is everything the authority states about it."
        )
    return summary


# --- the adapter ----------------------------------------------------------


class TamilNaduAdapter:
    """StateAdapter for TNRERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        session = _session()
        parsed = parse_registration_number(query)

        if parsed is None:
            rows, coverage, chosen = self._resolve_by_name(query, ctx)
            parsed = parse_registration_number(chosen["reg_no"])
        else:
            ctx.reporter.info(
                f"Reading TNRERA's {parsed['year']} {parsed['legacy_path']} registers "
                f"(both applications)..."
            )
            rows, coverage = registers_for(parsed, session)
            wanted = " ".join(query.split()).casefold()
            chosen = next((r for r in rows if r["reg_no"].casefold() == wanted), None)
            if chosen is None:
                raise StateResolutionError(
                    f"'{query}' was not found on TNRERA. {coverage_note(parsed, rows)}"
                )

        registration_number = chosen["reg_no"]
        reg_no = storage_key(registration_number)
        ctx.reporter.ok(f"Resolved: {registration_number} | {chosen['project_name']}")

        project_out_dir = os.path.join(ctx.output_dir, reg_no)
        raw_dir = os.path.join(project_out_dir, "raw")
        documents_dir = os.path.join(project_out_dir, "documents")
        os.makedirs(raw_dir, exist_ok=True)
        if ctx.on_resolved is not None:
            ctx.on_resolved(reg_no)
            os.makedirs(raw_dir, exist_ok=True)

        notes = list(_AUTHORITY_NOTES)
        for entry in coverage:
            if entry["status"] != "read":
                notes.append(
                    f"TNRERA's {entry['source']} {entry['status']}, so this project's record "
                    f"was assembled from the other register alone."
                )

        project_view, promoter_view = self._detail_views(session, chosen, ctx, notes)
        promoter = (field_value(promoter_view, "Firm Name")
                    or field_value(promoter_view, "Promoter Name")
                    or chosen["promoter_name"])

        orders, order_notes = self._orders(promoter, ctx)
        notes.extend(order_notes)

        category_data = {
            "projects": {
                "projectName": chosen["project_name"],
                "projectRegistartionNo": registration_number,
                "registrationDate": chosen["registered_on"],
                "district": field_value(project_view, "District"),
                "projectDescription": chosen["project_block"],
                "approvalDetails": chosen["approval_details"],
                "completionDate": chosen["completion_date"],
                "currentStatus": chosen["current_status"],
                "totalProjectCost": field_value(project_view, "Total Project Cost"),
                "latitude": field_value(project_view, "Latitude"),
                "longitude": field_value(project_view, "Longitude"),
            },
            "partners": {"promoterDetails": {
                "promoterName": promoter,
                "promoterBlock": chosen["promoter_block"],
                "typeOfPromoter": field_value(promoter_view, "Type of Promoter"),
                "emailId": field_value(promoter_view, "Email ID"),
                "mobileNo": field_value(promoter_view, "Mobile No. 1"),
                "address": field_value(promoter_view, "Address"),
                # Masked on this authority's record -- recorded as filed, and
                # deliberately not carried as a PAN.
                "panMasked": bool(promoter_view.get("pan_masked")),
            }},
            "professionals": self._professionals(project_view),
            "spocs": None,
            "sro_details": None,
            "past_experiences": None,
            "documents": {"entries": chosen["documents"] + project_view.get("documents", [])},
            "complaints": orders,
            "appeals": None,
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        documents_manifest = self._download_documents(
            session, category_data["documents"]["entries"], documents_dir, ctx
        )

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=registration_number,
            project_id=registration_number,
            detail_url=chosen.get("project_view_url") or None,
            category_data=category_data,
            documents_manifest=documents_manifest,
            documents_dir=documents_dir,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=promoter,
            promoter_portfolio=self._promoter_portfolio(promoter, chosen, ctx),
            raw_record=chosen,
            auth_source="none",
            categories_not_published={"spocs", "sro_details", "past_experiences", "appeals"},
            notes=notes,
        )

    # -- resolution --------------------------------------------------------
    def _resolve_by_name(self, query, ctx):
        """A project-name query, which costs the whole state.

        TNRERA has no project search, so a name can only be matched against
        the registers themselves -- every year of every type. Expensive, and
        stated rather than hidden.
        """
        ctx.reporter.info(
            "TNRERA publishes no project search, so a name query is matched against every "
            "year of both registers. This takes a few minutes."
        )
        rows, coverage = _whole_register(reporter=ctx.reporter)
        needle = " ".join(query.split()).casefold()
        matches = [r for r in rows if needle in (r["project_name"] or "").casefold()]
        if not matches:
            matches = [r for r in rows if needle in (r["project_block"] or "").casefold()]
        if not matches:
            unread = [c["source"] for c in coverage if c["status"] != "read"]
            raise StateResolutionError(
                f"No TNRERA project matched '{query}' across {len(rows)} registered projects."
                + (f" Note that {len(unread)} register-year page(s) could not be read this "
                   f"run ({', '.join(unread[:4])}), so this is not a complete search."
                   if unread else "")
            )
        chosen = matches[0]
        if len(matches) > 1:
            options = [f"{r['reg_no']} | {r['project_name']} | {r['promoter_name']}"
                       for r in matches[:20]]
            picked = ctx.reporter.choose(
                f"{len(matches)} TNRERA projects matched {query!r}:", options
            )
            if picked is not None and 0 <= picked < len(matches):
                chosen = matches[picked]
            else:
                ctx.reporter.warn(
                    f"{len(matches)} projects matched; using {chosen['reg_no']}."
                )
        return rows, coverage, chosen

    # -- detail views ------------------------------------------------------
    def _detail_views(self, session, chosen, ctx, notes):
        """The current application's project and promoter views, when the
        row has them. The static register's rows do not, and that is a limit
        of the older register rather than a failed fetch."""
        empty = {"fields": [], "documents": [], "pan_masked": False, "sections": []}
        project_view = promoter_view = empty

        if not chosen.get("project_view_url") and not chosen.get("promoter_view_url"):
            notes.append(
                "This registration sits on TNRERA's older static register, which publishes a "
                "row and its annexed PDFs but no per-project detail view. The promoter's "
                "contact details, the professionals of record, the bank account and the "
                "financial breakdown are not published for it -- they are absent from the "
                "interface, not from the promoter's filing."
            )
            return project_view, promoter_view

        if chosen.get("project_view_url"):
            try:
                ctx.reporter.info("Fetching TNRERA project record...")
                project_view = parse_public_view(
                    _get(session, chosen["project_view_url"], what="TNRERA project view")
                )
            except StateFetchError as e:
                notes.append(f"TNRERA's project detail view did not load ({type(e).__name__}), "
                             f"so this record carries only what the register states.")
        if chosen.get("promoter_view_url"):
            try:
                promoter_view = parse_public_view(
                    _get(session, chosen["promoter_view_url"], what="TNRERA promoter view")
                )
            except StateFetchError as e:
                notes.append(f"TNRERA's promoter detail view did not load "
                             f"({type(e).__name__}).")
        return project_view, promoter_view

    def _professionals(self, project_view):
        """Architect, structural engineer and contractor, each as its own
        block. Returns None when the view was never read, so an absence is
        not mistaken for 'this project filed no professionals'."""
        if not project_view.get("fields"):
            return None
        out = {}
        for key, section in (("architect", "Architect"),
                             ("structural_engineer", "Structural Engineer"),
                             ("contractor", "Contractor")):
            block = {
                "name": field_value(project_view, "Name", section),
                "email": field_value(project_view, "Email", section),
                "address": field_value(project_view, "Address", section),
                "licence": (field_value(project_view, "Registration No", section)
                            or field_value(project_view, "MCA No", section)),
                "licence_valid_upto": field_value(project_view, "License Valid Upto", section),
            }
            if any(block.values()):
                out[key] = block
        return out or None

    # -- orders ------------------------------------------------------------
    def _orders(self, promoter, ctx):
        """TNRERA's three order registers, matched on respondent."""
        try:
            hits, coverage = search_orders_by_promoter(promoter)
        except Exception:
            return ({"total_complaints_count": None,
                     "source": "TNRERA order registers"},
                    ["TNRERA's order registers could not be read this run, so this promoter's "
                     "complaint history is UNKNOWN. It must not be read as zero."])
        notes = [order_register_coverage(coverage)]
        if hits:
            ctx.reporter.warn(f"TNRERA order registers name {len(hits)} matching respondent(s).")
            notes.append(
                f"{len(hits)} order(s) on TNRERA's public registers name a respondent matching "
                f"this promoter's name. The registers are keyed on names rather than any "
                f"identifier, so these are possible matches to confirm, not confirmed orders "
                f"against this entity."
            )
        else:
            ctx.reporter.ok("TNRERA order registers name no matching respondent.")
        return ({"total_complaints_count": len(hits),
                 "source": "TNRERA order registers (name match on respondent)",
                 "matches": hits,
                 "coverage": coverage},
                notes)

    # -- documents ---------------------------------------------------------
    def _download_documents(self, session, entries, documents_dir, ctx):
        """Every PDF the register and the detail view link.

        A MISSING FILE ON THIS PORTAL IS HTTP 200. `.../99999-2024.pdf`
        returns a 14-byte "Page not found" HTML body with a 200, so status
        alone would record a placeholder as a retrieved document.

        The test is "did a document come back", NOT "is this a PDF": the
        detail view serves the carpet-area statement as .xlsx, and demanding
        a PDF magic number would file a real spreadsheet as missing.
        """
        if not entries:
            return []
        os.makedirs(documents_dir, exist_ok=True)
        manifest, used = [], set()
        for entry in entries:
            url, label = entry.get("url") or "", entry.get("label") or "document"
            if not url:
                manifest.append({"label": label, "status": "no link published"})
                continue
            name = safe_document_filename(documents_dir, label, used,
                                          extension=document_extension(url))
            path = os.path.join(documents_dir, name)
            try:
                response = session.get(url, timeout=_TIMEOUT, verify=False)
                response.raise_for_status()
                if not looks_like_a_document(response):
                    manifest.append({"label": label, "url": url,
                                     "status": "not held by the portal"})
                    continue
                with open(path, "wb") as f:
                    f.write(response.content)
                manifest.append({"label": label, "url": url, "path": path,
                                 "status": "downloaded"})
            except Exception as e:  # noqa: BLE001 -- recorded per document
                manifest.append({"label": label, "url": url,
                                 "status": f"failed: {type(e).__name__}"})
        downloaded = sum(1 for d in manifest if d.get("status") == "downloaded")
        ctx.reporter.ok(f"{downloaded}/{len(manifest)} TNRERA document(s) retrieved.")
        return manifest

    # -- portfolio ---------------------------------------------------------
    def _promoter_portfolio(self, promoter, chosen, ctx):
        """Other Tamil Nadu projects under this promoter's name.

        Every row is a candidate: TNRERA publishes no promoter identifier, so
        this is a name match and nothing stronger.
        """
        if not promoter:
            return None
        try:
            projects = search_promoter_projects(promoter, reporter=ctx.reporter)
        except StateFetchError:
            return None
        rows, coverage = _whole_register(reporter=ctx.reporter)
        unread = [c["source"] for c in coverage if c["status"] != "read"]
        ctx.reporter.info(f"TNRERA portfolio: {len(projects)} candidate project(s).")
        return {
            "promoter_name": promoter,
            "projects": projects,
            "totals": {"total_projects": len(projects)},
            "source": "TNRERA registered-project registers, all years, both applications",
            "notes": [
                "Matched on the promoter's name as the register prints it. TNRERA publishes no "
                "promoter identifier, so each of these is a candidate rather than a confirmed "
                "project of this entity, and a promoter who filed under a differently spelled "
                "name would not be joined to them.",
            ] + ([f"{len(unread)} register-year page(s) could not be read this run "
                  f"({', '.join(unread[:4])}), so this portfolio is not complete."]
                 if unread else []),
        }


ADAPTER = TamilNaduAdapter()

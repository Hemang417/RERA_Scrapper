"""
StateAdapter for Delhi / Delhi-RERA.

See states/delhi.py for the register's size and why that size is the single
most important thing this adapter reports.

THE WHOLE REGISTER IS ONE GET, AND THAT IS THE WHOLE ADAPTER. 130 projects,
no CAPTCHA, no login, no pagination -- district, project name, PROMOTER
name, registration number, validity and project type, all in the index. So
resolving a registration number is a lookup, not a search, and a promoter's
other Delhi projects are a local join rather than a second request.

THERE IS NO PER-PROJECT RECORD TO OPEN, AND THAT IS NOT AN OVERSIGHT. The
register's own "View Details" control is inert:

    <a class="btn view-button" href="javascript:void(0);"
       id="modalOpenerButton" title="View Details">View</a>

Verified against the served page: no href, no data- attribute, no ajax call,
no `url:` literal, and no detail route referenced anywhere in it. Six
plausible detail routes were probed and every one returned 404. So this
adapter returns what the index states and declares everything else NOT
PUBLISHED, rather than shipping a capability it cannot deliver -- the
mistake states/adapter_gujarat.py made once with promoter portfolios.

WHAT THAT MEANS FOR A READER, and why the notes below are the real product:
a Delhi project's record here is an identity and a validity date. It carries
no land details, no escrow account, no professionals of record and no
documents -- not because they were not fetched, but because this interface
does not serve them. Any of those appearing blank in a Charter is the
authority's limit, never a finding about the promoter.
"""

import hashlib
import json
import os
import re
import shutil

import fitz  # PyMuPDF
import pytesseract
import requests
from bs4 import BeautifulSoup
from PIL import Image

from .base import (
    AcquisitionResult,
    StateFetchError,
    StateResolutionError,
    fetch_with_retry,
    storage_key,
)
from .delhi import (
    APPEAL_REGISTER,
    EXECUTION_REGISTER,
    ORDER_REGISTER,
    PROFILE,
    STATE_INDEX,
    SUOMOTO_REGISTER,
)

_TIMEOUT = 90
_UA = "RERA-Scrapper-DueDiligence/1.0 (research tool, low-volume)"

# Same Tesseract-path bootstrap as mahabhumi.py/gst_portal.py/company_charter.py,
# for the same reason: pytesseract shells out to the binary by name, and on a
# machine where it's installed but not on PATH this would otherwise fail
# silently rather than fall back cleanly.
if not shutil.which("tesseract"):
    for _candidate in (
        os.environ.get("TESSERACT_CMD"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if _candidate and os.path.exists(_candidate):
            pytesseract.pytesseract.tesseract_cmd = _candidate
            break

# "RR TEXKNIT LLP (Other than Individual)" -- the parenthetical is the
# authority's own applicant classification, not part of the name. Worth
# keeping separately: an individual promoter and a company are different
# diligence subjects, and it is the only such signal the index carries.
_APPLICANT_KIND_RE = re.compile(r"\s*\((Individual|Other than Individual)\)\s*$", re.I)

_AUTHORITY_NOTES = [
    "Delhi-RERA's public register contains 130 projects in total, for the whole National "
    "Capital Territory, across 2018 to 2026 -- against roughly 55,000 on MahaRERA. Delhi's "
    "market is overwhelmingly resale and plotted development falling outside the "
    "registration thresholds, and the authority has registered 712 agents against those 130 "
    "projects. A promoter with genuine Delhi activity may therefore have no Delhi-RERA "
    "record at all, and the absence of one is close to worthless as evidence.",
    "Delhi-RERA publishes no per-project record through this interface: the register's own "
    "'View Details' control is inert and every detail route probed returns nothing. So this "
    "project's land details, escrow accounts, professionals of record and filed documents "
    "were not merely unfetched -- they are not published. None of them may be read as absent "
    "from the promoter's filing.",
]


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    return s


def _get(session, url, what="page"):
    def _fetch():
        response = session.get(url, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.text

    return fetch_with_retry(_fetch, what=what)


def split_applicant_kind(promoter):
    """('RR TEXKNIT LLP', 'Other than Individual') from the index's own cell.

    Pure so it is testable against the real string without a fetch.
    """
    promoter = " ".join(str(promoter or "").split())
    match = _APPLICANT_KIND_RE.search(promoter)
    if not match:
        return promoter, ""
    return _APPLICANT_KIND_RE.sub("", promoter).strip(), match.group(1)


def _rows_of(table):
    """(cell texts, cell elements) per row.

    The elements are kept because two things this register publishes live in
    the markup rather than the text: the clean registration number in a
    `data-diary-no` attribute, and each judgement's PDF link.
    """
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        texts = [c.get_text(" ", strip=True) for c in cells]
        if any(texts):
            rows.append((texts, cells, tr))
    return rows


def strip_disclaimer_marker(value):
    """'DLRERA2025P0003 *' -> 'DLRERA2025P0003'.

    THE REGISTER APPENDS A FOOTNOTE MARKER INTO THE CELL. One of the 130
    rows carries `<label style="color:red" title="view disclaimer">*</label>`
    inside both its registration-number cell and its project-name cell, so
    the cell text reads "DLRERA2025P0003 *" and "Good Earth Capital Crest *".

    Left in place that number matches nothing: not the DL profile's own
    reg-no pattern, and not a reader pasting the number as the authority
    issued it -- so this one project was unresolvable by its own
    registration number, and invisible to any portfolio or sweep match. It
    is the same species as HARERA's "Lapsed Project" flag concatenating onto
    a certificate number, which that adapter already strips.

    The marker itself is not discarded; `has_disclaimer` carries it, because
    the authority flagging a registration is diligence material.
    """
    return re.sub(r"\s*\*+\s*$", "", " ".join(str(value or "").split())).strip()


def parse_state_index(html):
    """The whole register as [{reg_no, project_name, promoter_name, ...}].

    THE TABLE IS FOUND BY ITS HEADER, NEVER BY POSITION. The page serves two
    131-row tables whose headers differ only in the middle: the register
    proper carries "Promoter's Name", and the quarterly-updates table
    carries "Quarter Name" instead. Taking tables[0] would work today and
    break silently the moment the page reorders them -- the lesson the
    JHARERA and K-RERA adapters both had to learn.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if not rows:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if "registration number" not in joined or "promoter" not in joined:
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        columns = {
            "district": _column("district"),
            "project_name": _column("project", "name"),
            "promoter": _column("promoter"),
            "reg_no": _column("registration number"),
            "valid_upto": _column("valid"),
            "project_type": _column("type"),
        }
        if columns["reg_no"] is None:
            continue

        out = []
        for texts, cells, row_element in rows[1:]:
            def _cell(key):
                index = columns.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            raw_reg_no = _cell("reg_no")
            # The register states the number twice: once as cell text, which
            # a footnote marker can contaminate, and once as a clean
            # `data-diary-no` attribute. Prefer the attribute; fall back to
            # the stripped text.
            diary = row_element.find(attrs={"data-diary-no": True})
            reg_no = (diary["data-diary-no"].strip() if diary
                      else strip_disclaimer_marker(raw_reg_no))
            if not reg_no:
                continue
            promoter, kind = split_applicant_kind(_cell("promoter"))
            out.append({
                "reg_no": reg_no,
                "project_name": strip_disclaimer_marker(_cell("project_name")),
                "promoter_name": promoter,
                "applicant_kind": kind,
                "district": _cell("district"),
                "registration_valid_upto": _cell("valid_upto"),
                "project_type": _cell("project_type"),
                # The authority attached a disclaimer to this registration.
                "has_disclaimer": "*" in raw_reg_no,
            })
        if out:
            return out
    return []


_INDEX_CACHE = []


def _index(session=None):
    if not _INDEX_CACHE:
        _INDEX_CACHE.extend(parse_state_index(
            _get(session or _session(), STATE_INDEX, what="Delhi-RERA register")
        ))
    return _INDEX_CACHE


def parse_order_register(html):
    """Delhi-RERA's complaint register, as [{complaint_no, complainant,
    respondent, decided_on}].

    It NAMES BOTH PARTIES in their own columns, which is what makes it
    searchable by promoter -- most authorities in this pipeline publish
    orders keyed only by case number. A complaint is filed against the
    promoter, so the RESPONDENT is the side to match on.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if not rows:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if "respondent" not in joined:
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        idx = {
            "complaint_no": _column("complaint"),
            "complainant": _column("complainant"),
            "respondent": _column("respondent"),
            "decided_on": _column("decision") or _column("date"),
        }
        out = []
        for texts, cells, row_element in rows[1:]:
            def _cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            if not _cell("respondent").strip():
                continue
            # ONE ROW IS ONE ORDER, NOT ONE COMPLAINT. 624 rows carry 539
            # distinct complaint numbers: a complaint decided more than once
            # gets a row per judgement, differing only in the serial number
            # and in the PDF each one links. Counting rows as complaints
            # overstates a promoter's complaint history, and dropping the
            # link throws away the only evidence of the order itself.
            order_url = ""
            for anchor in row_element.find_all("a", href=True):
                order_url = anchor["href"]
                break
            out.append({
                "complaint_no": _cell("complaint_no"),
                "complainant": _cell("complainant"),
                "respondent": _cell("respondent"),
                "decided_on": _cell("decided_on"),
                "order_url": order_url,
            })
        if out:
            return out
    return []


def distinct_complaints(rows):
    """How many COMPLAINTS a set of order rows represents.

    ONE ROW IS ONE ORDER, NOT ONE COMPLAINT, and the register publishes
    every interim order. Complaint 30/2020 -- one complainant, one
    respondent -- occupies THIRTY-FOUR rows differing only by decision date.
    Counting rows would report that promoter as having 34 complaints.

    But the complaint number alone is not the key either. Across the whole
    register 624 rows carry 539 distinct (number + parties + date)
    combinations, 68 distinct (number + parties), and only 51 distinct
    NUMBERS -- so numbers are reused between unrelated cases, and collapsing
    on the number alone would merge different people's complaints into one
    and UNDER-report. The parties are what separate them.
    """
    keys = set()
    for row in rows:
        number = " ".join((row.get("complaint_no") or "").split())
        if not number:
            continue
        keys.add((
            number.casefold(),
            " ".join((row.get("complainant") or "").split()).casefold(),
            " ".join((row.get("respondent") or "").split()).casefold(),
        ))
    return keys


_ORDER_CACHE = []


def fetch_order_register(fetcher=None):
    """The whole complaint register, cached for the process."""
    if _ORDER_CACHE and fetcher is None:
        return _ORDER_CACHE
    html = fetcher() if fetcher is not None else _get(
        _session(), ORDER_REGISTER, what="Delhi-RERA complaint register"
    )
    parsed = parse_order_register(html)
    if fetcher is None:
        _ORDER_CACHE.extend(parsed)
    return parsed


def search_orders_by_promoter(name, fetcher=None):
    """Complaints whose RESPONDENT names `name`.

    A complaint is filed against the promoter, so the respondent is the
    promoter. Matching is a normalised substring, so every hit is a
    CANDIDATE rather than a confirmed order against this entity -- the
    caller labels them that way.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return []
    return [row for row in fetch_order_register(fetcher)
            if needle in " ".join((row.get("respondent") or "").split()).casefold()]


def parse_appeal_register(html):
    """Delhi-RERA's Appellate Tribunal (REAT) order register, as
    [{appeal_no, decided_on, order_url}].

    NOT WIRED INTO acquire() -- observed and confirmed live 2026-08-26
    (505 data rows), not yet a Charter input. Kept separate from
    `parse_order_register` because the shape genuinely differs: THIS TABLE
    NAMES NO PARTY. A row is a free-text bundle of one or more appeal/CM
    numbers sharing one decision date and one judgement PDF -- e.g.
    "(CM No.73/2026 in Appeal No.108/REAT/2022) (Appeal No.197/REAT/2025)
    (Appeal No.193/REAT/2025)" is ONE row. So this register can be browsed
    and its PDFs opened, but -- unlike the complaint register -- it cannot
    be matched against a promoter's name; there is no respondent column.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if not rows:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if "appeal number" not in joined:
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        idx = {
            "appeal_no": _column("appeal", "number"),
            "decided_on": _column("date") or _column("decision"),
        }
        out = []
        for texts, cells, row_element in rows[1:]:
            def _cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            appeal_no = _cell("appeal_no")
            if not appeal_no:
                continue
            order_url = ""
            for anchor in row_element.find_all("a", href=True):
                order_url = anchor["href"]
                break
            out.append({
                "appeal_no": appeal_no,
                "decided_on": _cell("decided_on"),
                "order_url": order_url,
            })
        if out:
            return out
    return []


# --- REAT appeal register: the party names its own table doesn't carry -----
#
# NOT WIRED INTO acquire(). parse_appeal_register() above is real and
# live-verified, but it hands back exactly what the register's own columns
# state, and the register names NO party -- confirmed 2026-08-26. What it
# DOES do is link one judgement PDF per row, and REAT's own order PDFs open
# with a standard case caption naming the Appellant and Respondent, e.g.:
#
#   M/s Hiptage Infrastructure Pvt. Ltd. ..... Appellant
#   V/s
#   RERA for NCT of Delhi & Anr Respondents
#
# Confirmed live on three real orders, promoter on either side depending on
# who filed: a developer appealing an Authority order is the Appellant
# against "Real Estate Regulatory Authority" as Respondent; a homebuyer
# appealing IS the Appellant, with the developer as Respondent. So a
# promoter search must check BOTH sides.
#
# THE PDFS ARE SCANNED IMAGES, NOT TEXT -- confirmed on all three samples
# checked (0 characters of native text on every page). OCR is required, but
# only the FIRST page: the caption is always there, and a real order can run
# 20-25 pages, so OCRing the rest would cost time for nothing this needs.

# A role LABEL line, once the OCR's dotted leader is stripped, can be
# genuinely empty -- confirmed live on a real caption where the party name
# sat on its OWN line, a blank line followed, and "Appellant"/"Respondent"
# landed on a separate line preceded only by leader-dot noise. A
# same-line-only regex read that noise itself as the party NAME -- wrong,
# and worse, silent: it looked like a match, not a miss.
#
# THE NOISE IS NOT JUST DOTS. Tesseract renders a run of leader dots as a
# SINGLE ellipsis character, U+2026 ("…") -- confirmed live: a respondent
# line OCR'd as "...….Respondent" (three literal dots, THEN the
# ellipsis, then one more dot). A first version of this noise class only
# excluded U+FFFD (the replacement character, seen on a different sample)
# and left U+2026 untouched, so the strip removed one trailing "." and
# called "...…" a real name.
#
# THE LABEL CAN BE A COMPOUND ROLE, TOO. Confirmed live: an application
# WITHIN an appeal (a request to release deposited money, say) is captioned
# "Applicant/Respondent" -- the same person is the applicant on the
# application and the respondent on the underlying appeal. A pattern for
# bare "Respondent" alone matched inside that compound word, so `same_line`
# kept "Applicant/" and reported IT as the party name. `(?:\w+/)*` in front
# absorbs any such prefix into the match, so it gets excluded from
# `same_line` along with the role word itself.
_ROLE_WORD_RE = {
    "appellant": re.compile(r"(?:\w+/)*\bappellants?\b", re.I),
    "respondent": re.compile(r"(?:\w+/)*\brespondents?\b", re.I),
}
_NOISE_CHARS = r".…�\s"  # dots, ellipsis, replacement char, whitespace
# What "just leader noise, no real name" looks like on a line by itself.
_LEADER_NOISE_RE = re.compile(r"^[" + _NOISE_CHARS + r"]*$")


def _party_before_role(lines, role):
    """The party name for `role` ('appellant' or 'respondent'): the text
    before the role word on ITS OWN line if that text is more than leader
    noise, else the nearest preceding non-blank, non-noise line. Handles
    both caption shapes confirmed live: 'Name ..... Appellant' on one line,
    and 'Name' / (blank) / '�.. Appellant' split across three.
    """
    role_re = _ROLE_WORD_RE[role]
    for i, line in enumerate(lines):
        match = role_re.search(line)
        if not match:
            continue
        same_line = line[:match.start()]
        same_line = re.sub(r"[" + _NOISE_CHARS + r"]+$", "", same_line).strip()
        if len(same_line) >= 3:
            return same_line
        for j in range(i - 1, -1, -1):
            candidate = lines[j].strip()
            if candidate and not _LEADER_NOISE_RE.match(candidate):
                return candidate
        return ""
    return ""


def extract_appeal_parties(caption_text):
    """{'appellant': str, 'respondent': str} off ONE order's opening-page
    OCR text, or '' for either side no role word was found at all.

    Pure function over already-extracted text, so it's testable against a
    saved OCR capture without a network call or a Tesseract install. The
    FIRST line naming each role word wins -- the caption sits at the top of
    the page, and neither role word tends to recur in that shape later in
    an order's own prose.
    """
    lines = (caption_text or "").split("\n")
    return {
        "appellant": " ".join(_party_before_role(lines, "appellant").split()),
        "respondent": " ".join(_party_before_role(lines, "respondent").split()),
    }


def _ocr_first_page(pdf_bytes):
    """The first page's text -- native first, Tesseract OCR fallback --
    same fallback order as company_charter.py's _extract_document_text,
    scoped to one page because that is all a caption ever needs and these
    orders run up to 25 pages. Never raises: an unreadable PDF or a missing
    Tesseract install yields '', which the caller reports as unparseable
    rather than crashing a batch of hundreds.
    """
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.page_count == 0:
                return ""
            page = doc[0]
            text = page.get_text().strip()
            if text:
                return text
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return pytesseract.image_to_string(img)
    except Exception:
        return ""


def _cache_key(order_url):
    return hashlib.sha1(order_url.encode("utf-8")).hexdigest()[:20]


def fetch_appeal_order_parties(order_url, session=None, cache_dir=None):
    """Parties for ONE order PDF, as {'appellant', 'respondent', 'caption_text',
    'note'}. Caches the extracted TEXT (not the PDF itself -- these orders run
    several MB each and 481 of them is not worth keeping on disk) to
    `cache_dir`, keyed by a hash of the URL, so a re-run after an interrupted
    batch does not re-download and re-OCR everything it already read.
    """
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, _cache_key(order_url) + ".txt")
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = f.read()
            parties = extract_appeal_parties(cached)
            return {**parties, "caption_text": cached, "note": "cached"}

    session = session or requests.Session()
    try:
        response = fetch_with_retry(
            lambda: session.get(order_url, timeout=180, headers={"User-Agent": _UA}),
            what="Delhi-RERA/REAT order PDF",
        )
        response.raise_for_status()
    except Exception as e:  # noqa: BLE001 -- recorded, one bad PDF must not sink the batch
        return {"appellant": "", "respondent": "", "caption_text": "",
                "note": f"download failed: {type(e).__name__}"}

    caption_text = _ocr_first_page(response.content)
    if cache_dir:
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(caption_text)
    parties = extract_appeal_parties(caption_text)
    note = "" if (parties["appellant"] or parties["respondent"]) else (
        "OCR ran but no Appellant/Respondent caption pattern matched"
        if caption_text else "no text recovered (native or OCR)"
    )
    return {**parties, "caption_text": caption_text, "note": note}


def build_appeal_party_index(session=None, cache_dir=None, reporter=None, limit=None):
    """Every REAT appeal register row, enriched with the parties its own PDF
    names. Returns {"rows": [...], "coverage": {...}}.

    ONE PDF CAN COVER SEVERAL APPEAL NUMBERS (a batch order deciding
    multiple appeals together) -- 481 distinct order_urls across 505 rows,
    confirmed live. Fetched once per URL, not once per row, and every row
    sharing that URL gets the same parties. A row whose PDF's caption named
    a DIFFERENT combination of appellants (some multi-appeal orders do) is
    not distinguished -- the parties recorded are whichever pair sits first
    in that PDF's opening page, so a multi-appellant order's later
    appellants are a known gap, not a silent one (see 'coverage').
    """
    reporter = reporter or _NullReporter()
    session = session or requests.Session()
    session.headers.update({"User-Agent": _UA})

    html = fetch_with_retry(
        lambda: requests.get(APPEAL_REGISTER, timeout=_TIMEOUT).text,
        what="Delhi-RERA/REAT appeal register",
    )
    register_rows = parse_appeal_register(html)
    urls = []
    seen = set()
    for row in register_rows:
        url = row.get("order_url")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    if limit:
        urls = urls[:limit]

    by_url = {}
    failed = 0
    for i, url in enumerate(urls, start=1):
        result = fetch_appeal_order_parties(url, session=session, cache_dir=cache_dir)
        by_url[url] = result
        if result["note"] and result["note"] != "cached":
            failed += 1
        reporter.info(
            f"REAT order {i}/{len(urls)}: "
            + (f"appellant={result['appellant']!r} respondent={result['respondent']!r}"
               if (result["appellant"] or result["respondent"])
               else f"unparsed ({result['note']})")
        )

    enriched = []
    for row in register_rows:
        url = row.get("order_url")
        parties = by_url.get(url, {"appellant": "", "respondent": "", "note": "not processed (limit reached)"})
        enriched.append({**row, "appellant": parties["appellant"], "respondent": parties["respondent"],
                          "parties_note": parties["note"]})

    named = sum(1 for r in enriched if r["appellant"] or r["respondent"])
    reporter.ok(f"REAT appeal party index: {named}/{len(enriched)} rows carry a party name "
                f"({len(urls)} PDFs read, {failed} unparseable).")
    return {
        "rows": enriched,
        "coverage": {
            "total_rows": len(enriched), "distinct_pdfs": len(urls),
            "rows_with_a_party_name": named, "pdfs_unparseable": failed,
        },
    }


def search_appeals_by_party(name, rows):
    """Rows from build_appeal_party_index()['rows'] whose appellant OR
    respondent matches `name`. A normalised substring match, so every hit
    is a CANDIDATE -- same exposure as every other name-keyed register in
    this pipeline: these are OCR'd from a scanned document, and a promoter's
    name may be spelled differently between filings.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return []
    return [
        row for row in rows
        if needle in row.get("appellant", "").casefold()
        or needle in row.get("respondent", "").casefold()
    ]


def parse_suo_moto_register(html):
    """Delhi-RERA's own-motion (suo moto) notices and orders, as
    [{case_no, respondent_name, project_details, hearing_type, last_hearing,
    next_hearing, copy_url}].

    NOT WIRED INTO acquire(). The closest thing this portal publishes to
    "projects under investigation": proceedings the AUTHORITY opened on its
    own motion rather than ones a complainant filed. Confirmed live
    2026-08-26 with 1,797 data rows -- it DOES name a respondent/promoter
    per row, so unlike the appeal register above it is promoter-searchable
    the same way `search_orders_by_promoter` reads the complaint register.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if not rows:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if "respondent" not in joined and "promoter" not in joined:
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        idx = {
            "case_no": _column("case") or _column("complaint", "number"),
            "respondent_name": _column("respondent") or _column("promoter"),
            "project_details": _column("project"),
            "hearing_type": _column("type"),
            "last_hearing": _column("last", "hearing"),
            "next_hearing": _column("next", "hearing"),
        }
        out = []
        for texts, cells, row_element in rows[1:]:
            def _cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            if not _cell("respondent_name"):
                continue
            copy_url = ""
            for anchor in row_element.find_all("a", href=True):
                copy_url = anchor["href"]
                break
            out.append({
                "case_no": _cell("case_no"),
                "respondent_name": _cell("respondent_name"),
                "project_details": _cell("project_details"),
                "hearing_type": _cell("hearing_type"),
                "last_hearing": _cell("last_hearing"),
                "next_hearing": _cell("next_hearing"),
                "copy_url": copy_url,
            })
        if out:
            return out
    return []


def parse_execution_register(html):
    """Delhi-RERA's orders referred for EXECUTION -- non-compliance
    enforcement -- as [{execution_no, complaint_no, decree_holder,
    judgement_debtor, decided_on, next_hearing, notice_url, judgement_url}].

    NOT WIRED INTO acquire(). The closest thing this portal publishes to a
    "defaulters list", though the authority never uses that word: every row
    is an order the promoter (the Judgement Debtor) has not complied with,
    now pending enforcement. Confirmed live 2026-08-26 with 7,493 data rows.
    The Adjudicating-Officer equivalent
    (`.../courtview/ExecutionInOrderJudgementsAOInfo`) shares this exact
    column shape but returned zero data rows the same day -- a live,
    currently-empty register, not a broken route, so it is not given its
    own parser here.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = _rows_of(table)
        if not rows:
            continue
        header = [h.casefold() for h in rows[0][0]]
        joined = " | ".join(header)
        if "execution" not in joined or "judgement debtor" not in joined:
            continue

        def _column(*needles):
            for index, name in enumerate(header):
                if all(n in name for n in needles):
                    return index
            return None

        idx = {
            "execution_no": _column("execution", "number"),
            "complaint_no": _column("complaint", "number"),
            "decree_holder": _column("decree", "holder"),
            "judgement_debtor": _column("judgement", "debtor"),
            "decided_on": _column("date", "decision"),
            "next_hearing": _column("next", "hearing"),
        }
        out = []
        for texts, cells, row_element in rows[1:]:
            def _cell(key):
                index = idx.get(key)
                if index is None or index >= len(texts):
                    return ""
                return texts[index]

            if not _cell("judgement_debtor"):
                continue
            anchors = row_element.find_all("a", href=True)
            notice_url = anchors[0]["href"] if len(anchors) > 0 else ""
            judgement_url = anchors[1]["href"] if len(anchors) > 1 else ""
            out.append({
                "execution_no": _cell("execution_no"),
                "complaint_no": _cell("complaint_no"),
                "decree_holder": _cell("decree_holder"),
                "judgement_debtor": _cell("judgement_debtor"),
                "decided_on": _cell("decided_on"),
                "next_hearing": _cell("next_hearing"),
                "notice_url": notice_url,
                "judgement_url": judgement_url,
            })
        if out:
            return out
    return []


_SUOMOTO_CACHE = []
_EXECUTION_CACHE = []


def fetch_suo_moto_register(fetcher=None):
    """The whole suo-moto register, cached for the process. Same mirror as
    fetch_order_register() -- a national register, fetched whole and
    filtered locally by callers, never re-queried per name."""
    if _SUOMOTO_CACHE and fetcher is None:
        return _SUOMOTO_CACHE
    html = fetcher() if fetcher is not None else _get(
        _session(), SUOMOTO_REGISTER, what="Delhi-RERA suo-moto register"
    )
    parsed = parse_suo_moto_register(html)
    if fetcher is None:
        _SUOMOTO_CACHE.extend(parsed)
    return parsed


def fetch_execution_register(fetcher=None):
    """The whole execution register, cached for the process. Same mirror as
    fetch_order_register()."""
    if _EXECUTION_CACHE and fetcher is None:
        return _EXECUTION_CACHE
    html = fetcher() if fetcher is not None else _get(
        _session(), EXECUTION_REGISTER, what="Delhi-RERA execution register"
    )
    parsed = parse_execution_register(html)
    if fetcher is None:
        _EXECUTION_CACHE.extend(parsed)
    return parsed


class _NullReporter:
    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def ok(self, *a, **k): pass
    def choose(self, *a, **k): return None


def search_promoter_projects(name, reporter=None):
    """Projects in the Delhi register under a promoter matching `name`.

    One GET for the whole state, then a local substring match -- the same
    shape K-RERA's sweep search has, over 130 rows instead of 9,888.
    """
    needle = " ".join(str(name or "").split()).casefold()
    if not needle:
        return []
    try:
        index = _index()
    except StateFetchError:
        return []
    return [
        {"reg_no": entry["reg_no"], "project_name": entry["project_name"],
         "promoter_name": entry["promoter_name"],
         # Carried so a caller can open the project -- though on this
         # authority opening it adds nothing, since there is no per-project
         # record. fetch_project_summary says so rather than staying silent.
         "project_id": entry["reg_no"]}
        for entry in index
        if needle in " ".join((entry["promoter_name"] or "").split()).casefold()
    ]


def fetch_project_summary(project_ref, reporter=None):
    """What the register states about ONE Delhi project -- which is all
    there is.

    Deliberately returns `opened: True` with the index fields rather than
    pretending a detail page was read. The promoter name matters most: it is
    what group_sweep.enrich_projects confirms or refutes a candidate on, and
    Delhi's index carries it, so a Delhi hit CAN be confirmed even though
    nothing deeper is reachable.
    """
    reporter = reporter or _NullReporter()
    needle = str(project_ref or "").strip().casefold()
    if not needle:
        return {"opened": False, "note": "No Delhi-RERA registration number was carried."}
    try:
        index = _index()
    except StateFetchError as e:
        return {"opened": False,
                "note": f"Delhi-RERA's register could not be read ({type(e).__name__})."}

    entry = next((e for e in index if e["reg_no"].casefold() == needle), None)
    if entry is None:
        return {"opened": False,
                "note": (f"'{project_ref}' is not in Delhi-RERA's register of {len(index)} "
                         f"projects. Note that issued numbers have gaps, so this is not proof "
                         f"the registration never existed.")}
    return {
        "opened": True,
        "promoter_name": entry["promoter_name"],
        "applicant_kind": entry["applicant_kind"],
        "project_name": entry["project_name"],
        "reg_no": entry["reg_no"],
        "district": entry["district"],
        "project_type": entry["project_type"],
        "registration_valid_upto": entry["registration_valid_upto"],
        "notes": [
            "Delhi-RERA publishes no per-project record, so this is everything its register "
            "states about the project. Nothing further was withheld or missed."
        ],
    }


class DelhiAdapter:
    """StateAdapter for Delhi-RERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        session = _session()

        ctx.reporter.info("Fetching the Delhi-RERA register (all districts, one request)...")
        index = parse_state_index(_get(session, STATE_INDEX, what="Delhi-RERA register"))
        if not index:
            raise StateFetchError(
                "Delhi-RERA's register returned no projects at all this run, which means the "
                "register could not be read rather than that Delhi has no registered projects."
            )
        ctx.reporter.ok(f"{len(index)} Delhi-RERA projects indexed.")

        needle = query.strip().casefold()
        exact = [e for e in index if e["reg_no"].casefold() == needle]
        matches = exact or [e for e in index if needle in e["project_name"].casefold()]
        if not matches:
            raise StateResolutionError(
                f"No Delhi-RERA project found matching '{query}' in a register of "
                f"{len(index)} projects. Delhi's register is small -- 130 projects for the "
                f"whole NCT -- so an absence here is weak evidence: a genuine Delhi project "
                f"may fall below the registration threshold entirely."
            )
        chosen = matches[0]
        if len(matches) > 1:
            ctx.reporter.warn(
                f"{len(matches)} Delhi-RERA projects matched {query!r}; using "
                f"{chosen['reg_no']} ({chosen['project_name']})."
            )

        registration_number = chosen["reg_no"]
        reg_no = storage_key(registration_number)
        ctx.reporter.ok(f"Resolved: {registration_number} | {chosen['project_name']}")

        project_out_dir = os.path.join(ctx.output_dir, reg_no)
        raw_dir = os.path.join(project_out_dir, "raw")
        os.makedirs(raw_dir, exist_ok=True)
        if ctx.on_resolved is not None:
            ctx.on_resolved(reg_no)
            os.makedirs(raw_dir, exist_ok=True)

        promoter_name = chosen["promoter_name"]
        complaints, complaint_notes = self._complaints(promoter_name, ctx)

        disclaimer_notes = []
        if chosen.get("has_disclaimer"):
            ctx.reporter.warn(
                "Delhi-RERA has marked this registration with a disclaimer marker."
            )
            disclaimer_notes.append(
                "Delhi-RERA prints a red disclaimer marker against this registration on its own "
                "register, beside both the registration number and the project name. The "
                "authority does not publish what the marker means for a given project -- the "
                "only disclaimer text on the page is a site-wide notice about data migration -- "
                "so it is recorded here as stated and its meaning should be confirmed with the "
                "authority. It is not being read as a finding either way."
            )

        category_data = {
            "projects": {
                "projectName": chosen["project_name"],
                "projectRegistartionNo": registration_number,
                "district": chosen["district"],
                "projectTypeName": chosen["project_type"],
                "registrationValidUpto": chosen["registration_valid_upto"],
            },
            "partners": {"promoterDetails": {
                "promoterName": promoter_name,
                "applicantKind": chosen["applicant_kind"],
            }},
            # Not published by this authority -- None, never {} or 0, so
            # nothing downstream can read an absence as a clean record.
            "professionals": None,
            "spocs": None,
            "sro_details": None,
            "past_experiences": None,
            "documents": None,
            "complaints": complaints,
            "appeals": None,
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=registration_number,
            project_id=registration_number,
            detail_url=STATE_INDEX,
            category_data=category_data,
            documents_manifest=[],
            documents_dir=None,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=promoter_name,
            promoter_portfolio=self._promoter_portfolio(index, chosen, ctx),
            raw_record=chosen,
            auth_source="none",
            categories_not_published={
                "professionals", "spocs", "sro_details", "past_experiences",
                "documents", "appeals",
            },
            notes=list(_AUTHORITY_NOTES) + disclaimer_notes + complaint_notes,
        )

    # -- complaints --------------------------------------------------------
    def _complaints(self, promoter_name, ctx):
        """Delhi-RERA's complaint register, matched on RESPONDENT.

        A count that could not be read is None, never 0. The register names
        parties, so a match is a POSSIBLE complaint against this promoter
        rather than a confirmed one -- names repeat.
        """
        try:
            rows = search_orders_by_promoter(promoter_name)
        except Exception:
            return ({"total_complaints_count": None,
                     "source": "Delhi-RERA complaint register"},
                    ["Delhi-RERA's complaint register could not be read this run, so this "
                     "promoter's complaint history is UNKNOWN. It must not be read as zero."])
        complaints = distinct_complaints(rows)
        if rows:
            ctx.reporter.warn(
                f"Delhi-RERA's complaint register names {len(complaints)} matching "
                f"complaint(s) against {len(rows)} published order(s)."
            )
            note = (
                f"{len(complaints)} complaint(s) on Delhi-RERA's public register name a "
                f"respondent matching this promoter's name, carrying {len(rows)} published "
                f"order(s) between them. The register is keyed on names rather than any "
                f"identifier, so these are possible matches to confirm, not confirmed "
                f"complaints against this entity."
            )
        else:
            ctx.reporter.ok("Delhi-RERA's complaint register names no matching respondent.")
            note = None
        return ({"total_complaints_count": len(complaints),
                 "total_orders_published": len(rows),
                 "source": "Delhi-RERA complaint register (name match on respondent)",
                 "matches": rows},
                [note] if note else [])

    # -- portfolio ---------------------------------------------------------
    def _promoter_portfolio(self, index, chosen, ctx):
        """Other Delhi projects under the same promoter.

        An EXACT normalised name match, not a substring: the register is
        small and a loose match here would fold an unrelated promoter's
        projects into this one's track record.
        """
        target = " ".join((chosen["promoter_name"] or "").split()).casefold()
        if not target:
            return None
        others = [
            {"reg_no": e["reg_no"], "project_name": e["project_name"],
             "district": e["district"], "project_type": e["project_type"],
             "registration_valid_upto": e["registration_valid_upto"]}
            for e in index
            if " ".join((e["promoter_name"] or "").split()).casefold() == target
        ]
        ctx.reporter.info(f"Delhi-RERA portfolio: {len(others)} project(s) under this promoter.")
        return {
            "promoter_name": chosen["promoter_name"],
            "projects": others,
            "totals": {"total_projects": len(others)},
            "source": "Delhi-RERA registered-projects register",
            "notes": [
                "Matched on the promoter's name exactly as the register prints it. Delhi-RERA "
                "publishes no promoter identifier, so a promoter who filed under a differently "
                "spelled name would not be joined to these."
            ],
        }


ADAPTER = DelhiAdapter()

"""
K-RERA acquisition adapter.

Server-rendered HTML rather than JSON, so this module parses tables where
the MahaRERA and Gujarat adapters parse payloads. That difference is
entirely contained here -- everything above the seam sees the same
category_data / documents_manifest contract.

FLOW (all confirmed live against PRM/KA/RERA/1251/446/PR/040826/008858)
-----------------------------------------------------------------------
  GET  /viewAllProjects
       6.3 MB page whose real payload is the client-side search index:
       four parallel JS arrays pushed in lockstep --
         applicationNameList   ACK number
         applicationNameList2  PRM registration number
         applicationNameList3  project name
         applicationNameList4  promoter name
       8,887 approved projects. Parsed once, it gives both a reg-no lookup
       AND a promoter-to-projects map for the whole state.

  POST /projectViewDetails  {regNo | appNo, btn1: "Search"}
       -> one summary row: status, district, taluk, type, approved-on,
          proposed completion, COVID / Section 6 / further extensions, and
          an <a id=NNNN> whose id is the internal application key.

  POST /projectDetails          {action: <that id>}   -> 48 tables
  POST /projectComplaintDetails {action: <that id>}   -> complaints
  GET  /certificate?CER_NO=<regNo>                    -> registration cert
  GET  /download_jc?DOC_ID=<token>                    -> a document

WHY THE INDEX IS PARSED WITH A REGEX AND NOT A DOM WALK: the arrays are
JavaScript source inside a <script> block, not markup. BeautifulSoup sees
one text node. The four .push() calls appear in a fixed repeating order per
project, which is what the pairing below relies on -- and
_parse_search_index asserts the four lists came back the same length rather
than zipping mismatched data.
"""

import json
import os
import re
import urllib.parse

import requests
from bs4 import BeautifulSoup

from .base import AcquisitionResult, StateResolutionError, fetch_with_retry, storage_key
from .karnataka import (
    ORDERS_PAGE,
    INTERIM_ORDERS_PAGE,
    PROJECT_ORDERS_PAGE,
    AO_ORDERS_PAGE,
    COMPLAINT_DETAILS_PAGE,
    COMPLAINT_POST,
    COMPLAINT_REPORT,
    DETAIL_POST,
    DOWNLOAD_URL,
    PROFILE,
    SEARCH_PAGE,
    SEARCH_POST,
)

_TIMEOUT = 90
_UA = "RERA-Scrapper-DueDiligence/1.0 (research tool, low-volume)"

# The four parallel arrays, in the order the page pushes them.
_INDEX_FIELDS = ("ack_no", "reg_no", "project_name", "promoter_name")
_PUSH_RE = re.compile(
    r"applicationNameList(?P<idx>[234]?)\s*\.\s*push\('(?P<value>(?:[^'\\]|\\.)*)'\)",
    re.DOTALL,
)


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": _UA, "Referer": SEARCH_PAGE})
    return s


def parse_search_index(html: str) -> list:
    """The whole-state index, as [{ack_no, reg_no, project_name,
    promoter_name}].

    Raises rather than returning a partial index if the four arrays come
    back at different lengths -- zipping mismatched lists would silently
    attach the wrong promoter to a project, which is a far worse outcome
    than a loud failure."""
    buckets = {name: [] for name in _INDEX_FIELDS}
    order = {"": "ack_no", "2": "reg_no", "3": "project_name", "4": "promoter_name"}
    for match in _PUSH_RE.finditer(html):
        field = order.get(match.group("idx"))
        if field:
            buckets[field].append(match.group("value").replace("\\'", "'").strip())

    lengths = {name: len(values) for name, values in buckets.items()}
    if not any(lengths.values()):
        return []
    if len(set(lengths.values())) != 1:
        raise StateResolutionError(
            f"K-RERA search index arrays disagree in length ({lengths}); refusing to "
            f"pair them, since a mismatch would attach the wrong promoter to a project."
        )
    return [dict(zip(_INDEX_FIELDS, row)) for row in zip(*(buckets[f] for f in _INDEX_FIELDS))]


_ORDERS_CACHE = []


def fetch_order_index(fetcher=None) -> list:
    """K-RERA's whole order-search register, as
    [{ack_no, reg_no, project_name, promoter_name}].

    Same four parallel arrays as the project index, so it reuses
    parse_search_index -- including its refusal to zip mismatched lists.
    Here `ack_no` is the complaint/application number the order was made
    under (e.g. "00862/2025") and `reg_no` is the portal's own TMP
    reference, not a RERA registration number.

    One request for the entire state, so it is cached for the process.
    """
    global _ORDERS_CACHE
    if _ORDERS_CACHE:
        return _ORDERS_CACHE
    if fetcher is None:
        session = _session()
        html = _fetch_complete(session, ORDERS_PAGE, "K-RERA order register")
    else:
        html = fetcher()
    _ORDERS_CACHE = parse_search_index(html)
    return _ORDERS_CACHE


# K-RERA's order and complaint registers, and the column each one names the
# promoter in. Some say PROMOTER NAME, some say RESPONDENT NAME -- a
# complaint is filed against the promoter, so the respondent IS the
# promoter in the adjudication registers.
_ORDER_REGISTERS = (
    ("Authority orders", PROJECT_ORDERS_PAGE),
    ("Adjudicating Officer orders", AO_ORDERS_PAGE),
    ("Interim orders", INTERIM_ORDERS_PAGE),
    ("Complaints under process", COMPLAINT_DETAILS_PAGE),
)
_PROMOTER_COLUMNS = ("promoter name", "respondent name")
_REGISTER_CACHE = {}


def _looks_complete(html) -> bool:
    """Did the whole page arrive?

    THESE PAGES TRUNCATE SILENTLY, and a short read is indistinguishable
    from a smaller register unless this is checked. Confirmed 2026-08-21:
    the 10.4 MB authority-orders page came back once with 2 of its 3
    tables, dropping the PENALTY register entirely -- so a promoter with
    penalties would have been reported as having none. The AO-orders page
    truncated mid-attribute on another fetch. Every one of these pages
    ends with a closing </html>; a body that does not is a partial read,
    never a smaller register.
    """
    return str(html or "").rstrip().endswith("</html>")


def _fetch_complete(session, url, what, attempts=3):
    """Fetch until the page arrives whole. Raises rather than returning a
    truncated body -- a partial register must never look like a short one."""
    last = ""
    for _ in range(attempts):
        last = fetch_with_retry(
            lambda: session.get(url, timeout=_TIMEOUT).text, what=what,
        )
        if _looks_complete(last):
            return last
    raise StateResolutionError(
        f"{what} came back truncated on every attempt ({len(last)} chars, no closing "
        f"</html>). Refusing it: a partial register reads as a smaller one, and this "
        f"page has already been seen to drop its penalty table that way."
    )


def _header_key(text):
    return " ".join(str(text or "").split()).lower()


def parse_register_tables(html: str) -> list:
    """Every headed table on a register page, as [{headers, rows}].

    These pages carry MORE THAN ONE register in one document -- the
    authority-orders page holds complaint orders, project orders and a
    PENALTY table with violation sections and amounts, each with its own
    header row. Reading only the first would silently drop the penalties,
    which are the most consequential rows K-RERA publishes.
    """
    from bs4 import BeautifulSoup

    tables = []
    for table in BeautifulSoup(html or "", "html.parser").find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [_header_key(c.get_text(" ", strip=True))
                   for c in rows[0].find_all(["th", "td"])]
        if not any(headers):
            continue
        parsed = []
        for row in rows[1:]:
            cells = [" ".join(c.get_text(" ", strip=True).split())
                     for c in row.find_all(["td", "th"])]
            if not any(cells):
                continue
            parsed.append({headers[i]: cells[i]
                           for i in range(min(len(headers), len(cells)))})
        if parsed:
            tables.append({"headers": headers, "rows": parsed})
    return tables


def fetch_order_registers(fetcher=None) -> dict:
    """All of K-RERA's order and complaint registers, keyed by name.

    Roughly 22 MB across five requests, so it is cached for the process
    and should be treated as a once-per-run cost. A register that fails to
    load is OMITTED FROM THE RESULT rather than returned empty, so a
    caller can tell "no orders in that register" from "that register did
    not load" -- the distinction the whole litigation section rests on.
    """
    if _REGISTER_CACHE and fetcher is None:
        return _REGISTER_CACHE
    session = _session()
    out = {}
    for label, url in _ORDER_REGISTERS:
        try:
            html = fetcher(url) if fetcher else _fetch_complete(
                session, url, f"K-RERA {label}")
            out[label] = parse_register_tables(html)
        except Exception:
            continue  # omitted, never an empty register
    if fetcher is None:
        _REGISTER_CACHE.update(out)
    return out


def search_registers_by_promoter(name: str, fetcher=None) -> list:
    """Rows from every K-RERA register naming this promoter.

    Matched on a NAME substring, because K-RERA publishes no company
    identity number to join on -- so each row is a candidate, exactly as
    the project sweep and the affiliate graph already assume.
    """
    needle = " ".join(str(name or "").upper().split())
    if not needle:
        return []
    hits = []
    for label, tables in fetch_order_registers(fetcher).items():
        for table in tables:
            columns = [c for c in _PROMOTER_COLUMNS if c in table["headers"]]
            if not columns:
                continue
            for row in table["rows"]:
                for column in columns:
                    value = " ".join((row.get(column) or "").upper().split())
                    if needle and needle in value:
                        hits.append({
                            "register": label,
                            "promoter_name": row.get(column) or "",
                            "complaint_no": row.get("complaint no") or row.get("project no")
                            or row.get("registration number") or "",
                            "order_date": row.get("order date") or row.get("k-rera order date")
                            or row.get("penalty order date") or row.get("complaint date") or "",
                            "project_name": row.get("project name") or "",
                            "district": row.get("district") or "",
                            "detail": row.get("relief sought") or row.get("nature of disposal")
                            or row.get("violation") or row.get("order category")
                            or row.get("complaint on") or "",
                            "penalty_amount": row.get("penalty amount") or "",
                        })
                        break
    return hits


def order_register_coverage(fetcher=None) -> dict:
    """Which of K-RERA's registers actually loaded this pass.

    A register that failed is MISSING, not empty. Without this the section
    would show four registers' worth of silence and one register's worth
    of orders as though they were the same thing.
    """
    loaded = sorted(fetch_order_registers(fetcher))
    expected = [label for label, _ in _ORDER_REGISTERS]
    return {"loaded": loaded,
            "missing": [label for label in expected if label not in loaded]}


def search_all_orders_by_promoter(name: str, fetcher=None) -> list:
    """Every K-RERA row naming this promoter, across all its registers.

    The order-search index and the four order/complaint registers are
    different documents on the portal, and a promoter can appear in one
    and not the others.
    """
    hits = list(search_registers_by_promoter(name, fetcher))
    for row in search_orders_by_promoter(name):
        hits.append({
            "register": "Order search index",
            "promoter_name": row.get("promoter_name") or "",
            "complaint_no": row.get("ack_no") or "",
            "order_date": "",
            "project_name": row.get("project_name") or "",
            "district": "",
            "detail": "",
            "penalty_amount": "",
        })
    return hits


def search_orders_by_promoter(name: str, fetcher=None) -> list:
    """Entries in the order register whose PROMOTER name contains `name`.

    A substring match on a name, so every row is a candidate: K-RERA
    publishes no company identity number to join on, exactly as the RERA
    sweep and the affiliate graph already have to assume.
    """
    needle = " ".join(str(name or "").upper().split())
    if not needle:
        return []
    return [row for row in fetch_order_index(fetcher)
            if needle in " ".join((row.get("promoter_name") or "").upper().split())]


def _table_to_rows(table) -> list:
    """A table as list-of-lists of cell text. Header detection is left to
    the caller -- K-RERA mixes header-in-<th> and header-in-first-<tr>."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if any(cells):
            rows.append(cells)
    return rows


def _labelled_rows(rows: list) -> list:
    """Rows keyed by the first row's cells, when it looks like a header."""
    if len(rows) < 2:
        return []
    header = rows[0]
    out = []
    for row in rows[1:]:
        if len(row) != len(header):
            continue
        out.append({header[i]: row[i] for i in range(len(header))})
    return out


def _find_table_by_header(tables: list, *needles) -> list:
    """First table whose header row contains all `needles` (case-insensitive
    substring). Matching on CONTENT rather than table index because the
    index shifts with how many towers a project has -- block tables repeat
    per tower, so table[17] is the CA list for one project and something
    else entirely for the next."""
    for rows in tables:
        if not rows:
            continue
        header = " | ".join(rows[0]).lower()
        if all(n.lower() in header for n in needles):
            return rows
    return []


_INDEX_CACHE = []


def search_promoter_projects(name, reporter=None):
    """Projects in the K-RERA state index under a promoter matching `name`.

    K-RERA embeds the entire state register client-side, so this is one
    request for the whole state. Matching is a normalised SUBSTRING here,
    unlike the exact match _promoter_portfolio uses: a sweep is looking for
    candidate group projects to confirm, not building a single promoter's
    track record, and the caller labels every hit as unconfirmed.
    """
    # Cached for the process: the index is the WHOLE STATE in one page, and
    # a group sweep asks about dozens of entities. Re-fetching a large page
    # per entity would turn one request into dozens for identical bytes.
    if not _INDEX_CACHE:
        session = _session()
        _INDEX_CACHE.extend(parse_search_index(session.get(SEARCH_PAGE, timeout=_TIMEOUT).text))
    index = _INDEX_CACHE
    needle = " ".join((name or "").split()).casefold()
    if not needle:
        return []
    return [
        {"reg_no": e["reg_no"], "project_name": e["project_name"],
         "promoter_name": e["promoter_name"]}
        for e in index
        if needle in " ".join((e["promoter_name"] or "").split()).casefold()
    ]


class KarnatakaAdapter:
    """StateAdapter for K-RERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        session = _session()
        query = query.strip()

        ctx.reporter.info("Loading the K-RERA project index...")
        # The single most failure-prone request in this adapter: a 6.3 MB
        # response from a portal that is not highly available. One dropped
        # connection used to kill the whole run before anything was written,
        # and escaped main.py as a raw requests traceback.
        index_html = fetch_with_retry(
            lambda: session.get(SEARCH_PAGE, timeout=_TIMEOUT).text,
            what="K-RERA project index", reporter=ctx.reporter,
        )
        index = parse_search_index(index_html)
        ctx.reporter.info(f"K-RERA index: {len(index)} registered project(s).")

        entry = self._resolve(index, query, ctx)
        reg_no_real = entry["reg_no"] or entry["ack_no"]
        reg_no = storage_key(reg_no_real)
        ctx.reporter.ok(f"Resolved: {reg_no_real} | {entry['project_name']}")

        prior = ctx.prior or {}
        if ctx.on_resolved is not None:
            prior = ctx.on_resolved(reg_no) or {}

        project_out_dir = os.path.join(ctx.output_dir, reg_no)
        raw_dir = os.path.join(project_out_dir, "raw")
        documents_dir = os.path.join(project_out_dir, "documents")
        os.makedirs(raw_dir, exist_ok=True)

        # --- summary row + the internal application id ------------------
        summary_html = fetch_with_retry(
            lambda: session.post(
                SEARCH_POST,
                data={"regNo": entry["reg_no"], "appNo": entry["ack_no"], "btn1": "Search"},
                timeout=_TIMEOUT,
            ).text,
            what="K-RERA project summary", reporter=ctx.reporter,
        )
        summary, action_id = self._parse_summary(summary_html)
        if not action_id:
            raise StateResolutionError(
                f"K-RERA returned no detail handle for '{reg_no_real}' -- the project is in "
                f"the index but its detail view could not be opened."
            )

        # --- detail + complaints ----------------------------------------
        ctx.reporter.info("Fetching K-RERA project record...")
        detail_html = fetch_with_retry(
            lambda: session.post(DETAIL_POST, data={"action": action_id}, timeout=_TIMEOUT).text,
            what="K-RERA project detail", reporter=ctx.reporter,
        )
        complaint_html = fetch_with_retry(
            lambda: session.post(COMPLAINT_POST, data={"action": action_id}, timeout=_TIMEOUT).text,
            what="K-RERA complaint page", reporter=ctx.reporter,
        )

        detail_tables = [_table_to_rows(t) for t in BeautifulSoup(detail_html, "html.parser").find_all("table")]
        parsed = self._parse_detail(detail_tables, summary)

        complaint_count, complaint_row = self._complaint_count(session, entry["project_name"], ctx)
        complaints_payload = {
            "total_complaints_count": complaint_count,
            "source": "K-RERA state-wide complaint register (/projectComplaintReport)",
            "register_row": complaint_row,
        }
        notes = []
        if complaint_count is None:
            notes.append(
                "K-RERA's complaint register could not be read this run, so this project's "
                "complaint count is UNKNOWN -- it must not be read as zero."
            )
        elif complaint_count:
            ctx.reporter.warn(f"K-RERA register lists {complaint_count} complaint(s) for this project.")
        else:
            ctx.reporter.ok("K-RERA complaint register lists no complaint for this project.")
        category_data = {
            "projects": parsed["project"],
            "partners": {"promoterDetails": {
                "promoterName": entry["promoter_name"],
                "partners": parsed["partners"],
                "landOwners": parsed["land_owners"],
            }},
            "professionals": parsed["professionals"],
            "spocs": None,
            "sro_details": None,
            "past_experiences": None,
            "documents": parsed["documents"],
            "complaints": complaints_payload,
            "appeals": None,
            "cost_details": parsed["costs"],
            "extensions": parsed["extensions"],
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        # --- documents ---------------------------------------------------
        documents_manifest = self._download_documents(session, detail_html, documents_dir, ctx)

        # --- promoter portfolio, straight off the state index ------------
        portfolio = self._promoter_portfolio(index, entry, ctx)

        notes.append(
            "K-RERA does not publish a separate appeals register through this interface; "
            "complaints are published per project and are captured above."
        )

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=reg_no_real,
            project_id=str(action_id),
            detail_url=DETAIL_POST,
            category_data=category_data,
            documents_manifest=documents_manifest,
            documents_dir=documents_dir,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=entry["promoter_name"],
            promoter_portfolio=portfolio,
            raw_record=summary or None,
            auth_source="none",
            categories_not_published={"spocs", "sro_details", "past_experiences", "appeals"},
            notes=notes,
        )

    # -- resolve ---------------------------------------------------------
    def _resolve(self, index, query, ctx):
        exact = [
            e for e in index
            if query.casefold() in (e["reg_no"].casefold(), e["ack_no"].casefold())
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            matches = exact
        else:
            needle = query.casefold()
            matches = [
                e for e in index
                if needle in e["project_name"].casefold() or needle in e["promoter_name"].casefold()
            ]
        if not matches:
            raise StateResolutionError(f"No K-RERA project found matching '{query}'.")
        if len(matches) == 1:
            return matches[0]

        chosen = ctx.reporter.choose(
            f"{len(matches)} K-RERA projects match '{query}'. Which one?",
            [f"{m['reg_no']} | {m['project_name']} | {m['promoter_name']}" for m in matches[:40]],
        )
        if chosen is None:
            raise StateResolutionError(
                f"{len(matches)} K-RERA projects match '{query}' and the choice could not be "
                f"made non-interactively. Re-run with the exact registration number."
            )
        return matches[chosen]

    # -- parse -----------------------------------------------------------
    def _parse_summary(self, html):
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            return {}, None
        rows = _table_to_rows(table)
        summary = (_labelled_rows(rows) or [{}])[0]
        anchor = table.find("a", id=True)
        return summary, (anchor.get("id") if anchor else None)

    def _parse_detail(self, tables, summary):
        """Pulls the tables worth keeping, matched BY HEADER CONTENT.

        Index-based lookup would be wrong: the block/tower tables repeat
        once per tower, so a five-tower project shifts every later table by
        four positions relative to a one-tower project."""
        partners = _labelled_rows(_find_table_by_header(tables, "partner name"))
        land_owners = _labelled_rows(_find_table_by_header(tables, "land owner name"))
        inventory = _labelled_rows(_find_table_by_header(tables, "type of inventory"))
        costs_incurred = _labelled_rows(_find_table_by_header(tables, "cost incurred"))
        costs_estimated = _labelled_rows(_find_table_by_header(tables, "estimated cost"))
        extensions = _labelled_rows(_find_table_by_header(tables, "registration/extensions"))

        professionals = []
        for needle, type_name in (
            ("chartered accountant nam", "Chartered Accountant"),
            ("engineer name", "Engineer"),
            ("architect name", "Architect"),
            ("contractor name", "Contractor"),
        ):
            for row in _labelled_rows(_find_table_by_header(tables, needle)):
                merged = dict(row)
                merged["professionalTypeName"] = type_name
                name_key = next((k for k in row if "name" in k.lower()), None)
                merged["entityCompanyName"] = row.get(name_key, "") if name_key else ""
                professionals.append(merged)

        return {
            "project": {**summary, "inventory": inventory},
            "partners": partners,
            "land_owners": land_owners,
            "professionals": professionals,
            "documents": {"tables": [t for t in tables if t and "document name" in " | ".join(t[0]).lower()]},
            "costs": {"incurred": costs_incurred, "estimated": costs_estimated},
            "extensions": extensions,
        }

    def _complaint_count(self, session, project_name, ctx):
        """Complaint count from K-RERA's STATE-WIDE register.

        DO NOT use /projectComplaintDetails for this. Confirmed live: for
        ADARSH GREENS PHASE 1 -- which the state register lists with TWELVE
        complaints -- that per-project page returns only a Land Owner table
        and no complaint data whatsoever. Parsing it would have reported a
        FALSE CLEAN RECORD, which is the single worst failure this pipeline
        can produce.

        /projectComplaintReport lists only projects that HAVE complaints
        (2,426 of 9,888), so absence from it is a genuine zero rather than a
        lookup miss. Returns (count, matched_row) with count None when the
        register itself could not be read -- None and 0 must stay
        distinguishable, since one means 'unknown' and the other 'clean'."""
        try:
            html = session.get(COMPLAINT_REPORT, timeout=_TIMEOUT).text
        except Exception as e:
            ctx.reporter.warn(f"K-RERA complaint register unreadable ({e}) -- count left unknown.")
            return None, None

        tables = [_table_to_rows(t) for t in BeautifulSoup(html, "html.parser").find_all("table")]
        rows = _find_table_by_header(tables, "no of complaints")
        if not rows:
            ctx.reporter.warn("K-RERA complaint register had no recognisable table -- count left unknown.")
            return None, None

        target = " ".join((project_name or "").split()).casefold()
        for row in _labelled_rows(rows):
            name = next((v for k, v in row.items() if "project name" in k.lower()), "")
            if " ".join(name.split()).casefold() == target:
                raw = next((v for k, v in row.items() if "no of complaints" in k.lower()), "")
                digits = re.sub(r"[^\d]", "", raw or "")
                return (int(digits) if digits else 0), row
        return 0, None

    # -- documents -------------------------------------------------------
    def _download_documents(self, session, detail_html, documents_dir, ctx):
        soup = BeautifulSoup(detail_html, "html.parser")
        anchors = [
            a for a in soup.find_all("a", href=True)
            if "download_jc" in a["href"] or a["href"].startswith("/certificate")
        ]
        if not anchors:
            ctx.reporter.warn("K-RERA lists no downloadable documents for this project.")
            return []

        os.makedirs(documents_dir, exist_ok=True)
        ctx.reporter.info(f"Downloading {len(anchors)} K-RERA document(s)...")
        manifest, used_names = [], {}
        for anchor in anchors:
            href = anchor["href"]
            label = anchor.get_text(" ", strip=True) or "Document"
            url = href if href.startswith("http") else PROFILE.portal_domains[0].join(("https://", href))
            url = f"https://{PROFILE.portal_domains[0]}{href}" if href.startswith("/") else href
            doc_id = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("DOC_ID", [None])[0]
            row = {
                "label": label,
                "original_url": url,
                "saved_filename": None,
                "status": "failed",
                "method": "krera-download_jc",
                "document_id": doc_id,
                "source_filename": label,
            }
            try:
                resp = session.get(url, timeout=_TIMEOUT)
                if resp.status_code == 200 and not resp.content:
                    # Confirmed live: K-RERA lists documents whose DOC_ID
                    # resolves to an EMPTY body (200, 0 bytes). The document
                    # is declared on the record but not actually stored, so
                    # the honest status is "listed, not held" -- not a
                    # download failure the operator could retry.
                    row["status"] = "not held by the portal (listed, 0 bytes served)"
                    manifest.append(row)
                    continue
                if resp.status_code == 200 and resp.content and not resp.content.lstrip().startswith(b"<"):
                    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", label).strip() or "document"
                    if "." not in safe[-6:]:
                        safe += _extension_for(resp.headers.get("Content-Type", ""))
                    # Same collision discipline as the Gujarat adapter --
                    # K-RERA labels repeat across annexures.
                    if safe in used_names:
                        stem, ext = os.path.splitext(safe)
                        used_names[safe] += 1
                        safe = f"{stem} ({used_names[safe]}){ext}"
                    else:
                        used_names[safe] = 1
                    with open(os.path.join(documents_dir, safe), "wb") as f:
                        f.write(resp.content)
                    row["saved_filename"] = safe
                    row["status"] = "downloaded"
                else:
                    row["status"] = "failed (portal returned a page, not a file)"

            except Exception as e:
                row["status"] = f"failed ({e})"
            manifest.append(row)

        got = sum(1 for r in manifest if r["status"] == "downloaded")
        ctx.reporter.ok(f"{got}/{len(manifest)} K-RERA document(s) retrieved.")
        return manifest

    # -- promoter portfolio ----------------------------------------------
    def _promoter_portfolio(self, index, entry, ctx):
        """Every project in the state index under the same promoter name.

        Exact normalised-name match, never fuzzy: two promoters with similar
        names are different legal persons, and merging them would invent a
        track record. Same discipline company_charter.find_group_companies_
        by_cin applies to related parties.

        Returns the state-neutral shape promoter_portfolio.build_promoter_
        portfolio produces, so report.py and the Charter consume it
        unchanged -- with complaint/appeal counts left None, because this
        index does not carry them and a zero would read as 'clean'."""
        target = " ".join((entry["promoter_name"] or "").split()).casefold()
        if not target:
            return None
        siblings = [
            e for e in index
            if " ".join((e["promoter_name"] or "").split()).casefold() == target
        ]
        projects = [
            {
                "reg_no": s["reg_no"],
                "project_id": None,
                "project_name": s["project_name"],
                "status": None,
                "district": None,
                "complaint_count": None,
                "appeal_count": None,
                "is_lapsed": None,
            }
            for s in siblings
        ]
        ctx.reporter.info(
            f"Promoter portfolio: {len(projects)} K-RERA project(s) under '{entry['promoter_name']}'."
        )
        return {
            "promoter_name_searched": entry["promoter_name"],
            "search_match_count": len(siblings),
            "projects_analyzed": len(projects),
            "truncated": False,
            "projects": projects,
            "totals": {
                "total_projects": len(projects),
                "total_complaints": None,
                "total_appeals": None,
                "projects_with_complaints": None,
                "projects_with_appeals": None,
                "lapsed_or_flagged_count": None,
            },
            "limitations": [
                "Built from K-RERA's own client-side project index, which lists every "
                "registered project with its promoter name. Projects are matched on an EXACT "
                "normalised promoter name -- a promoter registering under two spellings would "
                "appear as two promoters, and no fuzzy matching is applied because that would "
                "invent a track record rather than find one.",
                "The index carries no complaint, appeal or status data, so those counts are "
                "left unset rather than zero -- an unknown count must not read as a clean record.",
            ],
        }


def _extension_for(content_type: str) -> str:
    ct = (content_type or "").lower()
    for needle, ext in (("pdf", ".pdf"), ("jpeg", ".jpg"), ("jpg", ".jpg"), ("png", ".png")):
        if needle in ct:
            return ext
    return ".bin"


ADAPTER = KarnatakaAdapter()

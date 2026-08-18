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

from .base import AcquisitionResult, StateResolutionError, storage_key
from .karnataka import (
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


class KarnatakaAdapter:
    """StateAdapter for K-RERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        session = _session()
        query = query.strip()

        ctx.reporter.info("Loading the K-RERA project index...")
        index_html = session.get(SEARCH_PAGE, timeout=_TIMEOUT).text
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
        summary_html = session.post(
            SEARCH_POST,
            data={"regNo": entry["reg_no"], "appNo": entry["ack_no"], "btn1": "Search"},
            timeout=_TIMEOUT,
        ).text
        summary, action_id = self._parse_summary(summary_html)
        if not action_id:
            raise StateResolutionError(
                f"K-RERA returned no detail handle for '{reg_no_real}' -- the project is in "
                f"the index but its detail view could not be opened."
            )

        # --- detail + complaints ----------------------------------------
        ctx.reporter.info("Fetching K-RERA project record...")
        detail_html = session.post(DETAIL_POST, data={"action": action_id}, timeout=_TIMEOUT).text
        complaint_html = session.post(COMPLAINT_POST, data={"action": action_id}, timeout=_TIMEOUT).text

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

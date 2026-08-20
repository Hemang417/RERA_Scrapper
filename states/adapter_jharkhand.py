"""
StateAdapter for Jharkhand / JHARERA.

See states/jharkhand.py for what this portal publishes and why it was built
ahead of much larger registers.

TWO PARSING RULES THIS FILE FOLLOWS, both learned the hard way elsewhere in
this package:

  1. TABLES ARE FOUND BY THEIR HEADER, NEVER BY INDEX. JHARERA's project
     page emits between 17 and 20 tables depending on how many blocks,
     bank accounts and contractors a project has, and the same logical
     table therefore sits at a different position on different projects.
     Karnataka's adapter learned this when block tables repeated per tower.

  2. AN ABSENCE IS ONLY REPORTED WHERE THE TABLE EXISTS AND IS EMPTY.
     JHARERA renders the litigation table with headers and zero rows for a
     project with no litigation, which is a genuine clean check. If the
     table is missing entirely, that is NOT a clean check, and this adapter
     reports it as unknown. Karnataka's per-project complaint page produced
     exactly that false clean record once already.

The portal paginates its registers ten rows at a time, so resolving a
registration number goes through the SEARCH box (one request) rather than
walking 120 pages.
"""

import json
import os
import re

import requests
from bs4 import BeautifulSoup

import group_entities

from .base import (
    AcquisitionResult,
    StateFetchError,
    StateResolutionError,
    fetch_with_retry,
    safe_document_filename,
    storage_key,
)
from .jharkhand import (
    BASE_URL,
    DISPOSED_COMPLAINTS,
    PROFILE,
    PROJECT_DETAIL,
    PROJECT_LIST,
    REJECTED_LIST,
    SURRENDERED_LIST,
)

_TIMEOUT = 60
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_PROFILE_ID_RE = re.compile(r"/Home/ViewProjectProfile/(\d+)", re.I)
_PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")

# Above this, a brand-level search result is a crowd rather than a group.
_MAX_BRAND_MATCHES = 6

_AUTHORITY_NOTES = [
    "JHARERA does not publish a single point of contact register or sub-registrar office "
    "details for a project, so those are not available from this authority. Their absence "
    "here does not mean none exist.",
]


def _session():
    session = requests.Session()
    session.headers.update(_HEADERS)
    # The certificate chain on this host is incomplete for some clients.
    # Verification is disabled deliberately and ONLY here, for a read-only
    # scrape of a public register; nothing is submitted to this portal.
    session.verify = False
    return session


def _get(session, url, ctx, params=None, what="page"):
    def _fetch():
        response = session.get(url, params=params, timeout=_TIMEOUT)
        response.raise_for_status()
        return response.text

    try:
        return fetch_with_retry(_fetch, what=f"JHARERA {what}", reporter=ctx.reporter)
    except requests.RequestException as e:
        raise StateFetchError(
            f"JHARERA {what} could not be fetched: {e}. The portal appears to be unreachable "
            f"right now; this is not a problem with the project or the registration number."
        ) from e


def _rows(table):
    """Every data row of a table as a list of cell strings.

    Skips rows with no <td> (JHARERA repeats its header inside <tr> on some
    tables) and rows whose cells are all empty.
    """
    out = []
    for tr in table.find_all("tr"):
        # JHARERA NESTS TABLES: several sections wrap the real table inside
        # an outer one whose single cell contains the whole inner table.
        # find_all("td") recurses, so that outer row yields a first cell
        # holding the inner table's ENTIRE text -- header words and data
        # run together. Left in, the director list gained a phantom member
        # called "Name Designation Emaild Photo Bijay Kumar Agarwal
        # Director projects@pranamigroup.com View", which would have been
        # printed in the Charter as a person.
        if tr.find("table") is not None:
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if cells and any(c for c in cells):
            out.append(cells)
    return out


def _headers_of(table):
    return [th.get_text(" ", strip=True).casefold() for th in table.find_all("th")]


def find_table(soup_or_tables, *required_headers):
    """The first table whose header row contains ALL of `required_headers`.

    By header, never by index -- see this module's docstring. Returns None
    when no table matches, which callers must treat as "not published",
    distinct from a table that exists with no rows.
    """
    tables = (
        soup_or_tables.find_all("table")
        if hasattr(soup_or_tables, "find_all")
        else soup_or_tables
    )
    wanted = [h.casefold() for h in required_headers]
    matches = [t for t in tables
               if all(any(w in h for h in _headers_of(t)) for w in wanted)]
    if not matches:
        return None
    # PREFER THE INNERMOST MATCH. JHARERA wraps several real tables inside an
    # outer one that repeats the same headers, so both match. The outer one's
    # rows are the inner table's text run together and are unusable -- taking
    # it silently emptied the professionals list (losing the contractor and
    # architect, and their PANs, which are the only PANs this authority
    # publishes as fields) while every other section still looked fine.
    for table in matches:
        if table.find("table") is None:
            return table
    return matches[0]


def labelled_rows(table, *keys):
    """Rows of `table` as dicts keyed on its own headers, lowercased."""
    if table is None:
        return None
    headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    out = []
    for cells in _rows(table):
        if len(cells) < len(headers):
            continue
        row = dict(zip(headers, cells))
        # JHARERA sometimes emits a first row that repeats the headers as
        # data. Drop it rather than carry a row of column names.
        if all(str(v).casefold() == str(k).casefold() for k, v in row.items() if v):
            continue
        out.append(row)
    return out


def parse_search_rows(html):
    """Search results as [{reg_no, project_name, address, project_id}].

    The registration number and the profile id are the load-bearing fields:
    everything else on the results page is repeated on the detail page.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = find_table(soup, "reg no", "project name")
    if table is None:
        return []
    results = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 3:
            continue
        link = ""
        for a in tr.find_all("a", href=True):
            match = _PROFILE_ID_RE.search(a["href"])
            if match:
                link = match.group(1)
                break
        if not link:
            continue
        reg_no = cells[1].split()[0] if cells[1] else ""
        results.append({
            "reg_no": reg_no,
            "reg_no_and_date": cells[1],
            "project_name": cells[2],
            "address": cells[3] if len(cells) > 3 else "",
            "project_id": link,
        })
    return results


def parse_project_detail(html):
    """The project page, as the category shapes the rest of the pipeline
    expects. Pure: takes HTML, returns dicts, touches no network -- so it is
    testable against a saved capture with no portal involved.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    directors = labelled_rows(find_table(tables, "name", "designation", "emaild"))
    banks = labelled_rows(find_table(tables, "account type", "bank name"))
    land = labelled_rows(find_table(tables, "owner type", "plot no"))
    units = labelled_rows(find_table(tables, "flat no", "sold status"))
    past = labelled_rows(find_table(tables, "project name", "rera registration number"))
    contractors = labelled_rows(find_table(tables, "contractor name", "pan no"))
    architects = labelled_rows(find_table(tables, "archiect name", "pan no"))
    # JHARERA publishes the structural engineer's PAN too, in its own table.
    # Missed at first because only the contractor and architect tables were
    # read -- and it is one of only three PANs this authority states as a
    # FIELD rather than filing as a scanned card.
    engineers = labelled_rows(find_table(tables, "structural engineer name", "pan no"))
    agents = labelled_rows(find_table(tables, "jhrera reg. no.", "name"))

    # Litigation: the table's PRESENCE is what makes an empty one a real
    # clean check. Absent table -> None -> reported as unknown downstream.
    litigation_table = find_table(tables, "caseno", "petitioner")
    litigation = labelled_rows(litigation_table)

    professionals = []
    for row in (contractors or []):
        professionals.append({
            "professionalTypeName": "Contractor",
            "name": row.get("Contractor Name"),
            "panNumber": row.get("PAN No."),
            "emailId": row.get("Email Id"),
            "mobileNo": row.get("Mobile"),
            "address": row.get("Address"),
        })
    for row in (architects or []):
        professionals.append({
            "professionalTypeName": "Architect",
            "name": row.get("Archiect Name") or row.get("Architect Name"),
            "panNumber": row.get("PAN No."),
            "emailId": row.get("Email id") or row.get("Email Id"),
            "mobileNo": row.get("Mobile"),
            "address": row.get("Address"),
        })
    for row in (engineers or []):
        professionals.append({
            "professionalTypeName": "Structural Engineer",
            "name": row.get("structural engineer Name"),
            "panNumber": row.get("PAN No."),
            "emailId": row.get("Email Id"),
            "mobileNo": row.get("Mobile Number"),
            "address": row.get("Address"),
        })

    promoter_name = ""
    for row in (banks or []):
        holder = row.get("Account Holder Name")
        if holder:
            promoter_name = holder
            break

    return {
        "directors": directors or [],
        "banks": banks or [],
        "land": land or [],
        "units": units or [],
        "past_projects": past or [],
        "professionals": professionals,
        "agents": agents or [],
        "litigation": litigation,
        "litigation_table_present": litigation_table is not None,
        "promoter_name": promoter_name,
        "pans_on_page": sorted(set(_PAN_RE.findall(soup.get_text(" ", strip=True)))),
    }


def project_notes(parsed):
    """Findings a reader would want that the page never states outright.

    Findings only, never boilerplate: a project with nothing notable
    produces an empty list.
    """
    notes = []
    if parsed.get("litigation_table_present") and not parsed.get("litigation"):
        notes.append(
            "JHARERA's own litigation table for this project is present and empty, which is a "
            "genuine clean result for litigation disclosed to this authority rather than an "
            "absence of information."
        )
    elif not parsed.get("litigation_table_present"):
        notes.append(
            "This project's page carried no litigation table at all, so litigation disclosed to "
            "JHARERA is UNKNOWN for it. This must not be read as a clean litigation record."
        )
    if parsed.get("past_projects"):
        notes.append(
            f"The promoter declared {len(parsed['past_projects'])} earlier JHARERA registration(s) "
            f"on this project's own filing, giving a track record beyond this single project."
        )
    return notes


_VIEW_DOC_RE = re.compile(r"/FirstLevel/ViewDocument/(\d+)", re.I)
_FILE_SUFFIX_RE = re.compile(r"\.(pdf|jpe?g|png)$", re.I)


def document_label(anchor):
    """A meaningful name for a document link whose own text is just "View".

    EVERY document link on a JHARERA project page is labelled "View" -- all
    67 of them on a real record. The name of the document lives in the
    table around it, in one of two shapes:

      * a COLUMN header, when the row is a set of slots
        ("Company Pan Card | Balance Sheet | Income Tax Preceeding Year 1"
        over cells that each hold a bare "View"), so the label is the header
        at this cell's own column index;
      * a SIBLING CELL, when the row is one document
        ("Non Encumbrance Certificate | View").

    Without this every file would be saved as "View", they would collide
    with each other, and promoter_identity would never recognise the PAN
    card among them -- which is the whole reason the PAN extraction exists.
    """
    cell = anchor.find_parent("td")
    row = anchor.find_parent("tr")
    if cell is not None and row is not None:
        cells = row.find_all("td")
        try:
            index = cells.index(cell)
        except ValueError:
            index = -1
        table = anchor.find_parent("table")
        if table is not None and index >= 0:
            headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
            if index < len(headers) and headers[index] and headers[index].casefold() not in (
                "download", "view", "photo", "sl.no.", "sl no.",
            ):
                return headers[index]
        siblings = [
            c.get_text(" ", strip=True) for c in cells
            if c is not cell and c.get_text(" ", strip=True)
            and c.get_text(" ", strip=True).casefold() not in ("view", "download")
        ]
        if siblings:
            return max(siblings, key=len)
    previous = anchor.find_previous(["h1", "h2", "h3", "h4", "h5", "strong", "b"])
    if previous is not None:
        text = previous.get_text(" ", strip=True)
        if text:
            return text[:90]
    return "Document"


def document_entries(html):
    """Every document the page links to, with a derived label.

    Pure so the label derivation above is testable against a saved capture
    rather than only against a live portal.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen, entries = set(), []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        match = _VIEW_DOC_RE.search(href)
        if not match and not _FILE_SUFFIX_RE.search(href.split("?")[0]):
            continue
        url = href if href.startswith("http") else BASE_URL + "/" + href.lstrip("/")
        if url in seen:
            continue
        seen.add(url)
        entries.append({
            "label": document_label(anchor),
            "url": url,
            "document_id": match.group(1) if match else None,
        })
    return entries


def search_promoter_projects(name, reporter=None):
    """Projects on JHARERA whose record matches `name`.

    The uniform promoter->projects call the group sweep needs. Module level,
    not a method, because a sweep asks the AUTHORITY a question about an
    entity; it is not acquiring a project and has no AcquisitionContext.

    JHARERA's search matches free text across the register rather than an
    exact promoter identifier, so every hit is a CANDIDATE. Callers must
    treat it that way; group_sweep records it as such.
    """
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.reporter = reporter or _NullReporter()
    session = _session()

    # JHARERA's search does not tokenise: a multi-word query matches only a
    # literal run of those words, so a full legal name returns NOTHING even
    # when the promoter plainly has projects here. Confirmed live -- "Pranami
    # Builders Private Limited" and "Pranami Builders" both return 0 rows,
    # while "PRANAMI" returns the group's two projects. A sweep passing legal
    # names would have reported this state as searched-and-empty for a group
    # that demonstrably has projects on it: a false clean record produced by
    # a search that ran perfectly.
    #
    # Each attempt records the query that produced it, so a brand-level hit
    # is never presented as a full-name match.
    attempts = [name]
    brand, _ = group_entities.brand_token(name)
    if brand and brand.casefold() != (name or "").strip().casefold():
        attempts.append(brand)

    for query in attempts:
        html = _get(session, PROJECT_LIST, ctx, params={"SearchBy": query},
                    what="promoter search")
        rows = parse_search_rows(html)
        # A BRAND-level query returning a crowd is evidence the word does not
        # identify this group, whatever the stop list thought. A real group
        # brand returns a handful of projects; a generic word returns the
        # register. Discarding the whole result is right here: there is no
        # way to tell which of thirty hits belong to this promoter, and
        # presenting them all as candidates would bury the real ones.
        if rows and query != name and len(rows) > _MAX_BRAND_MATCHES:
            continue
        if rows:
            return [
                {"reg_no": r["reg_no"], "project_name": r["project_name"],
                 "address": r["address"], "matched_on": query,
                 # Carried so a caller can OPEN the project. Without it a
                 # sweep can only ever list names.
                 "project_id": r["project_id"]}
                for r in rows
            ]
    return []


class _NullReporter:
    """A reporter for callers that have none. The sweep runs across many
    entities and states; per-request chatter from each would bury the
    result."""

    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def ok(self, *a, **k): pass
    def choose(self, *a, **k): return None


def fetch_project_summary(project_ref, reporter=None):
    """The diligence-relevant fields of ONE JHARERA project.

    The sweep alone only proves a project EXISTS. This opens it, which is
    where the things a reader actually needs live: whether it is in
    litigation, how much of it is sold, who the directors and contractors
    are, and what earlier registrations the promoter declared on it.

    Deliberately a SUMMARY and not a full acquire(): it does not download
    the project's ~70 documents. A group sweep can touch a dozen projects,
    and pulling several hundred megabytes of scanned filings to answer
    "does this promoter have litigation in Jharkhand" would be the wrong
    trade. The document COUNT is reported so a reader knows what is there
    to fetch if they want it.

    Never raises: one unreachable project must not sink a sweep.
    """
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.reporter = reporter or _NullReporter()
    try:
        html = _get(_session(), PROJECT_DETAIL.format(project_ref), ctx, what="project detail")
    except StateFetchError as e:
        return {"opened": False, "note": f"This project's page could not be opened ({e.__class__.__name__})."}

    parsed = parse_project_detail(html)
    units = parsed.get("units") or []
    sold = sum(1 for u in units
               if str(u.get("Sold Status", "")).strip().casefold() in ("yes", "sold"))
    return {
        "opened": True,
        "promoter_name": parsed.get("promoter_name") or "",
        "directors": [
            {"name": d.get("Name"), "designation": d.get("Designation"), "email": d.get("Emaild")}
            for d in (parsed.get("directors") or [])
        ],
        # Presence of the table is what makes an empty one a real nil
        # return; absence means nobody looked. Kept distinct here for the
        # same reason acquire() keeps it distinct.
        "litigation": parsed.get("litigation"),
        "litigation_table_present": parsed.get("litigation_table_present"),
        "declared_past_projects": [
            {"project_name": p.get("Project Name"),
             "reg_no": p.get("Rera Registration Number")}
            for p in (parsed.get("past_projects") or [])
        ],
        "professionals": parsed.get("professionals") or [],
        "pans_on_page": parsed.get("pans_on_page") or [],
        "units_total": len(units),
        "units_sold": sold,
        "land_parcels": len(parsed.get("land") or []),
        "documents_on_page": len(document_entries(html)),
        "notes": project_notes(parsed),
    }


class JharkhandAdapter:
    """StateAdapter for JHARERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        session = _session()

        # --- resolve ------------------------------------------------------
        ctx.reporter.info(f"Searching JHARERA for {query!r}...")
        html = _get(session, PROJECT_LIST, ctx, params={"SearchBy": query}, what="project search")
        matches = parse_search_rows(html)
        if not matches:
            raise StateResolutionError(
                f"No JHARERA project found matching '{query}'. JHARERA's search covers the "
                f"registration number, project name and address."
            )
        exact = [m for m in matches if m["reg_no"].casefold() == query.strip().casefold()]
        chosen = exact[0] if exact else matches[0]
        if not exact and len(matches) > 1:
            ctx.reporter.warn(
                f"{len(matches)} JHARERA projects matched {query!r}; using "
                f"{chosen['reg_no']} ({chosen['project_name']})."
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

        # --- fetch --------------------------------------------------------
        ctx.reporter.info("Fetching JHARERA project record...")
        detail_html = _get(
            session, PROJECT_DETAIL.format(chosen["project_id"]), ctx, what="project detail"
        )
        parsed = parse_project_detail(detail_html)
        promoter_name = parsed["promoter_name"] or chosen["project_name"]

        complaints_payload, complaint_notes = self._complaints(session, promoter_name, ctx)

        category_data = {
            "projects": {
                "projectName": chosen["project_name"],
                "projectRegistartionNo": registration_number,
                "address": chosen["address"],
                "units": parsed["units"],
                "landDetails": parsed["land"],
                "bankAccounts": parsed["banks"],
            },
            "partners": {"promoterDetails": {
                "promoterName": promoter_name,
                "directors": parsed["directors"],
                "emailId": (parsed["directors"] or [{}])[0].get("Emaild"),
            }},
            "professionals": parsed["professionals"],
            "spocs": None,
            "sro_details": None,
            "past_experiences": parsed["past_projects"],
            "documents": None,
            "complaints": complaints_payload,
            "appeals": None,
            "litigation": parsed["litigation"],
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        documents_manifest = self._download_documents(session, detail_html, documents_dir, ctx)

        notes = list(_AUTHORITY_NOTES) + project_notes(parsed) + complaint_notes
        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=registration_number,
            project_id=str(chosen["project_id"]),
            detail_url=PROJECT_DETAIL.format(chosen["project_id"]),
            category_data=category_data,
            documents_manifest=documents_manifest,
            documents_dir=documents_dir,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=promoter_name,
            promoter_portfolio=self._promoter_portfolio(session, promoter_name, registration_number, ctx),
            raw_record=None,
            auth_source="none",
            categories_not_published={"spocs", "sro_details", "appeals"},
            notes=notes,
        )

    # -- complaints --------------------------------------------------------
    def _complaints(self, session, promoter_name, ctx):
        """JHARERA's disposed-complaint register, matched on respondent.

        Deliberately conservative: this register names the respondent as
        free text, so a match is a POSSIBLE complaint against this promoter,
        never a confirmed one, and the count is reported that way. A
        register that cannot be read yields None, which downstream must not
        read as zero.
        """
        try:
            html = _get(session, DISPOSED_COMPLAINTS, ctx, params={"SearchBy": promoter_name},
                        what="disposed-complaint register")
        except StateFetchError:
            return ({"total_complaints_count": None,
                     "source": "JHARERA disposed-complaint register"},
                    ["JHARERA's disposed-complaint register could not be read this run, so this "
                     "promoter's complaint history is UNKNOWN. It must not be read as zero."])
        soup = BeautifulSoup(html, "html.parser")
        table = find_table(soup, "case no", "respondant")
        rows = labelled_rows(table) or []
        notes = []
        if rows:
            ctx.reporter.warn(
                f"JHARERA's disposed-complaint register has {len(rows)} entr(y/ies) naming a "
                f"respondent matching this promoter."
            )
            notes.append(
                f"{len(rows)} disposed complaint(s) on JHARERA's public register name a respondent "
                f"matching this promoter's name. The register identifies respondents by name only, "
                f"so these are possible matches to confirm, not confirmed complaints against this "
                f"entity."
            )
        else:
            ctx.reporter.ok("JHARERA's disposed-complaint register names no matching respondent.")
        return ({"total_complaints_count": len(rows),
                 "source": "JHARERA disposed-complaint register (name match on respondent)",
                 "matched_rows": rows}, notes)

    # -- promoter portfolio ------------------------------------------------
    def _promoter_portfolio(self, session, promoter_name, this_reg_no, ctx):
        if not promoter_name:
            return None
        try:
            html = _get(session, PROJECT_LIST, ctx, params={"SearchBy": promoter_name},
                        what="promoter project search")
        except StateFetchError:
            return None
        rows = [r for r in parse_search_rows(html) if r["reg_no"] != this_reg_no]
        if not rows:
            # A full legal name ("PBPL PRANAMI CREST RERA PRIVATE LIMITED")
            # is often the SPV for this one project and matches nothing
            # else, while the group's brand word matches its whole local
            # portfolio. Retry on the brand before concluding there is none.
            brand, _ = group_entities.brand_token(promoter_name)
            if not brand:
                return None
            try:
                html = _get(session, PROJECT_LIST, ctx, params={"SearchBy": brand},
                            what="promoter project search")
            except StateFetchError:
                return None
            rows = [r for r in parse_search_rows(html) if r["reg_no"] != this_reg_no]
            promoter_name = brand
        if not rows:
            return None
        return {
            "promoter_name_searched": promoter_name,
            "search_match_count": len(rows),
            "projects": [
                {"reg_no": r["reg_no"], "project_name": r["project_name"], "address": r["address"]}
                for r in rows
            ],
            "limitations": [
                "JHARERA's public search matches free text across the register rather than an "
                "exact promoter identifier, so this list may include projects by a differently "
                "named promoter whose record happens to contain the same words. Confirm each "
                "before relying on it."
            ],
        }

    # -- documents ---------------------------------------------------------
    def _download_documents(self, session, detail_html, documents_dir, ctx):
        """Every document the project page links to.

        Filenames are built from the DERIVED label plus the portal's own
        document id, then de-duplicated. Both Gujarat and Karnataka silently
        lost documents to filename collisions before that was guarded, and
        here the risk is worse: every link is labelled "View", so without a
        derived label all 67 documents would save over one file.
        """
        entries = document_entries(detail_html)
        if not entries:
            ctx.reporter.info("JHARERA project page linked no downloadable documents.")
            return []

        os.makedirs(documents_dir, exist_ok=True)
        manifest, used_names = [], set()
        for entry in entries:
            candidate = safe_document_filename(
                documents_dir, entry["label"], used_names,
                suffix=f"_{entry['document_id']}" if entry["document_id"] else "",
            )

            row = {"label": entry["label"], "original_url": entry["url"],
                   "saved_filename": candidate, "status": "failed", "method": "http-get",
                   "document_id": entry["document_id"]}
            try:
                response = session.get(entry["url"], timeout=_TIMEOUT)
                content_type = (response.headers.get("Content-Type") or "").lower()
                # An HTML body here is the portal's error page, not a
                # document. Saving it as a .pdf would put a file on disk that
                # every later reader would treat as a real filing.
                if response.status_code == 200 and response.content and "html" not in content_type:
                    with open(os.path.join(documents_dir, candidate), "wb") as f:
                        f.write(response.content)
                    row["status"] = "downloaded"
                elif "html" in content_type:
                    row["status"] = "failed (portal served a web page, not a document)"
                else:
                    row["status"] = f"failed (HTTP {response.status_code}, {len(response.content)} bytes)"
            except requests.RequestException as e:
                row["status"] = f"failed ({type(e).__name__})"
            manifest.append(row)

        got = sum(1 for r in manifest if r["status"] == "downloaded")
        ctx.reporter.ok(f"{got}/{len(manifest)} JHARERA document(s) retrieved.")
        return manifest


ADAPTER = JharkhandAdapter()

"""
StateAdapter for West Bengal / WBRERA.

See states/westbengal.py for the portal's shape and for why this register
only begins around 2023.

WHY urllib3 AND NOT requests. rera.wb.gov.in requires legacy TLS
renegotiation. requests 2.32.2 rebuilds its own SSL context per request and
discards one passed to init_poolmanager, so the option never reaches the
handshake and every call fails with an SSLError. This is the identical
failure states/adapter_gujarat.py hit, and it has the identical fix.

WHAT THIS ADAPTER WILL NOT DO. WBRERA publishes no promoter search and its
state index does not name the promoter, so building a promoter portfolio
would mean opening all 4,721 project pages. It returns None and says so,
rather than sampling a few pages and presenting the result as a portfolio.
Gujarat already shipped a capability it could not deliver once.
"""

import json
import os
import re
import ssl

import requests
import urllib3
from bs4 import BeautifulSoup

from .base import (
    AcquisitionResult,
    StateFetchError,
    StateResolutionError,
    fetch_with_retry,
    safe_document_filename,
    storage_key,
)
from .westbengal import (
    BASE_URL,
    CAUSE_LIST,
    DEFAULTERS,
    ORDER_REGISTER,
    PROFILE,
    PROJECT_DETAIL,
    STATE_INDEX,
)

urllib3.disable_warnings()

_TIMEOUT = 90.0
_PROCODE_RE = re.compile(r"project_details\.php\?procode=(\w+)", re.I)

_AUTHORITY_NOTES = [
    "WBRERA publishes no promoter search and its public register does not name the promoter, "
    "so this promoter's other West Bengal projects could not be listed. That is a limit of "
    "this authority's published data, not evidence the promoter has no other projects here.",
    "West Bengal regulated real estate under its own Housing Industry Regulation Act until the "
    "Supreme Court struck that Act down in May 2021, and WBRERA was constituted afterwards. A "
    "West Bengal project completed before that transition may legitimately have no record with "
    "this authority.",
    "WBRERA does not publish a single point of contact register or sub-registrar office details "
    "for a project. Their absence here does not mean none exist.",
]


def _pool():
    """A urllib3 PoolManager tolerating this host's legacy TLS.

    Verification is relaxed deliberately and ONLY here, for a read-only
    scrape of a public register; nothing is ever submitted to this portal.
    """
    ctx = ssl.create_default_context()
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return urllib3.PoolManager(
        ssl_context=ctx, cert_reqs="CERT_NONE", assert_hostname=False,
        headers={"User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )},
    )


def _get(pool, url, ctx, what="page"):
    def _fetch():
        response = pool.request("GET", url, timeout=_TIMEOUT, redirect=True)
        if response.status != 200:
            raise urllib3.exceptions.HTTPError(f"HTTP {response.status}")
        return response.data.decode("utf-8", "replace")

    try:
        return fetch_with_retry(_fetch, what=f"WBRERA {what}", reporter=ctx.reporter)
    except Exception as e:
        raise StateFetchError(
            f"WBRERA {what} could not be fetched: {e}. The portal appears to be unreachable "
            f"right now; this is not a problem with the project or the registration number."
        ) from e


# Reading every cause list is 565 PDFs of roughly 250 KB. The default is
# the most recent slice; whatever is not read is reported, never implied.
DEFAULT_CAUSE_LISTS = 25
_WB_CACHE = {}


def _fetch_text(url):
    pool = _pool()
    return fetch_with_retry(
        lambda: pool.request("GET", url, timeout=_TIMEOUT, redirect=True).data.decode("utf-8", "replace"),
        what="WBRERA page",
    )


def fetch_order_register(fetcher=None):
    """WBRERA's 4,881 authority orders, keyed by complaint number."""
    if "orders" in _WB_CACHE and fetcher is None:
        return _WB_CACHE["orders"]
    import wb_orders

    html = fetcher() if fetcher else _fetch_text(ORDER_REGISTER)
    parsed = wb_orders.parse_order_register(html)
    if fetcher is None:
        _WB_CACHE["orders"] = parsed
    return parsed


def cause_list_urls(fetcher=None):
    """Every cause-list PDF link, newest first (the page is in that order)."""
    from bs4 import BeautifulSoup

    html = fetcher() if fetcher else _fetch_text(CAUSE_LIST)
    table = BeautifulSoup(html or "", "html.parser").find("table")
    if not table:
        return []
    return [a["href"] for row in table.find_all("tr")[1:]
            for a in row.find_all("a", href=True)]


def fetch_cause_list_texts(limit=DEFAULT_CAUSE_LISTS, urls=None, pdf_fetcher=None):
    """Text of the most recent `limit` cause lists, and how many exist.

    Returns (texts, total). The total is what makes the shortfall visible:
    a promoter's orders are reachable only through the lists actually read.
    A PDF that fails is skipped rather than aborting -- one bad document
    must not cost the whole join -- and is simply absent from `texts`,
    which the coverage note accounts for.
    """
    import fitz

    urls = cause_list_urls() if urls is None else urls
    total = len(urls)
    # A PLAIN requests session, NOT the legacy-TLS pool -- the same reason
    # _download_documents uses one. The cause lists live on a different
    # host over plain HTTP, and urllib3 rejects assert_hostname on a
    # non-TLS connection with a TypeError. Through the pool, every single
    # PDF failed and the join reported "0 of 565 cause lists read".
    session = None
    if pdf_fetcher is None:
        session = requests.Session()
        session.verify = False
        session.headers.update({"User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )})
    texts = []
    for url in urls[:max(0, int(limit or 0))]:
        try:
            if pdf_fetcher is not None:
                raw = pdf_fetcher(url)
            else:
                raw = session.get(url, timeout=_TIMEOUT).content
            with fitz.open(stream=raw, filetype="pdf") as document:
                texts.append(chr(10).join(page.get_text() for page in document))
        except Exception:
            continue
    return texts, total


def search_orders_by_promoter(name, limit=DEFAULT_CAUSE_LISTS):
    """WBRERA orders whose complaint names this promoter in a cause list.

    Returns {"entries", "coverage"}. Every entry is a CANDIDATE: the OCR
    text layer does not preserve the cause list's columns, so the promoter
    is matched by proximity within its complaint's block and could in
    principle be the complainant. See wb_orders for the whole argument.
    """
    import wb_orders

    orders = fetch_order_register()
    key = ("cause", int(limit or 0))
    if key not in _WB_CACHE:
        _WB_CACHE[key] = fetch_cause_list_texts(limit)
    texts, total = _WB_CACHE[key]
    index = wb_orders.build_complaint_index(texts)
    return {
        "entries": wb_orders.orders_for_promoter(name, orders, index),
        "coverage": wb_orders.coverage_note(orders, len(texts), total, index),
    }


def fetch_defaulters(fetcher=None):
    """WBRERA's rejected/defaulting applications, keyed by NAME.

    Small, cheap and directly useful -- unlike the orders, this register
    names the party outright.
    """
    from bs4 import BeautifulSoup

    html = fetcher() if fetcher else _fetch_text(DEFAULTERS)
    table = BeautifulSoup(html or "", "html.parser").find("table")
    if not table:
        return []
    rows = table.find_all("tr")
    headers = [" ".join(c.get_text(" ", strip=True).split()).lower()
               for c in rows[0].find_all(["th", "td"])]
    out = []
    for row in rows[1:]:
        cells = [" ".join(c.get_text(" ", strip=True).split()) for c in row.find_all("td")]
        if not any(cells):
            continue
        out.append({headers[i]: cells[i] for i in range(min(len(headers), len(cells)))})
    return out


def _labelled_rows(table):
    if table is None:
        return None
    headers = [th.get_text(" ", strip=True) for th in table.find_all("th")]
    out = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not cells or not any(cells):
            continue
        out.append(dict(zip(headers, cells)) if len(cells) >= len(headers) else {"value": cells})
    return out


def find_table(tables, *required_headers):
    """The first table whose headers contain all of `required_headers`.

    By header, never by index: a WBRERA project page emits a different
    number of document tables depending on how many blocks and plan sets
    the promoter filed, so positions shift between projects.
    """
    wanted = [h.casefold() for h in required_headers]
    for table in tables:
        headers = [th.get_text(" ", strip=True).casefold() for th in table.find_all("th")]
        if all(any(w in h for h in headers) for w in wanted):
            return table
    return None


def parse_state_index(html):
    """Every project in West Bengal, from the all-districts register.

    Returns [{reg_no, project_id, project_name, completion_date,
    registration_date, procode}].
    """
    soup = BeautifulSoup(html, "html.parser")
    table = find_table(soup.find_all("table"), "project id", "registration no")
    if table is None:
        return []
    index = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cells) < 6:
            continue
        procode = ""
        for a in tr.find_all("a", href=True):
            match = _PROCODE_RE.search(a["href"])
            if match:
                procode = match.group(1)
                break
        if not procode:
            continue
        index.append({
            "project_id": cells[1],
            "project_name": cells[2],
            "completion_date": cells[3],
            "reg_no": cells[4],
            "registration_date": cells[5],
            "procode": procode,
        })
    return index


def parse_project_detail(html):
    """The project page as category-shaped dicts. Pure: HTML in, dicts out,
    no network, so it is testable against a saved capture."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    promoters = _labelled_rows(find_table(tables, "promoter name", "firm name"))
    agents = _labelled_rows(find_table(tables, "agent name", "agent address"))
    consultants = _labelled_rows(find_table(tables, "consultant name", "consultant type"))
    other_projects = _labelled_rows(
        find_table(tables, "project name", "registration no.", "construction status")
    )

    # Litigation: presence of the table is what makes an empty one a clean
    # check. WBRERA writes the literal string "NA" for none, which is an
    # answer, not a missing value.
    litigation_table = find_table(tables, "litigations")
    litigation_rows = _labelled_rows(litigation_table) or []
    litigation = [
        row for row in litigation_rows
        if str(row.get("Litigations", "")).strip().upper() not in ("NA", "N/A", "")
    ]

    professionals = []
    for row in (consultants or []):
        professionals.append({
            "professionalTypeName": row.get("Consultant Type"),
            "name": row.get("Consultant Name"),
            "address": row.get("Consultant Address"),
        })

    promoter = (promoters or [{}])[0]
    return {
        "promoter_name": promoter.get("Promoter Name") or promoter.get("Firm Name") or "",
        "promoter_row": promoter,
        "professionals": professionals,
        "agents": agents or [],
        "other_projects": other_projects or [],
        "litigation": litigation,
        "litigation_table_present": litigation_table is not None,
        "litigation_declared_none": bool(litigation_rows) and not litigation,
    }


def project_notes(parsed):
    """Findings only. A record with nothing notable produces none."""
    notes = []
    if parsed.get("litigation_declared_none"):
        notes.append(
            "WBRERA's own litigation field for this project is filled in and records none, which "
            "is a stated nil return by the promoter rather than missing information."
        )
    elif not parsed.get("litigation_table_present"):
        notes.append(
            "This project's page carried no litigation field at all, so litigation disclosed to "
            "WBRERA is UNKNOWN for it. This must not be read as a clean litigation record."
        )
    if parsed.get("other_projects"):
        notes.append(
            f"The promoter declared {len(parsed['other_projects'])} other project(s) on this "
            f"project's own filing."
        )
    return notes


# WBRERA serves a project's own filings from a SEPARATE host, and mixes
# links to site-wide boilerplate into the same page. Both distinctions
# matter for correctness, not just tidiness.
_DOC_HOST = "doc.repository.semtwb.in"

# Path segments on that host that mean "this belongs to THIS project".
# /scrol/ is the site's scrolling-notice attachment area -- it holds the
# QPR user manual and authority orders, which are the same for every
# project on the portal.
_PROJECT_DOC_SEGMENTS = ("/nproj/", "/aproj/", "/upcer/")

_DOC_SUFFIX_RE = re.compile(r"\.(pdf|jpe?g|png)$", re.I)


def is_project_document(href):
    """Whether a link is one of THIS project's filings.

    Without this the document library lists "West Bengal Real Estate
    Rules", "QPR_User_Manual.pdf" and the authority's advertising order as
    though the promoter had filed them. A reader counting documents on file
    would be counting the portal's own furniture, and a reader looking for
    a missing filing would think it was present.
    """
    if not _DOC_SUFFIX_RE.search(href.split("?")[0].replace("\\", "/")):
        return False
    if _DOC_HOST not in href:
        # Anything served from the portal itself is site chrome: the Rules,
        # the user manuals, the sample forms.
        return False
    return any(segment in href.lower() for segment in _PROJECT_DOC_SEGMENTS)


def document_entries(html):
    """This project's own filings, with the site's boilerplate excluded.

    Pure, so the classification above is testable against a saved capture.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen, entries = set(), []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].replace("\\", "/")
        if not is_project_document(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        label = anchor.get_text(" ", strip=True) or os.path.basename(href.split("?")[0])
        entries.append({"label": label, "url": href})
    return entries


class _NullReporter:
    def info(self, *a, **k): pass
    def warn(self, *a, **k): pass
    def ok(self, *a, **k): pass
    def choose(self, *a, **k): return None


class _NullCtx:
    """`_get` reports retries through ctx.reporter, and the sweep has no
    reporter to give it."""
    reporter = _NullReporter()


_INDEX_CACHE = []


def _index(ctx=None):
    if not _INDEX_CACHE:
        _INDEX_CACHE.extend(parse_state_index(
            _get(_pool(), STATE_INDEX, ctx or _NullCtx(), what="state register")
        ))
    return _INDEX_CACHE


def fetch_project_summary(project_ref, reporter=None):
    """Open ONE West Bengal project by its registration number, project id
    or procode.

    WEST BENGAL IS NOT SEARCHABLE BY PROMOTER, AND THIS IS NOT THAT.
    WBRERA's index does not name the promoter, so `group_sweep` cannot
    produce WB hits at all and says so (`_CANNOT_SEARCH["WB"]`). What this
    adds is the other half: a WB registration arriving from ANY other
    source -- a promoter's declared past project, an address in a
    past-experience row, a human -- can now be opened and read instead of
    being listed and left unconfirmed.

    That gap mattered here more than elsewhere, because the promoter's own
    page is where WBRERA puts the promoter name. Opening the project is the
    ONLY way to attach a West Bengal registration to a name.

    Never raises: one unreachable project must not sink the pass.
    """
    reporter = reporter or _NullReporter()
    needle = " ".join(str(project_ref or "").split()).casefold()
    if not needle:
        return {"opened": False, "note": "No WBRERA project reference was carried."}

    try:
        index = _index()
    except StateFetchError as e:
        return {"opened": False,
                "note": f"WBRERA's register could not be read ({type(e).__name__}), so this "
                        f"project was NOT opened and nothing was established about it."}
    if not index:
        return {"opened": False,
                "note": "WBRERA's register returned no projects at all, which means it could "
                        "not be read rather than that West Bengal has none."}

    entry = next(
        (e for e in index
         if needle in (e["reg_no"].casefold(), e["project_id"].casefold(),
                       str(e["procode"]).casefold())),
        None,
    )
    if entry is None:
        return {"opened": False,
                "note": (f"'{project_ref}' is not in WBRERA's register of {len(index)} "
                         f"projects. Note that West Bengal ran its own HIRA statute until the "
                         f"Supreme Court struck it down in May 2021, so a genuine pre-2021 "
                         f"West Bengal project may have no WBRERA record at all.")}

    try:
        parsed = parse_project_detail(
            _get(_pool(), PROJECT_DETAIL.format(entry["procode"]), _NullCtx(),
                 what="project detail")
        )
    except StateFetchError as e:
        return {"opened": False,
                "note": f"WBRERA's page for {entry['reg_no']} could not be read "
                        f"({type(e).__name__})."}

    return {
        "opened": True,
        # The whole point: the index does not carry this, the page does.
        "promoter_name": parsed["promoter_name"],
        "promoter_row": parsed["promoter_row"],
        "project_name": entry["project_name"],
        "reg_no": entry["reg_no"],
        "project_id": entry["project_id"],
        "registration_date": entry["registration_date"],
        "completion_date": entry["completion_date"],
        "professionals": parsed["professionals"],
        "litigation": parsed["litigation"],
        "declared_other_projects": parsed["other_projects"],
        "notes": project_notes(parsed),
    }


class WestBengalAdapter:
    """StateAdapter for WBRERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        pool = _pool()

        ctx.reporter.info("Fetching the WBRERA state register (all districts, one request)...")
        index = parse_state_index(_get(pool, STATE_INDEX, ctx, what="state register"))
        if not index:
            raise StateFetchError(
                "WBRERA's public register returned no projects at all this run, which means the "
                "register could not be read rather than that West Bengal has no projects."
            )
        ctx.reporter.ok(f"{len(index)} WBRERA projects indexed.")

        needle = query.strip().casefold()
        exact = [e for e in index
                 if needle in (e["reg_no"].casefold(), e["project_id"].casefold())]
        matches = exact or [e for e in index if needle in e["project_name"].casefold()]
        if not matches:
            raise StateResolutionError(
                f"No WBRERA project found matching '{query}' in a register of {len(index)} "
                f"projects."
            )
        chosen = matches[0]
        if len(matches) > 1:
            ctx.reporter.warn(
                f"{len(matches)} WBRERA projects matched {query!r}; using "
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

        ctx.reporter.info("Fetching WBRERA project record...")
        detail_html = _get(pool, PROJECT_DETAIL.format(chosen["procode"]), ctx, what="project detail")
        parsed = parse_project_detail(detail_html)
        promoter_name = parsed["promoter_name"] or chosen["project_name"]

        category_data = {
            "projects": {
                "projectName": chosen["project_name"],
                "projectRegistartionNo": registration_number,
                "projectId": chosen["project_id"],
                "projectProposeComplitionDate": chosen["completion_date"],
                "reraRegistrationDate": chosen["registration_date"],
            },
            "partners": {"promoterDetails": {
                "promoterName": promoter_name,
                "emailId": parsed["promoter_row"].get("Email ID"),
                "address": parsed["promoter_row"].get("Address"),
                "establishmentYear": parsed["promoter_row"].get("Establishment Year"),
            }},
            "professionals": parsed["professionals"],
            "spocs": None,
            "sro_details": None,
            "past_experiences": parsed["other_projects"] or None,
            "documents": None,
            # WBRERA publishes no complaint register at all through this
            # interface. None, never 0 -- a zero here would read downstream
            # as a clean complaint record, which is a claim nothing supports.
            "complaints": None,
            "appeals": None,
            "litigation": parsed["litigation"],
            "agents": parsed["agents"],
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        documents_manifest = self._download_documents(pool, detail_html, documents_dir, ctx)

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=registration_number,
            project_id=chosen["procode"],
            detail_url=PROJECT_DETAIL.format(chosen["procode"]),
            category_data=category_data,
            documents_manifest=documents_manifest,
            documents_dir=documents_dir,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=promoter_name,
            promoter_portfolio=None,
            raw_record=None,
            auth_source="none",
            categories_not_published={"spocs", "sro_details", "complaints", "appeals"},
            notes=list(_AUTHORITY_NOTES) + project_notes(parsed),
        )

    def _download_documents(self, pool, detail_html, documents_dir, ctx):
        """Downloads this project's filings.

        Uses a PLAIN requests session, not the legacy-TLS pool. Two reasons,
        both found live: the document host is a different machine that does
        not need the TLS workaround, and it serves over plain HTTP -- and
        urllib3 rejects `assert_hostname` on a non-TLS connection with a
        TypeError, so every single http:// document failed with what looked
        like a download error. 267 of 275 documents were being reported as
        unretrievable for that reason alone.
        """
        entries = document_entries(detail_html)
        if not entries:
            ctx.reporter.info("WBRERA project page linked no documents for this project.")
            return []

        os.makedirs(documents_dir, exist_ok=True)
        session = requests.Session()
        session.verify = False
        session.headers.update({"User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )})

        manifest, used = [], set()
        for entry in entries:
            base = os.path.basename(entry["url"].split("?")[0]) or "document.pdf"
            stem, ext = os.path.splitext(base)
            candidate = safe_document_filename(
                documents_dir, stem, used, extension=ext or ".pdf"
            )
            row = {"label": entry["label"], "original_url": entry["url"],
                   "saved_filename": candidate, "status": "failed", "method": "http-get"}
            try:
                response = session.get(entry["url"], timeout=_TIMEOUT)
                content_type = (response.headers.get("Content-Type") or "").lower()
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
        ctx.reporter.ok(f"{got}/{len(manifest)} WBRERA document(s) retrieved.")
        return manifest


ADAPTER = WestBengalAdapter()

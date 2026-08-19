"""
GujRERA acquisition adapter -- the first state after Maharashtra.

Runs entirely unattended: no CAPTCHA, no token, no browser. That makes it
the cheapest possible proof that the StateAdapter seam holds for a second
state, and it is why Gujarat was chosen ahead of finishing Telangana.

WHY THIS USES urllib3 DIRECTLY AND NOT requests.Session
-------------------------------------------------------
gujrera.gujarat.gov.in negotiates TLS with legacy renegotiation, which
OpenSSL 3 refuses by default:

    SSLError: [SSL: UNSAFE_LEGACY_RENEGOTIATION_DISABLED]

The fix is an SSLContext with ssl.OP_LEGACY_SERVER_CONNECT set. That works
through urllib3 directly (verified), but NOT through requests 2.32.2, which
rebuilds its own SSL context per request inside HTTPAdapter and discards the
one supplied to init_poolmanager. Rather than monkey-patch requests
internals -- which differ across 2.32.x and would break silently on upgrade
-- this module talks to urllib3, which requests depends on anyway.

The flag is scoped to this one host. Nothing else in the pipeline changes
its TLS behaviour.

ENDPOINTS (all public, all confirmed live against project 17020)
---------------------------------------------------------------
  POST /project_reg/public/global-search
       {"query": <text>, "startWith": 0, "dataSize": N}
       -> data[] of {entityType, entityId, entityName, regNo, distName, ...}
          entityType is one of PROMOTER / PROJECT / AGENT / ENGINEER / CA /
          ARCHITECT / DOCUMENT / LAWYER.
       The request schema was recovered from the server's own error envelope,
       which echoes back the fields it expected (query/startWith/dataSize/
       sortBy) as nulls when they are missing.

  GET  /project_reg/public/getproject-details/<id>
       -> {projectDetail{92 fields}, englist, calist, acrchlist, agentlist,
           dev, contr}
  GET  /project_reg/public/alldatabyprojectid/<id>   -> the full application
  GET  /project_reg/public/getproject-doc/<id>       -> {findoc, projectdoc}
  GET  /project_reg/public/project-app/getproject-banks/<id>
  GET  /project_reg/public/getprev-project-list/<id> -> {pervlist, gujrera}
  GET  /vdms/getDocMetadata/<uid>  -> {fileName, mimeType, totalPages, ...}

Documents arrive as <name>Id / <name>UId pairs inside findoc/projectdoc.
The UId is the opaque handle the DMS takes. `findoc` notably carries audited
balance sheets, P&L statements and income-tax returns -- financial disclosure
MahaRERA does not publish.
"""

import json
import os
import re
import ssl

import urllib3

from .base import AcquisitionResult, StateResolutionError, fetch_with_retry, storage_key
from .gujarat import DMS_DOWNLOAD_URL, DMS_METADATA_URL, PROFILE, PROJECT_REG_API

_TIMEOUT = 30

# Document fields worth surfacing, mapped to reader-facing labels. The
# portal names them in camelCase with paired <name>Id/<name>UId keys; only
# the UId is useful (it is the DMS handle).
_DOC_LABELS = {
    "performaForSaleOfAgreement": "Proforma agreement for sale",
    "auditorsDoc1": "Auditor's certificate (1)",
    "auditorsDoc2": "Auditor's certificate (2)",
    "auditorsDoc3": "Auditor's certificate (3)",
    "incomeTaxReturn1": "Income-tax return (1)",
    "incomeTaxReturn2": "Income-tax return (2)",
    "incomeTaxReturn3": "Income-tax return (3)",
    "auditedBalSheetDoc1": "Audited balance sheet (1)",
    "auditedBalSheetDoc2_": "Audited balance sheet (2)",
    "auditedBalSheetDoc3": "Audited balance sheet (3)",
    "auditedProfitLossSheetDoc1": "Audited profit & loss statement (1)",
    "auditedProfitLossSheetDoc2": "Audited profit & loss statement (2)",
    "auditedProfitLossSheetDoc3": "Audited profit & loss statement (3)",
}


def _pool():
    """A urllib3 PoolManager that tolerates this host's legacy TLS
    renegotiation. See the module docstring for why it is not requests."""
    ctx = ssl.create_default_context()
    ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return urllib3.PoolManager(
        ssl_context=ctx,
        headers={"User-Agent": "RERA-Scrapper-DueDiligence/1.0 (research tool, low-volume)"},
    )


def _unwrap(raw: bytes):
    """GujRERA wraps everything in {status, message, data, ...}. Returns
    `data`, or None when the call failed -- matching api_client's own
    convention of None-for-failed rather than raising per category."""
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None
    if str(payload.get("status")) not in ("200", "Success"):
        return None
    return payload.get("data")


def search(pool, query: str, size: int = 100) -> list:
    resp = pool.request(
        "POST", PROJECT_REG_API + "public/global-search",
        json={"query": query, "startWith": 0, "dataSize": size}, timeout=_TIMEOUT,
    )
    return _unwrap(resp.data) or []


def _get(pool, path: str):
    resp = pool.request("GET", PROJECT_REG_API + path, timeout=_TIMEOUT)
    return _unwrap(resp.data)


def _document_entries(doc_payload: dict) -> list:
    """Flattens findoc/projectdoc into manifest rows.

    Only the <name>UId keys matter -- the paired <name>Id is an internal row
    id the DMS does not accept. A null UId means that document was simply
    not filed, which is an absence to record rather than an error."""
    entries = []
    for block in ("projectdoc", "findoc"):
        block_data = (doc_payload or {}).get(block)
        if not isinstance(block_data, dict):
            continue
        for key, value in block_data.items():
            if not key.endswith("UId") or not value:
                continue
            stem = key[:-3]
            entries.append({
                "label": _DOC_LABELS.get(stem, _prettify(stem)),
                "uid": value,
                "block": block,
            })
    return entries


def _prettify(camel: str) -> str:
    """Fallback label for a document field this module has not named yet --
    better than showing the reader a raw camelCase key."""
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", camel.rstrip("_")).replace("_", " ")
    return spaced[:1].upper() + spaced[1:].lower()


class GujaratAdapter:
    """StateAdapter for GujRERA."""

    profile = PROFILE

    def acquire(self, query, ctx):
        pool = _pool()

        # --- resolve --------------------------------------------------
        # An exact registration number returns exactly one PROJECT row; a
        # free-text name may return several, so the caller chooses.
        rows = fetch_with_retry(
            lambda: search(pool, query.strip()),
            what="GujRERA search", reporter=ctx.reporter,
        )
        projects = [r for r in rows if r.get("entityType") == "PROJECT"]
        if not projects:
            raise StateResolutionError(
                f"No GujRERA project found matching '{query}'."
            )
        if len(projects) == 1:
            chosen = projects[0]
        else:
            index = ctx.reporter.choose(
                f"{len(projects)} GujRERA projects match '{query}'. Which one?",
                [f"{p.get('regNo') or p.get('entityId')} | {(p.get('entityName') or '').strip()}"
                 f" | {p.get('distName', '')}" for p in projects],
            )
            if index is None:
                raise StateResolutionError(
                    f"{len(projects)} GujRERA projects match '{query}' and the choice could "
                    f"not be made non-interactively. Re-run with the exact registration number."
                )
            chosen = projects[index]

        entity_id = chosen["entityId"]
        registration_number = (chosen.get("regNo") or "").strip()
        notes = []
        if registration_number:
            # Gujarat's number is full of path separators
            # (PR/GJ/SURAT/SURAT CITY/SUDA/...), which used unchanged as a
            # directory name produced a six-level nested tree instead of one
            # project folder. The safe form keys the filesystem; the real
            # number is carried separately for citation.
            reg_no = storage_key(registration_number)
        else:
            reg_no = f"GJ_{entity_id}"
            notes.append(
                "This project's GujRERA record carries no registration number, so the "
                "output folder is keyed on the portal's internal project id instead. "
                "That key is assigned by this pipeline, not by the authority."
            )
        ctx.reporter.ok(
            f"Resolved: {registration_number or reg_no} | {(chosen.get('entityName') or '').strip()}"
        )

        project_out_dir = os.path.join(ctx.output_dir, reg_no)
        raw_dir = os.path.join(project_out_dir, "raw")
        documents_dir = os.path.join(project_out_dir, "documents")
        os.makedirs(raw_dir, exist_ok=True)

        prior = ctx.prior or {}
        if ctx.on_resolved is not None:
            prior = ctx.on_resolved(reg_no) or {}
            os.makedirs(raw_dir, exist_ok=True)

        # --- fetch ----------------------------------------------------
        ctx.reporter.info("Fetching GujRERA project record...")
        details = _get(pool, f"public/getproject-details/{entity_id}") or {}
        alldata = _get(pool, f"public/alldatabyprojectid/{entity_id}") or {}
        docs = _get(pool, f"public/getproject-doc/{entity_id}") or {}
        banks = _get(pool, f"public/project-app/getproject-banks/{entity_id}")
        prev = _get(pool, f"public/getprev-project-list/{entity_id}") or {}

        # Map onto the SAME category_data contract MahaRERA produces, so
        # everything downstream (report.py's category tables, the Charter's
        # document-grounding, deep_research's prompt) works unchanged.
        # Categories GujRERA does not publish are None, which is exactly how
        # api_client records a category that failed -- an honest absence.
        category_data = {
            "projects": details.get("projectDetail") or alldata or None,
            "partners": {"promoterDetails": _promoter_details(alldata, details)},
            "professionals": _professionals(details),
            "spocs": None,
            "sro_details": None,
            "past_experiences": (prev.get("pervlist") or []) + (prev.get("gujrera") or []),
            "documents": docs or None,
            "complaints": None,
            "appeals": None,
            "bank_accounts": banks,
        }
        for name, payload in category_data.items():
            with open(os.path.join(raw_dir, f"{name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)

        notes.append(
            "GujRERA publishes no name-searchable complaint or appeal register through this "
            "interface, so the absence of complaints in this Charter means 'not published "
            "here', not 'none exist'."
        )

        # --- documents ------------------------------------------------
        documents_manifest = self._download_documents(pool, docs, documents_dir, ctx)

        promoter_name = _promoter_name(alldata, details)
        notes.append(
            "GujRERA's public interface does not link a promoter to their other registered "
            "projects, so no promoter track record was assembled. The promoter is named on "
            "this project's record, but their wider portfolio is not published here."
        )
        if not promoter_name:
            notes.append(
                "No promoter name could be read from this project's GujRERA record."
            )

        return AcquisitionResult(
            profile=PROFILE,
            reg_no=reg_no,
            registration_number=registration_number or None,
            project_id=str(entity_id),
            detail_url=f"{PROJECT_REG_API}public/getproject-details/{entity_id}",
            category_data=category_data,
            documents_manifest=documents_manifest,
            documents_dir=documents_dir,
            complaint_orders_manifest=[],
            complaint_orders_dir=None,
            promoter_name=promoter_name,
            promoter_portfolio=None,
            raw_record=alldata or None,
            auth_source="none",
            categories_not_published={"spocs", "sro_details", "complaints", "appeals"},
            notes=notes,
        )

    def _download_documents(self, pool, docs, documents_dir, ctx):
        """Records what the portal holds, and downloads each file.

        Manifest rows use the same keys api_client produces -- label,
        saved_filename, status -- because report.py and the Charter's
        document-grounding index on exactly those."""
        entries = _document_entries(docs)
        if not entries:
            ctx.reporter.warn("GujRERA lists no documents for this project.")
            return []

        os.makedirs(documents_dir, exist_ok=True)
        ctx.reporter.info(f"Downloading {len(entries)} GujRERA document(s)...")
        manifest = []
        used_names = {}
        for entry in entries:
            row = {
                "label": entry["label"],
                "original_url": DMS_METADATA_URL.format(entry["uid"]),
                "saved_filename": None,
                "status": "failed",
                "method": "gujrera-vdms",
                "document_id": entry["uid"],
                "source_filename": None,
            }
            try:
                meta_resp = pool.request("GET", DMS_METADATA_URL.format(entry["uid"]), timeout=_TIMEOUT)
                meta = json.loads(meta_resp.data.decode("utf-8", "replace"))
                source_name = meta.get("fileName") or f"{entry['uid']}.pdf"
                row["source_filename"] = source_name
                safe = re.sub(r"[^A-Za-z0-9._ -]", "_", source_name).strip() or "document.pdf"
                # Promoters routinely upload several DIFFERENT slots under
                # one filename -- this project files three separate
                # "NOT AVAILABLE.pdf" placeholders and reuses names across
                # balance-sheet years. Writing them all to the same path
                # silently destroyed 15 of 42 documents on the first live
                # run. De-duplicate so the manifest and the disk agree.
                if safe in used_names:
                    stem, ext = os.path.splitext(safe)
                    used_names[safe] += 1
                    safe = f"{stem} ({used_names[safe]}){ext}"
                else:
                    used_names[safe] = 1
                dest = os.path.join(documents_dir, safe)

                content = pool.request("GET", DMS_DOWNLOAD_URL.format(entry["uid"]), timeout=_TIMEOUT)
                if content.status == 200 and content.data and not content.data.lstrip().startswith(b"{"):
                    with open(dest, "wb") as f:
                        f.write(content.data)
                    row["saved_filename"] = safe
                    row["status"] = "downloaded"
                else:
                    # Metadata resolved but the bytes did not. Recorded, not
                    # guessed at -- the reader is told the document exists and
                    # was not retrieved.
                    row["status"] = "failed (metadata only, file bytes not served)"
            except Exception as e:
                row["status"] = f"failed ({e})"
            manifest.append(row)

        got = sum(1 for r in manifest if r["status"] in ("downloaded", "reused"))
        ctx.reporter.ok(f"{got}/{len(manifest)} GujRERA document(s) retrieved.")
        return manifest


def _promoter_details(alldata: dict, details: dict) -> dict:
    """Shaped like MahaRERA's partners.promoterDetails so
    main._extract_promoter_name and the Charter prompt read it unchanged."""
    dev = (details or {}).get("dev") or []
    first = dev[0] if dev else {}
    return {
        "promoterName": _promoter_name(alldata, details) or "",
        "promoterType": (alldata or {}).get("promoterType"),
        "emailId": (alldata or {}).get("promoterEmailId"),
        "_gujrera_developer_row": first,
    }


def _promoter_name(alldata: dict, details: dict):
    for source in ((details or {}).get("dev") or [{}])[:1]:
        for key in ("name", "promoterName", "firmName", "entityName"):
            value = (source or {}).get(key)
            if value and str(value).strip():
                return str(value).strip()
    for key in ("promoterName", "firmName"):
        value = (alldata or {}).get(key)
        if value and str(value).strip():
            return str(value).strip()
    return None


def _professionals(details: dict) -> list:
    """GujRERA splits professionals across four lists; MahaRERA's
    summarize_professionals expects ONE list of dicts carrying
    professionalTypeName + a name field. Normalised here so that function
    works untouched."""
    out = []
    for key, type_name in (
        ("englist", "Engineer"),
        ("calist", "Chartered Accountant"),
        ("acrchlist", "Architect"),
        ("contr", "Contractor"),
    ):
        for row in (details or {}).get(key) or []:
            if not isinstance(row, dict):
                continue
            merged = dict(row)
            merged["professionalTypeName"] = type_name
            merged.setdefault("entityCompanyName", row.get("name") or row.get("firmName") or "")
            out.append(merged)
    return out


ADAPTER = GujaratAdapter()

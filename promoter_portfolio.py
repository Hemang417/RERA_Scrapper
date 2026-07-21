"""
Builds a promoter's full RERA-registered project portfolio and aggregate
complaint/appeal history -- entirely from MahaRERA's own public search and
API. No external sources, no LLM; this is the deterministic half of the
promoter deep-research feature (see report.py for the agentic half).
"""

from datetime import datetime

import requests

import api_client
import config
import resolver


def _count_records(data) -> int | None:
    """Counts records in a category payload that may be a bare list, or a
    dict of sub-lists (the observed shape for complaints is
    {"complaintDetails": null, "miscComplaintDetails": null, "warrentDetails":
    null}). Returns None if the shape isn't recognized at all, so callers can
    tell "confirmed zero" apart from "couldn't be counted"."""
    if data is None:
        return None
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        total = 0
        for v in data.values():
            if isinstance(v, list):
                total += len(v)
        return total
    return None


def _is_lapsed(project_data: dict | None) -> bool:
    if not isinstance(project_data, dict):
        return False
    if project_data.get("isProjectLapsed") in (1, True, "1"):
        return True
    status_name = str(project_data.get("projectCurrentStatus") or "").lower()
    return any(term in status_name for term in ("lapsed", "revoked", "cancelled", "cancel"))


def _empty_portfolio(promoter_name: str, note: str) -> dict:
    return {
        "promoter_name_searched": promoter_name,
        "search_match_count": 0,
        "projects_analyzed": 0,
        "truncated": False,
        "projects": [],
        "totals": {
            "total_projects": 0,
            "total_complaints": 0,
            "total_appeals": 0,
            "projects_with_complaints": 0,
            "projects_with_appeals": 0,
            "lapsed_or_flagged_count": 0,
        },
        "limitations": [note],
        "generated_at": datetime.now().isoformat(),
    }


def build_promoter_portfolio(
    promoter_name: str,
    session: requests.Session,
    token: str | None,
    headless: bool = True,
    project_limit: int = config.PROMOTER_PROJECT_LIMIT,
) -> dict:
    """Searches MahaRERA's Promoters tab for every project registered under
    `promoter_name`, then fetches each one's status/complaints/appeals to
    build an aggregate track record. Never raises for an individual
    project's fetch failure -- those are recorded per-row instead so one bad
    project can't sink the whole portfolio."""
    promoter_name = (promoter_name or "").strip()
    if not promoter_name:
        return _empty_portfolio(promoter_name, "No promoter name was available to search with.")

    candidates = resolver.search_promoters(promoter_name, headless=headless)
    if not candidates:
        return _empty_portfolio(
            promoter_name,
            f"MahaRERA's Promoters-tab search returned no projects for '{promoter_name}'.",
        )

    truncated = len(candidates) > project_limit
    kept = candidates[:project_limit]

    rows = []
    total_complaints = 0
    total_appeals = 0
    projects_with_complaints = 0
    projects_with_appeals = 0
    lapsed_count = 0

    for c in kept:
        row = {
            "reg_no": c.reg_no,
            "project_id": c.project_id,
            "project_name": c.project_name,
            "status": None,
            "district": c.district,
            "complaint_count": None,
            "appeal_count": None,
            "is_lapsed": False,
            "complaints_fetch_error": None,
            "appeals_fetch_error": None,
        }

        try:
            project_data = api_client.fetch_category("projects", c.project_id, session, token)
            if isinstance(project_data, dict):
                row["status"] = project_data.get("projectCurrentStatus")
                row["is_lapsed"] = _is_lapsed(project_data)
                if row["is_lapsed"]:
                    lapsed_count += 1
        except api_client.CategoryFetchError as e:
            row["status"] = f"unknown (fetch failed: {e})"

        try:
            complaints_data = api_client.fetch_category("complaints", c.project_id, session, token)
            count = _count_records(complaints_data)
            row["complaint_count"] = count
            if count:
                total_complaints += count
                projects_with_complaints += 1
        except api_client.CategoryFetchError as e:
            row["complaints_fetch_error"] = str(e)

        if token:
            try:
                appeals_data = api_client.fetch_category("appeals", c.project_id, session, token)
                count = _count_records(appeals_data)
                row["appeal_count"] = count
                if count:
                    total_appeals += count
                    projects_with_appeals += 1
            except api_client.CategoryFetchError as e:
                row["appeals_fetch_error"] = str(e)
        else:
            row["appeals_fetch_error"] = "no session token available"

        rows.append(row)

    limitations = [
        "Portfolio is based on a name match against MahaRERA's own Promoters-tab "
        "search -- punctuation/suffix variants (e.g. 'Pvt Ltd', typos) may cause "
        "under-counting; cross-check manually if more projects are expected.",
        "MahaRERA's Promoters-tab search has no Registered/Revoked toggle (unlike "
        "the Projects tab) -- 'lapsed_or_flagged_count' is a best-effort signal from "
        "each project's own status field, not an independently confirmed revocation count.",
        "Mortgage lender is NOT tracked here across a promoter's portfolio: none of "
        "MahaRERA's structured project category APIs (projects, partners, professionals, "
        "sro_details, past_experiences, documents, complaints, appeals, spocs -- checked "
        "exhaustively) expose a bank/lender/mortgage/finance field anywhere. Lender identity "
        "is only ever recoverable as free text inside a project's own documents (see "
        "company_charter.py's mortgage_lender field, sourced from a per-project document "
        "read), which this deterministic, document-free portfolio scan does not open. "
        "Building cross-project lender tracking would require downloading and OCR/reading "
        "documents for every project in a promoter's portfolio -- a different, much heavier "
        "architecture, out of scope for this pass.",
    ]
    if truncated:
        limitations.append(
            f"Only the first {project_limit} of {len(candidates)} matching projects were "
            f"analyzed in detail; totals below only cover those {project_limit}."
        )

    return {
        "promoter_name_searched": promoter_name,
        "search_match_count": len(candidates),
        "projects_analyzed": len(kept),
        "truncated": truncated,
        "projects": rows,
        "totals": {
            "total_projects": len(candidates),
            "total_complaints": total_complaints,
            "total_appeals": total_appeals,
            "projects_with_complaints": projects_with_complaints,
            "projects_with_appeals": projects_with_appeals,
            "lapsed_or_flagged_count": lapsed_count,
        },
        "limitations": limitations,
        "generated_at": datetime.now().isoformat(),
    }

"""
Builds a promoter's full RERA-registered project portfolio and aggregate
complaint/appeal history -- entirely from MahaRERA's own public search and
API. No external sources, no LLM; this is the deterministic half of the
promoter deep-research feature (see report.py for the agentic half).
"""

import math
import re
import time
from datetime import datetime

import requests

import api_client
import config
import resolver

# OpenStreetMap's public Nominatim API -- free, no API key, used only to
# resolve a free-text address/locality into (lat, lon) for the "area within
# 5km" Developer Score criterion. Its usage policy caps this at ~1 request/
# second and requires a descriptive User-Agent identifying the app, not a
# generic library default -- both are honored below (_GEOCODE_MIN_INTERVAL_S,
# _GEOCODE_USER_AGENT). Never treat a failed/empty geocode as "0km away" --
# it must just drop that entry from the 5km sum, not include or exclude it
# by a guess.
_GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
_GEOCODE_USER_AGENT = "MahaRERA-Scrapper-DueDiligence/1.0 (personal research tool, low-volume)"
_GEOCODE_MIN_INTERVAL_S = 1.1
_FIVE_KM_RADIUS = 5.0
_last_geocode_at = 0.0


def _geocode(query: str) -> tuple | None:
    """Resolves a free-text address/locality string to (lat, lon) via
    Nominatim, rate-limited to Nominatim's own usage policy. Returns None
    -- never a guessed coordinate -- if the query is empty, the request
    fails, or nothing matches."""
    global _last_geocode_at
    query = (query or "").strip()
    if not query:
        return None

    elapsed = time.monotonic() - _last_geocode_at
    if elapsed < _GEOCODE_MIN_INTERVAL_S:
        time.sleep(_GEOCODE_MIN_INTERVAL_S - elapsed)
    _last_geocode_at = time.monotonic()

    try:
        resp = requests.get(
            _GEOCODE_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "in"},
            headers={"User-Agent": _GEOCODE_USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None


def _haversine_km(a: tuple, b: tuple) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(x))


def _geocode_query_for(address: str | None, district: str | None) -> str:
    """Builds the actual string to geocode for one past_experiences entry.
    Verified live against real MahaRERA data: its own `address` field is
    often a full legal land description (survey numbers, stray commas,
    "Plot 1 of Survey nos 11/1A, ...") that Nominatim's free-form search
    fails to resolve at all -- even when it contains the right locality
    name in the middle of it. A bare 6-digit Indian pincode pulled out of
    that same string geocodes reliably and precisely instead (confirmed:
    the full address for one real entry returned no match, but its own
    embedded pincode alone resolved within ~3km of the correct point), so
    a pincode match takes priority whenever the address has one. Falls
    back to the raw address, then to the portfolio project's own district,
    if no pincode is present."""
    address = (address or "").strip()
    pincode_match = re.search(r"\b(\d{6})\b", address)
    if pincode_match:
        return f"{pincode_match.group(1)}, India"
    if address:
        return address
    return f"{district}, Maharashtra, India"


def extract_subject_project_location(partners_category_data: dict | None) -> str | None:
    """Builds a geocodable locality string for the SUBJECT project (the one
    this portfolio is being built for context of) from its own `partners`
    category payload -- projectLegalLandAddressDetails.{locality, pinCode}
    plus the state, the same fields land_identification's village_locality/
    pincode are sourced from elsewhere in this pipeline. Returns None if
    that shape isn't present, so callers know the 5km filter can't run
    rather than silently geocoding an empty/wrong string."""
    if not isinstance(partners_category_data, dict):
        return None
    details = partners_category_data.get("projectDetails")
    if not isinstance(details, dict):
        return None
    addr = details.get("projectLegalLandAddressDetails")
    if not isinstance(addr, dict):
        return None
    locality = (addr.get("locality") or "").strip()
    pincode = (addr.get("pinCode") or "").strip()
    if not locality and not pincode:
        return None
    return ", ".join(part for part in (locality, pincode, "Maharashtra, India") if part)


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


def _classify_completion(entry: dict) -> str | None:
    """Compares one past_experiences entry's proposed vs. actual completion
    date. Returns "on_time" (actual <= proposed), "delayed", or None if
    either date is missing or doesn't parse as an ISO date -- callers must
    not count a None here toward either bucket."""
    proposed = entry.get("originalProposedCompletionDate")
    actual = entry.get("actualCompletionDate")
    if not proposed or not actual:
        return None
    try:
        proposed_date = datetime.fromisoformat(str(proposed))
        actual_date = datetime.fromisoformat(str(actual))
    except ValueError:
        return None
    return "on_time" if actual_date <= proposed_date else "delayed"


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
            "total_experience_entries_found": 0,
            "on_time_count": 0,
            "delayed_count": 0,
            "on_time_rate_pct": None,
            "total_area_developed_lakh_sqft": None,
            "area_within_5km_lakh_sqft": None,
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
    subject_project_partners_data: dict | None = None,
    subject_reg_no: str | None = None,
) -> dict:
    """Searches MahaRERA's Promoters tab for every project registered under
    `promoter_name`, then fetches each one's status/complaints/appeals to
    build an aggregate track record. Never raises for an individual
    project's fetch failure -- those are recorded per-row instead so one bad
    project can't sink the whole portfolio.

    `subject_project_partners_data`, if given, is the SUBJECT project's own
    `partners` category payload (the project this portfolio is being built
    for context of) -- used to geocode its locality once and filter each
    portfolio entry's past_experiences.landArea by whether it's within 5km,
    for the Developer Score's "promoter influence in the micro-market"
    criterion. Omitted or ungeocodable -> area_within_5km_lakh_sqft stays
    None, exactly as if this parameter were never added.

    `subject_reg_no`, if given, excludes that one registration number's own
    row from BOTH area totals (total_area_developed_lakh_sqft and
    area_within_5km_lakh_sqft) -- the subject project is itself registered
    under this same promoter name, so without this it would always appear
    in its own portfolio at 0km, trivially inflating both figures with a
    self-reference rather than genuine other-project track record. It still
    counts normally toward total_projects/complaints/appeals/on_time_rate,
    matching this function's existing, unchanged behavior for those."""
    promoter_name = (promoter_name or "").strip()
    if not promoter_name:
        return _empty_portfolio(promoter_name, "No promoter name was available to search with.")

    candidates = resolver.search_promoters(promoter_name, headless=headless)
    if not candidates:
        return _empty_portfolio(
            promoter_name,
            f"MahaRERA's Promoters-tab search returned no projects for '{promoter_name}'.",
        )

    subject_location = extract_subject_project_location(subject_project_partners_data)
    subject_coords = _geocode(subject_location) if subject_location else None
    geocode_cache = {}

    truncated = len(candidates) > project_limit
    kept = candidates[:project_limit]

    # The subject project's own name, resolved from the candidate whose
    # registration number matches subject_reg_no -- used to spot a
    # past-experience entry that is really a self-reference to the subject
    # project (see the entry loop below). Derived here rather than added as
    # another caller-supplied parameter, since the resolver already carries
    # it and a caller passing a name that disagreed with subject_reg_no
    # would be a silent trap.
    subject_project_name = None
    if subject_reg_no:
        subject_project_name = next(
            (
                str(c.project_name).strip().upper()
                for c in kept
                if c.reg_no and c.reg_no.strip().upper() == subject_reg_no.strip().upper() and c.project_name
            ),
            None,
        )

    rows = []
    total_complaints = 0
    total_appeals = 0
    projects_with_complaints = 0
    projects_with_appeals = 0
    lapsed_count = 0
    on_time_count = 0
    delayed_count = 0
    # Past-experience entries seen across every registration this promoter
    # holds, so the same historical project is never counted twice -- see the
    # dedup note in the entry loop below.
    seen_experience_keys = set()
    total_land_area_sqm = 0.0
    land_area_entries_found = 0
    total_land_area_within_5km_sqm = 0.0
    within_5km_entries_found = 0

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
            "past_experience_fetch_error": None,
        }

        project_data = None
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

        if token:
            try:
                # getPromoterPastExpProject rejects the generic {"projectId": ...}
                # body -- it keys off the promoter's userProfileId instead (same
                # quirk api_client._past_experiences_body works around for the
                # single-project pipeline). userProfileId lives on the
                # "projects" fetch above, already made for this same project.
                user_profile_id = project_data.get("userProfileId") if isinstance(project_data, dict) else None
                past_exp_body = {"userProfileId": user_profile_id, "projectId": c.project_id}
                past_experiences_data = api_client.fetch_category(
                    "past_experiences", c.project_id, session, token, body=past_exp_body
                )
                entries = past_experiences_data if isinstance(past_experiences_data, list) else []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    # A promoter's past-experience list is keyed on their
                    # userProfileId, NOT on the registration it was fetched
                    # under, so the same historical project comes back once
                    # per registration this promoter holds. Dedup on the
                    # API's own primary key for the entry
                    # (userProfilePastExperienceId), falling back to a
                    # name/area/completion-date triple if it's ever absent.
                    entry_key = entry.get("userProfilePastExperienceId")
                    if entry_key is None:
                        entry_key = (
                            str(entry.get("projectName", "")).strip().upper(),
                            entry.get("landArea"),
                            entry.get("actualCompletionDate"),
                        )
                    if entry_key in seen_experience_keys:
                        continue
                    seen_experience_keys.add(entry_key)

                    # Exclude only an entry that IS the subject project
                    # itself, identified by the ENTRY'S OWN declared identity
                    # (its MahaRERA registration number, or failing that its
                    # project name). This used to be keyed on whether the
                    # entry was FETCHED under the subject project's
                    # registration (c.reg_no == subject_reg_no), which was
                    # wrong in a way that silently destroyed real data: for a
                    # single-project SPV -- the normal structure for a Mumbai
                    # redevelopment -- the ONLY portfolio project IS the
                    # subject, so every genuine prior delivery declared under
                    # it had its area discarded, while the same entry still
                    # counted toward on_time_count/delayed_count (which never
                    # had the exclusion). Confirmed live on Pranami Bliss:
                    # "Mall of Ranchi" (5462.75 sqm, completed on time) gave
                    # on_time_rate_pct=100.0 but
                    # total_area_developed_lakh_sqft=None, leaving both the
                    # Past-Experience-Area and Influence-in-Micromarket
                    # Developer Score sub-metrics unscored for no real reason.
                    # Name matching is exact (normalised case/whitespace),
                    # never fuzzy -- same policy as every other identifier
                    # match in this pipeline.
                    entry_reg_no = str(entry.get("mahaRERARegistrationNumber") or "").strip().upper()
                    entry_name = str(entry.get("projectName") or "").strip().upper()
                    is_self_reference = bool(
                        (subject_reg_no and entry_reg_no and entry_reg_no == subject_reg_no.strip().upper())
                        or (subject_project_name and entry_name and entry_name == subject_project_name)
                    )
                    if is_self_reference:
                        continue

                    classification = _classify_completion(entry)
                    if classification == "on_time":
                        on_time_count += 1
                    elif classification == "delayed":
                        delayed_count += 1
                    land_area = entry.get("landArea")
                    if isinstance(land_area, (int, float)) and land_area > 0:
                        total_land_area_sqm += land_area
                        land_area_entries_found += 1

                        if subject_coords is not None:
                            entry_location = _geocode_query_for(entry.get("address"), c.district)
                            if entry_location not in geocode_cache:
                                geocode_cache[entry_location] = _geocode(entry_location)
                            entry_coords = geocode_cache[entry_location]
                            if entry_coords is not None and _haversine_km(subject_coords, entry_coords) <= _FIVE_KM_RADIUS:
                                total_land_area_within_5km_sqm += land_area
                                within_5km_entries_found += 1
            except api_client.CategoryFetchError as e:
                row["past_experience_fetch_error"] = str(e)
        else:
            row["past_experience_fetch_error"] = "no session token available"

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
        "Past-experience completion dates (on_time_rate_pct and friends) are self-reported by "
        "the promoter to MahaRERA, not independently verified against any external record -- "
        "treat this as the promoter's own claimed track record, not a confirmed one.",
        "total_area_developed_lakh_sqft and area_within_5km_lakh_sqft are both summed from each "
        "portfolio project's own past_experiences.landArea (self-reported, sqm, converted to "
        "lakh sq ft). An entry is excluded only when the entry's OWN declared MahaRERA "
        "registration number is the subject project itself, and entries are deduplicated across "
        "every registration this promoter holds (on the API's own "
        "userProfilePastExperienceId), so a historical project declared under several of the "
        "promoter's registrations is counted exactly once -- the same dedup now applies to "
        "on_time_count/delayed_count above.",
        "area_within_5km_lakh_sqft additionally requires geocoding: the subject project's own "
        "locality (from its partners category data, if supplied) is resolved via OpenStreetMap's "
        "public Nominatim API -- a free, no-API-key service, rate-limited to ~1 request/second "
        "per its usage policy. Each past_experiences entry's own address is geocoded by "
        "preference on any 6-digit Indian pincode found inside it (confirmed live: MahaRERA's "
        "own `address` field is often a full legal land description -- survey numbers, stray "
        "commas -- that Nominatim's free-form search fails on entirely, even when the correct "
        "locality name is embedded in it; the pincode alone resolves reliably), falling back to "
        "the raw address text, then to the portfolio project's district, if no pincode is "
        "present. An entry that still can't be geocoded is excluded from the 5km sum entirely "
        "(never guessed in or out), and a pincode covers a small area, not a point, so this "
        "figure is a reasonable estimate, not a surveyed one. If no subject location was "
        "supplied at all, or it can't be geocoded, area_within_5km_lakh_sqft stays None entirely.",
    ]
    if truncated:
        limitations.append(
            f"Only the first {project_limit} of {len(candidates)} matching projects were "
            f"analyzed in detail; totals below only cover those {project_limit}."
        )

    total_experience_entries_found = on_time_count + delayed_count
    on_time_rate_pct = (
        round(100 * on_time_count / total_experience_entries_found, 1)
        if total_experience_entries_found > 0
        else None
    )
    # 1 sqm = 10.7639 sqft; 1 lakh sqft = 100,000 sqft.
    total_area_developed_lakh_sqft = (
        round(total_land_area_sqm * 10.7639 / 100_000, 2) if land_area_entries_found > 0 else None
    )
    area_within_5km_lakh_sqft = (
        round(total_land_area_within_5km_sqm * 10.7639 / 100_000, 2) if within_5km_entries_found > 0 else None
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
            "total_experience_entries_found": total_experience_entries_found,
            "on_time_count": on_time_count,
            "delayed_count": delayed_count,
            "on_time_rate_pct": on_time_rate_pct,
            "total_area_developed_lakh_sqft": total_area_developed_lakh_sqft,
            "area_within_5km_lakh_sqft": area_within_5km_lakh_sqft,
        },
        "limitations": limitations,
        "generated_at": datetime.now().isoformat(),
    }

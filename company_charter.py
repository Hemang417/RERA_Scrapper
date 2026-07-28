"""
Agentic Company Charter generator. Fills output/company_charters/
Company_Charter_TEMPLATE_WebSourced.docx *in place* (python-docx, preserving
every style/color/table already baked into the template) with real,
sourced facts about one MahaRERA project -- replacing the manual
Node/docx-js scripts used for the first (Pranami Bliss) charter.

Reuses category_data already fetched by main.py, the documents already
downloaded to documents_dir, and -- if available -- the deep_research.json
already produced for this same project (its macro_market/micro_market/
promoter_external findings feed Area Intelligence and Corporate Identity
directly, so this pass doesn't re-research what deep_research.py already
confirmed).

Guardrails, same philosophy as deep_research.py:
  - Every sourced field carries its source; claims materially affecting the
    corporate-identity / litigation / FSI sections are independently
    re-verified (reusing deep_research._verify_claim) before being trusted.
  - Anything that can't be confirmed goes in `gaps`, verbatim, never
    silently filled in or approximated as fact.
  - Distances are estimated via web_search (no live browser/Maps API here)
    -- the template's own Methodology Note says so explicitly, matching its
    existing "state plainly what's confirmed vs approximated" philosophy.

Requires: ANTHROPIC_API_KEY (same as deep_research.py), and a local
Tesseract OCR install for scanned/no-text PDFs (falls back to a plain
"[OCR unavailable]" marker per-document if Tesseract isn't found, rather
than failing the whole run).

Optional precision upgrade for the Distances table: set
COMPANY_CHARTER_USE_MAPS_SCRAPE=1 to have _refine_distances_with_maps()
launch a headless Playwright browser per landmark and read the real
driving route off Google Maps, replacing the model's web_search estimate.
Off by default -- verified against the live site, but it scrapes Google's
consumer UI rather than their paid Distance Matrix/Routes API, so it may
not comply with Google's Terms of Service and can break without warning
if Google changes their page. Always falls back to the existing estimate
on any failure.

    python company_charter.py <REG_NO>
    COMPANY_CHARTER_USE_MAPS_SCRAPE=1 python company_charter.py <REG_NO>
"""

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import sys
from datetime import datetime

import fitz  # PyMuPDF
import pytesseract
import requests
from PIL import Image

import config
import deep_research
import finalize_report
import run_archive

MODEL = deep_research.MODEL

# pytesseract shells out to the tesseract binary by name -- if it's installed
# but not on PATH (confirmed live: this was the actual state on this machine,
# silently degrading every scanned document to "[OCR unavailable for this
# page]" rather than raising anything visible), it fails silently into that
# same per-page marker in _extract_document_text below. TESSERACT_CMD lets a
# different machine/OS override this; the two hardcoded fallbacks cover the
# default Windows installer locations.
if not shutil.which("tesseract"):
    for _candidate in (
        os.environ.get("TESSERACT_CMD"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if _candidate and os.path.exists(_candidate):
            pytesseract.pytesseract.tesseract_cmd = _candidate
            break
TEMPLATE_PATH = os.path.join(config.OUTPUT_ROOT, "company_charters", "Company_Charter_TEMPLATE_WebSourced.docx")
CHARTER_OUTPUT_DIR = os.path.join(config.OUTPUT_ROOT, "company_charters")

# Document labels worth extracting full text from (case-insensitive substring
# match against the manifest's `label`/`saved_filename`) -- everything else
# is listed in the Document Library table by name only, with an explicit
# "not opened this pass" reason, matching the template's own allowance for
# that (see paragraph 63/64 in the template).
_HIGH_PRIORITY_DOC_KEYWORDS = (
    "title", "legal", "encumbrance", "form b", "declaration", "ioa", "iod",
    "sanction", "approval", "layout", "plan", "allotment", "agreement",
)
_MAX_CHARS_PER_DOC = 6000
_MAX_TOTAL_DOC_CHARS = 30000


def _extract_document_text(path: str) -> str:
    """Native text first; falls back to Tesseract OCR per page with no
    extractable text. Never raises -- a broken/unreadable file or a missing
    Tesseract install yields a plain marker instead of failing the run."""
    try:
        parts = []
        with fitz.open(path) as doc:
            for page in doc:
                text = page.get_text().strip()
                if text:
                    parts.append(text)
                    continue
                try:
                    pix = page.get_pixmap(dpi=300)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    parts.append(pytesseract.image_to_string(img))
                except Exception:
                    parts.append("[OCR unavailable for this page]")
        return "\n".join(parts).strip()
    except Exception as e:
        return f"[Could not read this document: {e}]"


# ---------------------------------------------------------------------------
# Complaint-order outcome extraction. complaints.json has always carried
# orderDmsRefNo/orderFileName per complaint -- this session's own document-
# grounding work is what surfaced that these were never actually downloaded
# (see api_client.download_complaint_orders, which fetches them via the same
# DMS mechanism already used for project documents). Once downloaded, this
# reuses _extract_document_text (the OCR path this same session fixed) to
# read each order and classifies its outcome via a small, named set of
# keywords MahaRERA orders commonly use -- checked in priority order, most
# specific first, since e.g. "disposed of as withdrawn" should classify as
# "withdrawn" rather than the more generic "disposed_other" match. Anything
# that matches none of them is reported as not determinable, never guessed.
# ---------------------------------------------------------------------------

_ORDER_OUTCOME_PATTERNS = [
    ("withdrawn", re.compile(r"disposed?\s+(?:of|off)\s+as\s+withdrawn", re.I)),
    ("settled_by_conciliation", re.compile(r"\bconciliat(?:ion|ed)\b", re.I)),
    ("dismissed", re.compile(r"\b(?:complaint|appeal)\s+(?:is|stands)?\s*dismissed\b|\bdismissed\b", re.I)),
    ("allowed", re.compile(r"\b(?:complaint|appeal)\s+(?:is|stands)?\s*allowed\b", re.I)),
    ("disposed_other", re.compile(r"\bdisposed?\s+(?:of|off)\b", re.I)),
]


def _extract_complaint_outcome(document_text: str) -> str:
    """Best-effort classification of a downloaded complaint-order PDF's
    outcome from its extracted text. Returns "not determinable from
    extracted text" rather than guessing if nothing matches -- this is a
    heuristic label meant to summarize a large complaint count at a glance,
    not a substitute for a human reading the actual order for anything that
    matters to a real decision."""
    if not document_text or document_text.startswith("[Could not read") or "[OCR unavailable" in document_text:
        return "not determinable -- document could not be read"
    for label, pattern in _ORDER_OUTCOME_PATTERNS:
        if pattern.search(document_text):
            return label
    return "not determinable from extracted text"


def summarize_complaint_outcomes(complaint_orders_manifest: list, documents_dir: str) -> dict:
    """For each successfully downloaded/reused complaint-order PDF, reads it
    and classifies its outcome. Returns {outcome_counts: {label: count},
    per_complaint: [{complaint_registration_no, complaint_id, outcome,
    order_filename}]} -- both the aggregate breakdown for a summary
    paragraph and the per-complaint detail for anyone who wants to trace a
    specific outcome back to its actual order file."""
    outcome_counts = {}
    per_complaint = []
    for entry in complaint_orders_manifest:
        if entry.get("status") not in ("downloaded", "reused") or not entry.get("saved_filename"):
            continue
        path = os.path.join(documents_dir, entry["saved_filename"])
        text = _extract_document_text(path)
        outcome = _extract_complaint_outcome(text)
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        per_complaint.append({
            "complaint_registration_no": entry.get("complaint_registration_no"),
            "complaint_id": entry.get("complaint_id"),
            "outcome": outcome,
            "order_filename": entry["saved_filename"],
        })
    return {"outcome_counts": outcome_counts, "per_complaint": per_complaint}


def _select_documents_for_extraction(documents_manifest: list, documents_dir: str) -> tuple[dict, list]:
    """Returns (extracted_text_by_label, all_labels_with_status) -- the
    former feeds the Claude prompt (capped total size), the latter fills the
    Document Library table for every document regardless of whether it was
    opened."""
    extracted = {}
    all_labels = []
    total_chars = 0

    for entry in documents_manifest:
        label = entry.get("label") or entry.get("saved_filename") or "Unnamed document"
        available = entry.get("status") in ("downloaded", "reused") and entry.get("saved_filename")
        if not available:
            all_labels.append({"document_name": label, "status": f"Not available ({entry.get('status')})"})
            continue

        if label in extracted:
            # Multiple manifest entries can share the exact same generic
            # label (e.g. MahaRERA's own catch-all "Other -- Legal" bucket
            # covering many distinct sale deeds/orders) -- extracting a
            # second one would silently overwrite the first in `extracted`
            # (same dict key) while still burning the shared char budget,
            # starving out later, distinctly-labeled, higher-value documents
            # (a real bug, confirmed live: it silently dropped the Title
            # Report and Form B for a project whose library happened to have
            # a dozen same-labeled documents ahead of them). Keep only the
            # first document under a given label; skip re-extracting the rest.
            all_labels.append({"document_name": label, "status": "Not opened this pass -- another document already opened under this same shared label"})
            continue

        is_priority = any(k in label.lower() for k in _HIGH_PRIORITY_DOC_KEYWORDS) or any(
            k in (entry["saved_filename"] or "").lower() for k in _HIGH_PRIORITY_DOC_KEYWORDS
        )
        if is_priority and total_chars < _MAX_TOTAL_DOC_CHARS:
            path = os.path.join(documents_dir, entry["saved_filename"])
            text = _extract_document_text(path)[:_MAX_CHARS_PER_DOC]
            extracted[label] = text
            total_chars += len(text)
            all_labels.append({"document_name": label, "status": "Opened directly this pass"})
        else:
            reason = "not among the identified high-priority legal/regulatory document types" if is_priority else "not a high-priority legal/regulatory document type for this pass"
            all_labels.append({"document_name": label, "status": f"Not opened this pass -- {reason}"})

    return extracted, all_labels


_FIELD_WITH_SOURCE = {
    "type": "object",
    "properties": {"value": {"type": "string"}, "source": {"type": "string"}},
    "required": ["value", "source"],
}
_PLAIN_FIELD = {"type": "string"}
_INT_FIELD = {"type": "integer"}

_CHARTER_FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "methodology_note": _PLAIN_FIELD,
        "executive_summary": _PLAIN_FIELD,
        "land_identification": {
            "type": "object",
            "properties": {k: _FIELD_WITH_SOURCE for k in (
                "survey_cts_plot_numbers", "village_locality", "mandal_taluka_district",
                "pincode", "total_gross_area", "area_affected", "net_area",
            )},
        },
        "corporate_identity": {
            "type": "object",
            "properties": {k: _FIELD_WITH_SOURCE for k in (
                "promoter_name", "organization_type", "cin_llpin", "registered_office_main",
                "registered_office_board_resolution", "registered_office_planning_stage",
                "authorized_signatory", "partners_directors", "landowner_investor",
            )},
        },
        "address_discrepancy_note": _PLAIN_FIELD,
        "corporate_registry_cross_check": _PLAIN_FIELD,
        "litigation_status": _FIELD_WITH_SOURCE,
        "location_coordinates_note": _PLAIN_FIELD,
        "neighbourhood": {
            "type": "object",
            "properties": {k: _PLAIN_FIELD for k in ("east", "west", "north", "south")},
        },
        "distances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "landmark": {"type": "string"},
                    "distance_time": {"type": "string"},
                    "route_note": {"type": "string"},
                },
                "required": ["landmark", "distance_time", "route_note"],
            },
        },
        "connectivity": {
            "type": "object",
            "properties": {k: _PLAIN_FIELD for k in ("road", "rail", "metro", "air")},
        },
        "social_infrastructure": _PLAIN_FIELD,
        "fsi_governing_framework": _PLAIN_FIELD,
        "fsi_interpretation": _PLAIN_FIELD,
        "fsi_metrics": {
            "type": "object",
            "properties": {
                **{k: _PLAIN_FIELD for k in (
                    "net_land_area", "approved_bua", "sanctioned_bua", "mortgage_area", "implied_fsi",
                )},
                # {value, source} rather than plain text: MahaRERA's own API exposes NO lender/bank
                # field anywhere (confirmed by checking every one of the 9 raw category payloads --
                # projects, partners, professionals, sro_details, past_experiences, documents,
                # complaints, appeals, spocs -- zero matches for bank/lender/mortgage/finance/loan in
                # any of them). The only place a lender's identity ever appears is free text inside a
                # downloaded title/encumbrance/Form-B document, so this needs the same independent
                # re-verification path as land_identification/corporate_identity, which a bare string
                # wouldn't get.
                "mortgage_lender": _FIELD_WITH_SOURCE,
            },
        },
        "rules_statutory": {
            "type": "object",
            "properties": {k: _PLAIN_FIELD for k in (
                "governing_act", "planning_approval_sequence", "allotment_mechanics",
            )},
        },
        "rera_compliance": {
            "type": "object",
            "properties": {k: _PLAIN_FIELD for k in (
                "registration_summary", "collection_account", "escrow_subaccounts",
                "litigations_complaints_appeals", "statutory_declaration", "construction_progress",
            )},
        },
        "local_planning": {
            "type": "object",
            "properties": {k: _PLAIN_FIELD for k in (
                "authority_of_record", "project_type", "professionals_of_record",
            )},
        },
        "micro_market_overview": _PLAIN_FIELD,
        "comparables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project": {"type": "string"}, "configuration": {"type": "string"},
                    "pricing": {"type": "string"}, "source": {"type": "string"},
                    "distance_km": {"type": "string", "description": "Approximate straight-line or driving distance from the subject project, as a bare number with NO 'km' suffix -- e.g. '3.8', not '3.8 km'"},
                },
                "required": ["project", "configuration", "pricing", "source", "distance_km"],
            },
        },
        "area_intelligence_trend": _PLAIN_FIELD,
        "rera_core_fields": {
            "type": "object",
            "properties": {
                **{k: _PLAIN_FIELD for k in (
                    "project_name", "registration_number", "promoter_name", "authority",
                    "plan_approval_number", "project_status", "approved_date",
                    "proposed_completion_date", "project_type", "litigations_per_record",
                    "promoter_land_owner_investor", "collection_bank_account", "total_building_units",
                )},
                # Structured companions to the prose fields above, so downstream scoring/KPI-card
                # code can read the real number directly instead of regexing free text (see the
                # _SYSTEM_PROMPT rule on these -- a project's exact phrasing shouldn't determine
                # whether its own clean record silently drops out of the Developer Score).
                "total_complaints_count": _INT_FIELD,
                "total_appeals_count": _INT_FIELD,
                "units_total": _INT_FIELD,
                "units_sold": _INT_FIELD,
                "completion_date_current": _PLAIN_FIELD,
                "completion_date_original": _PLAIN_FIELD,
            },
        },
        # Feeds the code-computed Developer Score's "track record years" criterion --
        # deliberately its own top-level object, not nested under rera_core_fields, since
        # MahaRERA's own record never carries this (it's promoter/group history, not a
        # RERA-filed fact). Omit this object entirely if it can't be confirmed -- see the
        # _SYSTEM_PROMPT rule below -- rather than guessing a number.
        "developer_track_record": {
            "type": "object",
            "properties": {
                "years_in_industry": _INT_FIELD,
                "years_in_industry_basis": _PLAIN_FIELD,
            },
        },
        "unit_summary_note": _PLAIN_FIELD,
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block_wing": {"type": "string"}, "floors": {"type": "string"},
                    "config": {"type": "string"}, "units_counted": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["block_wing", "floors", "config", "units_counted", "note"],
            },
        },
        "documents_reviewed_note": _PLAIN_FIELD,
        "documents_absent_note": _PLAIN_FIELD,
        "gaps": {"type": "array", "items": {"type": "string"}},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "ref": {"type": "string"},
                    "topic": {"type": "string", "description": "Short category this source backs. Used both to check whether a material topic is backed by more than one independent source, AND to automatically render an inline citation next to any paragraph on that topic -- so use EXACTLY one of these known values whenever it applies (a value outside this list still gets a source-count check, but will NOT get an automatic inline citation anywhere): 'land_title', 'corporate_identity', 'litigation', 'pricing', 'market_trend', 'distance', 'project_registration' (any fact drawn from MahaRERA's own project record -- rera_core_fields, rules_statutory, local_planning, neighbourhood, unit_summary_note all draw from this), 'legal_documents', 'reputation', 'credit_rating', 'insolvency_status', 'company_profile', 'group_companies'."},
                    "published_date": {"type": "string", "description": "The date the underlying information was published or last updated, as YYYY-MM-DD if the source discloses one, or the literal string 'unknown' if it does not (e.g. a live database record with no publish date shown) -- never guessed or invented."},
                    "accessed_date": {"type": "string", "description": "The date this specific source was actually retrieved/read during this research pass, as YYYY-MM-DD."},
                },
                "required": ["label", "ref", "topic", "published_date", "accessed_date"],
            },
        },
    },
    "required": [
        "methodology_note", "executive_summary", "land_identification", "corporate_identity",
        "address_discrepancy_note", "corporate_registry_cross_check", "litigation_status",
        "location_coordinates_note", "neighbourhood", "distances", "connectivity",
        "social_infrastructure", "fsi_governing_framework", "fsi_interpretation", "fsi_metrics",
        "rules_statutory", "rera_compliance", "local_planning", "micro_market_overview",
        "comparables", "area_intelligence_trend", "rera_core_fields", "unit_summary_note",
        "blocks", "documents_reviewed_note", "documents_absent_note", "gaps", "sources",
    ],
}

_CHARTER_JSON_SHAPE = json.dumps(_CHARTER_FACTS_SCHEMA)

_SYSTEM_PROMPT = f"""You are producing the factual content for a real-estate Company Charter \
document (a due-diligence reference, not marketing material) for one MahaRERA-registered \
project, following a fixed template structure.

Rules:
- Every field must be either a real, sourced fact or an explicit statement that it could not \
be confirmed (put those in `gaps`, do not leave a field blank or guess).
- `rera_core_fields.total_complaints_count`, `total_appeals_count`, `units_total`, and \
`units_sold` must be the literal integers your own `litigations_per_record`/`total_building_units` \
prose describes -- not a restatement of the prose, the actual number a downstream scoring step \
can read directly. `completion_date_current` is the current proposed completion date as \
YYYY-MM-DD; `completion_date_original` is the ORIGINAL declared date as YYYY-MM-DD if it has ever \
been extended/pushed back, or the exact literal empty string "" if it has never been extended \
(same as current) -- never omit these because the prose already covers it, and never guess a \
number you can't confirm (state the uncertainty in the prose field instead, and leave the count \
fields out of your JSON entirely for that one project rather than invent a number).
- Distances/routes: you do NOT have a live Maps browsing tool here -- use web_search to find \
driving distances/times and state plainly in `location_coordinates_note` that these are \
web-search estimates, not live Maps-verified routes (this template explicitly wants that kind \
of honesty about what's confirmed vs approximated). Each `distances[].landmark` MUST be an \
actual named place (e.g. "Chhatrapati Shivaji Maharaj International Airport"), never a generic \
category label like "Nearest airport" -- a later step may look up the precise route for the \
named place you give.
- Do not conflate similarly-named companies -- cross-check any CIN/LLPIN claim against the \
promoter's registered legal name and address from the provided RERA data before accepting it.
- For the FSI section, show the computation (net area, approved/sanctioned BUA -> implied FSI) \
using the actual figures given, and state plainly whether this is your interpretation or a \
figure confirmed by an actual sanctioned-FSI certificate.
- The standing gap about promoter shareholding splits/personal net worth is permanent and \
already in the template -- do not attempt to research it, and do not repeat it in your own \
`gaps` list.
- Comparable projects (`comparables`) must be GENUINELY INDEPENDENT developments located within \
approximately 3-5 km of the subject project -- not the subject's own adjacent phase or the same \
complex under a different marketing name (e.g. do not count a project's own "Phase 2" or an \
immediately-adjacent same-developer extension as a comparable; it is the same product, not a \
market comparable). State the approximate distance for each comparable in `distance_km`. If no \
genuinely independent comparable exists within that radius, say so in `gaps` rather than \
substituting a same-complex neighbor or a locality-wide average as if it satisfied the radius.
- Use web_search to find how many years this promoter (or its parent group, if this is a \
group-affiliated SPV incorporated recently for one project) has actually been active in real- \
estate development, and fill `developer_track_record.years_in_industry` (a plain integer) with \
`years_in_industry_basis` stating what start event you counted from (e.g. "group's first \
recorded project launch in 1998, per the group's own corporate website") and its source. If you \
cannot confirm this from any source, omit `developer_track_record` entirely rather than guessing \
a number or assuming the SPV's own incorporation date equals the group's real experience.
- Every entry in `sources` needs a `topic` (see the exact known values listed on that field), a \
`published_date` (YYYY-MM-DD if the source states one, or literally "unknown" if it does not -- \
never invent one), and an `accessed_date` (YYYY-MM-DD, when you actually looked at it this pass). \
These feed a later, code-computed confidence score -- an omitted or fabricated date is worse than \
an honest "unknown".
- A later, code-only step renders an inline citation next to several prose fields (rules_statutory, \
local_planning, unit_summary_note, neighbourhood, connectivity, micro_market_overview, \
area_intelligence_trend, location_coordinates_note) by matching that field's likely topic against \
`sources[].topic`. It does NOT read your prose to figure out what backs it -- if a fact in one of \
those fields came from a source, tag that source with the matching known topic value (see the field \
description on `sources[].topic`) or it will render with no citation at all, even though you did \
have a source. The `_FIELD_WITH_SOURCE` fields (land_identification, corporate_identity, \
litigation_status, fsi_metrics.mortgage_lender) work differently: their own `source` value is shown \
directly, so just make sure it's a real, specific document name/URL, not a vague description.
- Use web_search as many times as you need across multiple turns. Once you have everything \
you need, your FINAL reply must be ONLY a single raw JSON object -- no prose, no markdown \
code fences, nothing before or after it -- matching exactly this JSON Schema: \
{_CHARTER_JSON_SHAPE}"""


def _run_charter_pass(user_prompt: str) -> dict:
    # Delegates to deep_research's tool-runner helper (iterates the
    # BetaToolRunner to its final BetaMessage and parses the raw-JSON reply)
    # rather than duplicating that logic here.
    return deep_research._run_agentic_pass(user_prompt, _SYSTEM_PROMPT)


_MAPS_SCRAPE_ENV_VAR = "COMPANY_CHARTER_USE_MAPS_SCRAPE"
_MAPS_ROUTE_PATTERN = re.compile(
    r"Copy link\n.*?\n((?:\d+\s*hr\s*)?\d+\s*min)\n([\d.]+\s*km)\nvia ([^\n]+)"
)
_MAPS_MIN_TOKEN_OVERLAP = 0.5


def _tokenize(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


def _destination_plausibly_resolved(requested: str, resolved: str) -> bool:
    """Sanity check against Maps silently mis-resolving a bad/ambiguous
    destination to an unrelated nearby point instead of failing outright --
    confirmed live: a nonsense query like "Not a real place asdkjaskdj12345"
    still returns a route, just to a random unrelated address, with zero
    word overlap with what was asked for. A genuine match (even lightly
    reworded/expanded by Maps, e.g. a city name appended) retains most of
    the requested landmark's significant words."""
    requested_tokens = _tokenize(requested)
    if not requested_tokens:
        return True  # nothing meaningful to check against
    overlap = requested_tokens & _tokenize(resolved)
    return len(overlap) / len(requested_tokens) >= _MAPS_MIN_TOKEN_OVERLAP


def _lookup_maps_distance(origin: str, destination: str) -> dict | None:
    """Launches a headless Playwright browser, opens Google Maps driving
    directions from origin to destination, and reads the rendered
    duration/distance/route off the page. Returns None on ANY failure
    (selector miss, timeout, network, or Maps resolving `destination` to an
    unrelated place -- see _destination_plausibly_resolved) -- callers must
    fall back to the model's own web_search estimate rather than treat None
    as an error.

    Verified against the live site (short in-city and long inter-city
    routes, plus a deliberately bogus destination to confirm the
    mis-resolution check actually fires) before shipping, but this scrapes
    Google Maps' consumer web UI rather than their Distance Matrix/Routes
    API -- it can break without warning if Google changes their page, and
    likely doesn't comply with Google's Terms of Service (which is exactly
    why they sell an API for this). Off by default -- see
    _MAPS_SCRAPE_ENV_VAR."""
    from urllib.parse import quote

    from playwright.sync_api import sync_playwright

    url = f"https://www.google.com/maps/dir/{quote(origin)}/{quote(destination)}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=20000)
            page.get_by_role("radio", name="Driving").click(timeout=10000)
            page.wait_for_timeout(4000)
            dest_box = page.get_by_role("combobox").nth(1)
            resolved_destination = dest_box.locator("input, textarea").first.input_value()
            text = page.inner_text("body")
            browser.close()
        if not _destination_plausibly_resolved(destination, resolved_destination):
            return None
        match = _MAPS_ROUTE_PATTERN.search(text)
        if not match:
            return None
        duration, distance, route = match.groups()
        return {"duration": duration, "distance": distance, "route": route}
    except Exception:
        return None


def _refine_distances_with_maps(facts: dict, origin: str) -> dict:
    """Opt-in (COMPANY_CHARTER_USE_MAPS_SCRAPE=1) precision upgrade: replaces
    each distance entry's web_search-estimated distance_time/route_note with
    a live-scraped Google Maps driving route when the lookup succeeds,
    leaving the model's own estimate untouched (not silently dropped) when
    it doesn't -- so a scrape failure degrades to exactly today's behavior
    rather than blanking the field."""
    if os.environ.get(_MAPS_SCRAPE_ENV_VAR) != "1":
        return facts

    for entry in facts.get("distances", []):
        landmark = entry.get("landmark")
        if not landmark:
            continue
        result = _lookup_maps_distance(origin, landmark)
        if result:
            entry["distance_time"] = f"{result['duration']} / {result['distance']}"
            entry["route_note"] = f"via {result['route']} (live Google Maps driving route, scraped this run)"
    return facts


def _verify_one_field(field_name: str, field, gaps: list, stats: dict) -> None:
    """Shared check applied to every {value, source}-shaped field below --
    only claims sourced from a live URL are re-verified (a claim sourced
    from a downloaded primary document is already as good as it gets; there
    is no independent web check to run against it -- see
    _check_document_grounding for that category instead). A claim that was
    actually checked and failed is demoted into `gaps` with its value
    replaced, never left silently asserted. A claim whose check could not
    even run (no ANTHROPIC_API_KEY, network failure, rate limit --
    deep_research._verify_claim's "verification_error" status) is a
    different, weaker finding than "checked and failed": it is NOT proof the
    claim is wrong, so the value is left untouched, but the fact that no
    independent check actually happened is still surfaced as an explicit gap
    -- this is what lets that absence show up in the document on its own,
    rather than depending on whoever is running this pass to remember and
    manually re-check it themselves.

    `stats` is mutated in place ({"attempted", "confirmed", "failed",
    "could_not_run"}) so _compute_documentation_confidence_score can report a
    real Independent Verification Rate instead of parsing gap text."""
    if not isinstance(field, dict) or not field.get("value") or not field.get("source"):
        return
    if not field["source"].startswith("http"):
        return
    stats["attempted"] += 1
    verdict = deep_research._verify_claim(f"{field_name}: {field['value']}", field["source"])
    status = verdict.get("status")
    if status == "confirmed":
        stats["confirmed"] += 1
        return
    if status == "verification_error":
        stats["could_not_run"] += 1
        gaps.append(
            f"{field_name}: {field['value']} (NOT independently re-verified this pass -- "
            f"{verdict.get('reason', 'verification error')} -- treat as unconfirmed, not as checked-and-passed)"
        )
        return
    stats["failed"] += 1
    gaps.append(f"{field_name}: {field['value']} (verification: {verdict.get('reason', 'unconfirmed')})")
    field["value"] = "Not independently confirmed -- see gaps"


def _verify_material_claims(facts: dict) -> dict:
    """Re-checks the highest-stakes sourced claims (corporate identity, land
    identification, litigation, mortgage lender) the same way
    deep_research._verify_claim does. Unconfirmed ones move to `gaps`
    rather than staying asserted."""
    gaps = list(facts.get("gaps", []))
    stats = {"attempted": 0, "confirmed": 0, "failed": 0, "could_not_run": 0}
    for section_name in ("land_identification", "corporate_identity", "fsi_metrics"):
        for field_name, field in list(facts.get(section_name, {}).items()):
            _verify_one_field(field_name, field, gaps, stats)
    _verify_one_field("litigation_status", facts.get("litigation_status"), gaps, stats)
    facts["gaps"] = gaps
    facts.setdefault("_verification_stats", {})["url"] = stats
    return facts


# ---------------------------------------------------------------------------
# Document-grounding check -- a second, DIFFERENT failure mode from the web
# re-verification above. _verify_one_field only ever re-checks claims sourced
# from a live URL; a claim sourced from a downloaded primary document (Form
# B, the title report, MahaRERA's own raw JSON) has no independent web check
# to run against it -- by design, that document IS the primary source. But
# "no independent check exists" is not the same as "cannot be checked at
# all": if a name, code, or figure was simply misread or mistranscribed out
# of that document while building `facts`, nothing above would ever catch it.
# This check is mechanical (plain string matching, no API call, no network,
# works with or without ANTHROPIC_API_KEY) -- it asks only "do this field's
# distinguishing, verbatim-checkable details actually appear somewhere in the
# text of the document(s) it cites?", not "is this a good summary of them" --
# so it is deliberately narrow: it flags a name/code that doesn't appear
# anywhere in its cited source at all (the exact class of error just found:
# a wrong director name), not a paraphrase or a computed/aggregated number
# that legitimately won't appear as a literal string in the source.
# ---------------------------------------------------------------------------

_PROPER_NOUN_RUN_RE = re.compile(r"\b([A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+)+)\b")
# Mixed letter+digit codes (CIN, survey/plot numbers, ...) -- deliberately
# excludes pure-digit tokens (dates, counts) since those are frequently
# computed/aggregated rather than copied verbatim from the source text, and
# flagging them would be a false alarm, not a real transcription signal.
_ALPHANUMERIC_CODE_RE = re.compile(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9/\-]{5,}\b")


def _tokenize_significant(value: str) -> set:
    tokens = {m.group(1) for m in _PROPER_NOUN_RUN_RE.finditer(value)}
    tokens |= {m.group(0) for m in _ALPHANUMERIC_CODE_RE.finditer(value)}
    return tokens


def _document_grounding_text(source: str, extracted_docs: dict, category_data: dict, documents_manifest: list) -> str:
    """Best-effort combined text of whatever this field's `source` string
    actually references: any extracted-document label OR saved_filename
    mentioned by name (both are checked -- a `source` string commonly names
    the raw filename, e.g. "Mamurdi_Supplemental title report.pdf", while
    extracted_docs is keyed by the manifest's often-different display label,
    e.g. "Legal Title report"; matching on label alone silently missed the
    real extracted text for every field authored this way, confirmed live),
    plus any `raw/<category>.json` mention resolved against that category's
    own already-fetched payload. Returns "" if the source doesn't resolve to
    anything we actually have text for (e.g. a document that wasn't opened
    this pass) -- callers must treat that as "nothing to check", not a
    failure."""
    source_lower = source.lower()
    matched_labels = {label for label in extracted_docs if label.lower() in source_lower}
    for entry in documents_manifest:
        label = entry.get("label") or entry.get("saved_filename") or "Unnamed document"
        filename = entry.get("saved_filename") or ""
        if label in extracted_docs and filename and filename.lower() in source_lower:
            matched_labels.add(label)
    combined = [extracted_docs[label] for label in matched_labels]
    for category, data in category_data.items():
        if f"raw/{category}.json" in source or f"raw\\{category}.json" in source:
            combined.append(json.dumps(data or {}))
    return "\n".join(combined)


_DOCUMENT_CLAIM_VERIFY_SYSTEM_PROMPT = """Re-check one specific claim against the actual extracted text of \
the local document it cites. This is NOT a web claim -- you are given the document's own text directly \
below; do not use web_search, judge only from the text provided (a faithful paraphrase or a computed/ \
summarized statement should still be judged "confirmed" if the text supports it -- this is a meaning \
check, not an exact-string match; that cheaper check already ran and failed, which is why you're being \
asked).

Your FINAL reply must be ONLY a single raw JSON object -- no prose, no markdown code fences -- matching \
exactly this shape: {"status": "confirmed" | "unsupported" | "stale", "reason": "one sentence"}"""


_GROK_ENV_VAR = "XAI_API_KEY"
_GROK_MODEL = "grok-4.5"
_GROK_BASE_URL = "https://api.x.ai/v1"


def _parse_json_text(text: str) -> dict:
    """Strips a leading/trailing markdown code fence if present, then parses
    raw JSON -- shared by every "final reply must be ONLY a JSON object"
    verifier prompt in this file, regardless of which provider answered."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _verify_document_claim_via_grok(claim: str, document_text: str) -> dict:
    """Fallback path for _verify_document_claim when Claude can't run the
    check (missing/invalid ANTHROPIC_API_KEY, rate limit, etc.) but an xAI
    key is available -- genuine redundancy against one provider being down
    or unconfigured, not a way to make this check API-free (it still needs
    a working key, just not necessarily this one). Uses the OpenAI-compatible
    SDK already installed in this environment, pointed at xAI's endpoint, per
    xAI's own documented pattern (client.responses.create with an `input`
    list, not client.chat.completions.create) -- response-text extraction
    tries the documented output_text convenience property first and falls
    back to walking `response.output` directly, since that exact shape
    wasn't independently confirmed against a live call (no XAI_API_KEY was
    available in this environment to test against)."""
    from openai import OpenAI

    client = OpenAI(api_key=os.environ[_GROK_ENV_VAR], base_url=_GROK_BASE_URL)
    prompt = f"Claim: {claim}\n\nExtracted document text:\n{document_text[:_MAX_CHARS_PER_DOC]}"
    response = client.responses.create(
        model=_GROK_MODEL,
        input=[
            {"role": "system", "content": _DOCUMENT_CLAIM_VERIFY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    text = getattr(response, "output_text", None)
    if not text:
        text = response.output[0].content[0].text
    result = _parse_json_text(text)
    if result.get("status") not in ("confirmed", "unsupported", "stale"):
        return {"status": "unsupported", "reason": "verifier returned an unrecognized status"}
    return result


def _verify_document_claim(claim: str, document_text: str) -> dict:
    """Same {status, reason} shape as deep_research._verify_claim, but the
    model is handed the document's own extracted text directly instead of
    being told to search the web for a URL. This is strictly an escalation
    on top of _check_document_grounding's free mechanical check, called only
    for a field that check couldn't confirm -- it costs a real API call and
    is bounded by the same extraction/OCR quality as the mechanical check: it
    cannot confirm something the extracted text genuinely doesn't contain,
    only recognize a paraphrase of something the text DOES contain -- exactly
    the gap a literal substring match leaves open.

    Tries Claude first (the project's primary provider); if that fails for
    any reason and an xAI key is configured, falls back to Grok before
    giving up -- redundancy against one provider being down or unconfigured,
    not a way to avoid needing a working key at all. Only returns
    "verification_error" if neither provider could complete the check."""
    prompt = f"Claim: {claim}\n\nExtracted document text:\n{document_text[:_MAX_CHARS_PER_DOC]}"
    try:
        result = deep_research._run_agentic_pass(prompt, _DOCUMENT_CLAIM_VERIFY_SYSTEM_PROMPT)
        if result.get("status") not in ("confirmed", "unsupported", "stale"):
            return {"status": "unsupported", "reason": "verifier returned an unrecognized status"}
        return result
    except Exception as claude_error:
        if not os.environ.get(_GROK_ENV_VAR):
            return {"status": "verification_error", "reason": f"verification could not run: {claude_error}"}
        try:
            return _verify_document_claim_via_grok(claim, document_text)
        except Exception as grok_error:
            return {
                "status": "verification_error",
                "reason": f"verification could not run on either provider -- Claude: {claude_error}; Grok: {grok_error}",
            }


def _check_document_grounding(facts: dict, extracted_docs: dict, category_data: dict, documents_manifest: list) -> dict:
    """Flags, as an explicit gap, a {value, source}-shaped field whose
    distinguishing details don't appear anywhere in the text of the local
    document(s)/raw-JSON it cites. Only runs against non-URL sources;
    anything starting with "http" is already covered by
    _verify_material_claims.

    A field that fails the free mechanical check gets one more chance: an
    LLM call (_verify_document_claim) that judges the claim against the same
    extracted text, catching a faithful paraphrase the substring match can't
    recognize. If that second check confirms it, no gap is added at all --
    the mechanical flag was a false alarm, not an error. If it also fails,
    the field is demoted the same way _verify_material_claims demotes a
    failed web claim: value replaced, explicit gap recorded -- we now have a
    real, checked failure, not just a heuristic flag. Only a genuine
    "verification_error" (the LLM check couldn't even be attempted) falls
    back to the original soft "manual re-check" gap without touching the
    value, since a heuristic flag alone is not proof of an error."""
    gaps = list(facts.get("gaps", []))
    stats = {"attempted": 0, "confirmed": 0, "failed": 0, "could_not_run": 0}
    fields_to_check = [
        (f"{section}.{name}", field)
        for section in ("land_identification", "corporate_identity", "fsi_metrics")
        for name, field in facts.get(section, {}).items()
    ]
    fields_to_check.append(("litigation_status", facts.get("litigation_status")))

    for field_name, field in fields_to_check:
        if not isinstance(field, dict) or not field.get("value") or not field.get("source"):
            continue
        if field["source"].startswith("http"):
            continue
        significant = _tokenize_significant(field["value"])
        if not significant:
            continue  # nothing verbatim-checkable here (pure narrative/summary text)
        grounding_text = _document_grounding_text(field["source"], extracted_docs, category_data, documents_manifest)
        if not grounding_text:
            continue  # cited document wasn't actually opened this pass -- nothing to check against
        stats["attempted"] += 1
        found = {t for t in significant if t.lower() in grounding_text.lower()}
        if found:
            stats["confirmed"] += 1
            continue

        verdict = _verify_document_claim(f"{field_name}: {field['value']}", grounding_text)
        status = verdict.get("status")
        if status == "confirmed":
            stats["confirmed"] += 1
            continue  # mechanical flag was a false alarm -- a faithful paraphrase, not an error
        sample = ", ".join(list(significant)[:3])
        if status == "verification_error":
            stats["could_not_run"] += 1
            gaps.append(
                f"{field_name}: none of its distinguishing details ({sample}) were found verbatim in "
                f"the text of its cited source document, and a second-opinion check could not run "
                f"({verdict.get('reason', 'verification error')}) -- flagged for a manual re-check "
                f"rather than trusted on transcription alone."
            )
            continue
        stats["failed"] += 1
        gaps.append(
            f"{field_name}: {field['value']} (neither a literal match nor an independent re-check against "
            f"its cited source document's text supported this claim -- {verdict.get('reason', 'unconfirmed')})"
        )
        field["value"] = "Not independently confirmed -- see gaps"
    facts["gaps"] = gaps
    facts.setdefault("_verification_stats", {})["document"] = stats
    return facts


# ---------------------------------------------------------------------------
# Credit-rating lookup (Phase 2) -- confirmed live against ICRA's real public
# rating database: a "Search by entity" box on icra.in backs onto a genuine
# AJAX endpoint (POST /Rating/GetRatingCompanys, requires the
# X-Requested-With: XMLHttpRequest header or it 404s -- an ASP.NET MVC
# AJAX-only-action quirk, not a real access restriction) returning
# {id, label} matches; the resulting /Rating/RatingDetails page is fully
# server-rendered (a plain requests.get() gets the real content, no JS
# execution needed) and its first table is always "Instrument | Ratings".
# Live-tested end to end against "Godrej Properties Limited" -- returned
# real, current ratings (ICRA A1+ commercial paper, AA+ (Stable) long-term).
#
# As of this pass, ICRA is no longer the only agency checked -- see
# _lookup_infomerics_rating below, added after ICRA-only coverage produced
# a real, misleading "not found" for a promoter's sister entity that was
# actually rated (by Infomerics, not ICRA). CRISIL's equivalent
# company-rating search still wasn't confirmed reverse-engineerable in the
# time spent on it (its site exposes an SME-specific autocomplete on a
# different product, not the main corporate-rating search) -- rather than
# build against an unconfirmed mechanism, coverage stays scoped to what
# actually works. CRISIL/CARE/India Ratings coverage can be added later if
# a working search endpoint is found for any of them.
#
# Matches ONLY on an exact (case-insensitive) name match against each
# agency's own company list -- never a fuzzy "probably the same company"
# guess, since misattributing a rating to the wrong legal entity would be
# a serious factual error in a due-diligence document. A promoter/SPV
# having no public rating from either agency is the NORMAL case, not a red
# flag: both only rate developers that sought a public rating (typically
# larger, listed, or NCD-issuing entities) -- most MahaRERA promoters are
# too small/private to ever be rated. Callers who also want to check a
# distinct parent/group entity (e.g. "Godrej Properties Limited" for
# promoter "Godrej Skyline Developers Limited") must call this a second
# time with that name explicitly and label the result as the PARENT's
# rating, never as if it were the promoter's own -- this function does not
# guess a parent itself.
# ---------------------------------------------------------------------------

_ICRA_SEARCH_URL = "https://www.icra.in/Rating/GetRatingCompanys"
_ICRA_DETAIL_URL = "https://www.icra.in/Rating/RatingDetails"


def _icra_search_companies(name: str) -> list:
    resp = requests.post(
        _ICRA_SEARCH_URL,
        data={"term": name},
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _icra_fetch_rating_detail(company_id: str, company_name: str) -> dict:
    from urllib.parse import quote

    from bs4 import BeautifulSoup

    url = f"{_ICRA_DETAIL_URL}?CompanyId={company_id}&CompanyName={quote(company_name)}"
    resp = requests.get(url, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    instruments = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if headers[:2] == ["Instrument", "Ratings"]:
            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) >= 2 and cells[0]:
                    instruments.append({"instrument": cells[0], "rating": cells[1]})
            break

    return {"instruments": instruments, "url": url}


def _lookup_icra_rating(company_name: str) -> dict:
    try:
        matches = _icra_search_companies(company_name)
    except (requests.RequestException, ValueError) as e:
        return {"found": False, "note": f"ICRA lookup could not run this pass: {e}"}

    exact = next(
        (m for m in matches if str(m.get("label", "")).strip().lower() == company_name.strip().lower()),
        None,
    )
    if not exact:
        return {"found": False, "note": "No public ICRA rating found for this exact legal entity name."}

    try:
        detail = _icra_fetch_rating_detail(exact["id"], exact["label"])
    except requests.RequestException as e:
        return {"found": False, "note": f"Found a matching ICRA entity ({exact['label']}) but could not fetch its rating detail this pass: {e}"}

    return {
        "found": True,
        "agency": "ICRA",
        "company_name": exact["label"],
        "instruments": detail["instruments"],
        "url": detail["url"],
    }


# ---------------------------------------------------------------------------
# Infomerics credit-rating lookup -- confirmed live against Infomerics'
# real public rating database (infomerics.com), added specifically because
# ICRA-only coverage produced a misleading "not found" for a real MahaRERA
# promoter's sister entity (Pranami Estates Private Limited) that turned
# out to be actively rated by Infomerics, not ICRA -- a materially
# different result (a live downgrade trajectory: BBB- in 2021 down to
# BB (INC), Negative outlook, by March 2026) that ICRA-only checking would
# have silently missed and reported as "no rating found."
#
# Mechanism: the site's header search box (id="search") is a plain
# client-side filter over a public autocomplete API --
# cms.infomerics.com/api/companies/autocomplete?query=<name> -- returning
# [{CompanyName, slug, documentId}, ...]; the matched `slug` then fetches
# cms.infomerics.com/api/companies/<slug> for the CURRENT instrument
# rating(s) (instrument, amount, rating, outlook, date). Both are plain,
# unauthenticated JSON GETs -- no CAPTCHA, no session, confirmed with a
# bare requests.get() (no custom headers needed, unlike ICRA's
# X-Requested-With quirk). Only the CURRENT rating is fetched here, not
# the full historical rationale table also shown on the site's own
# press-release page -- that would need a second, not-yet-identified
# endpoint, and current-only already matches ICRA's own scope above.
# ---------------------------------------------------------------------------

_INFOMERICS_AUTOCOMPLETE_URL = "https://cms.infomerics.com/api/companies/autocomplete"
_INFOMERICS_COMPANY_URL = "https://cms.infomerics.com/api/companies/{slug}"


def _infomerics_search_companies(name: str) -> list:
    resp = requests.get(_INFOMERICS_AUTOCOMPLETE_URL, params={"query": name}, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _infomerics_fetch_rating_detail(slug: str) -> dict:
    url = _INFOMERICS_COMPANY_URL.format(slug=slug)
    resp = requests.get(url, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    instruments = []
    for inst in data.get("companyInstrument", []):
        if inst.get("isPast"):
            continue
        rating = inst.get("Rating", "")
        outlook = (inst.get("outlook") or {}).get("Title")
        if outlook:
            rating = f"{rating} (Outlook: {outlook})"
        instruments.append({
            "instrument": f"{inst.get('InstrumentTitle', '')} ({inst.get('InstrumentAmount', '')})".strip(),
            "rating": rating,
        })

    return {"instruments": instruments, "url": f"https://www.infomerics.com/pressrelease/{slug}"}


def _lookup_infomerics_rating(company_name: str) -> dict:
    try:
        matches = _infomerics_search_companies(company_name)
    except (requests.RequestException, ValueError) as e:
        return {"found": False, "note": f"Infomerics lookup could not run this pass: {e}"}

    exact = next(
        (m for m in matches if str(m.get("CompanyName", "")).strip().lower() == company_name.strip().lower()),
        None,
    )
    if not exact:
        return {"found": False, "note": "No public Infomerics rating found for this exact legal entity name."}

    try:
        detail = _infomerics_fetch_rating_detail(exact["slug"])
    except requests.RequestException as e:
        return {"found": False, "note": f"Found a matching Infomerics entity ({exact['CompanyName']}) but could not fetch its rating detail this pass: {e}"}

    return {
        "found": True,
        "agency": "Infomerics",
        "company_name": exact["CompanyName"],
        "instruments": detail["instruments"],
        "url": detail["url"],
    }


_CREDIT_RATING_AGENCIES = (
    ("ICRA", _lookup_icra_rating),
    ("Infomerics", _lookup_infomerics_rating),
)


def lookup_credit_rating(company_name: str) -> dict:
    """Checks EVERY agency above (currently ICRA and Infomerics) for a
    public rating under an EXACT (case-insensitive) match on
    `company_name` -- never a fuzzy "probably the same company" guess,
    since misattributing a rating to the wrong legal entity would be a
    serious factual error in a due-diligence document. Always checks all
    agencies, even after an earlier one already found something -- so that
    if two agencies rate the same entity, BOTH ratings surface side by
    side for comparison, rather than silently showing only the first
    match. (This costs one extra request per additional agency versus a
    first-match-wins design, which is negligible next to the value of
    catching a real disagreement between agencies.) Returns:
      {"found": True, "ratings": [{"agency": "ICRA" | "Infomerics",
       "company_name": <matched label>, "instruments": [{"instrument":
       ..., "rating": ...}, ...], "url": ...}, ...], "not_found_agencies":
       [<agency names with no match>]}
        -- `ratings` holds one entry per agency that found something (in
        agency-check order); `not_found_agencies` names the rest so a
        reader knows they were checked, not skipped.
      {"found": False, "note": "..."} -- an honest explanation naming
      every agency actually checked, not an error, when nothing matches
      anywhere or every request failed. A promoter/SPV having no public
      rating from any agency is the NORMAL case, not a red flag: these
      agencies only rate developers that sought a public rating (typically
      larger, listed, or NCD-issuing entities); most MahaRERA promoters
      are too small or private to ever be rated. Callers who also want to
      check a distinct parent/group entity (e.g. "Godrej Properties
      Limited" for promoter "Godrej Skyline Developers Limited") must call
      this a second time with that name explicitly and label the result as
      the PARENT's rating, never as if it were the promoter's own -- this
      function does not guess a parent itself."""
    ratings = []
    not_found_agencies = []
    for agency_name, lookup_fn in _CREDIT_RATING_AGENCIES:
        result = lookup_fn(company_name)
        if result.get("found"):
            ratings.append(result)
        else:
            not_found_agencies.append(agency_name)

    if ratings:
        return {"found": True, "ratings": ratings, "not_found_agencies": not_found_agencies}

    agency_list = ", ".join(name for name, _ in _CREDIT_RATING_AGENCIES)
    return {
        "found": False,
        "note": (
            f"No public rating found for this exact legal entity name from any agency checked "
            f"({agency_list}). This is NOT itself a red flag -- these agencies only rate developers "
            f"that sought a public rating (typically larger, listed, or NCD-issuing entities); most "
            f"MahaRERA promoters are too small or private to ever be rated."
        ),
    }


# ---------------------------------------------------------------------------
# IBBI insolvency check -- confirmed live against IBBI's real Corporate
# Debtor Master Data. The site's own search form (/claims/corporate-personals)
# is CSRF-protected, but submitting it (by CIN) redirects to a plain,
# directly-linkable detail URL -- https://ibbi.gov.in/claims/inner-process/
# <CIN> -- which a fresh, cookie-less requests.get() reaches identically
# (confirmed: no CSRF token or session state needed for this specific URL).
# Also confirmed to accept an LLPIN in the same URL slot (e.g. AAI-5299 for
# Trimity Realty LLP) -- LLPs are "corporate persons" under the IBC too, and
# the identifier isn't validated by format, just passed through.
#
# IMPORTANT CORRECTION (found while testing the LLPIN path): the "no
# process" result is WEAKER evidence than earlier phrasing here implied.
# Feeding this endpoint a deliberately fake identifier (e.g. "ZZZ-0000")
# returns the exact same "ASSIGNMENT NOT APPROVED YET" page as a real,
# genuine CIN/LLPIN with no insolvency history -- the page does not
# validate or discriminate its input at all. So "found_process: False"
# confirms only that IBBI has no ACTIVE PROCESS PAGE at that URL; it is NOT
# proof the identifier itself was recognized as a real, existing entity.
# That confirmation has to come from elsewhere (ZaubaCorp/registry mirror),
# same as always. No real company/LLP with an ACTIVE/PAST insolvency
# process was available to test the positive-match case against, so that
# path still surfaces the raw extracted status text for a human to read
# rather than attempting to classify or summarize content this function was
# never validated against.
# ---------------------------------------------------------------------------

_IBBI_INNER_PROCESS_URL = "https://ibbi.gov.in/claims/inner-process/{cin}"
_IBBI_NO_PROCESS_PHRASE = "ASSIGNMENT NOT APPROVED YET"
_CIN_RE = re.compile(r"\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")
_LLPIN_RE = re.compile(r"\b[A-Z]{3}-\d{4}\b")


def extract_cin(text: str) -> str | None:
    """Pulls a standard-format Indian CIN (e.g. U45309MH2016PLC287858) out
    of a free-text field -- corporate_identity.cin_llpin's value is prose
    ("U45309MH2016PLC287858 (incorporated 22 November 2016; ROC Mumbai) --
    ..."), not a bare code, so this is needed before the IBBI/CIN-keyed
    lookups below can use it."""
    match = _CIN_RE.search(text or "")
    return match.group(0) if match else None


def extract_llpin(text: str) -> str | None:
    """Pulls a standard-format Indian LLPIN (e.g. AAI-5299) out of a
    free-text field, the LLP counterpart of extract_cin() above. A
    completely different format from CIN (3 letters, a hyphen, 4 digits,
    vs. CIN's 21-character U/L-prefixed code) -- confirmed live against
    Trimity Realty LLP's real LLPIN (AAI-5299), and confirmed NOT to
    false-positive-match on real CIN text (extract_cin's own format never
    contains a bare "XXX-9999" substring)."""
    match = _LLPIN_RE.search(text or "")
    return match.group(0) if match else None


def lookup_ibbi_insolvency_status(cin: str) -> dict:
    """Checks IBBI's public Corporate Debtor Master Data for an insolvency
    process tied to `cin` (a CIN or an LLPIN -- confirmed live against
    both). Returns:
      {"found_process": False, "status_text": "ASSIGNMENT NOT APPROVED YET", "url": ...}
        -- the ordinary, clean case: no IBC process recorded against this
        identifier. NOTE: this same result is also what IBBI returns for a
        completely fake/nonexistent identifier (confirmed live) -- it is
        NOT proof the identifier itself is real, only that no active
        process page exists at that URL. Confirm the entity's existence
        via a registry mirror (ZaubaCorp) separately, not via this result.
      {"found_process": True, "status_text": <raw extracted text>, "url": ...}
        -- something other than the known "no process" phrase was found;
        the raw text is surfaced rather than classified, since this
        function's positive-match path was never validated against a real
        example (see module note above) -- a human should read it directly.
      {"found_process": None, "note": "..."} -- the lookup itself failed,
        or no identifier was available to check."""
    if not cin or not cin.strip():
        return {"found_process": None, "note": "no CIN provided to check"}
    url = _IBBI_INNER_PROCESS_URL.format(cin=cin.strip())
    try:
        resp = requests.get(url, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"found_process": None, "note": f"IBBI lookup could not run this pass: {e}", "url": url}

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    if _IBBI_NO_PROCESS_PHRASE in text:
        return {"found_process": False, "status_text": _IBBI_NO_PROCESS_PHRASE, "url": url}

    lines = [ln for ln in text.split("\n") if ln.strip()]
    cin_idx = next((i for i, ln in enumerate(lines) if "CIN No" in ln), None)
    context = lines[cin_idx:cin_idx + 10] if cin_idx is not None else lines[:10]
    return {"found_process": True, "status_text": " | ".join(context), "url": url}


# ---------------------------------------------------------------------------
# CIN -> company profile / director-group lookup (ZaubaCorp). Confirmed live
# and scriptable with no browser needed: ZaubaCorp's company page URL only
# needs a correct CIN in the second path segment -- the company-name segment
# is cosmetic and can be any placeholder ("X"), and a real CIN 200s and
# redirects to the true canonical URL (e.g. /company/X/U45500MH2016PTC286108
# -> /WATERTIGHT-DEVELOPERS-PRIVATE-LIMITED-U45500MH2016PTC286108). A CIN
# with no matching company instead redirects to /companysearchresults/X,
# which has zero `li.row` detail elements -- a clean, reliable not-found
# signal. Live-tested against two real, unrelated CINs (Watertight
# Developers Private Limited, a private SPV; India Homes Limited, a listed
# public company formerly India Steel Works Limited) and a deliberately
# fake CIN, confirming both the found and not-found paths.
#
# The page's core details sit in `<li class="row"><span>label</span>
# <label>value</label></li>` pairs -- not a <table> -- which is why earlier
# passes over this page's `<table>` elements alone missed them. Director
# and group-affiliation data IS in tables, each preceded by its own heading:
#   - "Current/Past Directors & Key Managerial Personnel of X" -- this
#     company's own director roster (DIN, name, designation, dates).
#   - "Other Directorships of <NAME>" -- one table per director named in
#     the roster above, listing every OTHER company that same person is or
#     was a director of. This is the group/board-overlap signal, and it
#     arrives on the same page fetch -- no per-director follow-up request
#     is needed.
#   - "Companies with Similar Address" -- a second, independent
#     group-affiliation signal (shared registered office, not shared
#     directors); confirmed several Gupta-family-linked entities share
#     Watertight's exact registered address.
#   - "Subsidiaries, Associate Companies & Joint Ventures of X" -- present
#     for India Homes Limited (a listed company that files this), absent
#     for Watertight (a private SPV that doesn't); a third signal, this one
#     naming an actual percentage-of-shares-held figure.
#
# Shareholding is deliberately NOT surfaced as a precise ownership
# breakdown: per-shareholder/promoter percentage holdings are gated behind
# ZaubaCorp's paid report for every CIN tested here (both the private SPV
# and the listed company) -- only aggregate authorised/paid-up capital and
# (for companies that file it) subsidiary/associate stakes are free. Callers
# needing an actual cap table should treat this the same way the ICRA/IBBI
# checks treat their own gaps: report what's genuinely available, flag the
# rest as not publicly obtainable here rather than guessing.
# ---------------------------------------------------------------------------

_ZAUBACORP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def _zaubacorp_fetch(cin: str):
    """Returns (soup, canonical_url) for a real CIN, or None if ZaubaCorp
    has no company matching it. Raises requests.RequestException on a
    network/HTTP failure -- callers convert that into an honest gap."""
    from bs4 import BeautifulSoup

    url = f"https://www.zaubacorp.com/company/X/{cin.strip()}"
    resp = requests.get(url, headers=_ZAUBACORP_HEADERS, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    if "companysearchresults" in resp.url:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    if not soup.find("li", class_="row"):
        return None
    return soup, resp.url


def _zaubacorp_core_fields(soup) -> dict:
    fields = {}
    for li in soup.find_all("li", class_="row"):
        parts = li.find_all(["span", "label"])
        if len(parts) >= 2:
            fields[parts[0].get_text(strip=True)] = parts[1].get_text(strip=True)
    return fields


def _zaubacorp_director_table(table) -> list:
    rows = []
    header_cells = [th.get_text(strip=True) for th in table.find_all("th")]
    for tr in table.find_all("tr")[1:]:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) == len(header_cells) and any(cells):
            rows.append(dict(zip(header_cells, cells)))
    return rows


_ZAUBACORP_GATED_NOTE = "This information is part of the paid company report.Purchase Report"


def _zaubacorp_clean(value: str | None) -> str | None:
    """None instead of ZaubaCorp's free-tier paywall placeholder, so
    callers don't mistake "gated" for a genuine reported value."""
    if value is None or value.strip() == _ZAUBACORP_GATED_NOTE:
        return None
    return value


def lookup_company_by_cin(cin: str) -> dict:
    """Looks up a company's own registration profile and director roster
    from ZaubaCorp by CIN. Returns:
      {"found": True, "name": ..., "status": ..., "class_of_company": ...,
       "incorporation_date": ..., "roc": ..., "registered_address": ...,
       "authorised_capital": ..., "paid_up_capital": ...,
       "current_directors": [{"din", "name", "designation", "appointment_date"}, ...],
       "past_directors": [...], "shareholding_note": "...", "url": ...}
    or {"found": False, "note": "..."} -- either a genuine no-match or a
    request failure, never guessed."""
    if not cin or not cin.strip():
        return {"found": False, "note": "no CIN provided to look up"}
    try:
        result = _zaubacorp_fetch(cin)
    except requests.RequestException as e:
        return {"found": False, "note": f"ZaubaCorp lookup could not run this pass: {e}"}
    if result is None:
        return {"found": False, "note": f"no ZaubaCorp record found for CIN {cin.strip()}"}
    soup, url = result

    fields = _zaubacorp_core_fields(soup)
    current_directors, past_directors = [], []
    for table in soup.find_all("table"):
        heading = table.find_previous(["h1", "h2", "h3", "h4", "h5"])
        heading_text = heading.get_text(strip=True) if heading else ""
        if heading_text.startswith("Current Directors"):
            current_directors = _zaubacorp_director_table(table)
        elif heading_text.startswith("Past Directors"):
            past_directors = _zaubacorp_director_table(table)

    return {
        "found": True,
        "name": fields.get("Name"),
        "cin": fields.get("CIN", cin.strip()),
        "status": fields.get("Company Status"),
        "class_of_company": fields.get("Class of Company"),
        "company_category": fields.get("Company Category"),
        "roc": fields.get("ROC"),
        "incorporation_date": fields.get("Date of Incorporation"),
        "registered_address": fields.get("Address"),
        "authorised_capital": _zaubacorp_clean(fields.get("Authorised Share Capital")),
        "paid_up_capital": _zaubacorp_clean(fields.get("Paid-up Share Capital")),
        "current_directors": current_directors,
        "past_directors": past_directors,
        "shareholding_note": (
            "Per-shareholder/promoter shareholding percentages are gated behind ZaubaCorp's paid "
            "report and were not available on this pass; only aggregate authorised/paid-up capital "
            "above is free. This is not itself a red flag for a private company -- detailed cap "
            "tables are rarely public for unlisted entities."
        ),
        "url": url,
    }


def find_group_companies_by_cin(cin: str) -> dict:
    """Cross-references directors and registered address of the company at
    `cin` against every OTHER company ZaubaCorp lists them against, to
    surface a likely corporate group -- without guessing: every entry
    names the concrete signal (a specific shared director, or a shared
    registered office) that ties it to the target, so a reader can judge
    the strength of the link themselves. Returns:
      {"found": True, "companies": [{"cin", "name", "basis": [...]}, ...], "url": ...}
    or {"found": False, "note": "..."}."""
    if not cin or not cin.strip():
        return {"found": False, "note": "no CIN provided to look up"}
    try:
        result = _zaubacorp_fetch(cin)
    except requests.RequestException as e:
        return {"found": False, "note": f"ZaubaCorp lookup could not run this pass: {e}"}
    if result is None:
        return {"found": False, "note": f"no ZaubaCorp record found for CIN {cin.strip()}"}
    soup, url = result

    by_cin: dict[str, dict] = {}

    def _add(other_cin: str, name: str, basis: str):
        other_cin = (other_cin or "").strip()
        name = (name or "").strip()
        if not other_cin or not name or other_cin == cin.strip():
            return
        entry = by_cin.setdefault(other_cin, {"cin": other_cin, "name": name, "basis": []})
        entry["basis"].append(basis)

    for table in soup.find_all("table"):
        heading = table.find_previous(["h1", "h2", "h3", "h4", "h5"])
        heading_text = heading.get_text(strip=True) if heading else ""

        if heading_text.startswith("Other Directorships of "):
            director = heading_text[len("Other Directorships of "):].strip()
            for row in _zaubacorp_director_table(table):
                designation = row.get("Designation", "").strip()
                basis = f"shared director: {director}" + (f" ({designation})" if designation else "")
                _add(row.get("CIN"), row.get("Company Name"), basis)

        elif heading_text == "Companies with Similar Address":
            for row in _zaubacorp_director_table(table):
                _add(row.get("CIN"), row.get("Company Name"), "shared registered office")

        elif heading_text.startswith("Subsidiaries, Associate Companies"):
            for row in _zaubacorp_director_table(table):
                pct = row.get("Percentage of Shares Held", "").strip()
                basis = "subsidiary/associate/JV" + (f" ({pct} shares held)" if pct else "")
                _add(row.get("Company Identifier"), row.get("Name"), basis)

    return {"found": True, "companies": list(by_cin.values()), "url": url}


# ---------------------------------------------------------------------------
# CTS -> land-record lookup (Maha Bhulekh Property Card, see mahabhumi.py).
# Deliberately NOT wired to run automatically like the CIN checks above:
# mahabhumi.fetch_property_card() opens a visible browser and blocks waiting
# for a human to solve a fresh CAPTCHA on every single call (that site
# grants no reusable session -- see mahabhumi.py's own module note), so
# running it unconditionally would silently stall every automated Charter
# pass for up to CAPTCHA_TIMEOUT_SECONDS. Instead this follows the exact
# same opt-in convention already used for reviews.json just above: it does
# nothing unless a human has dropped output/<reg_no>/cts_lookup_input.json
# with the office/village already resolved to the site's exact Marathi
# labels (via `python mahabhumi.py offices/villages ...`) -- this never
# guesses one, for the same reason ZaubaCorp's CIN lookup never fuzzy-
# matches a company name.
# ---------------------------------------------------------------------------

def run_cts_land_lookup(facts: dict, reg_no: str, output_dir: str = config.OUTPUT_ROOT) -> dict:
    """Runs the CTS -> Property Card lookup only if output/<reg_no>/
    cts_lookup_input.json exists, containing:
      {"district": "Pune", "office": "<exact Marathi label from
       mahabhumi.list_offices>", "village": "<exact Marathi label from
       mahabhumi.list_villages>", "cts_number": "100", "mobile": "..."}
    Silently returns facts unchanged if that file is absent -- the ordinary
    case for every automated run. When present, opens a visible browser and
    blocks for up to CAPTCHA_TIMEOUT_SECONDS waiting for a human to solve
    the CAPTCHA (see mahabhumi.fetch_property_card)."""
    input_path = os.path.join(output_dir, reg_no, "cts_lookup_input.json")
    if not os.path.exists(input_path):
        return facts

    import mahabhumi

    with open(input_path, "r", encoding="utf-8") as f:
        cts_input = json.load(f)

    required = ("district", "office", "village", "cts_number", "mobile")
    missing = [k for k in required if not cts_input.get(k)]
    if missing:
        facts["cts_land_record_check"] = {"found": False, "note": f"{input_path} is missing required field(s): {', '.join(missing)}"}
        return facts

    recorded_cts = ((facts.get("land_identification", {}).get("survey_cts_plot_numbers") or {}).get("value") or "")
    if recorded_cts and cts_input["cts_number"] not in recorded_cts:
        facts.setdefault("gaps", []).append(
            f"CTS land-record lookup: cts_lookup_input.json's CTS number ({cts_input['cts_number']}) does not "
            f"appear in this Charter's own recorded survey_cts_plot_numbers ({recorded_cts!r}) -- confirm "
            f"these refer to the same plot before trusting the Property Card result below."
        )

    print(f"\n[INFO] {input_path} found -- resolving CTS {cts_input['cts_number']} candidates...")
    try:
        candidates_result = mahabhumi.search_cts_candidates(
            cts_input["district"], cts_input["office"], cts_input["village"], cts_input["cts_number"]
        )
    except Exception as e:
        facts["cts_land_record_check"] = {"found": False, "note": f"CTS candidate search could not run this pass: {e}"}
        return facts

    if not candidates_result.get("found"):
        facts["cts_land_record_check"] = {"found": False, "note": candidates_result.get("note", "CTS candidate search failed")}
        return facts

    candidates = candidates_result["candidates"]
    if cts_input["cts_number"] not in candidates:
        facts["cts_land_record_check"] = {
            "found": False,
            "note": (
                f"CTS number {cts_input['cts_number']!r} is not an exact match against the site's own "
                f"candidates for this village ({candidates}) -- update cts_lookup_input.json with the exact "
                f"value and re-run rather than guessing."
            ),
        }
        return facts

    print(f"[INFO] Opening a browser to fetch the Property Card -- please solve the CAPTCHA when it appears.")
    try:
        result = mahabhumi.fetch_property_card(
            cts_input["district"], cts_input["office"], cts_input["village"],
            cts_input["cts_number"], cts_input["mobile"],
        )
    except (mahabhumi.CaptchaTimeoutError, mahabhumi.BrowserClosedError, mahabhumi.AmbiguousSelectionError) as e:
        result = {"found": False, "note": f"CTS Property Card lookup did not complete: {e}"}
    except Exception as e:
        result = {"found": False, "note": f"CTS Property Card lookup could not run this pass: {e}"}

    facts["cts_land_record_check"] = result
    if result.get("found"):
        facts.setdefault("sources", []).append({
            "label": "Maha Bhulekh Property Card",
            "ref": f"CTS {cts_input['cts_number']}, {cts_input['village']} -- {result.get('url', '')}",
            "topic": "land_record",
            "published_date": "unknown",
            "accessed_date": datetime.now().strftime("%Y-%m-%d"),
        })
    return facts


# ---------------------------------------------------------------------------
# MahaRERA Orders/Judgments search -- confirmed live and scriptable with no
# browser needed. maharera.maharashtra.gov.in/orders-judgements is a Drupal
# form (POST, requires a fresh form_build_id read from a prior GET -- no
# CSRF/form_token beyond that). Live-tested: searching "Alta Monte" returned
# 11 real results; a search for a project with no published judgment yet
# (Godrej Park Greens) correctly returns zero, not an error.
#
# This complements, not replaces, api_client.download_complaint_orders --
# that mechanism only covers complaints (whose own record already carries
# an order reference); appeals.json carries no such reference at all
# (lastOrder/lastRoznama are always "NA"), which is exactly why this
# external search is needed for appeal-level judgments.
#
# Two real quirks worth documenting:
#   1. Each result's "View Judgement" link is not a URL at all -- the
#      judgment PDF's full bytes are embedded inline as base64 in an
#      `oj-data` attribute. No second request is needed to fetch it.
#   2. The search endpoint is genuinely flaky: because a result page with
#      several embedded PDFs can be 10+ MB, the server sometimes returns a
#      truncated ~60KB placeholder shell (a Drupal BigPipe artifact)
#      instead of the fully-rendered results, non-deterministically --
#      confirmed by requesting the identical search 4 times in a row and
#      getting the full response only 1-3 times. A real browser User-Agent
#      measurably improves (but does not eliminate) this, so both are used
#      together with retries here, rather than trusting a single request.
# ---------------------------------------------------------------------------

_MAHARERA_ORDERS_URL = "https://maharera.maharashtra.gov.in/orders-judgements"
_MAHARERA_COMPLAINT_TYPES = ("rulings_of_MahaRERA", "judgements_by_adjudicating_officers")
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _maharera_orders_search_once(project_name: str, complaint_type: str) -> list:
    """Single attempt -- may return [] either because there are genuinely
    no matches, or because of the BigPipe flakiness noted above; the caller
    (search_maharera_judgments) is responsible for retrying."""
    headers = {"User-Agent": _BROWSER_UA}
    session = requests.Session()
    resp1 = session.get(_MAHARERA_ORDERS_URL, headers=headers, timeout=config.REQUEST_TIMEOUT)
    resp1.raise_for_status()
    build_id_match = re.search(r'name="form_build_id" value="([^"]+)"', resp1.text)
    if not build_id_match:
        return []

    data = {
        "order_complaint_type": complaint_type,
        "order_project_name": project_name,
        "form_build_id": build_id_match.group(1),
        "form_id": "orders_judgements_form",
        "op": "Search",
    }
    resp2 = session.post(_MAHARERA_ORDERS_URL, data=data, headers=headers, timeout=config.REQUEST_TIMEOUT)
    resp2.raise_for_status()
    if "bg-body rounded" not in resp2.text:
        return []  # the truncated-shell case -- treat as "try again", not "no results"

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp2.text, "lxml")
    cards = [d for d in soup.find_all("div") if d.get("class") and "shadow" in d.get("class")]

    def _label_value(card, label_text):
        label = card.find("label", string=re.compile(re.escape(label_text)))
        if not label:
            return None
        p = label.find_next("p")
        return p.get_text(strip=True) if p else None

    results = []
    for card in cards:
        reg_no_tag = card.find("p", class_="p-0")
        title_tag = card.find("h4")
        pdf_link = card.find("a", attrs={"oj-data": True})
        results.append({
            "reg_no": reg_no_tag.get_text(strip=True).lstrip("#") if reg_no_tag else None,
            "project_name": title_tag.get_text(strip=True) if title_tag else None,
            "complainant_name": _label_value(card, "Complainant Name"),
            "complainant_no": _label_value(card, "Complainant No"),
            "respondent_name": _label_value(card, "Respondent Name"),
            "uploaded_date": _label_value(card, "Uploaded Date"),
            "judgment_pdf_base64": pdf_link.get("oj-data") if pdf_link else None,
            "complaint_type": complaint_type,
        })
    return results


def _maharera_orders_search_with_retry(project_name: str, complaint_type: str, max_attempts: int) -> list:
    """One complaint type's search, retried against the documented BigPipe
    flakiness -- structured to keep the common (non-flaky) case cheap
    while still speeding up genuine retries: tries once, and only if THAT
    comes back empty does it fire the remaining (max_attempts - 1) tries
    CONCURRENTLY, returning as soon as any one of them succeeds rather
    than waiting for all of them in sequence. This was the dominant cost
    of the whole Phase 2 check batch (confirmed live: ~41s of a ~45s
    total, vs. under 2.2s for each of the other four checks combined --
    see the concurrent Phase 2 checks above _safe_credit_rating) precisely
    because it could burn up to `max_attempts` sequential round-trips per
    complaint type. A clean project (no flakiness this pass) still costs
    exactly 1 request, identical to the old fully-sequential version; a
    flaky one now costs at most 2 sequential rounds (1 + 1 concurrent
    batch) instead of up to `max_attempts`."""
    try:
        results = _maharera_orders_search_once(project_name, complaint_type)
    except requests.RequestException:
        results = []
    if results or max_attempts <= 1:
        return results

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_attempts - 1)
    try:
        futures = [executor.submit(_maharera_orders_search_once, project_name, complaint_type) for _ in range(max_attempts - 1)]
        for future in concurrent.futures.as_completed(futures):
            try:
                retry_results = future.result()
            except requests.RequestException:
                retry_results = []
            if retry_results:
                return retry_results
        return []
    finally:
        # Deliberately wait=False: once a good result is found (or every
        # retry is exhausted empty), there's no reason to block returning
        # to the caller on some other still-in-flight retry finishing --
        # it'll just complete and be discarded in the background.
        executor.shutdown(wait=False)


def search_maharera_judgments(project_name: str, max_attempts: int = 3) -> list:
    """Searches MahaRERA's public Orders/Judgments page for `project_name`
    under both categories that can carry a genuine adjudicated outcome
    ("Rulings of MahaRERA" and "Judgements by Adjudicating Officers" --
    "Non-Registration Rulings" is a different, irrelevant category). Both
    categories are searched CONCURRENTLY (they're independent of each
    other), and each one's own retries against the BigPipe flakiness
    (see _maharera_orders_search_with_retry) are themselves partly
    concurrent -- so the worst case here is roughly 2 sequential
    round-trips, not up to `max_attempts` x 2 sequential round-trips as in
    the original fully-sequential version. A project with no published
    judgment yet returns [] after exhausting retries -- this is the
    expected, common case (most complaints/appeals are still pending), not
    a failure; there is no way from this function alone to distinguish
    "genuinely nothing published" from "every retry hit the flaky
    empty-shell response", so callers should not treat an empty result as
    a confirmed absence for a project with very few realistic search
    attempts left in a budget."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(_MAHARERA_COMPLAINT_TYPES)) as executor:
        futures = [
            executor.submit(_maharera_orders_search_with_retry, project_name, complaint_type, max_attempts)
            for complaint_type in _MAHARERA_COMPLAINT_TYPES
        ]
        all_results = []
        for future in futures:
            all_results.extend(future.result())
    return all_results


def cross_reference_appeals(judgments: list, appeals_data: list) -> list:
    """Matches MahaRERA Orders/Judgments search results against this
    project's own appeals.json records, using `complainant_no` -- confirmed
    to be the exact same ID scheme as appeals.json's own
    `complaintRegistrationNo` field (e.g. CC005000000023263), a real join
    key rather than fuzzy name matching. Returns only judgments that
    matched a real appeal record on this project, each annotated with the
    matched appeal's own fields."""
    by_complaint_no = {a.get("complaintRegistrationNo"): a for a in appeals_data if a.get("complaintRegistrationNo")}
    matched = []
    for j in judgments:
        appeal = by_complaint_no.get(j.get("complainant_no"))
        if appeal:
            matched.append({**j, "matched_appeal": appeal})
    return matched


def _save_appeal_judgment_pdfs(reg_no: str, matched_judgments: list, output_dir: str) -> list:
    """Decodes and saves each matched judgment's embedded base64 PDF to
    output/<reg_no>/appeal_judgments/ -- the same "documents belong on disk,
    referenced by filename" convention as every other document in this
    project, rather than keeping multi-megabyte base64 blobs inline in
    facts.json. Returns the same list with judgment_pdf_base64 replaced by
    saved_filename (or None if that specific save failed)."""
    if not matched_judgments:
        return matched_judgments
    import base64

    out_dir = os.path.join(output_dir, reg_no, "appeal_judgments")
    os.makedirs(out_dir, exist_ok=True)
    result = []
    for j in matched_judgments:
        entry = dict(j)
        b64 = entry.pop("judgment_pdf_base64", None)
        entry["saved_filename"] = None
        if b64:
            try:
                pdf_bytes = base64.b64decode(b64)
                filename = f"{entry.get('complainant_no') or 'judgment'}.pdf"
                with open(os.path.join(out_dir, filename), "wb") as f:
                    f.write(pdf_bytes)
                entry["saved_filename"] = filename
            except Exception:
                pass  # keep saved_filename=None -- the metadata match itself is still real and kept
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Review-authenticity heuristic triage (Phase 2) -- approved scope: a
# red-flag surfacing aid, explicitly NOT a fabrication verdict. Confirmed by
# research this session that true fabrication-probability scoring isn't
# achievable without platform-internal data (IP/device signals, removed-
# review logs, verified-purchase flags) this project has no access to.
#
# Each function below is a self-contained analysis primitive taking a plain
# list of review dicts as input ({rating, date (YYYY-MM-DD), text,
# reviewer_name, reviewer_review_count (optional -- the total number of
# reviews that reviewer has ever posted, on any listing, if known)) --
# deliberately decoupled from any specific review source (Google Maps,
# 99acres, MouthShut, ...), since fetching reviews live is a separate,
# harder, source-specific scraping problem (Google Maps in particular is a
# heavy JS SPA, not a plain-HTTP target like the other Phase 2 sources
# above) and was not built as part of this pass -- these functions are
# ready to consume whatever review data is gathered, by hand or by a future
# scraper, without needing to know where it came from.
#
# cross_reference_review_claims is the single highest-value check (per
# research): it costs nothing to fetch (uses only data this project already
# has) and catches the most concrete, checkable kind of dishonesty --
# reviews making a specific factual claim that contradicts this project's
# own confirmed RERA record.
# ---------------------------------------------------------------------------


def analyze_rating_polarization(reviews: list) -> dict:
    """Rating-distribution histogram (1-5 stars) plus the % that sit at the
    extremes (1 or 5 stars) versus the middle (2-4) -- fake-review research
    (Luca & Zervas) finds fabricated reviews skew disproportionately to the
    extremes, so a heavily bimodal distribution is a directional red flag,
    not proof of anything on its own."""
    counts = {i: 0 for i in range(1, 6)}
    for r in reviews:
        rating = r.get("rating")
        if isinstance(rating, (int, float)) and 1 <= rating <= 5:
            counts[int(round(rating))] += 1
    total = sum(counts.values())
    extreme = counts[1] + counts[5]
    return {
        "counts": counts,
        "total": total,
        "pct_extreme": round(100 * extreme / total, 1) if total else None,
    }


def detect_review_bursts(reviews: list, window_days: int = 7, burst_threshold: int = 5) -> list:
    """Flags clusters of `burst_threshold` or more reviews all posted within
    a `window_days`-day span -- an abnormally short posting window is one of
    the standard signals used in spammer-group detection research (a
    kernel-density/burst-clustering approach, simplified here to a sliding
    window over sorted dates, since this project doesn't need the full
    statistical machinery to surface an obvious cluster for a human to look
    at). Requires each review dict to have a parseable "date" (YYYY-MM-DD);
    reviews without one are ignored, not treated as evidence either way."""
    dated = []
    for r in reviews:
        try:
            d = datetime.strptime(str(r.get("date", ""))[:10], "%Y-%m-%d")
            dated.append((d, r))
        except ValueError:
            continue
    dated.sort(key=lambda pair: pair[0])

    bursts = []
    i = 0
    while i < len(dated):
        j = i
        while j < len(dated) and (dated[j][0] - dated[i][0]).days <= window_days:
            j += 1
        cluster_size = j - i
        if cluster_size >= burst_threshold:
            bursts.append({
                "start_date": dated[i][0].strftime("%Y-%m-%d"),
                "end_date": dated[j - 1][0].strftime("%Y-%m-%d"),
                "count": cluster_size,
                "reviews": [r for _, r in dated[i:j]],
            })
            i = j
        else:
            i += 1
    return bursts


def detect_near_duplicate_reviews(reviews: list, similarity_threshold: float = 0.85) -> list:
    """Flags pairs of reviews whose text is near-identical (templated/
    copy-pasted language) -- a documented pattern in fabricated-review
    campaigns, and one of the few fabrication signals checkable purely from
    review text with no platform-internal data. Uses difflib's
    SequenceMatcher (stdlib, no new dependency) rather than a heavier NLP
    similarity model, which is a reasonable trade-off for a triage tool
    flagging pairs for a human to actually read, not an automated verdict."""
    from difflib import SequenceMatcher

    texts = [(i, (r.get("text") or "").strip()) for i, r in enumerate(reviews)]
    texts = [(i, t) for i, t in texts if len(t) > 20]  # too short to meaningfully compare
    pairs = []
    for a in range(len(texts)):
        for b in range(a + 1, len(texts)):
            i1, t1 = texts[a]
            i2, t2 = texts[b]
            ratio = SequenceMatcher(None, t1, t2).ratio()
            if ratio >= similarity_threshold:
                pairs.append({"review_a": reviews[i1], "review_b": reviews[i2], "similarity": round(ratio, 3)})
    return pairs


def flag_one_hit_wonder_reviewers(reviews: list) -> list:
    """Flags reviewers whose total review count (across every listing
    they've ever reviewed, not just this one) is exactly 1 -- a documented
    heuristic from consumer review-integrity tools (ReviewMeta, Fakespot)
    for accounts that exist only to post a single review. Requires the
    caller to supply `reviewer_review_count` per review (obtainable by
    visiting each reviewer's own public profile page -- e.g. clicking
    through on Google Maps -- which this function does not do itself);
    reviews missing that field are skipped, not assumed to be one-hit
    wonders."""
    return [r for r in reviews if r.get("reviewer_review_count") == 1]


_POSSESSION_DELAY_CLAIM_RE = re.compile(
    r"(?:possession|handover|deliver\w*)\D{0,40}?(?:delay\w*|postpone\w*)\D{0,20}?(?:to\s+)?(20\d{2})",
    re.I,
)
_POSSESSION_YEAR_MENTION_RE = re.compile(r"\b(20\d{2})\b")


def cross_reference_review_claims(reviews: list, facts: dict) -> list:
    """The single highest-value check in this module (per Phase 2
    research): extracts specific, checkable factual claims from review text
    -- currently possession-delay year mentions, the most common concrete
    claim in real-estate reviews -- and compares them against this
    project's own confirmed RERA data (rera_core_fields.proposed_completion_date).
    Flags each review as "consistent", "inconsistent" (cites a materially
    different year than the project's own record), or leaves it unflagged
    if it makes no checkable claim at all -- deliberately narrow rather than
    trying to parse every possible claim type, since a wrong classification
    here would be worse than simply not flagging an ambiguous case."""
    completion_date = (facts.get("rera_core_fields", {}) or {}).get("proposed_completion_date", "")
    completion_year_match = re.search(r"\b(20\d{2})\b", completion_date)
    completion_year = int(completion_year_match.group(1)) if completion_year_match else None

    flagged = []
    for r in reviews:
        text = r.get("text") or ""
        delay_match = _POSSESSION_DELAY_CLAIM_RE.search(text)
        if not delay_match:
            continue
        claimed_year = int(delay_match.group(1))
        if completion_year is None:
            flagged.append({"review": r, "claimed_year": claimed_year, "verdict": "unverifiable -- no confirmed completion date on record to check against"})
        elif abs(claimed_year - completion_year) <= 1:
            flagged.append({"review": r, "claimed_year": claimed_year, "project_year": completion_year, "verdict": "consistent"})
        else:
            flagged.append({"review": r, "claimed_year": claimed_year, "project_year": completion_year, "verdict": "inconsistent -- materially different from this project's own confirmed record"})
    return flagged


def run_review_authenticity_triage(reviews: list, facts: dict) -> dict:
    """Runs all four checks above and returns a combined result -- the
    single entry point Company Charter generation (or any other caller)
    should use rather than calling each analysis function separately."""
    return {
        "total_reviews_analyzed": len(reviews),
        "rating_polarization": analyze_rating_polarization(reviews),
        "burst_clusters": detect_review_bursts(reviews),
        "near_duplicate_pairs": detect_near_duplicate_reviews(reviews),
        "one_hit_wonder_reviewers": flag_one_hit_wonder_reviewers(reviews),
        "claim_cross_reference": cross_reference_review_claims(reviews, facts),
    }


# ---------------------------------------------------------------------------
# Template filling
# ---------------------------------------------------------------------------

def _set_paragraph_text(paragraph, text: str) -> None:
    for run in paragraph.runs[1:]:
        run.text = ""
    if paragraph.runs:
        paragraph.runs[0].text = text
    else:
        paragraph.add_run(text)


def _set_cell(table, row: int, col: int, text: str) -> None:
    _set_paragraph_text(table.rows[row].cells[col].paragraphs[0], str(text))


def _set_row_cell(row, col: int, text: str) -> None:
    _set_paragraph_text(row.cells[col].paragraphs[0], str(text))


def _fill_variable_rows(table, header_rows: int, items: list, fill_row) -> None:
    """Grows/shrinks table to exactly len(items) data rows below
    header_rows, calling fill_row(row, item) for each new/existing row.
    Extra template rows are deleted from the end; missing ones are added
    with table.add_row() (already in correct trailing order)."""
    while len(table.rows) - header_rows < len(items):
        table.add_row()
    while len(table.rows) - header_rows > len(items):
        table._tbl.remove(table.rows[-1]._tr)

    for i, item in enumerate(items):
        fill_row(table.rows[header_rows + i], item)


def _fill_variable_paragraphs(doc, start_index: int, slot_count: int, texts: list) -> None:
    """Fills the slot_count existing template paragraphs starting at
    start_index with the first items of `texts`; deletes unused slots if
    there are fewer, or appends new paragraphs (cloning the last slot's
    style) if there are more."""
    slots = doc.paragraphs[start_index:start_index + slot_count]

    for i in range(min(slot_count, len(texts))):
        _set_paragraph_text(slots[i], texts[i])
    for i in range(len(texts), slot_count):
        slots[i]._element.getparent().remove(slots[i]._element)

    last = slots[-1] if slots else None
    for text in texts[slot_count:]:
        if last is None:
            break
        new_para = last.insert_paragraph_before(text, style=last.style)
        last._p.addnext(new_para._p)
        last = new_para


def _fill_template(reg_no: str, facts: dict, out_path: str) -> None:
    import docx

    shutil.copy2(TEMPLATE_PATH, out_path)
    doc = docx.Document(out_path)
    p = doc.paragraphs

    li = facts["land_identification"]
    ci = facts["corporate_identity"]
    conn = facts["connectivity"]
    fsi_m = facts["fsi_metrics"]
    rs = facts["rules_statutory"]
    rc = facts["rera_compliance"]
    lp = facts["local_planning"]
    core = facts["rera_core_fields"]
    nb = facts["neighbourhood"]

    fld = lambda d, k: (d.get(k) or {}).get("value", "") if isinstance(d.get(k), dict) else ""
    src = lambda d, k: (d.get(k) or {}).get("source", "") if isinstance(d.get(k), dict) else ""

    _set_paragraph_text(p[1], f'Project: {core.get("project_name", "[Unknown]")} | Promoter: {fld(ci, "promoter_name") or "[Unknown]"}')
    _set_paragraph_text(p[2], "Public Web-Sourced Edition -- Adapted for Maharashtra (MahaRERA)")
    _set_paragraph_text(p[3], f"Deep Market Research -- Prepared {datetime.now().strftime('%B %Y')}")
    _set_paragraph_text(p[5], facts["methodology_note"])
    _set_paragraph_text(p[7], facts["executive_summary"])
    _set_paragraph_text(p[12], facts["address_discrepancy_note"])
    _set_paragraph_text(p[13], facts["corporate_registry_cross_check"])
    litigation_citation = _clean_source_label(src(facts, "litigation_status"))
    litigation_text = fld(facts, "litigation_status")
    _set_paragraph_text(p[15], f"{litigation_text} ({litigation_citation})" if litigation_citation else litigation_text)
    _set_paragraph_text(p[17], _cite(facts["location_coordinates_note"], "distance", facts=facts))
    _set_paragraph_text(p[18], "Map screenshot not embedded -- a live map cannot be fetched programmatically, so distances below were sourced from mapping-service queries instead (see Sources).")
    _set_paragraph_text(p[24], _cite(f"Road: {conn.get('road', '')}", "distance", facts=facts))
    _set_paragraph_text(p[25], _cite(f"Rail: {conn.get('rail', '')}", "distance", facts=facts))
    _set_paragraph_text(p[26], _cite(f"Metro: {conn.get('metro', '')}", "distance", facts=facts))
    _set_paragraph_text(p[27], _cite(f"Air: {conn.get('air', '')}", "distance", facts=facts))
    _set_paragraph_text(p[30], facts["social_infrastructure"])
    _set_paragraph_text(p[32], facts["fsi_governing_framework"])
    _set_paragraph_text(p[33], facts["fsi_interpretation"])
    _set_paragraph_text(p[36], _cite(f"Governing act: {rs.get('governing_act', '')}", "project_registration", facts=facts))
    _set_paragraph_text(p[37], _cite(f"Planning approval sequence: {rs.get('planning_approval_sequence', '')}", "project_registration", facts=facts))
    _set_paragraph_text(p[38], f"Allotment mechanics: {rs.get('allotment_mechanics', '')}")
    _set_paragraph_text(p[41], rc.get("registration_summary", ""))
    _set_paragraph_text(p[42], f"Collection Account of the Project (100%): {rc.get('collection_account', '')}")
    _set_paragraph_text(p[43], f"Separate/Transaction RERA escrow sub-accounts: {rc.get('escrow_subaccounts', '')}")
    _set_paragraph_text(p[44], f"Litigations/complaints/appeals related to the project: {rc.get('litigations_complaints_appeals', '')}")
    _set_paragraph_text(p[45], rc.get("statutory_declaration", ""))
    _set_paragraph_text(p[46], f"Construction progress: {rc.get('construction_progress', '')}")
    _set_paragraph_text(p[49], _cite(f"Authority of record: {lp.get('authority_of_record', '')}", "project_registration", facts=facts))
    _set_paragraph_text(p[50], _cite(f"Project type: {lp.get('project_type', '')}", "project_registration", facts=facts))
    _set_paragraph_text(p[51], _cite(f"Professionals of record: {lp.get('professionals_of_record', '')}", "project_registration", facts=facts))
    _set_paragraph_text(p[54], _cite(facts["micro_market_overview"], "pricing", "market_trend", facts=facts))
    _set_paragraph_text(p[56], _cite(facts["area_intelligence_trend"], "market_trend", "pricing", facts=facts))
    _set_paragraph_text(p[58], facts.get("rera_scraping_note", f"Extracted directly from the live MahaRERA public project page for registration number {reg_no}."))
    _set_paragraph_text(p[61], _cite(facts["unit_summary_note"], "project_registration", facts=facts))
    _set_paragraph_text(p[63], facts["documents_reviewed_note"])
    _set_paragraph_text(p[64], facts["documents_absent_note"])

    gaps = facts.get("gaps", [])
    _set_paragraph_text(p[66], "\n".join(f"• {g}" for g in gaps) if gaps else "No additional gaps identified beyond the standing gap below.")
    # p[67] (the permanent standing gap) is left untouched deliberately.

    t = doc.tables
    for row, key in zip(range(1, 8), (
        "survey_cts_plot_numbers", "village_locality", "mandal_taluka_district",
        "pincode", "total_gross_area", "area_affected", "net_area",
    )):
        _set_cell(t[0], row, 1, fld(li, key))
        # The template's own "Source" column showed the raw output/<reg_no>/...
        # path verbatim -- cleaned to a short label here, same convention as
        # every other citation added below.
        _set_cell(t[0], row, 2, _clean_source_label(src(li, key)) or "")

    for row, key in zip(range(1, 10), (
        "promoter_name", "organization_type", "cin_llpin", "registered_office_main",
        "registered_office_board_resolution", "registered_office_planning_stage",
        "authorized_signatory", "partners_directors", "landowner_investor",
    )):
        # This table has no Source column in the template (unlike Land
        # Identification) -- each fact carries a real source in facts.json
        # that was never shown anywhere, so it's appended inline instead.
        value = fld(ci, key)
        citation = _clean_source_label(src(ci, key))
        _set_cell(t[1], row, 1, f"{value} ({citation})" if value and citation else value)

    for row, key in zip(range(1, 5), ("east", "west", "north", "south")):
        _set_cell(t[2], row, 1, _cite(nb.get(key, ""), "project_registration", "legal_documents", facts=facts))

    def _fill_distance_row(row, item):
        _set_row_cell(row, 0, item["landmark"])
        _set_row_cell(row, 1, item["distance_time"])
        _set_row_cell(row, 2, item["route_note"])

    _fill_variable_rows(t[3], 1, facts["distances"], _fill_distance_row)

    for row, key in zip(range(1, 6), ("net_land_area", "approved_bua", "sanctioned_bua", "mortgage_area", "implied_fsi")):
        _set_cell(t[4], row, 1, fsi_m.get(key, ""))
    mortgage_lender_value = fld(fsi_m, "mortgage_lender")
    if mortgage_lender_value:
        # Grown row, not a fixed template slot -- this field is new (see
        # _CHARTER_FACTS_SCHEMA note) and the template predates it.
        lender_row = t[4].add_row()
        _set_row_cell(lender_row, 0, "Mortgage lender (if disclosed)")
        lender_citation = _clean_source_label(src(fsi_m, "mortgage_lender"))
        _set_row_cell(lender_row, 1, f"{mortgage_lender_value} ({lender_citation})" if lender_citation else mortgage_lender_value)
    lender_history_note = facts.get("mortgage_lender_history_note")
    if lender_history_note:
        history_row = t[4].add_row()
        _set_row_cell(history_row, 0, "Mortgage lender -- change since prior run")
        _set_row_cell(history_row, 1, lender_history_note)

    def _fill_comparable_row(row, item):
        distance = item.get("distance_km")
        project_label = f"{item['project']} ({distance} km)" if distance else item["project"]
        _set_row_cell(row, 0, project_label)
        _set_row_cell(row, 1, item["configuration"])
        _set_row_cell(row, 2, item["pricing"])
        _set_row_cell(row, 3, item["source"])

    _fill_variable_rows(t[5], 1, facts.get("comparables", []), _fill_comparable_row)

    for row, key in zip(range(1, 14), (
        "project_name", "registration_number", "promoter_name", "authority",
        "plan_approval_number", "project_status", "approved_date",
        "proposed_completion_date", "project_type", "litigations_per_record",
        "promoter_land_owner_investor", "collection_bank_account", "total_building_units",
    )):
        _set_cell(t[6], row, 1, core.get(key, ""))

    def _fill_block_row(row, item):
        _set_row_cell(row, 0, item["block_wing"])
        _set_row_cell(row, 1, item["floors"])
        _set_row_cell(row, 2, item["config"])
        _set_row_cell(row, 3, item["units_counted"])
        _set_row_cell(row, 4, item["note"])

    _fill_variable_rows(t[7], 1, facts.get("blocks", []), _fill_block_row)

    def _fill_doc_library_row(row, item):
        _set_row_cell(row, 0, item["document_name"])
        _set_row_cell(row, 1, item["status"])

    _fill_variable_rows(t[8], 1, facts.get("document_library", []), _fill_doc_library_row)

    def _format_source_line(s: dict) -> str:
        line = f'{s.get("label", "")} -- {s.get("ref", "")}'
        # published_date/accessed_date are new fields (see _CHARTER_FACTS_SCHEMA) -- guard
        # with .get() so a facts dict built before this change still renders correctly.
        published = s.get("published_date")
        accessed = s.get("accessed_date")
        if published or accessed:
            line += f' [published: {published or "unknown"}, accessed: {accessed or "unknown"}]'
        return line

    sources = [_format_source_line(s) for s in facts.get("sources", [])]
    _fill_variable_paragraphs(doc, 69, 8, sources)

    # ---------------------------------------------------------------------
    # Section consolidation -- renames existing template headings to the
    # target vocabulary (docs/Company_Charter_Executive_Design.docx section
    # 3's 9-section table) and regroups content to match, without touching
    # any of the prose-generation logic above or in any _append_* function.
    # Three kinds of change, all via the same two primitives (retext an
    # EXISTING heading paragraph, or capture-then-relocate NEWLY appended
    # content -- the latter already proven safe for the checks bolted onto
    # the end in the previous pass):
    #   1. Renames: "1. Legal Identifiers..." -> "Counterparty", "2. Location
    #      Map" -> "The Asset", "4. Rules" -> "Compliance & Legal Detail",
    #      "5. Area Intelligence" -> "Market & Area Intelligence", "6. RERA
    #      Scraping..." -> "RERA Core Data", "Gaps & Limitations..." ->
    #      "Gaps & Sources". "3. FSI", "Document Library Contents", and
    #      "Sources" are demoted from Heading 1 to Heading 2, becoming
    #      subsections of "The Asset" / a new "Diligence Appendix" / the
    #      merged "Gaps & Sources" respectively, instead of standalone
    #      top-level sections.
    #   2. Land Identification is pulled out from under the old combined
    #      "Legal Identifiers" heading to sit under "The Asset" instead,
    #      alongside Location Map/FSI -- the one case here of relocating
    #      EXISTING template content, not newly-appended content.
    #   3. The 5 check sections (credit rating/IBBI/company profile/group
    #      companies/Developer Score) get a short, one-line-each SUMMARY
    #      under Counterparty (_append_counterparty_summary, new) instead
    #      of their full detail sitting there -- the full tables move into
    #      a new "Diligence Appendix" (alongside Document Library and Data
    #      Authenticity), demoted to Heading 2 subsections of it. Complaint
    #      Order Outcomes and Appeal-Level Judgments move into "Compliance
    #      & Legal Detail" (same anchor Appeal Judgments already used).
    # ---------------------------------------------------------------------

    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph as _Paragraph

    _set_paragraph_text(p[8], "Counterparty")
    _set_paragraph_text(p[16], "The Asset")
    _set_paragraph_text(p[34], "Compliance & Legal Detail")
    _set_paragraph_text(p[52], "Market & Area Intelligence")
    _set_paragraph_text(p[57], "RERA Core Data")
    _set_paragraph_text(p[65], "Gaps & Sources")

    h1_style = p[8].style
    h2_style = p[9].style  # "Land Identification" -- a known Heading 2
    _set_paragraph_text(p[31], "FSI (Floor Space Index)")
    p[31].style = h2_style  # was Heading 1 ("3. FSI...") -- now a subsection of "The Asset"
    p[62].style = h2_style  # "Document Library Contents..." -- now a subsection of the new Diligence Appendix
    p[68].style = h2_style  # "Sources" -- now a subsection of the merged "Gaps & Sources"

    # Every _append_* function below appends new content at the document's
    # true end (doc.add_paragraph()/doc.add_table()), same as they always
    # have -- several of them internally look up doc.paragraphs[4] fresh
    # for heading style, so ALL of them must run before anything gets
    # relocated below; relocating first would shift a fresh paragraphs[4]
    # lookup onto whatever new content happened to land there instead of
    # the real "Methodology Note" heading. `p` (captured at the top of this
    # function, before any of this) stays valid as a relocation anchor
    # throughout, since it holds resolved Paragraph objects, not indices
    # that shift as new paragraphs are inserted elsewhere in the tree --
    # true whether that paragraph's own text/style was just edited above
    # too, since neither changes which _p element `p[N]` wraps.
    body = doc.element.body
    sectPr = body[-1]

    def _capture_batch(append_fn) -> list:
        """Runs append_fn() (which appends new paragraphs/tables at the
        document's true end via doc.add_paragraph()/doc.add_table()) and
        returns exactly the elements it just added, in document order --
        found via XML tree navigation (getnext/getprevious), not Python
        object identity, since lxml doesn't guarantee stable proxy objects
        across separate accesses to the same underlying node."""
        marker = sectPr.getprevious()
        append_fn()
        new_elements = []
        el = marker.getnext() if marker is not None else (body[0] if body[0] is not sectPr else None)
        while el is not None and el is not sectPr:
            new_elements.append(el)
            el = el.getnext()
        return new_elements

    def _demote_first_heading(batch: list, new_style) -> None:
        """Finds the first Heading-1-styled paragraph in a captured batch
        (every _append_*_section function's own heading, since each was
        written as a standalone top-level section) and demotes it to
        `new_style` -- turns it into a subsection of whatever umbrella
        heading it's being relocated under, without touching that
        section's own rendering code at all."""
        for el in batch:
            if el.tag == qn("w:p"):
                para = _Paragraph(el, doc._body)
                if para.style and para.style.name == "Heading 1":
                    para.style = new_style
                    return

    # facts["developer_score"] must exist before _append_developer_score_section
    # / _append_counterparty_summary read it -- computed once here (Overview
    # needs the same flags/score too).
    flags = _classify_flags(facts)
    facts["developer_score"] = _compute_developer_score(facts, flags)

    overview_batch = _capture_batch(lambda: _append_overview_section(doc, facts, flags))

    # The material-findings cards (Completion Slippage/Units Sold/
    # Litigation Load/Land Title) land right under the Executive Summary's
    # own prose (p[7]) -- captured separately from Overview & Flags so it
    # can anchor at a different spot (immediately before p[8],
    # "Counterparty") while Overview & Flags (which carries its own,
    # differently-scoped scorecard) anchors at p[6].
    exec_summary_kpi_batch = _capture_batch(lambda: _append_executive_summary_kpis(doc, facts, flags))

    # Short summaries under Counterparty -- full detail lives in the
    # Diligence Appendix batch built right after.
    counterparty_summary_batch = _capture_batch(lambda: _append_counterparty_summary(doc, facts))

    diligence_appendix_batch = []
    for append_fn in (
        lambda: _append_credit_rating_section(doc, facts),
        lambda: _append_ibbi_check_section(doc, facts),
        lambda: _append_company_profile_section(doc, facts),
        lambda: _append_group_companies_section(doc, facts),
        lambda: _append_developer_score_section(doc, facts),
    ):
        batch = _capture_batch(append_fn)
        _demote_first_heading(batch, h2_style)
        diligence_appendix_batch.extend(batch)

    def _add_diligence_appendix_heading() -> None:
        heading_para = doc.add_paragraph("Diligence Appendix")
        heading_para.style = h1_style

    diligence_appendix_heading_batch = _capture_batch(_add_diligence_appendix_heading)

    # Compliance & Legal Detail gains the full litigation/complaint detail
    # -- Complaint Order Outcomes joins Appeal-Level Judgments there (same
    # anchor the latter already used in the previous pass). Both are
    # demoted to Heading 2, same reasoning as the Diligence Appendix
    # checks: each was written as its own standalone top-level section, so
    # becoming a subsection of an umbrella heading needs its own demotion.
    complaint_outcomes_batch = _capture_batch(lambda: _append_complaint_outcomes_section(doc, facts))
    _demote_first_heading(complaint_outcomes_batch, h2_style)
    _append_cts_land_record_section(doc, facts)  # unmoved, same relative position as before
    appeal_judgments_only_batch = _capture_batch(lambda: _append_appeal_judgments_section(doc, facts))
    _demote_first_heading(appeal_judgments_only_batch, h2_style)
    compliance_batch = complaint_outcomes_batch + appeal_judgments_only_batch

    _append_review_authenticity_section(doc, facts)  # unmoved

    authenticity_batch = _capture_batch(lambda: _append_authenticity_page(doc, facts))
    _demote_first_heading(authenticity_batch, h2_style)
    diligence_appendix_batch.extend(authenticity_batch)

    # Relocate each batch now that every _append_* call above is done, so
    # none of them could have been tripped up by an earlier relocation --
    # p[4]/p[16]/p[17]/p[48]/p[62]/p[65] are still exactly the same
    # paragraph objects as when this function started, since nothing has
    # moved yet (only text/style on some of them changed, which doesn't
    # affect their position).
    # Overview & Flags anchors at p[6] ("Executive Summary" heading), not
    # p[4] -- the Methodology Note (p[4]/p[5]) is removed entirely below,
    # so the batch lands directly before Executive Summary instead of
    # before what used to be the Methodology Note.
    for el in overview_batch:
        p[6]._p.addprevious(el)
    for el in exec_summary_kpi_batch:
        p[8]._p.addprevious(el)
    for el in counterparty_summary_batch:
        p[16]._p.addprevious(el)
    for el in diligence_appendix_heading_batch:
        p[62]._p.addprevious(el)
    for el in diligence_appendix_batch:
        p[65]._p.addprevious(el)
    for el in compliance_batch:
        p[48]._p.addprevious(el)

    # Land Identification -- the one relocation of EXISTING template
    # content rather than newly-appended content: pulled from under
    # Counterparty (where it used to anchor the old combined "Legal
    # Identifiers" section) to sit under "The Asset" instead, right before
    # its first piece of content (location_coordinates_note).
    for el in (p[9]._p, t[0]._tbl, p[10]._p):
        p[17]._p.addprevious(el)

    # Methodology Note removed entirely, per request -- safe only now that
    # every _append_*_section call above is done (several of them looked up
    # doc.paragraphs[4].style fresh for their own heading style, so this
    # element had to survive until the very last _append_* call finished).
    p[4]._p.getparent().remove(p[4]._p)
    p[5]._p.getparent().remove(p[5]._p)

    doc.save(out_path)


# ---------------------------------------------------------------------------
# Authenticity page -- computed from the actual sources/gaps data, not
# self-reported by the model. A narrative claim like "this report is highly
# reliable" would itself just be another unverified claim; a count of what
# tier each cited source actually falls into is auditable against the
# Sources section a reader can already see.
# ---------------------------------------------------------------------------

_SOURCE_TIERS = [
    ("Primary regulatory record (MahaRERA/MCA, or a document opened from it)", (
        "maharerait.maharashtra.gov.in", "maharera.maharashtra.gov.in", "mca.gov.in",
        "output" + os.sep, "output/",
    )),
    # Credit-rating and government legal/insolvency records: not yet produced by any pass
    # in this file as of this session (the credit-rating lookup and IBBI/NCLT check are
    # separate, not-yet-built follow-on work) -- listed here now so the tier taxonomy is
    # ready the moment those checks start citing sources, rather than everything from them
    # silently landing in "Other" until someone remembers to add the tier.
    ("Credit rating agency (CRISIL/ICRA/CARE/India Ratings)", (
        "crisil.com", "crisilratings.com", "icra.in", "careratings.com", "careedge.in", "indiaratings.co.in",
    )),
    ("Government legal/insolvency record (IBBI, NCLT, NCDRC, MahaREAT judgments)", (
        "ibbi.gov.in", "nclt.gov.in", "ncdrc.nic.in", "mahareat.maharashtra.gov.in", "indiankanoon.org",
    )),
    ("Corporate-registry mirror", ("zaubacorp.com", "tofler.in", "instafinancials.com")),
    ("Live Google Maps verification", ("google.com/maps", "Google Maps")),
    # Genuine third-party homebuyer-advocacy/watchdog bodies -- explicitly documented (this
    # session's research) as more reliable than a review site or listing portal, since they
    # aren't commercial review-hosting businesses with their own credibility problems.
    ("Homebuyer-advocacy / watchdog body", ("fpce.in", "indianrealestateforum.com")),
    ("Real-estate aggregator / listing site", (
        "99acres.com", "nobroker.in", "squareyards.com", "magicbricks.com", "housing.com",
        "indextap.com", "propertypistol.com", "navi.com", "regrob.com",
    )),
    # A developer/promoter's own website is self-published and not independently verified --
    # kept as its own, lower tier rather than folded into independent aggregators above (it
    # previously was; that conflated "the subject describing itself" with "an independent
    # third party describing the subject", which are very different trust levels).
    ("Developer/promoter's own website (self-published)", (
        "godrejproperties.com", "godrejvanantara.com", "godrejgroup.org.in",
    )),
    ("News / press coverage", ("sproutsnews.com", "economictimes", "moneycontrol.com", "livemint.com")),
    ("Social media / user-generated content", ("facebook.com", "twitter.com", "x.com", "reddit.com", "mouthshut.com", "quora.com")),
    # Crowd-sourced/user-editable references: not cited anywhere in this project to date,
    # listed for when one ever is, rather than letting it default to "Other / unclassified".
    ("Crowd-sourced / user-editable reference (Wikipedia, etc.)", ("wikipedia.org", "wikidata.org")),
]


def _classify_source_tier(ref: str) -> str:
    ref_lower = (ref or "").lower()
    for tier_name, markers in _SOURCE_TIERS:
        if any(marker.lower() in ref_lower for marker in markers):
            return tier_name
    return "Other / unclassified source"


def _clean_source_label(raw_source: str) -> str | None:
    """Turns a _FIELD_WITH_SOURCE value's raw `source` string -- a
    filesystem path under output/<reg_no>/, a URL, or several of either
    joined with "; " -- into a short, plain-text inline citation. Strips
    the output/<reg_no>/documents|raw/ path prefix down to just the
    filename (prefixing a bare raw/*.json file with "MahaRERA", since
    that's literally what those files are), reduces a URL to its domain,
    and preserves any trailing parenthetical annotation the source itself
    already carries (e.g. "(First Schedule)"). Returns None for a source
    that explicitly says it's an unconfirmed gap ("gap -- see Gaps
    section") -- there is nothing to cite there, not a citation to
    invent."""
    if not raw_source or not raw_source.strip():
        return None
    if raw_source.strip().lower().startswith("gap"):
        return None

    def _clean_one(piece: str) -> str:
        piece = piece.strip()
        annotation = ""
        m = re.search(r"\s*(\([^)]*\))\s*$", piece)
        if m:
            annotation = " " + m.group(1)
            piece = piece[: m.start()].strip()
        if piece.lower().startswith(("http://", "https://")):
            domain = re.sub(r"^https?://(www\.)?", "", piece).split("/")[0]
            return domain + annotation
        piece = piece.replace("\\", "/")
        name = piece.split("/")[-1]
        folder = piece.rsplit("/", 2)[-2] if "/" in piece else ""
        if folder == "raw" and name.endswith(".json"):
            name = f"MahaRERA {name}"
        return name + annotation

    pieces = [p for p in raw_source.split(";") if p.strip()]
    return "; ".join(_clean_one(p) for p in pieces)


def _topic_citation(facts: dict, topic: str) -> str | None:
    """Looks up facts['sources'] for the first entry tagged with `topic`
    and formats it as a plain-text inline citation, e.g. "(99acres,
    accessed 2026-07-17)". Returns None if no source carries that topic --
    never invents one just to fill a paragraph."""
    for s in facts.get("sources", []) or []:
        if s.get("topic") == topic:
            label = s.get("label") or s.get("ref") or "source"
            accessed = s.get("accessed_date")
            if accessed and accessed != "unknown":
                return f"({label}, accessed {accessed})"
            return f"({label})"
    return None


def _cite(text: str, *topics: str, facts: dict) -> str:
    """Appends the first matching topic citation (tried in the given
    order) to `text`, or returns `text` unchanged if none of the topics
    has a matching source -- never invents a citation just to fill a
    paragraph."""
    if not text:
        return text
    for topic in topics:
        citation = _topic_citation(facts, topic)
        if citation:
            return f"{text} {citation}"
    return text


def _compute_authenticity_summary(facts: dict) -> dict:
    tier_counts = {}
    for src in facts.get("sources", []):
        tier = _classify_source_tier(src.get("ref", ""))
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    total_sources = sum(tier_counts.values())
    primary_tiers = (
        "Primary regulatory record (MahaRERA/MCA, or a document opened from it)",
        "Credit rating agency (CRISIL/ICRA/CARE/India Ratings)",
        "Government legal/insolvency record (IBBI, NCLT, NCDRC, MahaREAT judgments)",
        "Live Google Maps verification",
    )
    primary_count = sum(c for t, c in tier_counts.items() if t in primary_tiers)

    return {
        "tier_counts": tier_counts,
        "total_sources": total_sources,
        "primary_count": primary_count,
        "total_gaps": len(facts.get("gaps", [])),
    }


# ---------------------------------------------------------------------------
# Cross-corroboration enforcement -- turns the Cross-Corroboration score
# criterion below (which only ever *measures* what fraction of topics have
# 2+ sources) into something that actually *acts* on a single-sourced topic,
# instead of letting it sit silently inside a percentage. For every topic
# backed by exactly one source, this makes ONE bounded attempt (never
# retried further, same policy as deep_research._resolve_gaps) to find a
# genuinely independent second source via an agentic web-search pass. A
# topic that gets a real second source is upgraded in place; a topic that
# doesn't gets an explicit, named gap -- so a reader always knows exactly
# which specific findings in this Charter rest on only one source, rather
# than having to infer it from the Cross-Corroboration percentage alone.
# ---------------------------------------------------------------------------

_SECOND_SOURCE_SYSTEM_PROMPT = """You are trying to find a SECOND, INDEPENDENT source that \
corroborates a claim already sourced from one place. Use web_search to look for a different \
publisher/site that confirms the same claim -- not a mirror, syndication, or re-post of the same \
original source, and not the same claim phrased differently by the same underlying publisher.

Your FINAL reply must be ONLY a single raw JSON object -- no prose, no markdown fences -- matching \
exactly this shape: {"found": true, "label": "publisher name", "ref": "one-line description -- url", \
"published_date": "YYYY-MM-DD or 'unknown'", "accessed_date": "YYYY-MM-DD"} if a genuinely \
independent second source was found, or {"found": false, "reason": "one sentence"} if not."""


def _find_single_source_topics(facts: dict) -> dict:
    """Groups facts['sources'] by topic and returns {topic: source} for
    every topic currently backed by exactly one source."""
    by_topic = {}
    for src in facts.get("sources", []):
        topic = src.get("topic")
        if not topic:
            continue
        by_topic.setdefault(topic, []).append(src)
    return {topic: srcs[0] for topic, srcs in by_topic.items() if len(srcs) == 1}


def _attempt_second_source(topic: str, existing_source: dict) -> dict:
    """One bounded attempt to find a second, independent source for a claim
    currently backed by only one. Returns {"found": True, "source": {...}}
    or {"found": False, "reason": ...} -- a missing ANTHROPIC_API_KEY or any
    other failure to even run the attempt surfaces honestly as a reason
    here rather than crashing the caller, same policy as
    deep_research._verify_claim elsewhere in this codebase."""
    prompt = (
        f"Existing source: {existing_source.get('label', '')} -- {existing_source.get('ref', '')}\n"
        f"Topic: {topic}\n"
        f"Find one genuinely independent second source that corroborates this."
    )
    try:
        result = deep_research._run_agentic_pass(prompt, _SECOND_SOURCE_SYSTEM_PROMPT)
    except Exception as e:
        return {"found": False, "reason": f"verification could not run: {e}"}
    if not isinstance(result, dict) or "found" not in result:
        return {"found": False, "reason": "second-source search returned an unrecognized response"}
    if result.get("found"):
        return {
            "found": True,
            "source": {
                "label": result.get("label", ""),
                "ref": result.get("ref", ""),
                "topic": topic,
                "published_date": result.get("published_date") or "unknown",
                "accessed_date": result.get("accessed_date") or datetime.now().strftime("%Y-%m-%d"),
            },
        }
    return {"found": False, "reason": result.get("reason", "no independent second source found")}


def verify_cross_corroboration(facts: dict) -> dict:
    """The code-enforced version of 'verify every material fact from 2+
    independent angles': finds every topic currently backed by exactly one
    source, attempts to upgrade each to two sources, and explicitly names
    the ones that can't be -- rather than leaving single-sourced topics
    invisible inside the Cross-Corroboration score's percentage. Call this
    after all other sources have been assembled (credit rating, IBBI,
    judgments, Maps, etc.), so the single-source topics found here are the
    real final set, not a mid-assembly snapshot."""
    single_source_topics = _find_single_source_topics(facts)
    gaps = list(facts.get("gaps", []))
    attempted = 0
    upgraded = 0
    for topic, existing_source in single_source_topics.items():
        attempted += 1
        attempt = _attempt_second_source(topic, existing_source)
        if attempt["found"]:
            facts.setdefault("sources", []).append(attempt["source"])
            upgraded += 1
        else:
            gaps.append(
                f"Cross-corroboration: the '{topic}' topic is backed by only one source "
                f"({existing_source.get('label', 'unknown')}) -- one independent-second-source "
                f"retry attempt did not find a genuinely separate corroborating source "
                f"({attempt['reason']}). Treat this specific finding with more caution than "
                f"multi-sourced ones in this Charter."
            )
    facts["gaps"] = gaps
    facts.setdefault("_verification_stats", {})["cross_corroboration"] = {
        "single_source_topics_found": len(single_source_topics),
        "attempted": attempted,
        "upgraded_to_two_sources": upgraded,
    }
    return facts


# ---------------------------------------------------------------------------
# Composite Documentation Confidence Score -- a score of how well-sourced and
# verified THIS DOCUMENT's own claims are (source quality, completeness,
# cross-corroboration, recency, re-check success), NOT a rating of the
# underlying project's quality, safety, or investment merit. A project with a
# genuinely bad track record whose problems are thoroughly documented can
# score HIGH here; a genuinely fine project with little public paper trail to
# verify against can score LOW. Six criteria, each computed directly from
# data already in `facts`, none of them a model self-assessment. CRISIL's own
# real-estate methodology (researched, not guessed -- see project notes) does
# NOT publish a numeric weighting formula for any of its three products
# (project grading, developer grading, or credit rating); it relies on a
# committee's qualitative judgment over a factor tree. So there is no
# published formula to replicate here -- the weights below are this project's
# own calibration, informed by CRISIL's STRUCTURE (a small number of named
# top-level buckets rather than one flat checklist; funding/verification
# treated as their own distinct dimension; recency of evidence mattering)
# but not a copy of anything CRISIL discloses. Say so plainly wherever this
# score is presented, so nobody mistakes "CRISIL-informed" for "CRISIL's
# formula".
# ---------------------------------------------------------------------------

_TIER_WEIGHTS = {
    "Primary regulatory record (MahaRERA/MCA, or a document opened from it)": 100,
    "Credit rating agency (CRISIL/ICRA/CARE/India Ratings)": 95,
    "Government legal/insolvency record (IBBI, NCLT, NCDRC, MahaREAT judgments)": 90,
    "Live Google Maps verification": 90,
    "Corporate-registry mirror": 75,
    "Homebuyer-advocacy / watchdog body": 60,
    "Real-estate aggregator / listing site": 50,
    "News / press coverage": 40,
    "Crowd-sourced / user-editable reference (Wikipedia, etc.)": 30,
    "Developer/promoter's own website (self-published)": 25,
    "Social media / user-generated content": 20,
    "Other / unclassified source": 30,
}

_DOC_CONFIDENCE_WEIGHTS = {
    "source_tier_quality": 0.20,
    "primary_tier_density": 0.15,
    "completeness_rate": 0.05,  # was 0.15 -- 0.10 moved to financial_figures_confirmed (Step 5)
    "cross_corroboration": 0.15,
    "recency_legal": 0.05,
    "recency_other": 0.05,
    "verification_rate": 0.25,
    "financial_figures_confirmed": 0.10,  # was 0.00/TBD -- implemented in Step 5
}

_RECENCY_WINDOW_MONTHS = 18  # this project's own calibration, not a disclosed CRISIL threshold
_RECENCY_WINDOW_MONTHS_LEGAL = 3  # legal/litigation sources are held to a much tighter freshness bar

# The four core investment-case figures financial_figures_confirmed checks
# for, and the gap-text markers used to detect each is still an open,
# unconfirmed gap rather than something read from a primary document.
_FINANCIAL_FIGURE_MARKERS = {
    "fsi": (r"\bfsi\b",),
    "land_built_up_area": (r"built-up area", r"built up area", r"\bbua\b"),
    "unit_counts": (r"unit breakdown", r"unit count", r"unit mix", r"number of units"),
    "pricing": (r"\bpricing\b", r"per-sq\.?ft", r"per sq\.?ft", r"\bprice\b"),
}

# ---------------------------------------------------------------------------
# Groundwork constants for the upcoming Developer Score / risk-flagging
# feature -- every number that currently lives only in the design memo, moved
# here so later steps import from one place instead of re-hardcoding it.
# Establishing the constants only: nothing below is wired into scoring or
# flagging logic yet (see _compute_documentation_confidence_score, which
# still computes a single "recency" criterion and has no
# "financial_figures_confirmed" criterion -- that wiring, and the
# corresponding _DOC_CONFIDENCE_WEIGHTS key split above, are a later step).
# ---------------------------------------------------------------------------

_FLAG_THRESHOLDS = {
    "complaint_monitor": 15, "complaint_imminent": 40,
    "appeal_monitor": 5, "appeal_imminent": 15,
    "credit_rating_min_units": 500,  # below this, "no rating found" isn't flagged
}
# The Developer Score's 7-criteria AAA-D framework (replaces the earlier
# 4-pillar composite entirely, per instruction): each criterion is scored
# independently against its own AAA-D band, converted to an even 0-100
# tier-equivalent, then combined by equal weight across whichever criteria
# have real data this pass -- same renormalize-and-skip convention this
# Charter already uses everywhere a computed value might be unavailable.
_DEVELOPER_SCORE_TIER_SCORES = {"AAA": 100.0, "AA": 83.3, "A": 66.7, "B": 50.0, "C": 33.3, "D": 16.7}
# Thresholds sit at the midpoint between adjacent tier-equivalents, used to
# map a combined composite (which can land between two tiers once several
# criteria are averaged) back onto a single overall letter grade.
_DEVELOPER_SCORE_TIER_THRESHOLDS = (("AAA", 91.65), ("AA", 75.0), ("A", 58.35), ("B", 41.65), ("C", 25.0))  # D below C
_DATA_AUTHENTICITY_BANDS = {"High": 70, "Moderate": 45}  # Limited below Moderate


def _band_label(score: float) -> str:
    if score >= _DATA_AUTHENTICITY_BANDS["High"]:
        return "High"
    if score >= _DATA_AUTHENTICITY_BANDS["Moderate"]:
        return "Moderate"
    return "Limited"


# Shared red/amber/green convention so a grade, a band, and a flag all read
# the same way wherever they appear -- fills for KPI cards, text colors for
# flag lines and headline score runs.
_FILL_RED = "F8CBAD"
_FILL_AMBER = "FFE699"
_FILL_GREEN = "C6E0B4"
_FILL_NEUTRAL = "D9E2F3"
_TEXT_RED = "C00000"
_TEXT_AMBER = "BF8F00"
_TEXT_GREEN = "375623"


def _grade_fill(grade: str) -> str:
    return {
        "AAA": _FILL_GREEN, "AA": _FILL_GREEN,
        "A": _FILL_AMBER, "B": _FILL_AMBER,
        "C": _FILL_RED, "D": _FILL_RED,
    }.get(grade, _FILL_NEUTRAL)


def _band_fill(band: str) -> str:
    return {"High": _FILL_GREEN, "Moderate": _FILL_AMBER, "Limited": _FILL_RED}.get(band, _FILL_NEUTRAL)


def _color_run(run, hex_color: str) -> None:
    from docx.shared import RGBColor

    run.font.color.rgb = RGBColor.from_string(hex_color)


def _months_since(date_str: str) -> float | None:
    """Parses a YYYY-MM-DD string and returns months elapsed since then, or
    None for anything unparseable -- including the literal "unknown" the
    schema explicitly allows, which must never be silently coerced into a
    fake date."""
    if not date_str or date_str.strip().lower() == "unknown":
        return None
    try:
        parsed = datetime.strptime(date_str.strip()[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return (datetime.now() - parsed).days / 30.44


def _compute_documentation_confidence_score(facts: dict, authenticity_summary: dict) -> dict:
    """Returns {overall, band, criteria: {name: {score, weight, note}}} -- a
    score of how well-sourced and verified THIS DOCUMENT's claims are, not a
    rating of the underlying project (see the module note above). Every
    criterion is either a plain ratio over data already in `facts`, or
    explicitly marked not-applicable and excluded (with the remaining
    weights renormalized to still sum to 100%) rather than silently scored
    as 0 -- a project with, say, no distinct source topics yet shouldn't be
    penalized on Cross-Corroboration as if it had failed that check."""
    sources = facts.get("sources", [])
    total_sources = authenticity_summary["total_sources"]
    total_gaps = authenticity_summary["total_gaps"]
    criteria = {}

    # 1. Primary-Tier Density
    if total_sources:
        criteria["primary_tier_density"] = {
            "score": 100 * authenticity_summary["primary_count"] / total_sources,
            "note": f"{authenticity_summary['primary_count']} of {total_sources} sources are top-tier (primary regulatory, credit rating, government legal record, or live Maps verification).",
        }

    # 2. Weighted Source-Tier Quality
    if total_sources:
        weighted_sum = sum(
            _TIER_WEIGHTS.get(tier, _TIER_WEIGHTS["Other / unclassified source"]) * count
            for tier, count in authenticity_summary["tier_counts"].items()
        )
        criteria["source_tier_quality"] = {
            "score": weighted_sum / total_sources,
            "note": "Weighted average trust level across all cited source tiers (see the tier table above for this document's own calibration of each tier's weight).",
        }

    # 3. Completeness Rate
    if total_sources + total_gaps:
        criteria["completeness_rate"] = {
            "score": 100 * total_sources / (total_sources + total_gaps),
            "note": f"{total_sources} confirmed source(s) versus {total_gaps} item(s) left as an explicit, unresolved gap.",
        }

    # 4. Independent Verification Rate -- combines the URL-based re-check
    # (_verify_material_claims) and the document-grounding re-check
    # (_check_document_grounding); N/A if neither ever ran (nothing sourced
    # from a URL or a checkable local document this pass). Zero attempts is
    # NOT just renormalized away silently, unlike every other N/A criterion
    # here -- it's a distinct, worse signal (nothing in this Charter was
    # independently re-checked at all) that gets its own override below,
    # after `overall`/`band` are otherwise computed.
    v = facts.get("_verification_stats", {})
    url_stats = v.get("url", {"attempted": 0, "confirmed": 0})
    doc_stats = v.get("document", {"attempted": 0, "confirmed": 0})
    total_attempted = url_stats["attempted"] + doc_stats["attempted"]
    if total_attempted:
        total_confirmed = url_stats["confirmed"] + doc_stats["confirmed"]
        criteria["verification_rate"] = {
            "score": 100 * total_confirmed / total_attempted,
            "note": f"{total_confirmed} of {total_attempted} independently-checkable claims were confirmed on re-check (web-sourced claims re-verified via search; local-document claims cross-checked against the actual extracted text).",
        }

    # 5. Cross-Corroboration -- % of distinct topics backed by >=2 sources.
    topics = [s.get("topic") for s in sources if s.get("topic")]
    if topics:
        topic_counts = {}
        for t in topics:
            topic_counts[t] = topic_counts.get(t, 0) + 1
        multi_backed = sum(1 for c in topic_counts.values() if c >= 2)
        criteria["cross_corroboration"] = {
            "score": 100 * multi_backed / len(topic_counts),
            "note": f"{multi_backed} of {len(topic_counts)} distinct topics cited are backed by 2 or more independent sources, not just one.",
        }

    # 6. Recency -- % of dated sources (published_date, falling back to
    # accessed_date for a live-record source with no publish date of its
    # own) accessed/published within _RECENCY_WINDOW_MONTHS. Sources with
    # no parseable date at all (both fields "unknown" or missing) are
    # excluded from the denominator, not counted as stale -- there's
    # nothing to assess. Split legal vs. other (not one blended "recency"
    # criterion) because a stale legal/insolvency source is a materially
    # different risk than a stale pricing/press source -- a legal record
    # going 18 months without a re-check is a real gap; a market-pricing
    # figure that old is a much smaller one. "Legal" is judged by the
    # source's own topic (litigation/insolvency/appeal-adjudication ones)
    # or, failing that, its classified tier being the government
    # legal/insolvency record tier -- everything else is "other". Each
    # half is independently omittable (renormalized away) if that half
    # simply has no dated sources this pass, same as every other criterion
    # here.
    _LEGAL_TOPICS = {"legal_documents", "litigation", "insolvency_status", "appeal_judgments"}
    _LEGAL_TIER = "Government legal/insolvency record (IBBI, NCLT, NCDRC, MahaREAT judgments)"
    legal_ages, other_ages = [], []
    for s in sources:
        age = _months_since(s.get("published_date", ""))
        if age is None:
            age = _months_since(s.get("accessed_date", ""))
        if age is None:
            continue
        is_legal = s.get("topic") in _LEGAL_TOPICS or _classify_source_tier(s.get("ref", "")) == _LEGAL_TIER
        (legal_ages if is_legal else other_ages).append(age)

    if legal_ages:
        fresh = sum(1 for a in legal_ages if a <= _RECENCY_WINDOW_MONTHS_LEGAL)
        criteria["recency_legal"] = {
            "score": 100 * fresh / len(legal_ages),
            "note": f"{fresh} of {len(legal_ages)} dated legal/litigation/insolvency sources are within this document's tighter {_RECENCY_WINDOW_MONTHS_LEGAL}-month freshness window for legal sources (this project's own calibration, not a disclosed external standard).",
        }
    if other_ages:
        fresh = sum(1 for a in other_ages if a <= _RECENCY_WINDOW_MONTHS)
        criteria["recency_other"] = {
            "score": 100 * fresh / len(other_ages),
            "note": f"{fresh} of {len(other_ages)} other dated sources (pricing, corporate identity, distances, etc.) are within this document's {_RECENCY_WINDOW_MONTHS}-month freshness window.",
        }

    # 7. Financial Figures Confirmed -- % of the four core investment-case
    # figures (FSI, land/built-up area, unit counts, pricing) that are NOT
    # named in facts["gaps"] -- i.e. confirmed from a primary document
    # rather than left an open gap. Unlike every other criterion above,
    # this one always has something to say (facts["gaps"] is always at
    # least an empty list), so it's never skipped/renormalized away.
    gaps_blob = " ".join(facts.get("gaps", [])).lower()
    unconfirmed_figures = [name for name, patterns in _FINANCIAL_FIGURE_MARKERS.items() if any(re.search(p, gaps_blob) for p in patterns)]
    confirmed_count = len(_FINANCIAL_FIGURE_MARKERS) - len(unconfirmed_figures)
    note = f"{confirmed_count} of {len(_FINANCIAL_FIGURE_MARKERS)} core figures (FSI, land/built-up area, unit counts, pricing) have no unresolved gap against them"
    if unconfirmed_figures:
        note += f" -- flagged as an open gap: {', '.join(unconfirmed_figures)}"
    criteria["financial_figures_confirmed"] = {
        "score": 100 * confirmed_count / len(_FINANCIAL_FIGURE_MARKERS),
        "note": note,
    }

    applicable_weight = sum(_DOC_CONFIDENCE_WEIGHTS[k] for k in criteria)
    if applicable_weight == 0:
        overall = 0.0
    else:
        overall = sum(criteria[k]["score"] * _DOC_CONFIDENCE_WEIGHTS[k] for k in criteria) / applicable_weight
        for k in criteria:
            criteria[k]["weight"] = round(100 * _DOC_CONFIDENCE_WEIGHTS[k] / applicable_weight, 1)

    band = _band_label(overall)
    result = {
        "overall": round(overall, 1),
        "band": band,
        "criteria": criteria,
        "skipped_criteria": sorted(set(_DOC_CONFIDENCE_WEIGHTS) - set(criteria)),
    }

    # Zero-verification safeguard: 0 independently-checkable claims is a
    # materially worse signal than "N/A, renormalize away" -- nothing in
    # this Charter was ever re-checked against an independent source, so
    # the band is capped at "Moderate" regardless of what every other
    # criterion computes (restrains a High down to Moderate; a Limited
    # stays Limited -- same "never worsen, only restrain" cap pattern as
    # the Developer Score's imminent-flag override).
    if total_attempted == 0:
        result["verification_warning"] = "0 claims were independently re-checked this pass."
        if band == "High":
            result["band"] = "Moderate"

    return result


# ---------------------------------------------------------------------------
# Flag classification -- reads the already-assembled facts dict (nothing new
# fetched here) and sorts every material risk signal into exactly one of
# three urgency tiers: imminent (act before proceeding), structural (a
# standing characteristic, raise directly with the developer), or monitor
# (re-check on a future pass). Ported from this project's design memo and
# validated against Company_Charter_GodrejParkGreens_P52100019639.facts.json,
# whose known-correct split (4 imminent / 5 structural / 5 monitor) is
# asserted in test_classify_flags.py.
#
# Two rules below go beyond what the memo states outright, both found
# necessary to reproduce that validated split and flagged here rather than
# silently invented:
#   1. "Near-sellout despite an extended completion date -> imminent" --
#      the memo's rule list has no explicit slot for this, but the reference
#      Charter's 4th Imminent flag is exactly this signal (a project that is
#      ~99% sold against a completion date already pushed back twice is a
#      real, high-urgency exposure for almost the entire buyer base). Gated
#      on _NEAR_SELLOUT_PCT below, a local threshold (not in _FLAG_THRESHOLDS,
#      since the memo's dict has no key for it).
#   2. Two specific gap texts (the one about complaint SUBSTANCE being
#      unconfirmable, and the one about no independent market comparable
#      being confirmable) are promoted to structural rather than falling to
#      the "any remaining gap -> monitor" default. Both describe a STANDING
#      limitation of the data source/market (not closable by a future
#      documents pass), versus every other unmatched gap here, which is
#      closable by more work. That distinction is judgment, not a clean
#      keyword rule -- _STRUCTURAL_GAP_PHRASES below is deliberately narrow
#      (calibrated to the one validated example, not claimed to generalize)
#      rather than pretending a broader heuristic was validated.
# ---------------------------------------------------------------------------

_NEAR_SELLOUT_PCT = 90  # this project's own calibration, not from the memo's threshold dict

_KNOWN_LISTED_GROUP_MARKERS = (
    "godrej", "dlf", "oberoi", "prestige", "sobha", "brigade", "lodha", "macrotech",
    "puravankara", "sunteck", "kolte", "mahindra lifespace", "shapoorji", "tata housing",
)

_FSI_AREA_GAP_MARKERS = ("fsi", "bua", "built-up area", "built up area")

# Deliberately narrow, matched to this one validated Charter's own wording --
# see the module note above.
_STRUCTURAL_GAP_PHRASES = ("complaint substance", "genuinely independent comparable")

_ESCROW_GAP_MARKERS = ("escrow", "collection account")


def _first_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text or "", re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_complaint_appeal_counts(facts: dict) -> tuple[int | None, int | None]:
    """Returns (complaint_count, appeal_count). Reads rera_core_fields'
    structured total_complaints_count/total_appeals_count companions first
    -- exact by construction, no regex involved -- and only falls back to
    parsing litigations_per_record's prose for older facts.json files that
    predate those fields. The prose-parsing fallback tries the specific
    phrasing first (e.g. "67 total complaint-category filings ..., 18
    appeals on record ..."), then a plainer "N complaints"/"N appeals"
    match (e.g. "0 complaints, 0 appeals on MahaRERA's own record ...") --
    a genuinely clean 0/0 record must parse just as confidently as a high
    one, or it silently drops out of the Developer Score's legal_compliance
    pillar. Either return value is None only if nothing above could
    confidently resolve it -- never a guessed number."""
    core = facts.get("rera_core_fields", {}) or {}
    complaint_count = core.get("total_complaints_count")
    appeal_count = core.get("total_appeals_count")
    if isinstance(complaint_count, int) and isinstance(appeal_count, int):
        return complaint_count, appeal_count

    text = core.get("litigations_per_record", "") or ""
    if not isinstance(complaint_count, int):
        complaint_count = _first_int(text, r"([\d,]+)\s+total complaint")
        if complaint_count is None:
            complaint_count = _first_int(text, r"([\d,]+)\s+complaints?\b")
    if not isinstance(appeal_count, int):
        appeal_count = _first_int(text, r"([\d,]+)\s+appeals?\s+on record")
        if appeal_count is None:
            appeal_count = _first_int(text, r"([\d,]+)\s+appeals?\b")
    return complaint_count, appeal_count


def _parse_total_units_and_sold(facts: dict) -> tuple[int | None, int | None]:
    """Returns (total_units, sold_units). Reads rera_core_fields'
    structured units_total/units_sold companions first, falling back to
    regexing total_building_units's prose only for older facts.json files
    that predate those fields. Either is None if not confidently
    resolvable -- never a guessed number."""
    core = facts.get("rera_core_fields", {}) or {}
    total = core.get("units_total")
    sold = core.get("units_sold")
    if isinstance(total, int) and isinstance(sold, int):
        return total, sold

    text = core.get("total_building_units", "") or ""
    if not isinstance(total, int):
        total = _first_int(text, r"([\d,]+)\s+total units")
    if not isinstance(sold, int):
        sold = _first_int(text, r"([\d,]+)\s+sold")
    return total, sold


def _matches_known_listed_group(promoter_name: str) -> bool:
    name = (promoter_name or "").lower()
    return any(marker in name for marker in _KNOWN_LISTED_GROUP_MARKERS)


def _credit_rating_applies(facts: dict, total_units: int | None) -> bool:
    """A missing credit rating is only worth flagging for a project large
    enough, or a promoter well-known enough, that a rating would plausibly
    exist -- small LLPs are not expected to have one (see
    lookup_credit_rating's own module note)."""
    if total_units is not None and total_units > _FLAG_THRESHOLDS["credit_rating_min_units"]:
        return True
    promoter_name = ((facts.get("corporate_identity", {}) or {}).get("promoter_name") or {}).get("value", "")
    return _matches_known_listed_group(promoter_name)


def _classify_ibbi_hit(note_text: str) -> tuple[str, str]:
    """Reads an IBBI status_text/note for whether a positive hit is against
    the exact promoter entity and whether it's open or closed/settled.
    Returns (severity, reason) where severity is "imminent", "structural",
    or "unclear" -- this text is freeform, so anything not confidently
    readable is treated as "unclear" (caller flags it structural and says
    so explicitly) rather than guessed at."""
    text = (note_text or "").lower()
    is_affiliated_not_itself = "affiliated" in text or "not " in text and " itself" in text
    is_closed = any(term in text for term in ("closure", "settlement", "settled", "12a", "withdrawn"))
    if is_closed or is_affiliated_not_itself:
        return "structural", "the IBBI record reads as either a closed/settled matter or tied to an affiliated (not the exact promoter) entity"
    if "open" in text or "ongoing" in text or "pending" in text:
        return "imminent", "the IBBI record reads as an open matter against the exact promoter entity"
    return "unclear", "whether this IBBI hit is open/closed or against the exact promoter entity could not be confidently determined from the freeform status text"


def _classify_flags(facts: dict) -> dict:
    """Sorts every material risk signal already present in `facts` into
    {"imminent": [...], "structural": [...], "monitor": [...]} -- each item
    is {"text": str, "field": str}. Never fetches anything new; purely a
    read-and-classify pass over data this Charter already assembled."""
    imminent, structural, monitor = [], [], []

    core = facts.get("rera_core_fields", {}) or {}
    corp = facts.get("corporate_identity", {}) or {}
    land = facts.get("land_identification", {}) or {}

    # 1. RERA registration number
    reg_no = (core.get("registration_number") or "").strip()
    if not reg_no:
        imminent.append({
            "text": "No MahaRERA registration number could be resolved for this project.",
            "field": "rera_core_fields.registration_number",
        })

    # 2. CTS/plot number
    cts_value = (land.get("survey_cts_plot_numbers") or {}).get("value", "") or ""
    if not cts_value.strip() or cts_value.strip().lower().startswith(("not found", "not disclosed", "not confirmed", "not available")):
        net_area = (land.get("net_area") or {}).get("value", "") or ""
        blocks_entirely = not net_area.strip() or net_area.strip().lower().startswith(("not found", "not disclosed", "not confirmed", "not available"))
        target = imminent if blocks_entirely else structural
        target.append({
            "text": "No CTS/plot number could be confirmed for this project's land" + (
                " -- and with no area figure available either, land verification is blocked entirely." if blocks_entirely
                else ", though area figures are otherwise available."
            ),
            "field": "land_identification.survey_cts_plot_numbers",
        })

    # 3. CIN/LLPIN
    cin_llpin_text = (corp.get("cin_llpin") or {}).get("value", "") or ""
    if not extract_cin(cin_llpin_text) and not extract_llpin(cin_llpin_text):
        structural.append({
            "text": "No CIN or LLPIN could be confirmed for the promoter.",
            "field": "corporate_identity.cin_llpin",
        })

    # 4 & 8. IBBI insolvency + credit rating -- handled together since,
    # when NEITHER ran this pass at all, the memo's reference Charter
    # reports them as a single combined "not checked" structural note
    # rather than two near-duplicate items.
    total_units, sold_units = _parse_total_units_and_sold(facts)
    promoter_name = (corp.get("promoter_name") or {}).get("value", "")
    ibbi_check = facts.get("ibbi_insolvency_check")
    credit_check = facts.get("credit_rating_check")

    if ibbi_check is None and credit_check is None:
        if _credit_rating_applies(facts, total_units):
            structural.append({
                "text": "Credit rating and IBBI insolvency status were not checked in this Charter pass -- a process gap, not a finding.",
                "field": "ibbi_insolvency_check / credit_rating_check",
            })
    else:
        if ibbi_check is not None and ibbi_check.get("found_process") is True:
            note_text = ibbi_check.get("status_text") or ibbi_check.get("note") or ""
            severity, reason = _classify_ibbi_hit(note_text)
            if severity == "imminent":
                imminent.append({"text": f"IBBI insolvency record found against the exact promoter entity, appearing open: {reason}.", "field": "ibbi_insolvency_check.status_text"})
            else:
                prefix = "IBBI insolvency record found" if severity == "structural" else "IBBI insolvency record found, but"
                structural.append({"text": f"{prefix} -- {reason}; shown here rather than suppressed.", "field": "ibbi_insolvency_check.status_text"})
        elif ibbi_check is not None and ibbi_check.get("found_process") is None:
            monitor.append({"text": f"IBBI insolvency check could not run this pass: {ibbi_check.get('note', 'reason not recorded')}.", "field": "ibbi_insolvency_check.note"})

        if credit_check is not None:
            promoter_result = (credit_check.get("promoter") or {})
            if promoter_result.get("found"):
                ratings_text = " ".join(
                    item.get("rating", "") for agency in promoter_result.get("ratings", []) for item in agency.get("instruments", [])
                ).lower()
                sub_investment_grade = bool(re.search(r"\b(bb|b|c|d)\b", ratings_text)) and "bbb" not in ratings_text
                if "downgraded" in ratings_text or sub_investment_grade:
                    imminent.append({
                        "text": "Credit rating found is below investment grade or was downgraded versus a prior run.",
                        "field": "credit_rating_check.promoter.ratings",
                    })
            elif _credit_rating_applies(facts, total_units):
                structural.append({
                    "text": "No public credit rating was found for the promoter, despite this project's scale/promoter profile making one plausible.",
                    "field": "credit_rating_check.promoter.note",
                })

    # 5. Complaint count vs thresholds
    complaint_count, appeal_count = _parse_complaint_appeal_counts(facts)
    if complaint_count is not None:
        if complaint_count > _FLAG_THRESHOLDS["complaint_imminent"]:
            imminent.append({"text": f"Complaint volume: {complaint_count} total filings against this project -- above the imminent threshold ({_FLAG_THRESHOLDS['complaint_imminent']}).", "field": "rera_core_fields.litigations_per_record"})
        elif complaint_count > _FLAG_THRESHOLDS["complaint_monitor"]:
            monitor.append({"text": f"Complaint volume: {complaint_count} total filings against this project -- above the monitor threshold ({_FLAG_THRESHOLDS['complaint_monitor']}) but not yet at the imminent level.", "field": "rera_core_fields.litigations_per_record"})
        elif complaint_count >= 1:
            structural.append({"text": f"Complaint volume: {complaint_count} total filing(s) on record against this project -- a visible floor worth noting, not blended into zero.", "field": "rera_core_fields.litigations_per_record"})

    # 6. Appeal count vs thresholds -- same pattern
    if appeal_count is not None:
        if appeal_count > _FLAG_THRESHOLDS["appeal_imminent"]:
            imminent.append({"text": f"Appeal volume: {appeal_count} appeals on record -- above the imminent threshold ({_FLAG_THRESHOLDS['appeal_imminent']}).", "field": "rera_core_fields.litigations_per_record"})
        elif appeal_count > _FLAG_THRESHOLDS["appeal_monitor"]:
            monitor.append({"text": f"Appeal volume: {appeal_count} appeals on record -- above the monitor threshold ({_FLAG_THRESHOLDS['appeal_monitor']}) but not yet at the imminent level.", "field": "rera_core_fields.litigations_per_record"})
        elif appeal_count >= 1:
            structural.append({"text": f"Appeal volume: {appeal_count} appeal(s) on record -- a visible floor worth noting, not blended into zero.", "field": "rera_core_fields.litigations_per_record"})

    # 7. Promoter portfolio total -- always structural, never colour-scored
    portfolio = facts.get("promoter_portfolio")
    if portfolio:
        portfolio_total_complaints = (portfolio.get("totals", {}) or {}).get("total_complaints")
        text = f"Promoter portfolio: {portfolio.get('projects_analyzed', 0)} MahaRERA-registered project(s) tied to this promoter."
        if portfolio_total_complaints and complaint_count is not None and portfolio_total_complaints > 0:
            concentration_pct = round(100 * complaint_count / portfolio_total_complaints, 1)
            text += f" This project alone accounts for {complaint_count} of {portfolio_total_complaints} total complaints across that portfolio ({concentration_pct}%)."
        structural.append({"text": text, "field": "promoter_portfolio.totals"})

    # Near-sellout despite an extended completion date -- see module note
    # above (a memo-gap-filling addition, not stated in the original rules).
    proposed_completion_text = (core.get("proposed_completion_date") or "").lower()
    if total_units and sold_units and total_units > 0 and "extend" in proposed_completion_text:
        sold_pct = 100 * sold_units / total_units
        if sold_pct >= _NEAR_SELLOUT_PCT:
            imminent.append({
                "text": f"Completion timeline has been extended, against a project that is {round(sold_pct, 1)}% sold -- nearly the entire buyer base is exposed to this delay.",
                "field": "rera_core_fields.proposed_completion_date",
            })

    # 10, 11, 12, 13. Mortgage lender change, escrow gap, FSI/area figures,
    # and the remaining gaps -- monitor by default.
    if facts.get("mortgage_lender_history_note"):
        monitor.append({"text": facts["mortgage_lender_history_note"], "field": "mortgage_lender_history_note"})

    for i, gap_text in enumerate(facts.get("gaps", [])):
        lowered = gap_text.lower()
        field = f"gaps[{i}]"
        if any(marker in lowered for marker in _ESCROW_GAP_MARKERS):
            structural.append({"text": gap_text, "field": field})
        elif any(marker in lowered for marker in _FSI_AREA_GAP_MARKERS):
            imminent.append({"text": gap_text, "field": field})
        elif any(phrase in lowered for phrase in _STRUCTURAL_GAP_PHRASES):
            structural.append({"text": gap_text, "field": field})
        else:
            monitor.append({"text": gap_text, "field": field})

    return {"imminent": imminent, "structural": structural, "monitor": monitor}


# ---------------------------------------------------------------------------
# Developer Score -- a composite read on the PROMOTER's own standing, scored
# against a fixed 7-criteria industry rubric (track record years, team
# strength, past area developed, area developed within 5km, financial
# strength/debt structure, past default count, entity/organization type),
# each independently banded AAA/AA/A/B/C/D. Distinct from
# _compute_documentation_confidence_score above (which scores this
# DOCUMENT's sourcing, not the promoter). Same renormalization philosophy
# as everywhere else in this Charter: a criterion with no underlying data
# is skipped and the remaining criteria are combined by equal weight,
# rather than faking a neutral or zero score for data that was never
# gathered. Two of the seven criteria (team strength; financial strength's
# debt ratios) have no public source this pipeline can check at all today
# -- they're included for completeness, not left out, so a reader always
# sees why they're missing rather than wondering if they were forgotten.
# ---------------------------------------------------------------------------


def _tier_from_score(score: float) -> str:
    for tier, threshold in _DEVELOPER_SCORE_TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "D"


def _score_track_record_years(facts: dict) -> dict:
    """Criterion 1: years this promoter (or its parent group, if this is a
    group-affiliated SPV) has been in the real-estate industry. Reads
    developer_track_record.years_in_industry -- a field the deep-research
    promoter-profile step is expected to populate with a sourced start
    date; not yet wired into every pipeline run, so this is commonly N/A
    today, not a parsing failure."""
    years = (facts.get("developer_track_record") or {}).get("years_in_industry")
    if not isinstance(years, (int, float)):
        return {"score": None, "tier": None, "reason": "Years in the industry not confirmed this pass -- needs a sourced start date for the promoter or its parent group from the deep-research profile step."}
    if years > 20:
        tier = "AAA"
    elif years >= 17:
        tier = "AA"
    elif years >= 13:
        tier = "A"
    elif years >= 9:
        tier = "B"
    elif years >= 5:
        tier = "C"
    else:
        tier = "D"
    return {"score": _DEVELOPER_SCORE_TIER_SCORES[tier], "tier": tier, "note": f"{years} years in the industry"}


def _score_team_strength(facts: dict) -> dict:
    """Criterion 2: strength/experience of the Liaisoning, Project
    Development, and Sales & CRM teams. No public source (MahaRERA,
    ZaubaCorp, or general web search) discloses a promoter's internal
    headcount by function -- this is a permanent, structural gap, not a
    pending build, unless a specific internal data feed is wired in."""
    return {"score": None, "tier": None, "reason": "No public source discloses internal team headcounts by function (Liaisoning / Project Development / Sales & CRM) -- a structural limitation of publicly-available data, not something a future pass can close."}


def _score_past_area_developed(facts: dict) -> dict:
    """Criterion 3: total area (lakh sq ft) this promoter has developed and
    delivered in the past, across its MahaRERA-registered portfolio.
    Reads promoter_portfolio.totals.total_area_developed_lakh_sqft --
    requires per-project area figures aggregated across the promoter's
    other registered projects, not yet computed by every pipeline run."""
    area = ((facts.get("promoter_portfolio") or {}).get("totals") or {}).get("total_area_developed_lakh_sqft")
    if not isinstance(area, (int, float)):
        return {"score": None, "tier": None, "reason": "promoter_portfolio.totals.total_area_developed_lakh_sqft not available -- requires area figures aggregated across the promoter's other MahaRERA-registered projects, not yet computed this pass."}
    if area > 120:
        tier = "AAA"
    elif area >= 81:
        tier = "AA"
    elif area >= 51:
        tier = "A"
    elif area >= 21:
        tier = "B"
    elif area >= 6:
        tier = "C"
    else:
        tier = "D"
    return {"score": _DEVELOPER_SCORE_TIER_SCORES[tier], "tier": tier, "note": f"~{area} lakh sq ft developed across the promoter's MahaRERA-registered portfolio"}


def _score_area_within_5km(facts: dict) -> dict:
    """Criterion 4: this promoter's own total developed area (lakh sq ft)
    within a 5km radius of THIS project -- a read on promoter influence in
    the micro-market, not a competitor/all-developer figure. Reads
    promoter_portfolio.totals.area_within_5km_lakh_sqft, geo-filtered from
    the same portfolio data as criterion 3."""
    area = ((facts.get("promoter_portfolio") or {}).get("totals") or {}).get("area_within_5km_lakh_sqft")
    if not isinstance(area, (int, float)):
        return {"score": None, "tier": None, "reason": "promoter_portfolio.totals.area_within_5km_lakh_sqft not available -- this pass's promoter_portfolio.json predates the geocoding-based 5km filter (build_promoter_portfolio's subject_project_partners_data/subject_reg_no params), or no subject location could be geocoded. Re-run the pipeline to compute it, rather than treating this as a permanent gap."}
    if area > 50:
        tier = "AAA"
    elif area >= 21:
        tier = "AA"
    elif area >= 6:
        tier = "A"
    elif area >= 2:
        tier = "B"
    elif area >= 1:
        tier = "C"
    else:
        tier = "D"
    return {"score": _DEVELOPER_SCORE_TIER_SCORES[tier], "tier": tier, "note": f"~{area} lakh sq ft developed by this promoter within 5km of this project"}


def _score_financial_strength_debt(facts: dict) -> dict:
    """Criterion 5: a combined 1-50 point score across % debt in total
    capital employed, % secured debt in total debt, and past-default
    occurrence (LOWER points = stronger, per the rubric). Requires actual
    balance-sheet debt structure -- MahaRERA and ZaubaCorp's public profile
    (authorised/paid-up capital only) don't disclose this; only resolvable
    when a credit-rating agency's rationale or an MCA financial filing
    states these ratios explicitly, which is uncommon for the private,
    unrated companies that make up most MahaRERA promoters."""
    points = (facts.get("developer_track_record") or {}).get("financial_strength_points")
    if not isinstance(points, (int, float)):
        return {"score": None, "tier": None, "reason": "Debt-to-capital ratio, secured-debt ratio, and default occurrence are not disclosed by any source this pipeline checks (MahaRERA/ZaubaCorp don't carry balance-sheet debt structure) -- only resolvable when a credit-rating rationale or MCA financial filing states these figures."}
    if points <= 8:
        tier = "AAA"
    elif points <= 17:
        tier = "AA"
    elif points <= 26:
        tier = "A"
    elif points <= 34:
        tier = "B"
    elif points <= 42:
        tier = "C"
    else:
        tier = "D"
    return {"score": _DEVELOPER_SCORE_TIER_SCORES[tier], "tier": tier, "note": f"{points} combined debt/default points (lower is stronger)"}


def _score_past_default_count(facts: dict) -> dict:
    """Criterion 6: number of past default events. Derived from the IBBI
    insolvency check (a clean IBBI record scores 0 defaults) cross-checked
    against the credit-rating agency's own note text when a rating exists
    -- IBBI alone only catches insolvency proceedings that reached the
    Board, not every default event, so a rating note that itself flags
    "default" language blocks this from being scored 0 without a human
    reading the raw text. Never guesses an exact count beyond 0/unclear."""
    ibbi_check = facts.get("ibbi_insolvency_check")
    if ibbi_check is None or ibbi_check.get("found_process") is None:
        return {"score": None, "tier": None, "reason": "IBBI insolvency check did not run this pass -- nothing to count past defaults from."}
    if ibbi_check["found_process"] is not False:
        return {"score": None, "tier": None, "reason": "IBBI returned an insolvency record against this CIN -- see the Insolvency Check section for the raw detail; an exact default count isn't machine-countable from that text, so this is left unscored rather than guessed."}
    credit_check = facts.get("credit_rating_check") or {}
    note_text = ((credit_check.get("promoter") or {}).get("note") or "").lower()
    if "default" in note_text and "no instance of default" not in note_text:
        return {"score": None, "tier": None, "reason": "IBBI shows no insolvency process, but the credit-rating check's own note mentions \"default\" in a way this checker can't confidently classify as zero -- see the Credit Rating Check section for the raw text rather than guessing a count."}
    return {"score": _DEVELOPER_SCORE_TIER_SCORES["AAA"], "tier": "AAA", "note": "0 defaults -- IBBI shows no insolvency process against this CIN, and nothing in the credit-rating check (if one ran) states otherwise."}


def _score_entity_rating(facts: dict) -> dict:
    """Criterion 7: entity/organization type. Reads
    corporate_identity.organization_type -- a Private or Public Limited
    Company scores AAA outright; an LLP or Partnership's stated
    "willingness to convert to Pvt Ltd" can't be independently verified
    from public filings, so both are conservatively scored at the "not
    willing to convert" band rather than crediting unconfirmed intent."""
    org_type = ((facts.get("corporate_identity") or {}).get("organization_type") or {}).get("value", "")
    text = org_type.lower()
    if not text:
        return {"score": None, "tier": None, "reason": "corporate_identity.organization_type not confirmed this pass."}
    if "llp" in text or "liability partnership" in text:
        return {"score": _DEVELOPER_SCORE_TIER_SCORES["D"], "tier": "D", "note": "LLP with no independently confirmed intent to convert to Pvt Ltd -- conservatively scored at the \"not willing to convert\" band rather than assuming unconfirmed intent."}
    if "partnership" in text:
        return {"score": _DEVELOPER_SCORE_TIER_SCORES["D"], "tier": "D", "note": "Partnership with no independently confirmed intent to convert to Pvt Ltd -- conservatively scored at the \"not willing to convert\" band rather than assuming unconfirmed intent."}
    if "limited" in text:
        return {"score": _DEVELOPER_SCORE_TIER_SCORES["AAA"], "tier": "AAA", "note": f"Corporatized entity ({org_type.split('(')[0].strip()})."}
    return {"score": None, "tier": None, "reason": f"organization_type ('{org_type}') did not match a recognized entity-type category (Private/Public Limited, LLP, or Partnership)."}


def _compute_developer_score(facts: dict, flags: dict) -> dict:
    """Returns {"composite": 0-100, "grade": one of AAA/AA/A/B/C/D,
    "criteria": {name: {"score", "tier", "weight", "note"} or {"score":
    None, "tier": None, "weight": None, "reason"}}}. All seven criteria
    always appear in `criteria` -- computed ones carry a score/tier/weight/
    note, uncomputable ones carry an explicit reason instead, so a reader
    always sees all seven named, never silently missing. Combines whichever
    criteria have real data by equal weight (renormalized) -- the same
    skip-and-renormalize convention this Charter uses everywhere else,
    rather than faking a neutral value for data that was never gathered."""
    criteria = {
        "track_record_years": _score_track_record_years(facts),
        "team_strength": _score_team_strength(facts),
        "past_area_developed": _score_past_area_developed(facts),
        "area_within_5km": _score_area_within_5km(facts),
        "financial_strength_debt": _score_financial_strength_debt(facts),
        "past_default_count": _score_past_default_count(facts),
        "entity_rating": _score_entity_rating(facts),
    }

    scored = {k: v for k, v in criteria.items() if v["score"] is not None}
    if not scored:
        composite = 0.0
    else:
        composite = sum(v["score"] for v in scored.values()) / len(scored)
        for k in scored:
            criteria[k]["weight"] = round(100 / len(scored), 1)
    for k, v in criteria.items():
        if v["score"] is None:
            v["weight"] = None

    grade = _tier_from_score(composite) if scored else "D"
    if flags.get("imminent") and grade in ("AAA", "AA"):
        # Hard cap: an imminent-tier flag means this can never be graded
        # better than A, regardless of how strong the composite otherwise
        # looks -- but a composite that already bands to A or below is left
        # as is, since the cap only restrains a grade that would otherwise
        # be too generous, never softens one that's already lower.
        grade = "A"

    return {"composite": round(composite, 1), "grade": grade, "criteria": criteria}


def _parse_completion_slippage(facts: dict) -> tuple[str, bool]:
    """Returns (display text, was_extended). Reads rera_core_fields'
    structured completion_date_current/completion_date_original companions
    first -- an empty completion_date_original means "never extended" by
    construction (see _SYSTEM_PROMPT), not "unknown". Falls back to
    regexing proposed_completion_date's prose (e.g. "2028-03-30 per the
    live RERA record (originally 2024-03-30; ...)" -> ("2024-03-30 -> 2028-
    03-30", True)) only for older facts.json files that predate those
    fields -- never a guessed date either way."""
    core = facts.get("rera_core_fields", {}) or {}
    current = core.get("completion_date_current")
    original = core.get("completion_date_original")
    if current:
        if original and original != current:
            return f"{original} -> {current}", True
        return current, False

    text = core.get("proposed_completion_date", "") or ""
    current_match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    original_match = re.search(r"originally\s+(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if current_match and original_match and current_match.group(1) != original_match.group(1):
        return f"{original_match.group(1)} -> {current_match.group(1)}", True
    return (current_match.group(1) if current_match else "Not confirmed"), False


def _land_title_summary(facts: dict) -> tuple[str, str]:
    """Returns (short display text, fill color) for the land-title portion
    of litigation_status.value -- green if that text reads as no title
    litigation/dispute/encumbrance found (checked against several
    projects' worth of actual phrasing, not just one), amber if
    litigation_status exists but doesn't read clean, neutral only if
    litigation_status itself is empty. Never invents a status beyond what
    that text actually says."""
    text = ((facts.get("litigation_status") or {}).get("value", "") or "")
    if not text.strip():
        return "Not confirmed this pass", _FILL_NEUTRAL
    lowered = text.lower()
    clean_signals = (
        "no litigation affecting the property",
        "not shown to be under title dispute",
        "no litigation directly against",
        "clear, marketable, encumbrance-free title",
        "no litigation on the land",
    )
    if any(signal in lowered for signal in clean_signals):
        return "Clear (see Litigation Status for source)", _FILL_GREEN
    return "See Litigation Status -- not a clean read", _FILL_AMBER


def _append_executive_summary_kpis(doc, facts: dict, flags: dict) -> None:
    """Appends the four material-findings cards a reviewer needs inside 60
    seconds -- Completion Slippage, Units Sold, Litigation Load, Land Title
    -- extracted from facts this Charter already established (nothing new
    computed here), each shaded by real severity. Deliberately distinct
    from the Developer Score/Data Authenticity/Flags/Project Status
    scorecard, which lives in Overview & Flags -- this card row is about
    what makes THIS deal specifically notable, not the generic scorecard."""
    total_units, sold_units = _parse_total_units_and_sold(facts)
    complaint_count, appeal_count = _parse_complaint_appeal_counts(facts)
    completion_text, was_extended = _parse_completion_slippage(facts)
    land_title_text, land_title_fill = _land_title_summary(facts)

    if total_units and sold_units:
        sold_pct = round(100 * sold_units / total_units, 1)
        units_text = f"{sold_units:,} / {total_units:,} ({sold_pct}%)"
        units_fill = _FILL_RED if (was_extended and sold_pct >= _NEAR_SELLOUT_PCT) else _FILL_NEUTRAL
    else:
        units_text, units_fill = "Not confirmed", _FILL_NEUTRAL

    if complaint_count is not None and appeal_count is not None:
        litigation_text = f"{complaint_count} complaints / {appeal_count} appeals"
        # Colored off this card's own numbers against their own thresholds
        # -- not off flags["imminent"] in general, which can be entirely
        # unrelated (e.g. an FSI/BUA gap) and would otherwise paint a
        # genuinely clean 0/0 record red.
        if complaint_count > _FLAG_THRESHOLDS["complaint_imminent"] or appeal_count > _FLAG_THRESHOLDS["appeal_imminent"]:
            litigation_fill = _FILL_RED
        elif complaint_count > _FLAG_THRESHOLDS["complaint_monitor"] or appeal_count > _FLAG_THRESHOLDS["appeal_monitor"]:
            litigation_fill = _FILL_AMBER
        else:
            litigation_fill = _FILL_GREEN if complaint_count == 0 and appeal_count == 0 else _FILL_NEUTRAL
    else:
        litigation_text, litigation_fill = "Not confirmed", _FILL_NEUTRAL

    cards = (
        ("Completion Slippage", completion_text, _FILL_RED if was_extended else _FILL_NEUTRAL),
        ("Units Sold", units_text, units_fill),
        ("Litigation Load", litigation_text, litigation_fill),
        ("Land Title", land_title_text, land_title_fill),
    )

    kpi_table = doc.add_table(rows=2, cols=len(cards))
    _set_table_borders(kpi_table)
    header_cells = kpi_table.rows[0].cells
    value_cells = kpi_table.rows[1].cells
    for i, (label, value, fill) in enumerate(cards):
        header_cells[i].text = label
        value_cells[i].text = value
        _shade_cell(header_cells[i], fill)
        for para in header_cells[i].paragraphs:
            for run in para.runs:
                run.bold = True
        for para in value_cells[i].paragraphs:
            for run in para.runs:
                run.bold = True

    doc.add_paragraph()
    doc.add_paragraph(
        "The cards above are the material findings from this Charter's own research -- see Overview & "
        "Flags below for the full flag detail behind each one."
    )


def _append_overview_section(doc, facts: dict, flags: dict) -> None:
    """The first substantive thing a reader sees after the title block: a
    deal snapshot table, the headline KPI scorecard (Developer Score, Data
    Authenticity, flag counts, project status -- each shaded red/amber/
    green by its own grade or band), and the full Imminent/Structural/
    Monitor flag lists from _classify_flags -- each item shown with both
    its plain-English text and the exact facts.json field it came from,
    never just a bare label, and colored by severity (red/amber/plain) so
    risk is visible at a glance. Nothing here is a new data point; it's a
    new, urgency-ordered arrangement of facts this Charter already
    established elsewhere."""
    heading_style = doc.paragraphs[4].style  # a known Heading 1 in the template -- removed from the doc later, but only after every _append_* call finishes

    heading_para = doc.add_paragraph("Overview & Flags")
    heading_para.style = heading_style

    core = facts.get("rera_core_fields", {}) or {}
    ci = facts.get("corporate_identity", {}) or {}
    promoter_name = (ci.get("promoter_name") or {}).get("value", "")

    def _header_row(cells):
        for cell in cells:
            _shade_cell(cell, "D9E2F3")
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True

    doc.add_paragraph("Deal snapshot")
    snapshot_table = doc.add_table(rows=1, cols=2)
    _set_table_borders(snapshot_table)
    header_cells = snapshot_table.rows[0].cells
    header_cells[0].text = "Field"
    header_cells[1].text = "Value"
    _header_row(header_cells)
    for label, key in (
        ("Project", "project_name"),
        ("Promoter", None),
        ("MahaRERA Registration", "registration_number"),
        ("Project Status", "project_status"),
        ("Proposed Completion Date", "proposed_completion_date"),
        ("Total Units", "total_building_units"),
        ("Litigations on Record", "litigations_per_record"),
    ):
        value = promoter_name if key is None else core.get(key, "")
        row = snapshot_table.add_row()
        row.cells[0].text = label
        cell = row.cells[1]
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(value or ""))
        if label == "Proposed Completion Date" and _parse_completion_slippage(facts)[1]:
            # Only colored when this project's own text documents a real
            # slippage (original date vs. the extended one(s)) -- an
            # un-extended date is a plain fact, not a discrepancy, and
            # must not be colored red just because this field sometimes is.
            _color_run(run, _TEXT_RED)

    doc.add_paragraph()
    developer_score = facts.get("developer_score", {}) or {}
    doc_confidence = facts.get("documentation_confidence_score", {}) or {}
    imminent_count = len(flags.get("imminent", []))
    grade = developer_score.get("grade")
    band = doc_confidence.get("band")

    kpi_table = doc.add_table(rows=2, cols=4)
    _set_table_borders(kpi_table)
    header_cells = kpi_table.rows[0].cells
    for i, label in enumerate(("Developer Score", "Data Authenticity", "Flags (Imminent / Structural / Monitor)", "Project Status")):
        header_cells[i].text = label
        for para in header_cells[i].paragraphs:
            for run in para.runs:
                run.bold = True
    value_cells = kpi_table.rows[1].cells
    value_cells[0].text = f"{grade or 'N/A'} ({developer_score.get('composite', 'N/A')}/100)"
    value_cells[1].text = f"{band or 'N/A'} ({doc_confidence.get('overall', 'N/A')}/100)"
    value_cells[2].text = f"{imminent_count} / {len(flags.get('structural', []))} / {len(flags.get('monitor', []))}"
    value_cells[3].text = str(core.get("project_status", ""))
    for cell, fill in (
        (header_cells[0], _grade_fill(grade)),
        (header_cells[1], _band_fill(band)),
        (header_cells[2], _FILL_RED if imminent_count else _FILL_GREEN),
        (header_cells[3], _FILL_NEUTRAL),
    ):
        _shade_cell(cell, fill)
    for cell in value_cells:
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True

    doc.add_paragraph()
    doc.add_paragraph(
        "Every figure above is drawn directly from the underlying facts already researched for this "
        "project -- see the flag lists below for detail and the Diligence Appendix for the per-pillar/"
        "per-check breakdown behind the Developer Score and Data Authenticity figures."
    )

    def _append_flag_list(title: str, items: list, text_color: str | None) -> None:
        doc.add_paragraph()
        list_heading = doc.add_paragraph(f"{title} ({len(items)})")
        for run in list_heading.runs:
            run.bold = True
            if text_color:
                _color_run(run, text_color)
        if not items:
            doc.add_paragraph("None identified this pass.")
            return
        for item in items:
            line = doc.add_paragraph()
            run = line.add_run(f"• {item['text']} (see {item['field']})")
            if text_color:
                _color_run(run, text_color)

    _append_flag_list("Imminent Red Flags -- act on these before proceeding", flags.get("imminent", []), _TEXT_RED)
    _append_flag_list("Structural Flags -- standing characteristics, raise directly with the developer", flags.get("structural", []), _TEXT_AMBER)
    _append_flag_list("Monitor Flags -- re-check on a future pass", flags.get("monitor", []), None)


def _append_developer_score_section(doc, facts: dict) -> None:
    """Renders facts["developer_score"] (see _compute_developer_score) as a
    per-criterion table against the 7-criteria AAA-D industry rubric --
    silently does nothing if it was never computed (defensive only; the
    normal pipeline always sets it via _append_overview_section before
    this is called)."""
    developer_score = facts.get("developer_score")
    if not developer_score:
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Developer Score (Code-Computed)")
    heading_para.style = heading_style
    doc.add_paragraph(
        f"Composite: {developer_score['composite']}/100 -- Grade {developer_score['grade']}. Scored "
        "against a fixed 7-criteria industry rubric (track record years, team strength, past area "
        "developed, area developed within 5km, financial strength/debt structure, past default count, "
        "entity/organization type), each independently banded AAA/AA/A/B/C/D, then combined by equal "
        "weight across whatever criteria had underlying data this pass -- a criterion with none is "
        "explicitly skipped below, never faked as neutral or zero. An imminent-tier flag (see Overview "
        "& Flags) caps this grade at A regardless of the composite, unless the composite alone already "
        "bands lower than that."
    )

    table = doc.add_table(rows=1, cols=5)
    _set_table_borders(table)
    header_cells = table.rows[0].cells
    for i, label in enumerate(("Criterion", "Tier", "Score", "Weight", "Note / Reason")):
        header_cells[i].text = label
        _shade_cell(header_cells[i], "D9E2F3")
        for para in header_cells[i].paragraphs:
            for run in para.runs:
                run.bold = True
    for name, criterion in developer_score.get("criteria", {}).items():
        row = table.add_row()
        row.cells[0].text = name.replace("_", " ").title()
        if criterion.get("score") is None:
            row.cells[1].text = "N/A"
            row.cells[2].text = "N/A"
            row.cells[3].text = "N/A"
            row.cells[4].text = criterion.get("reason", "")
        else:
            row.cells[1].text = criterion["tier"]
            row.cells[2].text = str(criterion["score"])
            row.cells[3].text = f"{criterion['weight']}%"
            row.cells[4].text = criterion.get("note", "")


def _append_counterparty_summary(doc, facts: dict) -> None:
    """A few short, one-line-each summaries of the credit rating,
    insolvency, company-registration, group-affiliation, and Developer
    Score checks -- the key figure and nothing more, each pointing to the
    Diligence Appendix for the full table. Lets Counterparty answer "how
    strong is this promoter" at a glance without repeating the full detail
    twice. Silently omits any check that never ran, same as every other
    _append_*_section here; adds no heading of its own if nothing ran at
    all (so an empty Counterparty subsection never appears)."""
    heading_style = doc.paragraphs[9].style  # "Land Identification" -- a known Heading 2
    heading_added = False

    def _ensure_heading():
        nonlocal heading_added
        if not heading_added:
            heading_para = doc.add_paragraph("Credit Rating, Insolvency & Group Affiliation (Summary)")
            heading_para.style = heading_style
            heading_added = True

    credit_check = facts.get("credit_rating_check")
    if credit_check:
        _ensure_heading()
        promoter_result = credit_check.get("promoter") or {}
        if promoter_result.get("found"):
            agencies = ", ".join(r["agency"] for r in promoter_result.get("ratings", []))
            doc.add_paragraph(f"Credit rating: found ({agencies or 'agency unspecified'}) -- see Diligence Appendix for the full instrument/rating detail.")
        else:
            doc.add_paragraph(f"Credit rating: {promoter_result.get('note', 'not found')} (full detail in Diligence Appendix).")

    ibbi_check = facts.get("ibbi_insolvency_check")
    if ibbi_check and ibbi_check.get("found_process") is not None:
        _ensure_heading()
        if ibbi_check["found_process"] is False:
            doc.add_paragraph(f"IBBI insolvency status: clean -- \"{ibbi_check.get('status_text', '')}\" (full detail in Diligence Appendix).")
        else:
            doc.add_paragraph("IBBI insolvency status: a record was found against this CIN -- see Diligence Appendix for the raw detail (not auto-classified).")

    profile_check = facts.get("company_profile_check")
    if profile_check and profile_check.get("found"):
        _ensure_heading()
        doc.add_paragraph(
            f"Company registration: confirmed via ZaubaCorp -- {profile_check.get('status', 'status unknown')}, "
            f"incorporated {profile_check.get('incorporation_date', 'unknown')} (directors and full detail in Diligence Appendix)."
        )

    group_check = facts.get("group_companies_check")
    if group_check and group_check.get("found") and group_check.get("companies"):
        _ensure_heading()
        doc.add_paragraph(
            f"Group affiliation: {len(group_check['companies'])} linked entit(y/ies) via shared directors or "
            "registered office (full crosswalk in Diligence Appendix)."
        )

    developer_score = facts.get("developer_score")
    if developer_score:
        _ensure_heading()
        doc.add_paragraph(
            f"Developer Score: {developer_score['composite']}/100 -- Grade {developer_score['grade']} "
            "(per-pillar breakdown in Diligence Appendix)."
        )


def _shade_cell(cell, hex_color: str) -> None:
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def _set_table_borders(table) -> None:
    """Applies grid borders directly via XML rather than a named table
    style -- this template (built with docx-js, not Word) has no "Table
    Grid" style defined, confirmed live (table.style = "Table Grid" raises
    KeyError even though the existing template tables render with visible
    grid lines -- they use explicit borders, not a named style)."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:color"), "808080")
        borders.append(el)
    tbl_pr.append(borders)


def _append_complaint_outcomes_section(doc, facts: dict) -> None:
    """Appends a page summarizing real, per-complaint outcomes extracted
    from downloaded order PDFs (see api_client.download_complaint_orders +
    summarize_complaint_outcomes) -- code-computed, not model-authored, same
    reasoning as the Document Library table. Silently does nothing if
    complaint_outcomes_summary was never set (e.g. no auth token was
    available to download the orders this pass)."""
    summary = facts.get("complaint_outcomes_summary")
    if not summary or not summary.get("per_complaint"):
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Complaint Order Outcomes (Code-Extracted)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Each row below reflects the actual order PDF already on file for that complaint (downloaded "
        "via the same document-retrieval mechanism used for project documents), classified by a small, "
        "named set of outcome keywords -- not a self-reported summary. An outcome of \"not determinable\" "
        "means the extracted text didn't match any of those keywords; it is not a claim that the "
        "complaint was resolved favourably or unfavourably, only that the automated classification "
        "could not tell from the text available."
    )

    table = doc.add_table(rows=1, cols=2)
    _set_table_borders(table)
    header_cells = table.rows[0].cells
    header_cells[0].text = "Outcome"
    header_cells[1].text = "Count"
    for cell in header_cells:
        _shade_cell(cell, "D9E2F3")
        for p in cell.paragraphs:
            for run in p.runs:
                run.bold = True
    for outcome, count in sorted(summary["outcome_counts"].items(), key=lambda kv: -kv[1]):
        row = table.add_row()
        row.cells[0].text = outcome.replace("_", " ")
        row.cells[1].text = str(count)

    doc.add_paragraph()
    doc.add_paragraph(
        f"{len(summary['per_complaint'])} complaint order(s) were available and read this pass. "
        f"Per-complaint detail (registration number, outcome, source order filename) is retained in "
        f"this Charter's .facts.json rather than repeated in full here."
    )


def _add_rating_comparison_table(doc, rating_result: dict) -> None:
    """Renders every agency's rating(s) for one entity into a SINGLE
    Agency | Instrument | Rating table -- deliberately one table, not one
    per agency, so that if two agencies rate the same entity differently
    (or agree), a reader sees both rows side by side instead of having to
    flip between separate mini-sections to compare them."""
    ratings = rating_result.get("ratings", [])
    for r in ratings:
        doc.add_paragraph(f"Match found ({r['agency']}): {r['company_name']} ({_clean_source_label(r['url']) or r['url']})")

    table = doc.add_table(rows=1, cols=3)
    _set_table_borders(table)
    header_cells = table.rows[0].cells
    header_cells[0].text = "Agency"
    header_cells[1].text = "Instrument"
    header_cells[2].text = "Rating"
    for cell in header_cells:
        _shade_cell(cell, "D9E2F3")
        for p in cell.paragraphs:
            for run in p.runs:
                run.bold = True
    for r in ratings:
        for item in r.get("instruments", []):
            row = table.add_row()
            row.cells[0].text = r["agency"]
            row.cells[1].text = item["instrument"]
            row.cells[2].text = item["rating"]

    not_found = rating_result.get("not_found_agencies", [])
    if not_found:
        doc.add_paragraph(f"No public rating found from: {', '.join(not_found)}.")


def _append_credit_rating_section(doc, facts: dict) -> None:
    """Appends a section reporting the code-computed credit-rating check
    on the promoter's exact legal name across every agency checked (see
    lookup_credit_rating) -- if more than one agency rates the same
    entity, all of their ratings are shown together for direct comparison,
    rather than just the first match found. Silently does nothing if
    credit_rating_check was never set (e.g. no promoter name was available
    to check against)."""
    check = facts.get("credit_rating_check")
    if not check:
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Credit Rating Check (Code-Computed)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Checked directly against every rating agency's public database (currently ICRA and Infomerics) "
        "for an exact match on the promoter's own legal name -- not a fuzzy or \"probably the same "
        "company\" guess, since attributing a rating to the wrong legal entity would itself be a serious "
        "error. Every agency is checked regardless of whether an earlier one already found something, so "
        "that if two agencies rate the same entity, both ratings are shown here for comparison rather "
        "than silently reporting only one. A promoter having no public rating anywhere is the ordinary "
        "case, not a red flag: these agencies only rate developers that sought a public rating (typically "
        "larger, listed, or NCD-issuing entities)."
    )

    promoter_result = check.get("promoter", {})
    if promoter_result.get("found"):
        _add_rating_comparison_table(doc, promoter_result)
    else:
        doc.add_paragraph(promoter_result.get("note", "No result recorded."))

    parent_result = check.get("parent_group")
    if parent_result:
        doc.add_paragraph()
        doc.add_paragraph(
            "The following is a SEPARATE check on a distinct parent/group entity -- it describes that "
            "entity's own credit standing, not the specific promoter/SPV named above; the two must not "
            "be conflated."
        )
        if parent_result.get("found"):
            _add_rating_comparison_table(doc, parent_result)
        else:
            doc.add_paragraph(parent_result.get("note", "No result recorded."))


def _append_ibbi_check_section(doc, facts: dict) -> None:
    """Appends a section reporting the code-computed IBBI insolvency check
    on the promoter's CIN (see lookup_ibbi_insolvency_status). Silently
    does nothing if ibbi_insolvency_check was never set (e.g. no CIN was
    extractable this pass)."""
    check = facts.get("ibbi_insolvency_check")
    if not check or check.get("found_process") is None:
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Insolvency Check -- IBBI Corporate Debtor Master Data (Code-Computed)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Checked directly against the Insolvency and Bankruptcy Board of India's public Corporate "
        "Debtor Master Data, by the promoter's own CIN -- an exact-identifier lookup, not a name-based "
        "guess."
    )
    if check["found_process"] is False:
        ibbi_citation = _clean_source_label(check.get("url", "")) or check.get("url", "")
        doc.add_paragraph(
            f"Result: \"{check['status_text']}\" -- no insolvency process is recorded against this CIN "
            f"in IBBI's public database. ({ibbi_citation})"
        )
    else:
        doc.add_paragraph(
            "Result: this CIN returned something other than the standard \"no process\" result. The "
            "raw extracted page content is reproduced below verbatim for a human to read directly -- "
            "this checker was not validated against a real active/past insolvency case, so it does not "
            "attempt to summarize or classify this content itself:"
        )
        doc.add_paragraph(check.get("status_text", ""))
        doc.add_paragraph(f"Source: {_clean_source_label(check.get('url', '')) or check.get('url', '')}")


def _append_company_profile_section(doc, facts: dict) -> None:
    """Appends a section reporting the code-computed company registration
    profile from ZaubaCorp (see lookup_company_by_cin). Silently does
    nothing if company_profile_check was never set or found no record."""
    check = facts.get("company_profile_check")
    if not check or not check.get("found"):
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Company Registration Profile (ZaubaCorp, Code-Computed)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Pulled directly from ZaubaCorp's public company record by CIN -- an exact-identifier lookup, "
        "not a name-based guess."
    )
    profile_citation = _clean_source_label(check.get("url", ""))
    doc.add_paragraph(
        f"{check['name']} ({check['cin']}) -- Status: {check.get('status', 'unknown')}; "
        f"Class: {check.get('class_of_company', 'unknown')}; "
        f"Category: {check.get('company_category', 'unknown')}; ROC: {check.get('roc', 'unknown')}"
        + (f" ({profile_citation})" if profile_citation else "")
    )
    doc.add_paragraph(f"Incorporated: {check.get('incorporation_date', 'unknown')}")
    doc.add_paragraph(f"Registered address: {check.get('registered_address', 'unknown')}")
    doc.add_paragraph(
        f"Authorised capital: {check.get('authorised_capital') or 'not publicly available'}; "
        f"Paid-up capital: {check.get('paid_up_capital') or 'not publicly available'}"
    )
    doc.add_paragraph(check.get("shareholding_note", ""))

    def _add_director_table(title: str, directors: list):
        if not directors:
            return
        doc.add_paragraph()
        doc.add_paragraph(title)
        cols = list(directors[0].keys())
        table = doc.add_table(rows=1, cols=len(cols))
        _set_table_borders(table)
        header_cells = table.rows[0].cells
        for i, col in enumerate(cols):
            header_cells[i].text = col
            _shade_cell(header_cells[i], "D9E2F3")
            for p in header_cells[i].paragraphs:
                for run in p.runs:
                    run.bold = True
        for director in directors:
            row = table.add_row()
            for i, col in enumerate(cols):
                row.cells[i].text = director.get(col, "")

    _add_director_table("Current Directors & Key Managerial Personnel", check.get("current_directors", []))
    _add_director_table("Past Directors & Key Managerial Personnel", check.get("past_directors", []))
    doc.add_paragraph()
    doc.add_paragraph(f"Source: {check.get('url', '')}")


def _build_director_company_links(facts: dict) -> list:
    """Cross-references company_profile_check's current + past directors
    against group_companies_check's per-company "shared director: NAME
    (designation)" basis strings, returning one row per named director --
    {name, role, tenure, link_count}, sorted by link_count descending.
    Built entirely from data this Charter already fetched; no new lookup.
    A director who shows 0 links here genuinely has none among the
    companies found, not a parsing gap."""
    from collections import Counter

    cp = facts.get("company_profile_check") or {}
    gc = facts.get("group_companies_check") or {}

    link_counts = Counter()
    for company in gc.get("companies") or []:
        for basis in company.get("basis", []):
            m = re.match(r"shared director:\s*(.+?)\s*\(", basis)
            if m:
                link_counts[" ".join(m.group(1).split())] += 1

    past_sorted = sorted(cp.get("past_directors") or [], key=lambda d: d.get("Cessation") or "", reverse=True)
    rows, seen = [], set()
    for status, directors in (("Current", cp.get("current_directors") or []), ("Past", past_sorted)):
        for d in directors:
            name = " ".join((d.get("Director Name") or "").split())
            if not name or name in seen:
                continue
            seen.add(name)
            appointment, cessation = d.get("Appointment Date"), d.get("Cessation")
            if status == "Current":
                tenure = f"since {appointment}" if appointment and appointment != "-" else "tenure not confirmed"
            else:
                tenure = f"until {cessation}" if cessation and cessation != "-" else "cessation date not confirmed"
            rows.append({
                "name": name, "role": f"{status} {d.get('Designation', '')}".strip(),
                "tenure": tenure, "link_count": link_counts.get(name, 0),
            })
    rows.sort(key=lambda r: -r["link_count"])
    return rows


def _render_director_hub_diagram(promoter_name: str, rows: list):
    """Renders a small hub-and-spoke diagram (promoter at the center, each
    director with at least one group-company link as a spoke sized by that
    count) to an in-memory PNG buffer -- returns None if no director has
    any links (nothing meaningful to draw). The 299 linked companies
    themselves are deliberately NOT drawn as individual nodes, only counts
    per director -- a graph with that many nodes would be unreadable, and
    the point of this diagram is what a reviewer can read in 60 seconds."""
    linked = [r for r in rows if r["link_count"] > 0]
    if not linked:
        return None

    import io
    import math
    import textwrap

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 5.6))
    ax.axis("off")
    ax.set_xlim(-1.55, 1.55)
    ax.set_ylim(-1.4, 1.4)

    max_count = max(r["link_count"] for r in linked)
    n = len(linked)
    # Offset the first spoke away from due-east/due-west so no line runs
    # straight through the horizontal midline the center label sits on.
    angle_offset = math.pi / n
    for i, r in enumerate(linked):
        angle = angle_offset + 2 * math.pi * i / n
        x, y = math.cos(angle), math.sin(angle)
        weight = r["link_count"] / max_count
        ax.plot([0, x], [0, y], color="#BF8F00", linewidth=1 + 3 * weight, zorder=1)
        ax.scatter([x], [y], s=400 + 2200 * weight, color="#FFE699", edgecolor="#BF8F00", zorder=2)
        ax.text(x * 1.22, y * 1.22, f"{r['name']}\n({r['link_count']} linked)", ha="center", va="center", fontsize=7.5, zorder=4)

    # Drawn after the spokes (not before) so the center disc fully covers
    # every line's inner end rather than the lines drawing over its edge.
    ax.scatter([0], [0], s=4200, color="#2E74B5", zorder=3)
    ax.text(0, 0, textwrap.fill(promoter_name, width=14), ha="center", va="center", color="white", fontsize=7.5, fontweight="bold", zorder=4)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _append_group_companies_section(doc, facts: dict) -> None:
    """Appends a section listing companies linked to the promoter via
    shared directors or a shared registered office (see
    find_group_companies_by_cin), led by a director relationship map (a
    compact table plus a hub-and-spoke diagram) so a reviewer can read how
    concentrated the group's leadership is without scanning all 299 rows.
    Silently does nothing if group_companies_check was never set or found
    nothing."""
    check = facts.get("group_companies_check")
    if not check or not check.get("found") or not check.get("companies"):
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Group / Affiliated Companies (Code-Computed Director & Address Crosswalk)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Every entity below shares at least one concrete, named link with the promoter above -- a "
        "specific director in common, a shared registered office, or a filed subsidiary/associate/JV "
        "relationship -- rather than being inferred from a name or industry match. The strength of each "
        "link should be judged from the basis given, not assumed uniform across the list."
    )

    director_rows = _build_director_company_links(facts)
    if any(r["link_count"] for r in director_rows):
        sub_heading = doc.add_paragraph("Director Relationship Map")
        for run in sub_heading.runs:
            run.bold = True
        doc.add_paragraph(
            "Each of this promoter's directors, current or past, cross-referenced against how many of "
            "the group companies below name them as a shared director -- collapses the 299-entity list "
            "to the thing that actually matters here: how concentrated the group's leadership is around "
            "a small number of individuals, not a name-by-name read of every affiliated entity."
        )
        dir_table = doc.add_table(rows=1, cols=3)
        _set_table_borders(dir_table)
        header_cells = dir_table.rows[0].cells
        for i, label in enumerate(("Director", "Role at This Promoter", "Group Companies Linked (of 299)")):
            header_cells[i].text = label
            _shade_cell(header_cells[i], "D9E2F3")
            for p in header_cells[i].paragraphs:
                for run in p.runs:
                    run.bold = True
        for r in director_rows:
            row = dir_table.add_row()
            row.cells[0].text = r["name"]
            row.cells[1].text = f"{r['role']} ({r['tenure']})"
            row.cells[2].text = str(r["link_count"])

        promoter_name = ((facts.get("corporate_identity") or {}).get("promoter_name") or {}).get("value") or "This promoter"
        diagram_buf = _render_director_hub_diagram(promoter_name, director_rows)
        if diagram_buf is not None:
            from docx.shared import Inches

            doc.add_paragraph()
            doc.add_picture(diagram_buf, width=Inches(6))

        doc.add_paragraph()

    table = doc.add_table(rows=1, cols=3)
    _set_table_borders(table)
    header_cells = table.rows[0].cells
    header_cells[0].text = "Company Name"
    header_cells[1].text = "CIN / Identifier"
    header_cells[2].text = "Basis for Link"
    for cell in header_cells:
        _shade_cell(cell, "D9E2F3")
        for p in cell.paragraphs:
            for run in p.runs:
                run.bold = True
    for company in sorted(check["companies"], key=lambda c: -len(c.get("basis", []))):
        row = table.add_row()
        row.cells[0].text = company.get("name", "")
        row.cells[1].text = company.get("cin", "")
        row.cells[2].text = "; ".join(company.get("basis", []))

    doc.add_paragraph()
    doc.add_paragraph(f"Source: {check.get('url', '')}")


def _append_cts_land_record_section(doc, facts: dict) -> None:
    """Appends a section reporting the CTS -> Property Card lookup (see
    run_cts_land_lookup / mahabhumi.fetch_property_card). Silently does
    nothing if cts_land_record_check was never set (the ordinary case --
    it only runs when a human has supplied cts_lookup_input.json) or found
    no result."""
    check = facts.get("cts_land_record_check")
    if not check or not check.get("found"):
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Land Record Check -- Maha Bhulekh Property Card (Code-Assisted, Human-Verified)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Fetched directly from bhulekh.mahabhumi.gov.in, Maharashtra's official land-records portal, by "
        "exact CTS number -- every field/office/village selection leading up to this was an exact match, "
        "never a guess, and a human read and solved the site's own CAPTCHA to reveal this record."
    )

    fields = check.get("fields") or {}
    if fields:
        table = doc.add_table(rows=1, cols=2)
        _set_table_borders(table)
        header_cells = table.rows[0].cells
        header_cells[0].text = "Field"
        header_cells[1].text = "Value"
        for cell in header_cells:
            _shade_cell(cell, "D9E2F3")
            for p in cell.paragraphs:
                for run in p.runs:
                    run.bold = True
        for k, v in fields.items():
            row = table.add_row()
            row.cells[0].text = k
            row.cells[1].text = v
    else:
        # This lookup's result-page structure was never independently
        # confirmed live (see mahabhumi.py's module note) -- when no
        # structured fields could be pulled out, the full raw page text is
        # shown instead of silently dropping the result.
        doc.add_paragraph(
            "This lookup's result page could not be parsed into structured fields this pass -- the raw "
            "page text is reproduced below for a human to read directly:"
        )
        doc.add_paragraph((check.get("raw_text") or "")[:4000])

    doc.add_paragraph()
    doc.add_paragraph(f"Source: {check.get('url', '')}")


def _append_appeal_judgments_section(doc, facts: dict) -> None:
    """Appends a section listing any appeal-level judgments matched against
    this project's own appeals.json (see search_maharera_judgments +
    cross_reference_appeals). Silently does nothing if the field was never
    set or nothing matched -- the common case, since most appeals are still
    pending and have no published judgment yet."""
    matched = facts.get("appeal_judgments_found")
    if not matched:
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Appeal-Level Judgments Found (Code-Matched)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Each row below is a real judgment from MahaRERA's public Orders/Judgments search, matched to "
        "one of this project's own appeal records by an exact ID (the search result's Complainant No. "
        "against this project's own complaintRegistrationNo) -- not a name-based guess. The underlying "
        "judgment PDF has been downloaded and saved alongside this project's other documents."
    )

    table = doc.add_table(rows=1, cols=4)
    _set_table_borders(table)
    header_cells = table.rows[0].cells
    for i, text in enumerate(("Complainant No.", "Respondent", "Uploaded", "Judgment PDF")):
        header_cells[i].text = text
        _shade_cell(header_cells[i], "D9E2F3")
        for p in header_cells[i].paragraphs:
            for run in p.runs:
                run.bold = True
    for item in matched:
        row = table.add_row()
        row.cells[0].text = item.get("complainant_no") or ""
        row.cells[1].text = item.get("respondent_name") or ""
        row.cells[2].text = item.get("uploaded_date") or ""
        row.cells[3].text = item.get("saved_filename") or "(save failed -- see facts.json)"


def _append_review_authenticity_section(doc, facts: dict) -> None:
    """Appends a section reporting the code-computed review-authenticity
    heuristic triage (see run_review_authenticity_triage). Silently does
    nothing if review_authenticity_triage was never set -- the ordinary
    case, since this pipeline has no automated review-fetching mechanism;
    it only runs when a caller supplies pre-collected reviews (see
    run_company_charter's `reviews` param and --reviews-file)."""
    triage = facts.get("review_authenticity_triage")
    if not triage:
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Review Authenticity Triage (Code-Computed Heuristics)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Five heuristic checks over reviews supplied for this run -- a red-flag surfacing aid for a "
        "human to look at, NOT a fabrication verdict. True fabrication-probability scoring would need "
        "platform-internal data (poster IP/device clustering, account history) this project has no "
        "access to. There is no automated review-fetching mechanism in this pipeline; the reviews "
        "analyzed here were supplied directly to this run."
    )
    doc.add_paragraph(f"Total reviews analyzed: {triage.get('total_reviews_analyzed', 0)}")

    polarization = triage.get("rating_polarization", {})
    if polarization.get("total"):
        doc.add_paragraph(
            f"Rating polarization: {polarization['pct_extreme']}% of ratings sit at the extremes "
            f"(1 or 5 stars) rather than the middle -- a heavily bimodal split is a directional red "
            f"flag (fabricated reviews skew to the extremes), not proof on its own."
        )

    bursts = triage.get("burst_clusters", [])
    if bursts:
        doc.add_paragraph(f"Review bursts detected ({len(bursts)}): {len(bursts)} cluster(s) of 5+ reviews posted within a 7-day window:")
        for b in bursts:
            doc.add_paragraph(f"  - {b['count']} reviews between {b['start_date']} and {b['end_date']}")

    dupes = triage.get("near_duplicate_pairs", [])
    if dupes:
        doc.add_paragraph(
            f"Near-duplicate review text: {len(dupes)} pair(s) of reviews with near-identical "
            f"(templated/copy-pasted) language -- see facts.json for the full text of each pair."
        )

    one_hit = triage.get("one_hit_wonder_reviewers", [])
    if one_hit:
        doc.add_paragraph(
            f"One-hit-wonder reviewers: {len(one_hit)} reviewer(s) whose only review anywhere is this "
            f"one -- a documented pattern for accounts that exist solely to post a single review."
        )

    claims = triage.get("claim_cross_reference", [])
    if claims:
        doc.add_paragraph(
            "Possession-delay claims cross-referenced against this project's own confirmed "
            "proposed_completion_date:"
        )
        table = doc.add_table(rows=1, cols=3)
        _set_table_borders(table)
        header_cells = table.rows[0].cells
        for i, text in enumerate(("Claimed year", "Project record year", "Verdict")):
            header_cells[i].text = text
            _shade_cell(header_cells[i], "D9E2F3")
            for p in header_cells[i].paragraphs:
                for run in p.runs:
                    run.bold = True
        for item in claims:
            row = table.add_row()
            row.cells[0].text = str(item.get("claimed_year", ""))
            row.cells[1].text = str(item.get("project_year", "")) if item.get("project_year") is not None else "n/a"
            row.cells[2].text = item.get("verdict", "")


def _append_authenticity_page(doc, facts: dict) -> None:
    """Appends a new, code-computed section (not model-authored) summarizing
    what tier each cited source falls into and how many claims remain
    explicit gaps -- lets a reader judge this report's reliability from the
    same underlying data already visible in the Sources/Gaps sections,
    rather than trusting a self-assessment."""
    summary = _compute_authenticity_summary(facts)
    confidence = _compute_documentation_confidence_score(facts, summary)
    # Stored back onto facts (not just rendered here) so callers building a
    # separate output from the same facts dict -- e.g. report.py's PDF --
    # can show the same score without recomputing it.
    facts["documentation_confidence_score"] = confidence

    # This template's heading styles were created via docx-js, not Word --
    # python-docx's add_heading() looks up a style named "Heading 1" and
    # doesn't find a matching one (confirmed live: raises KeyError even
    # though paragraph.style.name reports "Heading 1" when reading an
    # existing heading). Reusing the actual style OBJECT already applied to
    # an existing Heading-1 paragraph in this same document sidesteps the
    # name-lookup mismatch entirely.
    heading_style = doc.paragraphs[4].style  # a known Heading 1 in the template -- removed from the doc later, but only after every _append_* call finishes

    doc.add_page_break()
    heading_para = doc.add_paragraph("Documentation Authenticity & Confidence Summary")
    heading_para.style = heading_style
    doc.add_paragraph(
        "This page is generated directly from the same sources and gaps already listed earlier in "
        "this document -- it is a count, not a self-assessment. A report author claiming its own "
        "work is \"reliable\" would just be another unverified claim; this page instead classifies "
        "every cited source by tier so a reader can judge confidence from the same underlying data."
    )

    from docx.shared import Pt

    score_para = doc.add_paragraph()
    score_run = score_para.add_run(f"Documentation Confidence Score: {confidence['overall']}/100 -- {confidence['band']}")
    score_run.bold = True
    score_run.font.size = Pt(14)
    doc.add_paragraph(
        "This score rates how well-sourced and verified THIS DOCUMENT's own claims are -- source "
        "quality, completeness, cross-corroboration, recency, and re-check success -- it is NOT a "
        "rating of the underlying project's quality, safety, or investment merit. A project with a "
        "genuinely poor track record whose problems are thoroughly documented can score HIGH here; a "
        "genuinely sound project with little public paper trail to verify against can score LOW. It "
        "is a weighted average of six criteria computed below from this document's own sources and "
        "gaps -- informed by the structure of CRISIL's real-estate methodology (a small number of "
        "named factors rather than one flat checklist) but NOT a replica of any CRISIL formula: CRISIL "
        "does not publish a numeric weighting scheme for any of its three real-estate products, and "
        "the weights used here are this project's own calibration."
    )

    score_table = doc.add_table(rows=1, cols=4)
    _set_table_borders(score_table)
    score_headers = score_table.rows[0].cells
    for i, text in enumerate(("Criterion", "Score", "Weight", "What it means")):
        score_headers[i].text = text
        _shade_cell(score_headers[i], "D9E2F3")
        for p in score_headers[i].paragraphs:
            for run in p.runs:
                run.bold = True

    _CRITERION_LABELS = {
        "source_tier_quality": "Weighted Source-Tier Quality",
        "primary_tier_density": "Primary-Tier Density",
        "completeness_rate": "Completeness Rate",
        "cross_corroboration": "Cross-Corroboration",
        "recency": "Recency",
        "verification_rate": "Independent Verification Rate",
    }
    for key, label in _CRITERION_LABELS.items():
        row = score_table.add_row()
        if key in confidence["criteria"]:
            c = confidence["criteria"][key]
            row.cells[0].text = label
            row.cells[1].text = f"{round(c['score'])}/100"
            row.cells[2].text = f"{c['weight']}%"
            row.cells[3].text = c["note"]
        else:
            row.cells[0].text = label
            row.cells[1].text = "N/A"
            row.cells[2].text = "excluded"
            row.cells[3].text = "Not applicable this pass -- excluded rather than scored as a failure; remaining weights renormalized to still sum to 100%."

    doc.add_paragraph()

    table = doc.add_table(rows=1, cols=2)
    _set_table_borders(table)
    header_cells = table.rows[0].cells
    header_cells[0].text = "Source Tier"
    header_cells[1].text = "Count"
    for cell in header_cells:
        _shade_cell(cell, "D9E2F3")
        for p in cell.paragraphs:
            for run in p.runs:
                run.bold = True

    tier_order = [t for t, _ in _SOURCE_TIERS] + ["Other / unclassified source"]
    for tier in tier_order:
        count = summary["tier_counts"].get(tier, 0)
        if count == 0:
            continue
        row = table.add_row()
        row.cells[0].text = tier
        row.cells[1].text = str(count)

    total = summary["total_sources"]
    primary = summary["primary_count"]
    gaps = summary["total_gaps"]
    pct_primary = round(100 * primary / total) if total else 0

    doc.add_paragraph()
    doc.add_paragraph(
        f"Of {total} cited source(s) in this report, {primary} ({pct_primary}%) come from a primary "
        f"regulatory record, a document opened directly from it, or a live Google Maps route checked "
        f"in this session -- the highest-confidence tier. The remainder are corporate-registry "
        f"mirrors, real-estate aggregator listings, press, or social-media corroboration, all "
        f"explicitly labelled by tier above rather than presented as equivalent to a primary source. "
        f"Separately, {gaps} item(s) in this report are recorded as explicit, unresolved gaps -- facts "
        f"that were sought but could not be confirmed, listed in full under \"Gaps & Limitations\" "
        f"earlier in this document -- rather than filled in with an estimate."
    )
    doc.add_paragraph(
        "Recommended reading of this summary: treat primary-regulatory-tier and live-Maps findings as "
        "confirmed; treat aggregator/press/social-tier findings as directional and worth an independent "
        "check before any financial or legal decision; treat every listed gap as genuinely open, not as "
        "an implicit \"probably fine.\""
    )


def _diff_mortgage_lender(facts: dict, prior_facts: dict | None) -> str | None:
    """Compares this run's disclosed mortgage_lender against the immediately
    prior Company Charter run for the same project (see
    run_archive.archive_previous_charter). Returns a plain-English note if
    the disclosed lender changed, was newly added, or was no longer
    confirmed -- None if there's no prior run to compare against or nothing
    changed. This is the only historical-diffing this project can do for
    lender identity: MahaRERA's own API exposes no lender/mortgage field at
    all (confirmed by checking every category JSON), so there is nothing to
    diff at the promoter-portfolio level, only same-project run-over-run."""
    if not prior_facts:
        return None
    current = ((facts.get("fsi_metrics", {}) or {}).get("mortgage_lender") or {}).get("value", "").strip()
    prior = ((prior_facts.get("fsi_metrics", {}) or {}).get("mortgage_lender") or {}).get("value", "").strip()
    if current == prior:
        return None
    if current and prior:
        return f"Mortgage lender changed since the prior Charter run: \"{prior}\" -> \"{current}\"."
    if current and not prior:
        return f"Mortgage lender newly disclosed since the prior Charter run: \"{current}\" (not disclosed previously)."
    return (
        f"Mortgage lender \"{prior}\" was disclosed in the prior Charter run but is not "
        "confirmed in this one -- this does not necessarily mean it was paid off, only that "
        "it wasn't re-confirmed from a document opened this pass."
    )


# ---------------------------------------------------------------------------
# Phase 2 checks run concurrently, not sequentially -- confirmed safe: each
# of the 5 checks below hits a different external site (ICRA, Infomerics,
# IBBI, ZaubaCorp x2, MahaRERA Orders) via its own plain requests.get/post
# call, and none of them share module-level session state (the one
# requests.Session() in this file, inside _maharera_orders_search_once, is
# a local variable created fresh per call) or depend on each other's
# results -- so running them in a thread pool is safe and shortens the
# wall-clock cost of this step from the SUM of all 5 calls to roughly the
# SLOWEST single one. Each wrapper below swallows its own exceptions into
# the same honest {"found": False, "note": ...}-shaped result the
# sequential code already produced, so a future's .result() never raises
# and one check's failure still can't affect any other's.
# ---------------------------------------------------------------------------

def _safe_credit_rating(promoter_name: str) -> dict:
    try:
        return lookup_credit_rating(promoter_name)
    except Exception as e:
        return {"found": False, "note": f"Credit rating lookup could not run this pass: {e}"}


def _safe_ibbi_check(identifier: str) -> dict:
    try:
        return lookup_ibbi_insolvency_status(identifier)
    except Exception as e:
        return {"found_process": None, "note": f"IBBI lookup could not run this pass: {e}"}


def _safe_company_profile(identifier: str) -> dict:
    try:
        return lookup_company_by_cin(identifier)
    except Exception as e:
        return {"found": False, "note": f"ZaubaCorp company-profile lookup could not run this pass: {e}"}


def _safe_group_companies(identifier: str) -> dict:
    try:
        return find_group_companies_by_cin(identifier)
    except Exception as e:
        return {"found": False, "note": f"ZaubaCorp group-companies crosswalk could not run this pass: {e}"}


def _safe_judgments_search(project_name: str) -> tuple:
    """Returns (judgments, error_note) instead of raising, matching the
    other _safe_* wrappers -- error_note is None on success."""
    try:
        return search_maharera_judgments(project_name), None
    except Exception as e:
        return [], f"MahaRERA Orders/Judgments search for appeal-level outcomes could not run this pass: {e}"


def run_company_charter(
    reg_no: str,
    category_data: dict,
    documents_manifest: list,
    documents_dir: str,
    research_data: dict | None = None,
    output_dir: str = config.OUTPUT_ROOT,
    complaint_orders_manifest: list | None = None,
    complaint_orders_dir: str | None = None,
    reviews: list | None = None,
    review_source_label: str | None = None,
    promoter_portfolio: dict | None = None,
) -> tuple[str, dict]:
    """Returns (out_path, facts) -- facts is the complete, code-and-model
    -assembled Charter data (same content as the .facts.json written
    alongside the docx), so callers can build a separate output (e.g.
    report.py's PDF) from the same source data without re-reading it from
    disk."""
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(f"Template not found at {TEMPLATE_PATH}")

    extracted_docs, doc_library_status = _select_documents_for_extraction(documents_manifest, documents_dir)

    research_context = ""
    if research_data:
        research_context = (
            "\n\nAlready-confirmed deep-research findings for this project (reuse these, don't "
            "re-research from scratch):\n" + json.dumps({k: research_data.get(k) for k in deep_research.RESEARCH_KEYS})
        )

    user_prompt = (
        f"MahaRERA registration: {reg_no}\n"
        f"RERA project data (JSON): {json.dumps(category_data.get('projects') or {})}\n"
        f"Promoter/partner data (JSON): {json.dumps(category_data.get('partners') or {})}\n"
        f"Extracted document text (label -> text, high-priority documents only):\n"
        f"{json.dumps(extracted_docs)[:_MAX_TOTAL_DOC_CHARS]}\n"
        f"Full document library ({len(doc_library_status)} entries, for reference/completeness):\n"
        f"{json.dumps([d['document_name'] for d in doc_library_status])}"
        f"{research_context}\n\n"
        f"Produce the complete Company Charter facts as the raw JSON object described above."
    )

    facts = _run_charter_pass(user_prompt)
    facts = _verify_material_claims(facts)
    facts = _check_document_grounding(facts, extracted_docs, category_data, documents_manifest)
    facts["document_library"] = doc_library_status  # always the full, code-computed list -- not model-generated
    if promoter_portfolio is not None:
        # Also code-computed, never model-authored -- same reasoning as
        # document_library above: the promoter's cross-project track record
        # (see promoter_portfolio.build_promoter_portfolio) is already a
        # deterministic MahaRERA-only computation by the time it reaches
        # here, so it's attached as-is rather than re-derived or
        # paraphrased by the model.
        facts["promoter_portfolio"] = promoter_portfolio
    if complaint_orders_manifest and complaint_orders_dir:
        # Also code-computed, never model-authored -- the same reasoning as
        # document_library above: a real per-complaint outcome breakdown
        # should never be re-derived or paraphrased by the model when we
        # can just compute it directly from the actual downloaded orders.
        facts["complaint_outcomes_summary"] = summarize_complaint_outcomes(complaint_orders_manifest, complaint_orders_dir)

    promoter_name_for_rating = (facts.get("corporate_identity", {}).get("promoter_name") or {}).get("value", "")

    # Prefer a CIN when both are present in the text (the common case for a
    # company); fall back to an LLPIN for LLP promoters (e.g. Trimity Realty
    # LLP), which have no CIN at all. Confirmed live that every check below
    # accepts either format identically -- IBBI's URL slot and ZaubaCorp's
    # redirect-by-identifier trick both work the same way for a CIN or an
    # LLPIN (see lookup_ibbi_insolvency_status / lookup_company_by_cin's own
    # module notes) -- so one identifier drives all three checks either way.
    identity_text = (facts.get("corporate_identity", {}).get("cin_llpin") or {}).get("value", "")
    corp_identifier = extract_cin(identity_text) or extract_llpin(identity_text)
    identifier_label = "CIN" if extract_cin(identity_text) else "LLPIN"

    project_name_for_judgments = (facts.get("rera_core_fields", {}) or {}).get("project_name", "")

    # All 5 checks below hit different external sites and don't depend on
    # each other -- run them concurrently rather than one after another
    # (see the module note above _safe_credit_rating). Each is gated on the
    # same input-availability condition the sequential version used, so a
    # check is simply never submitted (not run at all, not run-and-discarded)
    # when e.g. no CIN/LLPIN was extractable.
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        if promoter_name_for_rating:
            futures["rating"] = executor.submit(_safe_credit_rating, promoter_name_for_rating)
        if corp_identifier:
            futures["ibbi"] = executor.submit(_safe_ibbi_check, corp_identifier)
            futures["profile"] = executor.submit(_safe_company_profile, corp_identifier)
            futures["group"] = executor.submit(_safe_group_companies, corp_identifier)
        if project_name_for_judgments:
            futures["judgments"] = executor.submit(_safe_judgments_search, project_name_for_judgments)

        results = {key: future.result() for key, future in futures.items()}

    # From here on, everything is pure in-memory processing of already-fetched
    # results (facts assignment, source bookkeeping) -- fast enough that
    # doing it sequentially costs nothing worth parallelizing further.
    if "rating" in results:
        rating_result = results["rating"]
        facts["credit_rating_check"] = {"promoter": rating_result}
        # One source entry PER agency that found a rating -- both agencies
        # checking the same entity is exactly the comparison case this is
        # meant to surface, so each gets credited as its own source rather
        # than collapsing into one generic "credit rating" entry.
        for agency_rating in rating_result.get("ratings", []):
            facts.setdefault("sources", []).append({
                "label": f"{agency_rating['agency']} credit rating",
                "ref": f"{agency_rating['company_name']} -- {agency_rating['url']}",
                "topic": "credit_rating",
                "published_date": "unknown",
                "accessed_date": datetime.now().strftime("%Y-%m-%d"),
            })

    if "ibbi" in results:
        ibbi_result = results["ibbi"]
        facts["ibbi_insolvency_check"] = ibbi_result
        if ibbi_result.get("found_process") is not None:
            facts.setdefault("sources", []).append({
                "label": "IBBI Corporate Debtor Master Data",
                "ref": f"{identifier_label} {corp_identifier} -- {ibbi_result.get('url', '')}",
                "topic": "insolvency_status",
                "published_date": "unknown",
                "accessed_date": datetime.now().strftime("%Y-%m-%d"),
            })

    if "profile" in results:
        profile_result = results["profile"]
        facts["company_profile_check"] = profile_result
        if profile_result.get("found"):
            facts.setdefault("sources", []).append({
                "label": "ZaubaCorp company registration profile",
                "ref": f"{profile_result['name']} -- {profile_result['url']}",
                "topic": "company_profile",
                "published_date": "unknown",
                "accessed_date": datetime.now().strftime("%Y-%m-%d"),
            })

    if "group" in results:
        group_result = results["group"]
        facts["group_companies_check"] = group_result
        if group_result.get("found") and group_result.get("companies"):
            facts.setdefault("sources", []).append({
                "label": "ZaubaCorp director/address crosswalk",
                "ref": f"{identifier_label} {corp_identifier} -- {group_result.get('url', '')} -- {len(group_result['companies'])} linked entit(y/ies)",
                "topic": "group_companies",
                "published_date": "unknown",
                "accessed_date": datetime.now().strftime("%Y-%m-%d"),
            })

    if "judgments" in results:
        judgments, judgments_error = results["judgments"]
        if judgments_error:
            facts.setdefault("gaps", []).append(judgments_error)
        appeals_data = category_data.get("appeals") or []
        matched_judgments = cross_reference_appeals(judgments, appeals_data)
        matched_judgments = _save_appeal_judgment_pdfs(reg_no, matched_judgments, output_dir)
        facts["appeal_judgments_found"] = matched_judgments
        if matched_judgments:
            facts.setdefault("sources", []).append({
                "label": "MahaRERA Orders/Judgments",
                "ref": f"{project_name_for_judgments} -- {_MAHARERA_ORDERS_URL} -- {len(matched_judgments)} matched judgment(s)",
                "topic": "appeal_judgments",
                "published_date": "unknown",
                "accessed_date": datetime.now().strftime("%Y-%m-%d"),
            })

    # No automated review-fetching mechanism exists in this pipeline (see
    # run_review_authenticity_triage's own module note) -- this only runs
    # when a caller supplies pre-collected reviews, e.g. via --reviews-file.
    if reviews:
        facts["review_authenticity_triage"] = run_review_authenticity_triage(reviews, facts)
        facts.setdefault("sources", []).append({
            "label": review_source_label or "User reviews (authenticity triage)",
            "ref": f"{len(reviews)} review(s) analyzed for authenticity signals",
            "topic": "review_authenticity",
            "published_date": "unknown",
            "accessed_date": datetime.now().strftime("%Y-%m-%d"),
        })

    facts = run_cts_land_lookup(facts, reg_no, output_dir)

    land = facts.get("land_identification", {})
    origin_locality = (land.get("village_locality") or {}).get("value", "")
    origin_district = (land.get("mandal_taluka_district") or {}).get("value", "")
    if origin_locality:
        origin = f"{origin_locality}, {origin_district}, Maharashtra" if origin_district else f"{origin_locality}, Maharashtra"
        facts = _refine_distances_with_maps(facts, origin)

    # Runs last, after every other step above has had its chance to add a
    # source -- otherwise a topic that only looks single-sourced mid-assembly
    # (e.g. before the credit-rating/IBBI checks ran) would get a spurious
    # gap for a corroboration problem that a later step already fixed.
    facts = verify_cross_corroboration(facts)

    # The template is a fixed reference asset checked into the real repo
    # output root regardless of --output-dir; generated charters follow
    # whatever output_dir the caller actually asked for.
    out_dir = os.path.join(output_dir, "company_charters")
    os.makedirs(out_dir, exist_ok=True)
    project_name = (facts.get("rera_core_fields", {}).get("project_name") or reg_no).strip().replace(" ", "_")

    # Snapshot the prior Charter run (if any) before this run's write
    # overwrites it, so lender/mortgage disclosure can be diffed run-over-run.
    prior_charter_archive_dir = run_archive.archive_previous_charter(reg_no, output_dir)
    prior_charter_facts = run_archive.load_prior_charter_facts(prior_charter_archive_dir)
    lender_history_note = _diff_mortgage_lender(facts, prior_charter_facts)
    if lender_history_note:
        facts["mortgage_lender_history_note"] = lender_history_note

    out_path = os.path.join(out_dir, f"Company_Charter_{project_name}_{reg_no}.docx")
    _fill_template(reg_no, facts, out_path)

    facts_path = os.path.join(out_dir, f"Company_Charter_{project_name}_{reg_no}.facts.json")
    with open(facts_path, "w", encoding="utf-8") as f:
        json.dump(facts, f, indent=2, ensure_ascii=False)

    return out_path, facts


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Company Charter docx for an already-scraped project.")
    parser.add_argument("reg_no", help="MahaRERA registration number whose output/ folder already exists.")
    parser.add_argument("--output-dir", default=config.OUTPUT_ROOT)
    parser.add_argument(
        "--reviews-file",
        default=None,
        help=(
            "Optional path to a JSON file containing a list of pre-collected reviews "
            "({rating, date, text, reviewer_name, reviewer_review_count}) to run the "
            "review-authenticity heuristic triage against. There is no automated "
            "review-fetching mechanism in this pipeline -- reviews must be collected "
            "and saved to this file separately."
        ),
    )
    parser.add_argument(
        "--reviews-source-label",
        default=None,
        help="Label for the reviews source shown in the Charter's Sources list (e.g. 'MouthShut.com'). Defaults to a generic label.",
    )
    args = parser.parse_args()

    project_out_dir = os.path.join(args.output_dir, args.reg_no)
    raw_dir = os.path.join(project_out_dir, "raw")
    if not os.path.isdir(raw_dir):
        print(f"[ERROR] No raw data found at {raw_dir} -- run `python main.py {args.reg_no}` first.")
        return 1

    category_data = finalize_report.load_category_data(raw_dir)
    manifest_path = os.path.join(project_out_dir, "documents_manifest.json")
    documents_manifest = json.load(open(manifest_path, encoding="utf-8")) if os.path.exists(manifest_path) else []
    documents_dir = os.path.join(project_out_dir, "documents")

    research_path = os.path.join(project_out_dir, "research", "deep_research.json")
    research_data = json.load(open(research_path, encoding="utf-8")) if os.path.exists(research_path) else None

    reviews = None
    if args.reviews_file:
        if not os.path.exists(args.reviews_file):
            print(f"[ERROR] --reviews-file not found: {args.reviews_file}")
            return 1
        reviews = json.load(open(args.reviews_file, encoding="utf-8"))

    print(f"[..] Generating Company Charter for {args.reg_no} (model={MODEL})")
    out_path, _facts = run_company_charter(
        args.reg_no, category_data, documents_manifest, documents_dir, research_data, args.output_dir,
        reviews=reviews, review_source_label=args.reviews_source_label,
    )
    print(f"[OK] Company Charter written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

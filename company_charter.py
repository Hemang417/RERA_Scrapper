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
            "properties": {k: _PLAIN_FIELD for k in (
                "project_name", "registration_number", "promoter_name", "authority",
                "plan_approval_number", "project_status", "approved_date",
                "proposed_completion_date", "project_type", "litigations_per_record",
                "promoter_land_owner_investor", "collection_bank_account", "total_building_units",
            )},
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
                    "topic": {"type": "string", "description": "Short category this source backs -- e.g. 'land_title', 'corporate_identity', 'litigation', 'pricing', 'distance'. Used to check whether a material topic is backed by more than one independent source, not just one."},
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
- Every entry in `sources` needs a `topic` (which fact area it backs -- land_title, \
corporate_identity, litigation, pricing, distance, etc.), a `published_date` (YYYY-MM-DD if the \
source states one, or literally "unknown" if it does not -- never invent one), and an \
`accessed_date` (YYYY-MM-DD, when you actually looked at it this pass). These feed a later, \
code-computed confidence score -- an omitted or fabricated date is worse than an honest "unknown".
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
    "could_not_run"}) so _compute_confidence_score can report a real
    Independent Verification Rate instead of parsing gap text."""
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
# Deliberately ICRA-only for now: CRISIL's equivalent company-rating search
# was not confirmed reverse-engineerable in the time spent on it this pass
# (its site exposes an SME-specific autocomplete on a different product,
# not the main corporate-rating search) -- rather than build against an
# unconfirmed mechanism, this is scoped to what actually works. CRISIL
# coverage can be added later if a working search endpoint is found.
#
# Matches ONLY on an exact (case-insensitive) name match against ICRA's own
# company list -- never a fuzzy "probably the same company" guess, since
# misattributing a rating to the wrong legal entity would be a serious
# factual error in a due-diligence document. A promoter/SPV having no
# public rating is the NORMAL case, not a red flag: ICRA only rates
# developers that sought a public rating (typically larger, listed, or
# NCD-issuing entities) -- most MahaRERA promoters are too small/private to
# ever be rated. Callers who also want to check a distinct parent/group
# entity (e.g. "Godrej Properties Limited" for promoter "Godrej Skyline
# Developers Limited") must call this a second time with that name
# explicitly and label the result as the PARENT's rating, never as if it
# were the promoter's own -- this function does not guess a parent itself.
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


def lookup_credit_rating(company_name: str) -> dict:
    """Checks ICRA's public rating database for an exact match on
    `company_name`. Returns either:
      {"found": True, "agency": "ICRA", "company_name": <matched label>,
       "instruments": [{"instrument": ..., "rating": ...}, ...], "url": ...}
    or
      {"found": False, "note": "..."} -- an honest explanation, not an
      error, when nothing matches or a request fails."""
    try:
        matches = _icra_search_companies(company_name)
    except (requests.RequestException, ValueError) as e:
        return {"found": False, "note": f"ICRA lookup could not run this pass: {e}"}

    exact = next(
        (m for m in matches if str(m.get("label", "")).strip().lower() == company_name.strip().lower()),
        None,
    )
    if not exact:
        return {
            "found": False,
            "note": (
                "No public ICRA rating found for this exact legal entity name. This is NOT itself a "
                "red flag -- ICRA only rates developers that sought a public rating (typically larger, "
                "listed, or NCD-issuing entities); most MahaRERA promoters are too small or private to "
                "ever be rated."
            ),
        }

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
# IBBI insolvency check -- confirmed live against IBBI's real Corporate
# Debtor Master Data. The site's own search form (/claims/corporate-personals)
# is CSRF-protected, but submitting it (by CIN) redirects to a plain,
# directly-linkable detail URL -- https://ibbi.gov.in/claims/inner-process/
# <CIN> -- which a fresh, cookie-less requests.get() reaches identically
# (confirmed: no CSRF token or session state needed for this specific URL).
# Live-tested against Godrej Skyline Developers Limited's real CIN
# (U45309MH2016PLC287858): returned the clean "ASSIGNMENT NOT APPROVED YET"
# result -- i.e. no IBC process on record for this CIN, the expected,
# unremarkable outcome for the large majority of promoters. No real
# company with an ACTIVE/PAST insolvency process was available to test the
# positive-match case against, so that path surfaces the raw extracted
# status text for a human to read rather than attempting to classify or
# summarize content this function was never validated against.
# ---------------------------------------------------------------------------

_IBBI_INNER_PROCESS_URL = "https://ibbi.gov.in/claims/inner-process/{cin}"
_IBBI_NO_PROCESS_PHRASE = "ASSIGNMENT NOT APPROVED YET"
_CIN_RE = re.compile(r"\b[UL]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")


def extract_cin(text: str) -> str | None:
    """Pulls a standard-format Indian CIN (e.g. U45309MH2016PLC287858) out
    of a free-text field -- corporate_identity.cin_llpin's value is prose
    ("U45309MH2016PLC287858 (incorporated 22 November 2016; ROC Mumbai) --
    ..."), not a bare code, so this is needed before the IBBI/CIN-keyed
    lookups below can use it."""
    match = _CIN_RE.search(text or "")
    return match.group(0) if match else None


def lookup_ibbi_insolvency_status(cin: str) -> dict:
    """Checks IBBI's public Corporate Debtor Master Data for an insolvency
    process tied to `cin`. Returns:
      {"found_process": False, "status_text": "ASSIGNMENT NOT APPROVED YET", "url": ...}
        -- the ordinary, clean case: no IBC process recorded against this CIN.
      {"found_process": True, "status_text": <raw extracted text>, "url": ...}
        -- something other than the known "no process" phrase was found;
        the raw text is surfaced rather than classified, since this
        function's positive-match path was never validated against a real
        example (see module note above) -- a human should read it directly.
      {"found_process": None, "note": "..."} -- the lookup itself failed,
        or no CIN was available to check."""
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


def search_maharera_judgments(project_name: str, max_attempts: int = 3) -> list:
    """Searches MahaRERA's public Orders/Judgments page for `project_name`
    under both categories that can carry a genuine adjudicated outcome
    ("Rulings of MahaRERA" and "Judgements by Adjudicating Officers" --
    "Non-Registration Rulings" is a different, irrelevant category). Retries
    each category up to `max_attempts` times to work around the BigPipe
    flakiness documented above. A project with no published judgment yet
    returns [] after exhausting retries -- this is the expected, common
    case (most complaints/appeals are still pending), not a failure; there
    is no way from this function alone to distinguish "genuinely nothing
    published" from "every retry hit the flaky empty-shell response", so
    callers should not treat an empty result as a confirmed absence for a
    project with very few realistic search attempts left in a budget."""
    all_results = []
    for complaint_type in _MAHARERA_COMPLAINT_TYPES:
        for _attempt in range(max_attempts):
            try:
                results = _maharera_orders_search_once(project_name, complaint_type)
            except requests.RequestException:
                results = []
            if results:
                all_results.extend(results)
                break
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
    _set_paragraph_text(p[15], fld(facts, "litigation_status"))
    _set_paragraph_text(p[17], facts["location_coordinates_note"])
    _set_paragraph_text(p[18], "Map screenshot not embedded -- see the Methodology Note above for how distances below were sourced (this template's own instructions note a live map cannot be fetched programmatically).")
    _set_paragraph_text(p[24], f"Road: {conn.get('road', '')}")
    _set_paragraph_text(p[25], f"Rail: {conn.get('rail', '')}")
    _set_paragraph_text(p[26], f"Metro: {conn.get('metro', '')}")
    _set_paragraph_text(p[27], f"Air: {conn.get('air', '')}")
    _set_paragraph_text(p[30], facts["social_infrastructure"])
    _set_paragraph_text(p[32], facts["fsi_governing_framework"])
    _set_paragraph_text(p[33], facts["fsi_interpretation"])
    _set_paragraph_text(p[36], f"Governing act: {rs.get('governing_act', '')}")
    _set_paragraph_text(p[37], f"Planning approval sequence: {rs.get('planning_approval_sequence', '')}")
    _set_paragraph_text(p[38], f"Allotment mechanics: {rs.get('allotment_mechanics', '')}")
    _set_paragraph_text(p[41], rc.get("registration_summary", ""))
    _set_paragraph_text(p[42], f"Collection Account of the Project (100%): {rc.get('collection_account', '')}")
    _set_paragraph_text(p[43], f"Separate/Transaction RERA escrow sub-accounts: {rc.get('escrow_subaccounts', '')}")
    _set_paragraph_text(p[44], f"Litigations/complaints/appeals related to the project: {rc.get('litigations_complaints_appeals', '')}")
    _set_paragraph_text(p[45], rc.get("statutory_declaration", ""))
    _set_paragraph_text(p[46], f"Construction progress: {rc.get('construction_progress', '')}")
    _set_paragraph_text(p[49], f"Authority of record: {lp.get('authority_of_record', '')}")
    _set_paragraph_text(p[50], f"Project type: {lp.get('project_type', '')}")
    _set_paragraph_text(p[51], f"Professionals of record: {lp.get('professionals_of_record', '')}")
    _set_paragraph_text(p[54], facts["micro_market_overview"])
    _set_paragraph_text(p[56], facts["area_intelligence_trend"])
    _set_paragraph_text(p[58], facts.get("rera_scraping_note", f"Extracted directly from the live MahaRERA public project page for registration number {reg_no}."))
    _set_paragraph_text(p[61], facts["unit_summary_note"])
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
        _set_cell(t[0], row, 2, src(li, key))

    for row, key in zip(range(1, 10), (
        "promoter_name", "organization_type", "cin_llpin", "registered_office_main",
        "registered_office_board_resolution", "registered_office_planning_stage",
        "authorized_signatory", "partners_directors", "landowner_investor",
    )):
        _set_cell(t[1], row, 1, fld(ci, key))

    for row, key in zip(range(1, 5), ("east", "west", "north", "south")):
        _set_cell(t[2], row, 1, nb.get(key, ""))

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
        _set_row_cell(lender_row, 1, mortgage_lender_value)
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

    _append_complaint_outcomes_section(doc, facts)
    _append_credit_rating_section(doc, facts)
    _append_ibbi_check_section(doc, facts)
    _append_appeal_judgments_section(doc, facts)
    _append_authenticity_page(doc, facts)

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
# Composite Data Confidence Score -- six criteria, each computed directly from
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

_CONFIDENCE_WEIGHTS = {
    "source_tier_quality": 0.30,
    "primary_tier_density": 0.20,
    "completeness_rate": 0.20,
    "cross_corroboration": 0.15,
    "recency": 0.10,
    "verification_rate": 0.05,
}

_RECENCY_WINDOW_MONTHS = 18  # this project's own calibration, not a disclosed CRISIL threshold


def _band_label(score: float) -> str:
    if score >= 80:
        return "High"
    if score >= 50:
        return "Moderate"
    return "Limited"


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


def _compute_confidence_score(facts: dict, authenticity_summary: dict) -> dict:
    """Returns {overall, band, criteria: {name: {score, weight, note}}}. Every
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
    # from a URL or a checkable local document this pass).
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
    # nothing to assess.
    ages = []
    for s in sources:
        age = _months_since(s.get("published_date", ""))
        if age is None:
            age = _months_since(s.get("accessed_date", ""))
        if age is not None:
            ages.append(age)
    if ages:
        fresh = sum(1 for a in ages if a <= _RECENCY_WINDOW_MONTHS)
        criteria["recency"] = {
            "score": 100 * fresh / len(ages),
            "note": f"{fresh} of {len(ages)} dated sources are within this document's {_RECENCY_WINDOW_MONTHS}-month freshness window (this project's own calibration, not a disclosed external standard).",
        }

    applicable_weight = sum(_CONFIDENCE_WEIGHTS[k] for k in criteria)
    if applicable_weight == 0:
        overall = 0.0
    else:
        overall = sum(criteria[k]["score"] * _CONFIDENCE_WEIGHTS[k] for k in criteria) / applicable_weight
        for k in criteria:
            criteria[k]["weight"] = round(100 * _CONFIDENCE_WEIGHTS[k] / applicable_weight, 1)

    skipped = sorted(set(_CONFIDENCE_WEIGHTS) - set(criteria))
    return {
        "overall": round(overall, 1),
        "band": _band_label(overall),
        "criteria": criteria,
        "skipped_criteria": skipped,
    }


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


def _append_credit_rating_section(doc, facts: dict) -> None:
    """Appends a section reporting the code-computed ICRA credit-rating
    check on the promoter's exact legal name (see lookup_credit_rating).
    Silently does nothing if credit_rating_check was never set (e.g. no
    promoter name was available to check against)."""
    check = facts.get("credit_rating_check")
    if not check:
        return

    heading_style = doc.paragraphs[4].style

    doc.add_page_break()
    heading_para = doc.add_paragraph("Credit Rating Check (Code-Computed)")
    heading_para.style = heading_style
    doc.add_paragraph(
        "Checked directly against ICRA's public rating database for an exact match on the promoter's "
        "own legal name -- not a fuzzy or \"probably the same company\" guess, since attributing a "
        "rating to the wrong legal entity would itself be a serious error. A promoter having no public "
        "rating is the ordinary case, not a red flag: ICRA only rates developers that sought a public "
        "rating (typically larger, listed, or NCD-issuing entities)."
    )

    promoter_result = check.get("promoter", {})
    if promoter_result.get("found"):
        doc.add_paragraph(f"Match found: {promoter_result['company_name']} ({promoter_result['url']})")
        table = doc.add_table(rows=1, cols=2)
        _set_table_borders(table)
        header_cells = table.rows[0].cells
        header_cells[0].text = "Instrument"
        header_cells[1].text = "Rating"
        for cell in header_cells:
            _shade_cell(cell, "D9E2F3")
            for p in cell.paragraphs:
                for run in p.runs:
                    run.bold = True
        for item in promoter_result.get("instruments", []):
            row = table.add_row()
            row.cells[0].text = item["instrument"]
            row.cells[1].text = item["rating"]
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
            doc.add_paragraph(f"Parent/group match found: {parent_result['company_name']} ({parent_result['url']})")
            table = doc.add_table(rows=1, cols=2)
            _set_table_borders(table)
            header_cells = table.rows[0].cells
            header_cells[0].text = "Instrument"
            header_cells[1].text = "Rating"
            for cell in header_cells:
                _shade_cell(cell, "D9E2F3")
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.bold = True
            for item in parent_result.get("instruments", []):
                row = table.add_row()
                row.cells[0].text = item["instrument"]
                row.cells[1].text = item["rating"]
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
        doc.add_paragraph(
            f"Result: \"{check['status_text']}\" -- no insolvency process is recorded against this CIN "
            f"in IBBI's public database. ({check.get('url', '')})"
        )
    else:
        doc.add_paragraph(
            "Result: this CIN returned something other than the standard \"no process\" result. The "
            "raw extracted page content is reproduced below verbatim for a human to read directly -- "
            "this checker was not validated against a real active/past insolvency case, so it does not "
            "attempt to summarize or classify this content itself:"
        )
        doc.add_paragraph(check.get("status_text", ""))
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


def _append_authenticity_page(doc, facts: dict) -> None:
    """Appends a new, code-computed section (not model-authored) summarizing
    what tier each cited source falls into and how many claims remain
    explicit gaps -- lets a reader judge this report's reliability from the
    same underlying data already visible in the Sources/Gaps sections,
    rather than trusting a self-assessment."""
    summary = _compute_authenticity_summary(facts)
    confidence = _compute_confidence_score(facts, summary)

    # This template's heading styles were created via docx-js, not Word --
    # python-docx's add_heading() looks up a style named "Heading 1" and
    # doesn't find a matching one (confirmed live: raises KeyError even
    # though paragraph.style.name reports "Heading 1" when reading an
    # existing heading). Reusing the actual style OBJECT already applied to
    # an existing Heading-1 paragraph in this same document sidesteps the
    # name-lookup mismatch entirely.
    heading_style = doc.paragraphs[4].style  # "Methodology Note" -- a known Heading 1 in the template

    doc.add_page_break()
    heading_para = doc.add_paragraph("Data Authenticity & Confidence Summary")
    heading_para.style = heading_style
    doc.add_paragraph(
        "This page is generated directly from the same sources and gaps already listed earlier in "
        "this document -- it is a count, not a self-assessment. A report author claiming its own "
        "work is \"reliable\" would just be another unverified claim; this page instead classifies "
        "every cited source by tier so a reader can judge confidence from the same underlying data."
    )

    from docx.shared import Pt

    score_para = doc.add_paragraph()
    score_run = score_para.add_run(f"Data Confidence Score: {confidence['overall']}/100 -- {confidence['band']}")
    score_run.bold = True
    score_run.font.size = Pt(14)
    doc.add_paragraph(
        "This score is a weighted average of six criteria computed below from this document's own "
        "sources and gaps -- it is informed by the structure of CRISIL's real-estate methodology "
        "(a small number of named factors rather than one flat checklist) but is NOT a replica of any "
        "CRISIL formula: CRISIL does not publish a numeric weighting scheme for any of its three "
        "real-estate products, and the weights used here are this project's own calibration."
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


def run_company_charter(
    reg_no: str,
    category_data: dict,
    documents_manifest: list,
    documents_dir: str,
    research_data: dict | None = None,
    output_dir: str = config.OUTPUT_ROOT,
    complaint_orders_manifest: list | None = None,
    complaint_orders_dir: str | None = None,
) -> str:
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
    if complaint_orders_manifest and complaint_orders_dir:
        # Also code-computed, never model-authored -- the same reasoning as
        # document_library above: a real per-complaint outcome breakdown
        # should never be re-derived or paraphrased by the model when we
        # can just compute it directly from the actual downloaded orders.
        facts["complaint_outcomes_summary"] = summarize_complaint_outcomes(complaint_orders_manifest, complaint_orders_dir)

    promoter_name_for_rating = (facts.get("corporate_identity", {}).get("promoter_name") or {}).get("value", "")
    if promoter_name_for_rating:
        try:
            rating_result = lookup_credit_rating(promoter_name_for_rating)
        except Exception as e:
            # A network hiccup or an ICRA-side change to the endpoints above
            # must not take down Company Charter generation over an optional
            # enrichment -- same policy as every other external check here.
            rating_result = {"found": False, "note": f"ICRA lookup could not run this pass: {e}"}
        facts["credit_rating_check"] = {"promoter": rating_result}
        if rating_result.get("found"):
            facts.setdefault("sources", []).append({
                "label": "ICRA credit rating",
                "ref": f"{rating_result['company_name']} -- {rating_result['url']}",
                "topic": "credit_rating",
                "published_date": "unknown",
                "accessed_date": datetime.now().strftime("%Y-%m-%d"),
            })

    cin_for_ibbi = extract_cin((facts.get("corporate_identity", {}).get("cin_llpin") or {}).get("value", ""))
    if cin_for_ibbi:
        try:
            ibbi_result = lookup_ibbi_insolvency_status(cin_for_ibbi)
        except Exception as e:
            ibbi_result = {"found_process": None, "note": f"IBBI lookup could not run this pass: {e}"}
        facts["ibbi_insolvency_check"] = ibbi_result
        if ibbi_result.get("found_process") is not None:
            facts.setdefault("sources", []).append({
                "label": "IBBI Corporate Debtor Master Data",
                "ref": f"CIN {cin_for_ibbi} -- {ibbi_result.get('url', '')}",
                "topic": "insolvency_status",
                "published_date": "unknown",
                "accessed_date": datetime.now().strftime("%Y-%m-%d"),
            })

    project_name_for_judgments = (facts.get("rera_core_fields", {}) or {}).get("project_name", "")
    if project_name_for_judgments:
        try:
            judgments = search_maharera_judgments(project_name_for_judgments)
        except Exception as e:
            judgments = []
            facts.setdefault("gaps", []).append(
                f"MahaRERA Orders/Judgments search for appeal-level outcomes could not run this pass: {e}"
            )
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

    land = facts.get("land_identification", {})
    origin_locality = (land.get("village_locality") or {}).get("value", "")
    origin_district = (land.get("mandal_taluka_district") or {}).get("value", "")
    if origin_locality:
        origin = f"{origin_locality}, {origin_district}, Maharashtra" if origin_district else f"{origin_locality}, Maharashtra"
        facts = _refine_distances_with_maps(facts, origin)

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

    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Company Charter docx for an already-scraped project.")
    parser.add_argument("reg_no", help="MahaRERA registration number whose output/ folder already exists.")
    parser.add_argument("--output-dir", default=config.OUTPUT_ROOT)
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

    print(f"[..] Generating Company Charter for {args.reg_no} (model={MODEL})")
    out_path = run_company_charter(args.reg_no, category_data, documents_manifest, documents_dir, research_data, args.output_dir)
    print(f"[OK] Company Charter written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

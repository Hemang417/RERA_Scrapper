"""
Restructured Company Charter document builder -- the Counterparty +
Collateral layout agreed in design (see the "charter restructure" spec).

WHY THIS IS A SEPARATE MODULE, built fresh rather than editing
company_charter._fill_template: that function fills the template's
paragraphs BY FIXED INDEX (p[8], p[16], p[34] ...) and then relocates
headings, which works only because the template's own section order matches
the document's. The new layout reorders everything, so index-based filling
stops being a help and becomes a hazard. This builder instead takes the
template purely for its STYLES (heading styles are latent in this template
and cannot be looked up by name -- confirmed live, doc.styles["Heading 1"]
raises KeyError while paragraphs report that style, which is why the
existing code reads styles off real paragraphs), clears the body, and
writes every section explicitly in the new order.

_fill_template is left completely untouched, so the existing Internal/
External documents keep generating exactly as before while this is built
and compared against them.

Structure (both variants share this skeleton; Internal shows more per
section, via company_charter._variant/_variant_paragraph):
  Counterparty
    1. Verification Summary        -- claim-by-claim + 2 bucketed rating tables
    2. Counterparty Identity
    3. Leadership, Related Entities & Litigation Screen
    4. Litigation & Regulatory Screening
    5. Organisation & Team
    6. Portfolio
  Collateral
    7. Asset Identity & Land Record
    8. Approvals, FSI & Title
    9. RERA Compliance & Escrow
   10. Location, Connectivity & Market Read
   11. Document & Diligence Trail
  Covering both
   12. Consolidated Flags & Recommended Steps
   13. Closing Read                -- rule-derived, never model-authored
   14. Scoring Detail appendix     -- full sub-metric breakdowns
   15. Sources                     -- Harvard style
"""

from __future__ import annotations

import re

import company_charter as cc

# ---------------------------------------------------------------------------
# Documentation Confidence Score buckets. The Developer Score already carries
# a `bucket` on every criterion (see _DEVELOPER_SCORE_STRUCTURE); the
# Documentation Confidence Score is a flat criteria list with no buckets at
# all, so its grouping is defined here. Weights are NOT reassigned -- each
# bucket's weight is just the sum of its criteria's existing weights, so the
# bucketed table and the detailed breakdown can never disagree.
#
# Each bucket answers a distinct question a reader actually asks: how good
# were the sources, how much did we independently confirm, and how complete
# and current is it.
# ---------------------------------------------------------------------------
_DOC_CONFIDENCE_BUCKETS = (
    ("Source Quality", ("source_tier_quality", "primary_tier_density")),
    ("Corroboration & Confirmation", ("cross_corroboration", "financial_figures_confirmed", "verification_rate")),
    ("Coverage & Recency", ("completeness_rate", "recency_legal", "recency_other")),
)

_DOC_CONFIDENCE_CRITERION_LABELS = {
    "source_tier_quality": "Weighted Source-Tier Quality",
    "primary_tier_density": "Primary-Tier Density",
    "cross_corroboration": "Cross-Corroboration",
    "financial_figures_confirmed": "Financial Figures Confirmed",
    "verification_rate": "Independent Verification Rate",
    "completeness_rate": "Completeness Rate",
    "recency_legal": "Recency - Legal Records",
    "recency_other": "Recency - Other Sources",
}


def _band_developer_rating(score: float) -> str:
    """Maps a 0-100 rolled-up bucket score onto the same AAA-D ladder the
    individual sub-metrics use, via the thresholds already defined for the
    composite grade."""
    for tier, floor in cc._DEVELOPER_SCORE_TIER_THRESHOLDS:
        if score >= floor:
            return tier
    return "D"


def rollup_developer_score_buckets(developer_score: dict) -> list:
    """Rolls the 9 Developer Score sub-metrics up into their 3 buckets for
    the summary table. Returns [{bucket, weight, rating, score, scored,
    total, unscored_display_names}].

    A bucket's rating averages only the sub-metrics that ACTUALLY scored,
    renormalised within the bucket -- deliberately different from the
    composite, which holds every weight fixed and lets an unscored
    sub-metric contribute nothing (see _compute_developer_score's own note).
    Both readings are honest but answer different questions: the composite
    answers "how much of this promoter is verifiable at all", the bucket
    rating answers "of what could be measured here, how strong is it". They
    are therefore never presented as the same number, and the unscored
    sub-metric names travel with the bucket so they can be flagged rather
    than silently averaged away -- the no-N/A rule.

    `rating` is None when nothing in the bucket scored, so a caller can
    render a flag line instead of a fake grade."""
    criteria = (developer_score or {}).get("criteria") or {}
    buckets = []
    for bucket_name, bucket_weight, metrics in cc._DEVELOPER_SCORE_STRUCTURE:
        keys = [key for key, _display, _fn in metrics]
        scored, unscored = [], []
        for key in keys:
            criterion = criteria.get(key) or {}
            display = criterion.get("display_name") or key.replace("_", " ").title()
            if criterion.get("score") is None:
                unscored.append(display)
            else:
                scored.append((criterion["score"], criterion.get("weight") or 0.0))

        weight_sum = sum(w for _s, w in scored)
        score = round(sum(s * w for s, w in scored) / weight_sum, 1) if weight_sum else None
        buckets.append({
            "bucket": bucket_name,
            "weight": bucket_weight,
            "score": score,
            "rating": _band_developer_rating(score) if score is not None else None,
            "scored": len(scored),
            "total": len(keys),
            "unscored_display_names": unscored,
        })
    return buckets


def rollup_doc_confidence_buckets(doc_confidence: dict) -> list:
    """Same idea as rollup_developer_score_buckets, for the Documentation
    Confidence Score's 7-8 criteria grouped by _DOC_CONFIDENCE_BUCKETS.
    Returns [{bucket, weight, band, score, scored, total,
    unscored_display_names}].

    Banded with the High/Moderate/Limited labels this score already uses
    (_DATA_AUTHENTICITY_BANDS) rather than the AAA-D ladder, since it rates
    how well-evidenced THIS REPORT is, not the promoter -- conflating the
    two scales is exactly the misreading the existing document's own
    wording works to prevent."""
    criteria = (doc_confidence or {}).get("criteria") or {}
    skipped = set((doc_confidence or {}).get("skipped_criteria") or [])

    buckets = []
    for bucket_name, keys in _DOC_CONFIDENCE_BUCKETS:
        scored, unscored = [], []
        for key in keys:
            criterion = criteria.get(key)
            label = _DOC_CONFIDENCE_CRITERION_LABELS.get(key, key.replace("_", " ").title())
            if not criterion or criterion.get("score") is None or key in skipped:
                # A criterion absent from `criteria` entirely is only a real
                # gap if it was explicitly skipped this pass or is a known
                # member of this bucket -- either way it is named, never
                # silently dropped.
                if key in skipped or criterion is not None:
                    unscored.append(label)
                continue
            scored.append((criterion["score"], criterion.get("weight") or 0.0))

        weight_sum = sum(w for _s, w in scored)
        score = round(sum(s * w for s, w in scored) / weight_sum, 1) if weight_sum else None
        buckets.append({
            "bucket": bucket_name,
            "weight": round(weight_sum, 1),
            "score": score,
            "band": cc._band_label(score) if score is not None else None,
            "scored": len(scored),
            "total": len(keys),
            "unscored_display_names": unscored,
        })
    return buckets


# ---------------------------------------------------------------------------
# Claim-by-claim verification, the spine of section 1.
#
# The comparison is promoter-stated vs independently verified -- there is no
# deck or brochure ingested anywhere in this pipeline, so a "deck claim"
# column would have nothing behind it. What every sourced field DOES carry is
# where the claim came from, and those sources fall into genuinely different
# evidence classes. The distinction that matters most: a MahaRERA filing is
# the promoter's OWN statement to the regulator (promoter-stated, however
# official the venue), whereas an MCA mirror or a government land record is a
# third party (independent). Collapsing those two into "verified" would
# overstate the evidence.
#
# Status is derived from the source, NOT from a stored per-field verdict,
# because no such verdict generally exists: _verify_material_claims only
# re-checks URL-sourced claims and a real project can have none at all
# (confirmed live -- every material field on Pranami Bliss is
# document-sourced, so _verification_stats is null and every gap is
# model-authored prose rather than a per-field record). Deriving from the
# source is therefore the honest available signal, and it never claims a
# check that didn't run.
# ---------------------------------------------------------------------------

STATUS_CONFIRMED_DOCUMENT = "Confirmed (primary document)"
STATUS_CONFIRMED_INDEPENDENT = "Confirmed (independent registry)"
STATUS_PROMOTER_FILED = "Promoter-filed (RERA record)"
STATUS_NOT_ESTABLISHED = "Not established"
STATUS_STATED_ONLY = "Stated, not independently verified"
STATUS_DISCREPANCY = "Discrepancy - see flags"


def classify_claim_evidence(source: str) -> tuple:
    """Returns (verified_position, status, is_independent) for one field's
    `source` string. `verified_position` names WHAT the claim was checked
    against, in reader-facing terms rather than a file path."""
    src = str(source or "").strip()
    lowered = src.lower()

    if not src or lowered.startswith("gap"):
        return ("No source established this pass", STATUS_NOT_ESTABLISHED, False)

    # A downloaded primary document -- the strongest single source available,
    # and the class _check_document_grounding mechanically verifies.
    if "/documents/" in src or ".pdf" in lowered:
        label = cc._clean_source_label(src) or "primary document"
        return (f"Primary document: {label}", STATUS_CONFIRMED_DOCUMENT, False)

    # Third-party registries -- genuinely independent of the promoter.
    if "mca-mirror" in lowered or "zaubacorp" in lowered or "tofler" in lowered or "instafinancials" in lowered:
        return ("MCA-mirror company registration profile", STATUS_CONFIRMED_INDEPENDENT, True)
    if "bhulekh" in lowered or "property card" in lowered:
        return ("Maha Bhulekh Property Card (government land record)", STATUS_CONFIRMED_INDEPENDENT, True)
    if "ibbi" in lowered:
        return ("IBBI Corporate Debtor Master Data", STATUS_CONFIRMED_INDEPENDENT, True)

    # MahaRERA is official, but what it holds is the promoter's own filing.
    if "maharera" in lowered:
        return ("MahaRERA project record (promoter's own filing)", STATUS_PROMOTER_FILED, False)

    return (cc._clean_source_label(src) or src, STATUS_STATED_ONLY, False)


# The four material-claim groups _verify_material_claims itself re-checks,
# with reader-facing group titles.
_MATERIAL_CLAIM_GROUPS = (
    ("land_identification", "Land identification"),
    ("corporate_identity", "Counterparty identity"),
    ("fsi_metrics", "FSI and area"),
)


# Explicit labels for fields whose key doesn't title-case into something a
# reader should see. Spelled out rather than derived by string surgery: an
# earlier attempt applied replacements and THEN .capitalize(), which silently
# lowercased them again and rendered "CIN / LLPIN" as "Cin / llpin".
_FIELD_DISPLAY_OVERRIDES = {
    "survey_cts_plot_numbers": "Survey / CTS / plot numbers",
    "mandal_taluka_district": "Mandal / taluka / district",
    "cin_llpin": "CIN / LLPIN",
    "registered_office_main": "Registered office (main)",
    "registered_office_board_resolution": "Registered office (per board resolution)",
    "registered_office_planning_stage": "Registered office (at planning stage)",
    "partners_directors": "Partners / directors",
    "landowner_investor": "Landowner / investor",
    "authorized_signatory": "Authorised signatory",
    "organization_type": "Organisation type",
    "net_land_area": "Net land area",
    "approved_bua": "Approved built-up area",
    "sanctioned_bua": "Sanctioned built-up area",
    "implied_fsi": "Implied FSI",
    "mortgage_area": "Mortgaged area",
    "mortgage_lender": "Mortgage lender",
}


def _field_display_name(key: str) -> str:
    override = _FIELD_DISPLAY_OVERRIDES.get(key)
    if override:
        return override
    return key.replace("_", " ").capitalize()


def build_claim_rows(facts: dict) -> list:
    """Builds section 1's claim-by-claim rows from every {value, source}-
    shaped field in the four material-claim groups. Returns [{group, claim,
    stated, verified_position, status}].

    Only dict-shaped fields appear: a group can also hold plain strings with
    no source of their own (fsi_metrics carries five such), and a claim with
    no recorded source cannot honestly be given a verification status, so
    those belong in their own section's prose rather than in a table whose
    whole purpose is claim-versus-evidence."""
    rows = []
    gap_texts = [str(g).lower() for g in (facts.get("gaps") or [])]

    def _add(group_title: str, key: str, field: dict):
        value = str(field.get("value") or "").strip()
        if not value:
            return
        verified_position, status, _independent = classify_claim_evidence(field.get("source"))

        # A MACHINE-written per-field gap overrides the source-derived
        # status: an unconfirmed claim must never read as confirmed just
        # because it cites a document. Matched on the exact structured
        # prefix _verify_one_field/_check_document_grounding write
        # ("<field_key>: <value> (verification: ...)"), NOT by searching the
        # gap prose for the field's name -- that was tried and produced a
        # false positive immediately: a model-authored gap describing a
        # portfolio search ("matched only this one project under this exact
        # promoter name") flipped the promoter_name claim to Discrepancy
        # despite saying nothing about whether that claim was verified.
        # Model-authored gaps are general findings and are surfaced in the
        # flags section on their own terms, not as per-field verdicts.
        display = _field_display_name(key)
        if any(g.startswith(f"{key.lower()}:") for g in gap_texts):
            status = STATUS_DISCREPANCY

        rows.append({
            "group": group_title,
            "claim": display,
            "stated": value,
            "verified_position": verified_position,
            "status": status,
            "field_key": key,
            "source": field.get("source"),
        })

    for group_key, group_title in _MATERIAL_CLAIM_GROUPS:
        for key, field in (facts.get(group_key) or {}).items():
            if isinstance(field, dict):
                _add(group_title, key, field)

    litigation = facts.get("litigation_status")
    if isinstance(litigation, dict):
        _add("Litigation", "litigation_status", litigation)

    return rows


# ---------------------------------------------------------------------------
# Document construction.
# ---------------------------------------------------------------------------







_STATUS_COLORS = {
    STATUS_DISCREPANCY: cc._TEXT_RED,
    STATUS_NOT_ESTABLISHED: cc._TEXT_AMBER,
    STATUS_STATED_ONLY: cc._TEXT_AMBER,
    STATUS_PROMOTER_FILED: cc._TEXT_AMBER,
}








_LINK_TYPE_IMPLICATIONS = {
    "Shared director": "Common leadership, so decisions and reputation travel between the entities.",
    "Shared registered office": "A shared address alone is weak evidence; common for group holding structures and for unrelated tenants of the same building.",
    "Subsidiary / associate / JV": "A filed ownership relationship, the strongest form of link in this set.",
}


def _basis_role(basis: str) -> str:
    """Turns one raw basis string into a reader-facing link type."""
    text = str(basis or "")
    if text.startswith("shared director"):
        return "Shared director"
    if text.startswith("shared registered office"):
        return "Shared registered office"
    if text.startswith("subsidiary"):
        return "Subsidiary / associate / JV"
    return text or "Unspecified link"




# ---------------------------------------------------------------------------
# Combined related-entity Annexure (charter_report.py) -- one shared-director
# basis string parsed into its real per-entity role and current/resigned
# status, e.g. "shared director: Mahir Haresh Wadhwani (Designated Partner,
# resigned 2023-04-03)". Confirmed live as a real inconsistency: prose
# elsewhere correctly said "a past partner" for an LLP promoter, while this
# table's own "Role of the promoter" column still said the generic category
# "Shared director" regardless of the LINKED entity's own legal form -- the
# fix is to show the real designation ZaubaCorp recorded at THAT entity
# (already correct per-entity, e.g. "Director" for a Pvt Ltd link and
# "Designated Partner" for an LLP link) instead of a category label.
# ---------------------------------------------------------------------------
_SHARED_DIRECTOR_BASIS_RE = re.compile(r"^shared director:\s*([^(]+?)(?:\s*\(([^)]*)\))?\s*$", re.IGNORECASE)


def _parse_shared_director_basis(basis: str) -> dict | None:
    """Returns {"person", "designation", "status"} or None if `basis` isn't
    a shared-director entry. Tolerates the OLD basis format too (no status
    suffix, e.g. "shared director: X (Director)", or no parenthetical at
    all) for backward compatibility with a Charter built before this
    status-capture existed -- `designation`/`status` are just "" then,
    rather than raising or guessing a value that was never scraped."""
    m = _SHARED_DIRECTOR_BASIS_RE.match(str(basis or "").strip())
    if not m:
        return None
    person = cc._normalise_entity_name(m.group(1).strip())
    detail = (m.group(2) or "").strip()
    designation, status = "", ""
    if detail:
        last_comma = detail.rfind(",")
        tail = detail[last_comma + 1:].strip() if last_comma != -1 else detail
        if tail.lower() == "ongoing" or tail.lower().startswith("resigned"):
            status = tail
            designation = detail[:last_comma].strip() if last_comma != -1 else ""
        else:
            designation = detail  # old format: just "(Designation)", no status captured
    return {"person": person, "designation": designation, "status": status}


def _related_entity_people(basis_list: list) -> list:
    """One entry per distinct person named in a shared-director basis on
    this list (deduped by name, first occurrence wins)."""
    seen = {}
    for b in basis_list or []:
        parsed = _parse_shared_director_basis(b)
        if parsed and parsed["person"] and parsed["person"] not in seen:
            seen[parsed["person"]] = parsed
    return list(seen.values())


def _related_entity_linked_via(basis_list: list) -> str:
    """"Person (real designation, ongoing/resigned date)" for every distinct
    shared-director person on this entity, joined -- e.g. "Girish Sanjay
    Yadav (Designated Partner, ongoing); Swapnil Yuvraj Marathe (Designated
    Partner, ongoing)" when an entity is genuinely shared by two people, so
    a reader can see exactly who they're shared with rather than a generic
    "Shared director" label that reads as if a position itself were shared."""
    parts = []
    for p in _related_entity_people(basis_list):
        detail_bits = [x for x in (p["designation"], p["status"]) if x]
        detail = f" ({', '.join(detail_bits)})" if detail_bits else ""
        parts.append(f"{p['person']}{detail}")
    return "; ".join(parts)


def _related_entity_implication(basis_list: list) -> str:
    """General commentary on what this link TYPE means -- never restates a
    name (that's _related_entity_linked_via's job), and explicitly flags
    when two or more DIFFERENT people are the shared connection, since
    that's materially stronger evidence of a real group relationship than
    a single shared person."""
    roles = sorted({_basis_role(b) for b in basis_list or []})
    people = _related_entity_people(basis_list)
    parts = []
    if "Shared director" in roles:
        if len(people) >= 2:
            parts.append(
                f"Shared with {len(people)} named individuals ({', '.join(p['person'] for p in people)}), "
                f"so decisions and reputation travel between the entities via more than one person."
            )
        else:
            parts.append(_LINK_TYPE_IMPLICATIONS["Shared director"])
    if "Shared registered office" in roles:
        parts.append(_LINK_TYPE_IMPLICATIONS["Shared registered office"])
    if "Subsidiary / associate / JV" in roles:
        parts.append(_LINK_TYPE_IMPLICATIONS["Subsidiary / associate / JV"])
    return " ".join(parts)








def _headline_claim_rows(facts: dict, totals: dict) -> list:
    """Builds the claim-versus-record rows for section 6 from whatever
    headline claims this pass actually captured. Returns [] rather than
    inventing rows when nothing is available."""
    rows = []
    track_record = facts.get("developer_track_record") or {}
    years = track_record.get("years_in_industry")
    basis = str(track_record.get("years_in_industry_basis") or "")
    if years:
        read = "Consistent"
        if "not credited" in basis.lower() or "no source confirms" in basis.lower():
            read = "Possibly related, unconfirmed"
        rows.append([
            f"Around {years} years in the industry",
            basis or "No basis recorded for this figure.",
            read,
        ])

    area = totals.get("total_area_developed_lakh_sqft")
    if area is not None:
        rows.append([
            f"{area} lakh sq ft delivered across declared prior projects",
            "Summed from the promoter's own past-experience filings to MahaRERA, excluding the subject project.",
            "Promoter-declared, not independently verified",
        ])

    within_5km = totals.get("area_within_5km_lakh_sqft")
    if within_5km is None and totals.get("total_experience_entries_found"):
        rows.append([
            "Experience in this micro-market",
            "Could not be measured: this project's own locality did not resolve to coordinates, so no "
            "distance comparison against prior deliveries was possible.",
            "Not established",
        ])
    return rows


# ---------------------------------------------------------------------------
# Collateral block. Everything from here to section 11 describes the ASSET,
# not the counterparty.
# ---------------------------------------------------------------------------













# ---------------------------------------------------------------------------
# Sections covering BOTH halves.
# ---------------------------------------------------------------------------



def _green_flags(facts: dict) -> list:
    """Positive confirmations, each tied to a specific check that actually
    ran. Never a generic reassurance: if a check did not run, its absence
    belongs in the flags, not here."""
    green = []
    ibbi = facts.get("ibbi_insolvency_check") or {}
    if ibbi.get("found_process") is False:
        green.append("No corporate insolvency process is on record against this entity in IBBI's register.")

    complaint_count, appeal_count = cc._parse_complaint_appeal_counts(facts)
    if complaint_count == 0:
        green.append("No MahaRERA complaint is on record against this project.")
    if appeal_count == 0:
        green.append("No MahaRERA appeal is on record against this project.")

    _original, was_extended = cc._parse_completion_slippage(facts)
    if not was_extended:
        green.append("The RERA-registered completion date has never been extended.")

    profile = facts.get("company_profile_check") or {}
    if str(profile.get("status") or "").lower() == "active":
        green.append(f"The counterparty is an active company on the MCA record ({profile.get('roc', 'registrar not stated')}).")
    if len(profile.get("sources_used") or []) > 1:
        green.append(
            f"Its registration profile agrees across {len(profile['sources_used'])} independent registry mirrors."
        )

    totals = (facts.get("promoter_portfolio") or {}).get("totals") or {}
    if totals.get("on_time_rate_pct") == 100.0 and totals.get("on_time_count"):
        green.append(
            f"All {totals['on_time_count']} declared prior delivery/deliveries completed on or before the "
            f"originally proposed date, per the promoter's own filings."
        )
    return green


def _recommended_steps(facts: dict, flags: dict) -> list:
    """Concrete next steps derived from what this pass could NOT establish."""
    steps = []
    if not (facts.get("cts_land_record_check") or {}).get("found"):
        steps.append("Retrieve the Maha Bhulekh Property Card for the project's CTS number to confirm land ownership independently.")
    if not (facts.get("gst_compliance_check") or {}).get("found"):
        steps.append("Request the developer's GSTIN and pull their GSTR-1 / GSTR-3B filing history to assess statutory filing discipline.")
    if not (facts.get("credit_rating_check") or {}).get("promoter", facts.get("credit_rating_check") or {}).get("ratings"):
        steps.append("Ask the developer whether any agency rating, banking appraisal, or lender credit note exists for this entity.")
    if (facts.get("group_companies_check") or {}).get("undisclosed_relationship_counts"):
        steps.append("Obtain the counterparty's own shareholding and subsidiary disclosure; the registry's free tier withholds those counterparty identities.")
    steps.append("Request the firm-wide organisation structure and team headcount by function, which no public source discloses.")
    for item in (flags.get("imminent") or []):
        steps.append(f"Raise directly with the developer: {item['text']}")
    return steps


def assess_counterparty(facts: dict, flags: dict, developer_score: dict) -> dict:
    """Rule-derived read on substance, for section 13. Returns
    {"verdict", "signals": [(label, finding)], "spv_like": bool}.

    Deliberately NOT model-authored. This is the single most consequential
    passage in the document, so every clause is computed from a named fact
    and can be traced back to it. A model could phrase it more smoothly but
    could also be confidently wrong about whether a real company is a shell,
    which is a materially harmful thing to get wrong.
    """
    signals = []
    portfolio = facts.get("promoter_portfolio") or {}
    totals = portfolio.get("totals") or {}
    profile = facts.get("company_profile_check") or {}
    core = facts.get("rera_core_fields") or {}

    registrations = totals.get("total_projects")
    single_project = registrations == 1
    if registrations is not None:
        signals.append((
            "MahaRERA registrations",
            f"{registrations} held under this promoter's name"
            + (", which is the subject project itself" if single_project else ""),
        ))

    prior = totals.get("total_experience_entries_found") or 0
    signals.append((
        "Declared prior deliveries",
        f"{prior} declared to MahaRERA" if prior else "none declared to MahaRERA",
    ))

    incorporation = str(profile.get("incorporation_date") or "")
    approved = str(core.get("approved_date") or "")
    if incorporation and approved:
        signals.append((
            "Incorporation against project registration",
            f"incorporated {incorporation}, project approved {approved}",
        ))

    directors = profile.get("current_directors") or []
    if directors:
        signals.append(("Board", f"{len(directors)} current {cc._role_word(facts, len(directors))} on the registry roster"))

    group = facts.get("group_companies_check") or {}
    linked = len(group.get("companies") or [])
    if linked:
        signals.append((
            "Group footprint",
            f"{linked} entities share a named {cc._role_word(facts)} or registered office with this counterparty",
        ))

    paid_up = str(profile.get("paid_up_capital") or "").strip()
    if paid_up:
        signals.append(("Paid-up capital", paid_up))

    imminent_count = len(flags.get("imminent") or [])
    signals.append((
        "Imminent flags",
        f"{imminent_count} raised in this review" if imminent_count else "none raised in this review",
    ))

    # --- verdict ------------------------------------------------------------
    # "Paper firm" is a claim about SUBSTANCE, and a single-project SPV inside
    # a real corporate group is a completely different animal from a
    # standalone entity with nothing behind it. The two are separated here
    # rather than collapsed into one label.
    substantial_group = linked >= 10
    if single_project and prior == 0 and not substantial_group:
        verdict = (
            "This counterparty presents as a standalone single-project entity with no declared prior delivery "
            "and no substantial group footprint behind it. On the evidence gathered, treat its execution "
            "capability as unproven and seek direct evidence of delivery capacity before proceeding."
        )
    elif single_project and substantial_group:
        verdict = (
            f"This counterparty is a single-project vehicle, but not a shell: {linked} entities share named "
            f"leadership or a registered office with it, and it is an active company on the MCA record. Read it "
            f"as a project-specific vehicle of a larger group, which means execution capability rests on the "
            f"group rather than on this entity's own balance sheet or track record."
        )
    elif single_project:
        verdict = (
            "This counterparty is a single-project vehicle with a limited independent track record. Execution "
            "capability cannot be established from this entity's own history alone."
        )
    else:
        verdict = (
            f"This counterparty holds {registrations} MahaRERA registrations and has a track record that can be "
            f"reviewed on its own terms rather than inferred from a parent group."
        )

    grade = developer_score.get("grade")
    if grade:
        verdict += (
            f" Its composite Developer Score is {developer_score.get('composite')}/100 (grade {grade}), which "
            f"reflects both measured strength and how much of this promoter is publicly verifiable at all."
        )

    return {"verdict": verdict, "signals": signals, "spv_like": bool(single_project)}






# ---------------------------------------------------------------------------
# Harvard-style source list.
# ---------------------------------------------------------------------------

_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")











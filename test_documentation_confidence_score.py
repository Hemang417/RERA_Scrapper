"""
Verifies company_charter._compute_documentation_confidence_score() after the
Step 5 rebalance: rebalanced weights, recency split into a 3-month legal
window vs. an 18-month "other" window, the new financial_figures_confirmed
criterion, and the zero-verification band cap.

2026-08-28 additions, both from auditing the score for improvements:
  - The thin-coverage safeguard (_MIN_CRITERIA_FOR_HIGH_BAND): a "High" band
    renormalized across only 1-2 of the 8 criteria is a materially weaker
    claim than one built from most of them, so it's now capped the same
    "restrain, never worsen" way the zero-verification cap already was --
    and independently of it (a fact dict can trip one, both, or neither).
  - _FINANCIAL_FIGURE_MARKERS widened after finding two REAL gap sentences
    (from this repo's own past Charter runs, output/*.facts.json --
    gitignored, so reproduced verbatim as literals here rather than read
    from those files) that financial_figures_confirmed silently missed,
    each a real scoring bug: a figure the Charter's own prose says is
    unconfirmed still counted as "confirmed" (no matching gap) purely
    because the wording didn't match any listed regex.

Run directly: python test_documentation_confidence_score.py
"""

import company_charter as cc


def test_zero_verification_caps_high_to_moderate():
    """A synthetic facts dict engineered to score near-perfectly on every
    OTHER criterion, but with zero independent-verification attempts, must
    have its band capped at Moderate (never allowed to show as High) and
    must carry the verification_warning."""
    facts = {
        "sources": [
            {"topic": "land_title", "ref": "https://maharera.maharashtra.gov.in/x", "published_date": "2026-07-01", "accessed_date": "2026-07-01"},
            {"topic": "land_title", "ref": "https://www.icra.in/x", "published_date": "2026-07-01", "accessed_date": "2026-07-01"},
        ],
        "gaps": [],
        "_verification_stats": {},
    }
    authenticity_summary = cc._compute_authenticity_summary(facts)
    result = cc._compute_documentation_confidence_score(facts, authenticity_summary)

    assert result["overall"] >= cc._DATA_AUTHENTICITY_BANDS["High"], "sanity: the raw score really would have banded High without the cap"
    assert result["band"] == "Moderate", result
    assert result["verification_warning"] == "0 claims were independently re-checked this pass."
    print(f"test_zero_verification_caps_high_to_moderate: PASS (raw overall={result['overall']}, capped band={result['band']})")


def test_zero_verification_never_worsens_an_already_low_band():
    """The zero-verification cap only ever restrains a band that would
    otherwise be too generous (High) -- it must never push an
    already-Limited band down further, or force a Moderate one lower."""
    facts = {
        "sources": [],
        "gaps": ["FSI could not be confirmed", "pricing not reconciled", "unit count not confirmed", "built-up area not confirmed"],
        "_verification_stats": {},
    }
    authenticity_summary = cc._compute_authenticity_summary(facts)
    result = cc._compute_documentation_confidence_score(facts, authenticity_summary)

    assert result["band"] == "Limited", result  # unchanged by the cap
    assert result["verification_warning"] == "0 claims were independently re-checked this pass."
    print(f"test_zero_verification_never_worsens_an_already_low_band: PASS (band stays {result['band']})")


def test_thin_coverage_caps_high_to_moderate():
    """A facts dict deliberately made too sparse for MOST criteria to apply
    (no sources, no dated anything, no cross-corroboration possible) but
    WITH verification attempts present (so the zero-verification cap can't
    be the thing doing the capping) -- only verification_rate and
    financial_figures_confirmed end up applicable, both scoring perfectly.
    The raw renormalized score would band High; the thin-coverage safeguard
    must restrain it to Moderate and explain why via coverage_warning."""
    facts = {
        "sources": [],
        "gaps": [],
        "_verification_stats": {"url": {"attempted": 1, "confirmed": 1}},
    }
    authenticity_summary = cc._compute_authenticity_summary(facts)
    result = cc._compute_documentation_confidence_score(facts, authenticity_summary)

    assert len(result["criteria"]) == 2, result["criteria"]  # verification_rate + financial_figures_confirmed only
    assert "verification_warning" not in result, "this scenario has a real verification attempt -- must not also trip the other cap"
    assert result["overall"] >= cc._DATA_AUTHENTICITY_BANDS["High"], "sanity: the raw renormalized score really would have banded High"
    assert result["band"] == "Moderate", result
    assert "2 of 8" in result["coverage_warning"], result
    print(f"test_thin_coverage_caps_high_to_moderate: PASS (raw overall={result['overall']}, capped band={result['band']})")


def test_thin_coverage_never_worsens_an_already_low_band():
    """Same thin-coverage shape (only 2 of 8 criteria applicable), but both
    of those score poorly -- the band is already Limited on the raw numbers,
    and the safeguard must not push it any lower, only still surface the
    coverage_warning."""
    facts = {
        "sources": [],
        "gaps": ["FSI could not be confirmed", "pricing not reconciled"],
        "_verification_stats": {"url": {"attempted": 2, "confirmed": 0}},
    }
    authenticity_summary = cc._compute_authenticity_summary(facts)
    result = cc._compute_documentation_confidence_score(facts, authenticity_summary)

    assert len(result["criteria"]) == 3, result["criteria"]  # completeness_rate + verification_rate + financial_figures_confirmed
    assert "verification_warning" not in result
    assert result["band"] == "Limited", result  # unchanged by the cap
    assert "3 of 8" in result["coverage_warning"], result
    print(f"test_thin_coverage_never_worsens_an_already_low_band: PASS (band stays {result['band']})")


def test_widened_unit_count_marker_catches_real_discrepancy_gap():
    """Real gap sentence from a past Charter run (Bellagio Courtyards,
    P52100055794 -- output/*.facts.json, gitignored) that the ORIGINAL
    _FINANCIAL_FIGURE_MARKERS missed entirely: a live unit-count
    discrepancy between the authority's record and the promoter's own
    disclosure, phrased as "units"/"apartments" rather than "unit count/
    mix/breakdown". Before the widening this scored unit_counts as
    confirmed (no matching gap) for a project whose own gap text describes
    exactly the opposite -- a real scoring bug, not a hypothetical."""
    real_gap = (
        "The authority's record states 47 units and 3 sold, while the promoter's own inventory "
        "disclosure dated 20 April 2026 lists 87 apartments with 7 sold and 50 allotted. Both "
        "figures come from the same registration. Confirm which is current before relying on "
        "either for pricing or absorption analysis."
    )
    facts = {"sources": [], "gaps": [real_gap], "_verification_stats": {}}
    authenticity_summary = cc._compute_authenticity_summary(facts)
    result = cc._compute_documentation_confidence_score(facts, authenticity_summary)

    note = result["criteria"]["financial_figures_confirmed"]["note"]
    assert "unit_counts" in note, note
    print("test_widened_unit_count_marker_catches_real_discrepancy_gap: PASS")


def test_widened_fsi_and_land_area_markers_catch_real_karma_shine_gap():
    """Real gap sentence from a past Charter run (Karma Shine,
    KARMASHINE_MARKETYARD_411037 -- output/*.facts.json, gitignored) that
    the ORIGINAL markers missed for BOTH fsi and land_built_up_area: the
    project's own gap text says its development potential (floor space
    entitlement, buildable area) is entirely undetermined, phrased in
    terms neither original marker set matched -- both figures scored
    "confirmed" for a project openly stating the opposite."""
    real_gap = (
        "The project has no established scale. Plot area, buildable area, floor space entitlement, "
        "unit mix, unit count, launch pricing and completion timetable are all undetermined, and no "
        "indicative development value can be responsibly estimated."
    )
    facts = {"sources": [], "gaps": [real_gap], "_verification_stats": {}}
    authenticity_summary = cc._compute_authenticity_summary(facts)
    result = cc._compute_documentation_confidence_score(facts, authenticity_summary)

    note = result["criteria"]["financial_figures_confirmed"]["note"]
    assert "fsi" in note, note
    assert "land_built_up_area" in note, note
    print("test_widened_fsi_and_land_area_markers_catch_real_karma_shine_gap: PASS")


def test_criterion_labels_cover_every_real_key():
    """Guards against the exact staleness bug found while auditing this
    score: the rendered Documentation Confidence table's label dict used to
    live only as a local variable inside _append_authenticity_page, hardcoded
    with a stale singular "recency" key (pre-Step-5) and no
    "financial_figures_confirmed" entry at all -- so every real Charter
    rendered since the Step 5 rebalance silently dropped 3 of the 8 criteria
    rows instead of showing them, and nothing caught it because nothing
    could import the local var to check. Now promoted to the module-level
    _DOC_CONFIDENCE_CRITERION_LABELS specifically so this test can assert
    its key set directly against _DOC_CONFIDENCE_WEIGHTS's."""
    assert set(cc._DOC_CONFIDENCE_CRITERION_LABELS) == set(cc._DOC_CONFIDENCE_WEIGHTS), (
        set(cc._DOC_CONFIDENCE_WEIGHTS) - set(cc._DOC_CONFIDENCE_CRITERION_LABELS),
        set(cc._DOC_CONFIDENCE_CRITERION_LABELS) - set(cc._DOC_CONFIDENCE_WEIGHTS),
    )
    print("test_criterion_labels_cover_every_real_key: PASS")


if __name__ == "__main__":
    test_zero_verification_caps_high_to_moderate()
    test_zero_verification_never_worsens_an_already_low_band()
    test_thin_coverage_caps_high_to_moderate()
    test_thin_coverage_never_worsens_an_already_low_band()
    test_widened_unit_count_marker_catches_real_discrepancy_gap()
    test_widened_fsi_and_land_area_markers_catch_real_karma_shine_gap()
    test_criterion_labels_cover_every_real_key()
    print("\nAll tests passed.")

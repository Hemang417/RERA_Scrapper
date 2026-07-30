"""
Verifies company_charter._compute_developer_score() against the 3-bucket /
9-sub-metric AAA-D industry rubric it now implements:

  Operational Strength (50%): Team Strength, Influence in Micromarket
    (area within 5km), Past Experience - Area, Track Record -- 12.5% each.
  Financial Strength (20%): Financial Strength (debt structure) -- the
    whole bucket, one sub-metric.
  Governance Strength (30%): RERA Compliance, GST Compliance, Cases
    (Past Defaults), Entity Rating -- 7.5% each.

This replaced the earlier flat 7-criteria equal-weight-renormalized
composite. The key behavioral difference: each sub-metric's weight is
FIXED (12.5%/20%/7.5%) whether or not it actually scores this pass -- an
unscored sub-metric's weight is never redistributed to the others, and the
composite is never divided by only the available weight. So a promoter
with less publicly-verifiable data structurally scores lower, even when
everything that IS available is top-tier -- this is a deliberate design
choice (confirmed explicitly), not an oversight.

team_strength has no data source wired in at all today -- it always
resolves to None/N/A, a permanent gap, not a temporary one. rera_compliance
and gst_compliance both now score from real signals when the data is
available (completion-extension status + this project's own complaint/
appeal counts for the former; a human-supplied GST filing pattern for the
latter -- see run_gst_compliance_check, gated on output/<reg_no>/
gst_filing_input.json since the GST portal's own CAPTCHA makes live
scraping unautomatable), staying honestly unscored (not a permanent gap)
when that data wasn't supplied this pass -- see _score_rera_compliance/
_score_gst_compliance and test_rera_compliance_scores_when_data_
available_else_unscored / test_gst_compliance_scores_when_data_
available_else_unscored.

Run directly: python test_developer_score.py
"""

import json
import os

import company_charter as cc

_GPG_FACTS_PATH = os.path.join("output", "company_charters", "Company_Charter_GodrejParkGreens_P52100019639.facts.json")
_GPG_PORTFOLIO_PATH = os.path.join("output", "P52100019639", "promoter", "portfolio.json")
_PRANAMI_FACTS_PATH = os.path.join("output", "company_charters", "Company_Charter_PranamiBliss_P51800077150.facts.json")

_ALL_SUBMETRICS = {
    "team_strength", "area_within_5km", "past_area_developed", "track_record_years",
    "financial_strength_debt",
    "rera_compliance", "gst_compliance", "past_default_count", "entity_rating",
}

_EXPECTED_WEIGHTS = {
    "team_strength": 12.5, "area_within_5km": 12.5, "past_area_developed": 12.5, "track_record_years": 12.5,
    "financial_strength_debt": 20.0,
    "rera_compliance": 7.5, "gst_compliance": 7.5, "past_default_count": 7.5, "entity_rating": 7.5,
}


def _load_gpg_facts() -> dict:
    with open(_GPG_FACTS_PATH, encoding="utf-8") as f:
        facts = json.load(f)
    with open(_GPG_PORTFOLIO_PATH, encoding="utf-8") as f:
        facts["promoter_portfolio"] = json.load(f)
    return facts


def test_all_nine_submetrics_always_named_with_fixed_weight():
    """Regardless of input -- even an empty facts dict -- all nine
    sub-metrics must appear in the result, each carrying its FIXED
    structural weight (12.5%/20%/7.5%) even though none of them scored."""
    result = cc._compute_developer_score({}, {"imminent": [], "structural": [], "monitor": []})
    assert set(result["criteria"]) == _ALL_SUBMETRICS, set(result["criteria"])
    for name, criterion in result["criteria"].items():
        assert criterion["score"] is None and criterion["tier"] is None, f"{name} should be unscored on empty facts: {criterion}"
        assert criterion.get("reason"), f"{name} must carry an explicit reason when unscored"
        assert criterion["weight"] == _EXPECTED_WEIGHTS[name], f"{name} weight should stay fixed at {_EXPECTED_WEIGHTS[name]} even when unscored, got {criterion['weight']}"
    assert result["composite"] == 0.0, "an entirely-unscored facts dict must yield a 0 composite, not None/error"
    print("test_all_nine_submetrics_always_named_with_fixed_weight: PASS")


def test_bucket_weights_sum_correctly():
    """Operational (4x12.5=50) + Financial (20) + Governance (4x7.5=30)
    must sum to exactly 100 -- a structural invariant of the rubric."""
    total = sum(_EXPECTED_WEIGHTS.values())
    assert abs(total - 100.0) < 0.01, total
    print("test_bucket_weights_sum_correctly: PASS")


def test_team_strength_and_financial_debt_are_permanent_gaps():
    """team_strength has no public data source at all -- must stay None
    even when other criteria (including a hypothetical
    financial_strength_points input) resolve. financial_strength_debt
    should resolve once its point value IS present, unlike team_strength
    which never can."""
    facts = {
        "corporate_identity": {"organization_type": {"value": "Private Limited Company"}},
        "developer_track_record": {"financial_strength_points": 5},
    }
    flags = {"imminent": [], "structural": [], "monitor": []}
    result = cc._compute_developer_score(facts, flags)

    assert result["criteria"]["team_strength"]["score"] is None, "team_strength must never resolve -- no public source exists"
    assert result["criteria"]["financial_strength_debt"]["tier"] == "AAA", result["criteria"]["financial_strength_debt"]
    print("test_team_strength_and_financial_debt_are_permanent_gaps: PASS")


def test_gst_compliance_scores_when_data_available_else_unscored():
    """GST Compliance is no longer an unconditional gap: it scores a
    "compliance friction" band from a human-supplied filing pattern (see
    run_gst_compliance_check/gst_compliance.summarize_filing_pattern), but
    stays honestly unscored -- a pending-input gap, not a permanent one --
    when no gst_compliance_check was ever populated (the ordinary case:
    no output/<reg_no>/gst_filing_input.json was dropped in)."""
    unscored = cc._score_gst_compliance({})
    assert unscored["score"] is None, unscored
    assert "gst_filing_input.json" in unscored["reason"] or "no gst filing data" in unscored["reason"].lower(), unscored

    clean = cc._score_gst_compliance({
        "gst_compliance_check": {
            "found": True, "gstin": "27AANCM5273D1ZA",
            "summary": {"total_periods": 4, "filed": 4, "on_time": 4, "late": 0, "late_pct": 0.0, "worst_delay_days": None, "delays_last_12_months": 0},
        },
    })
    assert clean["tier"] == "AAA", clean

    one_late = cc._score_gst_compliance({
        "gst_compliance_check": {
            "found": True, "gstin": "27AANCM5273D1ZA",
            "summary": {"total_periods": 4, "filed": 4, "on_time": 3, "late": 1, "late_pct": 25.0, "worst_delay_days": 5, "delays_last_12_months": 1},
        },
    })
    assert one_late["tier"] == "A", one_late  # 30 (late_pct>15) points, nothing else crosses a breakpoint

    heavy = cc._score_gst_compliance({
        "gst_compliance_check": {
            "found": True, "gstin": "27AANCM5273D1ZA",
            "summary": {"total_periods": 8, "filed": 8, "on_time": 3, "late": 5, "late_pct": 62.5, "worst_delay_days": 90, "delays_last_12_months": 5},
        },
    })
    assert heavy["tier"] == "D", heavy  # 45 (late_pct>40) + 30 (worst_delay>60) + 45 (delays_recent>3) = 120 points
    print("test_gst_compliance_scores_when_data_available_else_unscored: PASS")


def test_rera_compliance_scores_when_data_available_else_unscored():
    """RERA Compliance is no longer an unconditional gap: it scores a
    "compliance friction" band from completion-extension status plus this
    project's own complaint/appeal counts, but stays honestly unscored
    when those counts aren't confidently parseable (an empty facts dict,
    or one missing rera_core_fields entirely)."""
    unscored = cc._score_rera_compliance({})
    assert unscored["score"] is None, unscored
    assert "not confidently parseable" in unscored["reason"].lower(), unscored

    clean = cc._score_rera_compliance({
        "rera_core_fields": {
            "total_complaints_count": 0, "total_appeals_count": 0,
            "completion_date_current": "2027-01-01", "completion_date_original": "2027-01-01",
        },
    })
    assert clean["tier"] == "AAA", clean

    one_complaint = cc._score_rera_compliance({
        "rera_core_fields": {
            "total_complaints_count": 3, "total_appeals_count": 0,
            "completion_date_current": "2027-01-01", "completion_date_original": "2027-01-01",
        },
    })
    assert one_complaint["tier"] == "AA", one_complaint  # 15 friction points -- within the 1-15 complaint band, no extension

    extended_and_heavy = cc._score_rera_compliance({
        "rera_core_fields": {
            "total_complaints_count": 50, "total_appeals_count": 20,
            "completion_date_current": "2029-01-01", "completion_date_original": "2027-01-01",
        },
    })
    assert extended_and_heavy["tier"] == "D", extended_and_heavy  # 25 (extended) + 45 (>40 complaints) + 45 (>15 appeals) = 115 points
    print("test_rera_compliance_scores_when_data_available_else_unscored: PASS")


def test_entity_rating_bands():
    """Pvt Ltd / Ltd -> AAA outright. LLP / Partnership -> conservatively
    D, since "willingness to convert to Pvt Ltd" can't be independently
    verified from public filings -- never credited without confirmation."""
    def _rating_for(org_type_value):
        facts = {"corporate_identity": {"organization_type": {"value": org_type_value}}}
        return cc._score_entity_rating(facts)

    assert _rating_for("Private Limited Company")["tier"] == "AAA"
    assert _rating_for("Public Limited Company (converted from Private Limited)")["tier"] == "AAA"
    assert _rating_for("Limited Liability Partnership (LLP)")["tier"] == "D"
    assert _rating_for("Partnership Firm")["tier"] == "D"
    assert _rating_for("")["score"] is None
    print("test_entity_rating_bands: PASS")


def test_area_band_thresholds():
    """Spot-check the area-based bands (Influence in Micromarket / Past
    Experience - Area) at a few real threshold points, since they use
    different cutoffs from each other."""
    def _past_area(value):
        return cc._score_past_area_developed({"promoter_portfolio": {"totals": {"total_area_developed_lakh_sqft": value}}})

    def _area_5km(value):
        return cc._score_area_within_5km({"promoter_portfolio": {"totals": {"area_within_5km_lakh_sqft": value}}})

    assert _past_area(150)["tier"] == "AAA"
    assert _past_area(100)["tier"] == "AA"
    assert _past_area(3)["tier"] == "D"
    assert _area_5km(60)["tier"] == "AAA"
    assert _area_5km(21)["tier"] == "AA"
    assert _area_5km(0.5)["tier"] == "D"
    print("test_area_band_thresholds: PASS")


def test_past_default_count_from_clean_ibbi():
    """A clean IBBI record (no insolvency process) with no credit rating
    at all scores 0 defaults -- AAA. An IBBI hit leaves it unscored rather
    than guessing a count from raw text."""
    clean = cc._score_past_default_count({"ibbi_insolvency_check": {"found_process": False}})
    assert clean["tier"] == "AAA", clean

    not_checked = cc._score_past_default_count({})
    assert not_checked["score"] is None, not_checked

    hit = cc._score_past_default_count({"ibbi_insolvency_check": {"found_process": True}})
    assert hit["score"] is None, hit
    print("test_past_default_count_from_clean_ibbi: PASS")


def test_godrej_park_greens_real_fixture():
    """GPG's real, live-refreshed data (promoter_portfolio.json rebuilt via
    an actual `python main.py P52100019639` run). Four sub-metrics have
    real data: Entity Rating (Public Limited -> AAA, 7.5% weight -> 7.5
    contribution), Cases/Past Defaults (clean IBBI, no rating found -> AAA,
    7.5% -> 7.5), Past Experience - Area (~24.54 lakh sq ft -> B, 12.5% ->
    6.25), and Influence in Micromarket (~3.54 lakh sq ft -> B, 12.5% ->
    6.25). The other five stay honestly N/A (no team-strength/debt-ratio
    source exists; RERA/GST-TDS Compliance not yet built; Track Record
    needs a deep-research years-in-industry figure not populated on this
    fixture). Composite = 7.5+7.5+6.25+6.25 = 27.5 -- NOT the 75.0 the old
    equal-weight-renormalized formula gave, since unscored weight (72.5%
    of the total) is no longer redistributed to the four that DID score.
    GPG carries real imminent flags (67 complaints, 18 appeals, FSI gap,
    near-sellout+delay) -- 27.5 alone already bands below A, so the hard
    cap is not what's restraining the grade here; the composite itself
    does. If promoter_portfolio.json is refreshed again, update these
    specific numbers to match -- that's tracking real, live data, not a
    regression.

    NOTE: this fixture's facts.json was deleted in an output-folder wipe
    and hasn't been rebuilt (this test currently fails on FileNotFoundError,
    a known, pre-existing, unrelated gap). When it IS rebuilt, rera_compliance
    will also resolve now (see _score_rera_compliance) -- GPG's own 67
    complaints/18 appeals almost certainly cross the imminent threshold, so
    expect a D there and re-verify every assertion below (including scored_names
    and composite) against the real rebuilt data rather than assuming these
    numbers still hold."""
    facts = _load_gpg_facts()
    flags = cc._classify_flags(facts)
    assert flags["imminent"], "fixture must have imminent flags for this test's premise to hold"

    result = cc._compute_developer_score(facts, flags)
    criteria = result["criteria"]

    assert set(criteria) == _ALL_SUBMETRICS
    scored_names = {name for name, c in criteria.items() if c["score"] is not None}
    assert scored_names == {"entity_rating", "past_default_count", "past_area_developed", "area_within_5km"}, scored_names

    assert criteria["entity_rating"]["tier"] == "AAA", criteria["entity_rating"]
    assert criteria["past_default_count"]["tier"] == "AAA", criteria["past_default_count"]
    assert criteria["past_area_developed"]["tier"] == "B", criteria["past_area_developed"]
    assert criteria["area_within_5km"]["tier"] == "B", criteria["area_within_5km"]
    assert result["composite"] == 27.5, result["composite"]

    for name in scored_names:
        assert criteria[name]["weight"] == _EXPECTED_WEIGHTS[name], (name, criteria[name])

    print("test_godrej_park_greens_real_fixture: PASS")
    print(f"  composite={result['composite']} grade={result['grade']}")


def test_pranami_bliss_real_fixture():
    """Pranami Bliss: five sub-metrics resolve -- Entity Rating (AAA,
    7.5%), Cases/Past Defaults (AAA, 7.5%), Track Record (AAA, 24 years --
    sourced to Pranami Group's own "founded 2002" claim for this SPV's
    parent group, 12.5%), RERA Compliance (AAA, 7.5% -- 0 compliance-
    friction points: never extended, 0 complaints, 0 appeals on record for
    this project), and Past Experience - Area (D, 12.5% -- 0.59 lakh sq ft,
    the promoter's single declared prior delivery, "Mall of Ranchi").
    Composite = 7.5+7.5+12.5+7.5 + (12.5 x 16.7/100) = 37.1.

    Past Experience - Area only started resolving once the past-experience
    entry-identity bug was fixed (see test_promoter_portfolio's
    test_single_project_spv_keeps_its_genuine_prior_delivery): this promoter
    is a single-project SPV, so the old "exclude anything fetched under the
    subject's own registration" rule discarded its one genuine prior
    delivery's area entirely.

    area_within_5km stays unscored, and honestly so: this project's raw
    partners.json is empty, so the SUBJECT's own locality never geocodes,
    and without a subject coordinate there is nothing to measure 5km from.

    Pranami Bliss also carries its own imminent flag (the FSI/BUA gap), but
    37.1 already bands well below A on the composite alone, so the hard cap
    isn't what's active here either."""
    with open(_PRANAMI_FACTS_PATH, encoding="utf-8") as f:
        facts = json.load(f)
    flags = cc._classify_flags(facts)
    assert flags["imminent"], "fixture must have imminent flags for this test's premise to hold"

    result = cc._compute_developer_score(facts, flags)
    criteria = result["criteria"]
    scored_names = {name for name, c in criteria.items() if c["score"] is not None}
    assert scored_names == {
        "entity_rating", "past_default_count", "track_record_years", "rera_compliance", "past_area_developed",
    }, scored_names
    assert criteria["rera_compliance"]["tier"] == "AAA", criteria["rera_compliance"]
    assert criteria["past_area_developed"]["tier"] == "D", criteria["past_area_developed"]
    assert criteria["area_within_5km"]["score"] is None, criteria["area_within_5km"]
    assert result["composite"] == 37.1, result["composite"]

    print("test_pranami_bliss_real_fixture: PASS")
    print(f"  composite={result['composite']} grade={result['grade']}")


def test_max_achievable_grade_today_is_aa_not_capped():
    """team_strength (12.5%) is a permanent gap (no data source at all),
    and gst_compliance (7.5%) stays unscored here simply because
    facts_strong supplies no gst_compliance_check -- both are the only
    zero-weight sub-metrics for THIS fixture, so a facts dict maxed out on
    every OTHER sub-metric, including rera_compliance (now scoreable --
    see _score_rera_compliance) and gst_compliance when data IS supplied
    (see test_gst_compliance_scores_when_data_available_else_unscored),
    reaches an 80.0 composite (100 - 12.5 - 7.5), which bands to "AA" (AA
    >= 75.0, AAA >= 91.65) -- AAA remains structurally unreachable with
    today's built criteria (team_strength has no source at all), but AA is
    now reachable where it wasn't before RERA/GST Compliance were wired
    in. This documents that ceiling honestly rather than silently assuming
    the hard cap is what's restraining every real project's grade."""
    no_flags = {"imminent": [], "structural": [], "monitor": []}
    facts_strong = {
        "corporate_identity": {"organization_type": {"value": "Private Limited Company"}},
        "ibbi_insolvency_check": {"found_process": False},
        "developer_track_record": {"years_in_industry": 25, "financial_strength_points": 5},
        "promoter_portfolio": {"totals": {"total_area_developed_lakh_sqft": 150, "area_within_5km_lakh_sqft": 60}},
        "rera_core_fields": {
            "total_complaints_count": 0, "total_appeals_count": 0,
            "completion_date_current": "2027-01-01", "completion_date_original": "2027-01-01",
        },
    }
    result = cc._compute_developer_score(facts_strong, no_flags)
    assert result["composite"] == 80.0, result["composite"]
    assert result["grade"] == "AA", result["grade"]
    print("test_max_achievable_grade_today_is_aa_not_capped: PASS")


def test_hard_cap_restrains_but_never_worsens():
    """A composite that would otherwise band to AAA/AA must be capped to A
    when imminent flags exist -- but a composite already at A or below
    must be left alone, never pushed down further by the same cap.

    Since team_strength has no data source at all, and this test's own
    facts_strong fixture supplies no gst_compliance_check either, a facts
    dict built from THIS fixture can never naturally drive the composite
    above 80.0 (see test_max_achievable_grade_today_is_aa_not_capped) -- so
    this test temporarily monkeypatches _DEVELOPER_SCORE_STRUCTURE to stub
    every sub-metric as a guaranteed AAA, to prove the cap mechanism itself
    still fires correctly for the day team_strength gets a real data
    source and a genuine AAA/AA composite becomes reachable without this
    workaround."""
    imminent_flags = {"imminent": [{"text": "test imminent flag", "field": "test"}], "structural": [], "monitor": []}
    no_flags = {"imminent": [], "structural": [], "monitor": []}

    def _fake_aaa(facts):
        return {"score": 100.0, "tier": "AAA", "note": "test stub"}

    original_structure = cc._DEVELOPER_SCORE_STRUCTURE
    cc._DEVELOPER_SCORE_STRUCTURE = tuple(
        (bucket_name, bucket_weight, tuple((key, name, _fake_aaa) for key, name, _fn in metrics))
        for bucket_name, bucket_weight, metrics in original_structure
    )
    try:
        capped = cc._compute_developer_score({}, imminent_flags)
        uncapped = cc._compute_developer_score({}, no_flags)
        assert uncapped["grade"] in ("AAA", "AA"), uncapped  # sanity: the uncapped case really would have graded well
        assert capped["grade"] == "A", capped
        assert capped["composite"] == uncapped["composite"] == 100.0, "the cap changes the grade label, never the composite itself"
    finally:
        cc._DEVELOPER_SCORE_STRUCTURE = original_structure

    facts_weak = {
        "corporate_identity": {"organization_type": {"value": "Partnership Firm"}},
        "ibbi_insolvency_check": {"found_process": True},
    }
    already_weak = cc._compute_developer_score(facts_weak, imminent_flags)
    assert already_weak["grade"] not in ("AAA", "AA"), already_weak  # cap must not be needed here, but must not misfire either

    print("test_hard_cap_restrains_but_never_worsens: PASS")


if __name__ == "__main__":
    test_all_nine_submetrics_always_named_with_fixed_weight()
    test_bucket_weights_sum_correctly()
    test_team_strength_and_financial_debt_are_permanent_gaps()
    test_gst_compliance_scores_when_data_available_else_unscored()
    test_rera_compliance_scores_when_data_available_else_unscored()
    test_entity_rating_bands()
    test_area_band_thresholds()
    test_past_default_count_from_clean_ibbi()
    test_godrej_park_greens_real_fixture()
    test_pranami_bliss_real_fixture()
    test_max_achievable_grade_today_is_aa_not_capped()
    test_hard_cap_restrains_but_never_worsens()
    print("\nAll tests passed.")

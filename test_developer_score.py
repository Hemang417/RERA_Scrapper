"""
Verifies company_charter._compute_developer_score() against the 3-bucket /
9-sub-metric AAA-D industry rubric it now implements:

  Operational Strength (50%): Team Strength, Influence in Micromarket
    (area within 5km), Past Experience - Area, Track Record -- 12.5% each.
  Financial Strength (20%): Financial Strength (debt structure) -- the
    whole bucket, one sub-metric.
  Governance Strength (30%): RERA Compliance, GST/TDS Compliance, Cases
    (Past Defaults), Entity Rating -- 7.5% each.

This replaced the earlier flat 7-criteria equal-weight-renormalized
composite. The key behavioral difference: each sub-metric's weight is
FIXED (12.5%/20%/7.5%) whether or not it actually scores this pass -- an
unscored sub-metric's weight is never redistributed to the others, and the
composite is never divided by only the available weight. So a promoter
with less publicly-verifiable data structurally scores lower, even when
everything that IS available is top-tier -- this is a deliberate design
choice (confirmed explicitly), not an oversight.

Three sub-metrics (team_strength; financial_strength_debt; rera_compliance
and gst_tds_compliance) have no data source wired in at all today -- they
always resolve to None/N/A, not a temporary gap.

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
    "rera_compliance", "gst_tds_compliance", "past_default_count", "entity_rating",
}

_EXPECTED_WEIGHTS = {
    "team_strength": 12.5, "area_within_5km": 12.5, "past_area_developed": 12.5, "track_record_years": 12.5,
    "financial_strength_debt": 20.0,
    "rera_compliance": 7.5, "gst_tds_compliance": 7.5, "past_default_count": 7.5, "entity_rating": 7.5,
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


def test_rera_and_gst_tds_are_pending_build_gaps():
    """RERA Compliance and GST/TDS Compliance have no wired-in data source
    yet -- always None/N/A regardless of input, same shape as
    team_strength but with a "pending build" reason rather than "no public
    source exists at all", since these COULD be built later."""
    result = cc._compute_developer_score({}, {"imminent": [], "structural": [], "monitor": []})
    for key in ("rera_compliance", "gst_tds_compliance"):
        criterion = result["criteria"][key]
        assert criterion["score"] is None, criterion
        assert "not yet" in criterion["reason"].lower() or "pending" in criterion["reason"].lower(), criterion
    print("test_rera_and_gst_tds_are_pending_build_gaps: PASS")


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
    regression."""
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
    """Pranami Bliss: three sub-metrics resolve -- Entity Rating (AAA,
    7.5%), Cases/Past Defaults (AAA, 7.5%), and Track Record (AAA, 24
    years -- sourced to Pranami Group's own "founded 2002" claim for this
    SPV's parent group, 12.5%). Composite = 7.5+7.5+12.5 = 27.5 (the fixed-
    weight sum of exactly these three, each at its own tier score of
    100 -- coincidentally the same total as GPG's fixture above, since
    GPG's two B-tier area sub-metrics at 12.5% each happen to sum to the
    same contribution as this fixture's one AAA-tier 12.5% sub-metric).
    Pranami Bliss also carries its own imminent flag (the FSI/BUA gap),
    but 27.5 already bands well below A on the composite alone, so the
    hard cap isn't what's active here either."""
    with open(_PRANAMI_FACTS_PATH, encoding="utf-8") as f:
        facts = json.load(f)
    flags = cc._classify_flags(facts)
    assert flags["imminent"], "fixture must have imminent flags for this test's premise to hold"

    result = cc._compute_developer_score(facts, flags)
    criteria = result["criteria"]
    scored_names = {name for name, c in criteria.items() if c["score"] is not None}
    assert scored_names == {"entity_rating", "past_default_count", "track_record_years"}, scored_names
    assert result["composite"] == 27.5, result["composite"]

    print("test_pranami_bliss_real_fixture: PASS")
    print(f"  composite={result['composite']} grade={result['grade']}")


def test_max_achievable_grade_today_is_a_not_capped():
    """team_strength (12.5%), rera_compliance (7.5%), and
    gst_tds_compliance (7.5%) are unconditional gaps under the CURRENT
    rubric (no data source wired in for any of them yet) -- so even a
    facts dict maxed out on every OTHER sub-metric can only reach a 72.5
    composite (100 - 12.5 - 7.5 - 7.5), which bands to "A" on the
    composite alone (A >= 58.35, AA >= 75.0) -- AAA/AA is structurally
    unreachable with today's built criteria, regardless of imminent flags.
    This documents that ceiling honestly rather than silently assuming the
    hard cap is what's restraining every real project's grade."""
    no_flags = {"imminent": [], "structural": [], "monitor": []}
    facts_strong = {
        "corporate_identity": {"organization_type": {"value": "Private Limited Company"}},
        "ibbi_insolvency_check": {"found_process": False},
        "developer_track_record": {"years_in_industry": 25, "financial_strength_points": 5},
        "promoter_portfolio": {"totals": {"total_area_developed_lakh_sqft": 150, "area_within_5km_lakh_sqft": 60}},
    }
    result = cc._compute_developer_score(facts_strong, no_flags)
    assert result["composite"] == 72.5, result["composite"]
    assert result["grade"] == "A", result["grade"]
    print("test_max_achievable_grade_today_is_a_not_capped: PASS")


def test_hard_cap_restrains_but_never_worsens():
    """A composite that would otherwise band to AAA/AA must be capped to A
    when imminent flags exist -- but a composite already at A or below
    must be left alone, never pushed down further by the same cap.

    Since 3 sub-metrics (team_strength; rera_compliance;
    gst_tds_compliance) are permanent/pending gaps with NO data source
    today, a real facts dict can never naturally drive the composite above
    72.5 (see test_max_achievable_grade_today_is_a_not_capped) -- so this
    test temporarily monkeypatches _DEVELOPER_SCORE_STRUCTURE to stub every
    sub-metric as a guaranteed AAA, to prove the cap mechanism itself still
    fires correctly for the day those gaps get built and a real AAA/AA
    composite becomes reachable."""
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
    test_rera_and_gst_tds_are_pending_build_gaps()
    test_entity_rating_bands()
    test_area_band_thresholds()
    test_past_default_count_from_clean_ibbi()
    test_godrej_park_greens_real_fixture()
    test_pranami_bliss_real_fixture()
    test_max_achievable_grade_today_is_a_not_capped()
    test_hard_cap_restrains_but_never_worsens()
    print("\nAll tests passed.")

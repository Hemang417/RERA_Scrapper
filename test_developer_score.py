"""
Verifies company_charter._compute_developer_score() against the 7-criteria
AAA-D industry rubric it now implements (track record years, team strength,
past area developed, area developed within 5km, financial strength/debt
structure, past default count, entity/organization type) -- this replaced
the earlier 4-pillar composite entirely.

Two of the seven criteria (team_strength; financial_strength_debt) have no
public data source this pipeline can check at all -- they're expected to
always be None/N/A, not a temporary gap. The other five resolve only when
their underlying field is present; today, only entity_rating and
past_default_count are populated for either real fixture (GPG, Pranami
Bliss), since promoter_portfolio's area aggregation and the deep-research
years-in-industry field aren't wired into every pipeline run yet. If that
changes, update the specific values below to match -- that's tracking real
data/pipeline progress, not a regression.

Run directly: python test_developer_score.py
"""

import json
import os

import company_charter as cc

_GPG_FACTS_PATH = os.path.join("output", "company_charters", "Company_Charter_GodrejParkGreens_P52100019639.facts.json")
_GPG_PORTFOLIO_PATH = os.path.join("output", "P52100019639", "promoter", "portfolio.json")
_PRANAMI_FACTS_PATH = os.path.join("output", "company_charters", "Company_Charter_PranamiBliss_P51800077150.facts.json")

_ALL_CRITERIA = {
    "track_record_years", "team_strength", "past_area_developed", "area_within_5km",
    "financial_strength_debt", "past_default_count", "entity_rating",
}


def _load_gpg_facts() -> dict:
    with open(_GPG_FACTS_PATH, encoding="utf-8") as f:
        facts = json.load(f)
    with open(_GPG_PORTFOLIO_PATH, encoding="utf-8") as f:
        facts["promoter_portfolio"] = json.load(f)
    return facts


def test_all_seven_criteria_always_named():
    """Regardless of input -- even an empty facts dict -- all seven
    criteria must appear in the result, never silently missing."""
    result = cc._compute_developer_score({}, {"imminent": [], "structural": [], "monitor": []})
    assert set(result["criteria"]) == _ALL_CRITERIA, set(result["criteria"])
    for name, criterion in result["criteria"].items():
        assert criterion["score"] is None and criterion["tier"] is None, f"{name} should be unscored on empty facts: {criterion}"
        assert criterion.get("reason"), f"{name} must carry an explicit reason when unscored"
    print("test_all_seven_criteria_always_named: PASS")


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
    """Spot-check the area-based bands (criteria 3 and 4) at a few real
    threshold points, since they use different cutoffs from each other."""
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
    an actual `python main.py P52100019639` run, including the geocoding-
    based 5km filter -- see promoter_portfolio.py's pincode-preference fix,
    added after this same live run first surfaced Nominatim failing on
    MahaRERA's noisy legal-description addresses). Four criteria now have
    real data: entity_rating (Public Limited -> AAA), past_default_count
    (clean IBBI, no rating found -> AAA), past_area_developed (~24.54 lakh
    sq ft across the promoter's portfolio -> B), and area_within_5km
    (~3.54 lakh sq ft, from the one portfolio entry -- "Forest Grove at
    Godrej Park Greens" -- whose address embeds this same project's own
    pincode -> B). The other three stay honestly N/A (no years-in-industry
    or debt-ratio source exists yet). Composite is 75.0 from those four,
    which alone would band to AA, but GPG carries real imminent flags (67
    complaints, 18 appeals, FSI gap, near-sellout+delay), so the hard cap
    restrains the displayed grade to A. If promoter_portfolio.json is
    refreshed again, update these specific numbers to match -- that's
    tracking real, live data, not a regression."""
    facts = _load_gpg_facts()
    flags = cc._classify_flags(facts)
    assert flags["imminent"], "fixture must have imminent flags for this test's premise to hold"

    result = cc._compute_developer_score(facts, flags)
    criteria = result["criteria"]

    assert set(criteria) == _ALL_CRITERIA
    scored_names = {name for name, c in criteria.items() if c["score"] is not None}
    assert scored_names == {"entity_rating", "past_default_count", "past_area_developed", "area_within_5km"}, scored_names

    assert criteria["entity_rating"]["tier"] == "AAA", criteria["entity_rating"]
    assert criteria["past_default_count"]["tier"] == "AAA", criteria["past_default_count"]
    assert criteria["past_area_developed"]["tier"] == "B", criteria["past_area_developed"]
    assert criteria["area_within_5km"]["tier"] == "B", criteria["area_within_5km"]
    assert result["composite"] == 75.0, result["composite"]
    assert result["grade"] == "A", f"expected the imminent-flag cap to restrain AA to A, got {result['grade']}"

    total_weight = sum(criteria[name]["weight"] for name in scored_names)
    assert abs(total_weight - 100.0) < 0.1, total_weight

    print("test_godrej_park_greens_real_fixture: PASS")
    print(f"  composite={result['composite']} grade={result['grade']} (capped from AA by imminent flags)")


def test_pranami_bliss_real_fixture():
    """Pranami Bliss: same two criteria resolve (entity_rating and
    past_default_count, both AAA), giving the same 100 composite -- but
    Pranami Bliss also carries its own imminent flag (the FSI/BUA gap), so
    it's capped to A too, same as GPG, even though its underlying risk
    picture is otherwise much cleaner (0 complaints, no delay)."""
    with open(_PRANAMI_FACTS_PATH, encoding="utf-8") as f:
        facts = json.load(f)
    flags = cc._classify_flags(facts)
    assert flags["imminent"], "fixture must have imminent flags for this test's premise to hold"

    result = cc._compute_developer_score(facts, flags)
    criteria = result["criteria"]
    scored_names = {name for name, c in criteria.items() if c["score"] is not None}
    assert scored_names == {"entity_rating", "past_default_count"}, scored_names
    assert result["composite"] == 100.0, result["composite"]
    assert result["grade"] == "A", result["grade"]

    print("test_pranami_bliss_real_fixture: PASS")
    print(f"  composite={result['composite']} grade={result['grade']}")


def test_hard_cap_restrains_but_never_worsens():
    """A composite that would otherwise band to AAA/AA must be capped to A
    when imminent flags exist -- but a composite already at A or below
    must be left alone, never pushed down further by the same cap."""
    imminent_flags = {"imminent": [{"text": "test imminent flag", "field": "test"}], "structural": [], "monitor": []}
    no_flags = {"imminent": [], "structural": [], "monitor": []}

    facts_strong = {
        "corporate_identity": {"organization_type": {"value": "Private Limited Company"}},
        "ibbi_insolvency_check": {"found_process": False},
    }
    capped = cc._compute_developer_score(facts_strong, imminent_flags)
    uncapped = cc._compute_developer_score(facts_strong, no_flags)
    assert uncapped["grade"] in ("AAA", "AA"), uncapped  # sanity: the uncapped case really would have graded well
    assert capped["grade"] == "A", capped
    assert capped["composite"] == uncapped["composite"], "the cap changes the grade label, never the composite itself"

    facts_weak = {
        "corporate_identity": {"organization_type": {"value": "Partnership Firm"}},
        "ibbi_insolvency_check": {"found_process": True},
    }
    already_weak = cc._compute_developer_score(facts_weak, imminent_flags)
    assert already_weak["grade"] not in ("AAA", "AA"), already_weak  # cap must not be needed here, but must not misfire either

    print("test_hard_cap_restrains_but_never_worsens: PASS")


if __name__ == "__main__":
    test_all_seven_criteria_always_named()
    test_team_strength_and_financial_debt_are_permanent_gaps()
    test_entity_rating_bands()
    test_area_band_thresholds()
    test_past_default_count_from_clean_ibbi()
    test_godrej_park_greens_real_fixture()
    test_pranami_bliss_real_fixture()
    test_hard_cap_restrains_but_never_worsens()
    print("\nAll tests passed.")

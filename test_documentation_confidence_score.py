"""
Verifies company_charter._compute_documentation_confidence_score() after the
Step 5 rebalance: rebalanced weights, recency split into a 3-month legal
window vs. an 18-month "other" window, the new financial_figures_confirmed
criterion, and the zero-verification band cap.

Run directly: python test_documentation_confidence_score.py
"""

import json
import os

import company_charter as cc

_FACTS_PATH = os.path.join("output", "company_charters", "Company_Charter_GodrejParkGreens_P52100019639.facts.json")


def test_godrej_park_greens_still_lands_moderate():
    """Regression check: after the rebalance, the real GPG fixture (whose
    verification_rate had 11 attempted claims, 90.9% confirmed -- nowhere
    near zero) must still land in the Moderate band, and must NOT trigger
    the zero-verification cap or carry a verification_warning."""
    with open(_FACTS_PATH, encoding="utf-8") as f:
        facts = json.load(f)

    authenticity_summary = cc._compute_authenticity_summary(facts)
    result = cc._compute_documentation_confidence_score(facts, authenticity_summary)

    assert result["band"] == "Moderate", result
    assert "verification_warning" not in result, result
    assert result["criteria"]["verification_rate"]["score"] > 0, "sanity: this fixture has real, non-zero verification attempts"

    # financial_figures_confirmed must always be present (never skipped --
    # facts["gaps"] is always at least an empty list) and, for this
    # fixture, correctly finds all four core figures still open as gaps.
    ffc = result["criteria"]["financial_figures_confirmed"]
    assert ffc["score"] == 0.0, ffc
    assert "financial_figures_confirmed" not in result["skipped_criteria"]

    # recency_legal must use the tighter 3-month window in its own note text.
    if "recency_legal" in result["criteria"]:
        assert "3-month" in result["criteria"]["recency_legal"]["note"]

    # Weights actually used must still renormalize to 100% across whatever
    # criteria this fixture produced.
    total_weight = sum(c["weight"] for c in result["criteria"].values())
    assert abs(total_weight - 100.0) < 0.2, total_weight

    print(f"test_godrej_park_greens_still_lands_moderate: PASS (overall={result['overall']}, band={result['band']})")


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


if __name__ == "__main__":
    test_godrej_park_greens_still_lands_moderate()
    test_zero_verification_caps_high_to_moderate()
    test_zero_verification_never_worsens_an_already_low_band()
    print("\nAll tests passed.")

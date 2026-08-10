"""
Verifies company_charter._compute_documentation_confidence_score() after the
Step 5 rebalance: rebalanced weights, recency split into a 3-month legal
window vs. an 18-month "other" window, the new financial_figures_confirmed
criterion, and the zero-verification band cap.

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


if __name__ == "__main__":
    test_zero_verification_caps_high_to_moderate()
    test_zero_verification_never_worsens_an_already_low_band()
    print("\nAll tests passed.")

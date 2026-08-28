"""
Guards on the CRISIL lookup and the group-wide credit-rating check.

WHY BOTH EXIST, and it is one root cause seen twice: THE RATED ENTITY IS
USUALLY NOT THE SUBJECT, AND THE BIGGEST AGENCY WAS NOT BEING ASKED.

For a real subject the structured check reported "no public rating found
from any agency checked (ICRA, Infomerics)". Two things were wrong with
that one sentence:

  * CRISIL, India's largest rating agency, was never queried. It had the
    promoter's parent rated the whole time.
  * The check ran on the project promoter -- a special purpose vehicle
    incorporated three weeks before the registration, which no agency would
    ever rate -- rather than on the group entities that actually borrow.

The consequence was worse than a blank. The only rating information that
reached the Charter came from the agentic web-search pass, which surfaced a
single rationale document from JANUARY 2022 reading "Issuer Not
Cooperating", and that was reported as a live governance flag. CRISIL's own
factsheet for the same company reads Crisil BBB / Stable, reaffirmed 4 June
2026: investment grade, four years newer, opposite conclusion. A structured
check would have had the current rating; a one-off search result had a
snapshot of the worst moment in the company's history.

THE THIRD BUG, found while testing the fix: a bounded check whose ordering
is arbitrary systematically misses what matters. The group list arrives in
the registry's scrape order, so checking the first fourteen names walked
through carbon and logistics entities while the two rated companies sat at
positions 28 and 29. The pass reported "0 rated" and disclosed the bound
honestly -- honest and useless at the same time.

CARE and India Ratings were added later still (both confirmed reverse-
engineerable the same way ICRA/Infomerics were: a plain, unauthenticated
search endpoint plus a plain detail endpoint, no browser/JS needed), closing
the gap between what the file's own doc comments already claimed the tool
checked ("CRISIL/ICRA/CARE/India Ratings") and what it actually queried.

Network tests are opt-in: CRISIL_LIVE=1.

Run directly: python test_crisil_and_group_ratings.py
"""

import os

import company_charter as cc

_LIVE = os.environ.get("CRISIL_LIVE") == "1"


def _rating(name, agency="CRISIL", rating="Crisil BBB (Outlook: Stable)"):
    return {"ratings": [{"found": True, "agency": agency, "company_name": name,
                         "instruments": [{"instrument": "Long Term", "rating": rating}]}]}


# --- name matching --------------------------------------------------------

def test_a_legal_form_abbreviation_does_not_hide_a_rating():
    """MCA writes "Pranami Estates Pvt. Ltd."; CRISIL writes "Pranami
    Estates Private Limited". Exact matching on the raw strings missed a
    live investment-grade rating and the entity read as unrated."""
    assert cc.canonical_company_name("Pranami Estates Pvt. Ltd.") == \
        cc.canonical_company_name("Pranami Estates Private Limited")
    assert cc.canonical_company_name("ABC Corp.") == cc.canonical_company_name("ABC Corporation")
    assert cc.canonical_company_name("X & Y Ltd") == cc.canonical_company_name("X and Y Limited")
    print("test_a_legal_form_abbreviation_does_not_hide_a_rating: PASS")


def test_canonicalising_never_merges_two_different_companies():
    """The guard that keeps the above safe. Only the legal-form suffix and
    punctuation are touched. Pranami Builders and Pranami Estates are
    different companies, BOTH CRISIL-rated with different rating dates, and
    collapsing them would attribute one's rating to the other."""
    assert cc.canonical_company_name("Pranami Builders Pvt Ltd") != \
        cc.canonical_company_name("Pranami Estates Pvt Ltd")
    assert cc.canonical_company_name("Adarsh Haven Private Limited") != \
        cc.canonical_company_name("Adarsh Greens Private Limited")
    assert cc.canonical_company_name("") == ""
    assert cc.canonical_company_name(None) == ""
    print("test_canonicalising_never_merges_two_different_companies: PASS")


def test_crisil_is_actually_in_the_agency_list():
    """The whole defect in one assertion: the largest agency in the country
    was not among the ones checked, while the document told the reader that
    every agency had been."""
    import inspect

    agencies = [name for name, _ in cc._CREDIT_RATING_AGENCIES]
    assert "CRISIL" in agencies, agencies
    assert "ICRA" in agencies and "Infomerics" in agencies, agencies
    assert "CARE" in agencies and "India Ratings" in agencies, agencies
    source = inspect.getsource(cc._append_credit_rating_section)
    assert "CRISIL" in source, \
        "the section still tells the reader only ICRA and Infomerics were checked"
    print("test_crisil_is_actually_in_the_agency_list: PASS")


# --- CARE and India Ratings ------------------------------------------------

def test_care_matches_only_the_exact_entity_not_a_securitisation_tranche():
    """CARE's own search returns "Godrej Finance Limited-Securitisation" as
    a SEPARATE company alongside "Godrej Finance Limited" itself (a real,
    live search result, not a hypothetical) -- exact matching must not
    confuse the tranche entity with the parent, the same discipline already
    proven for CRISIL/ICRA/Infomerics."""
    original_search, original_detail = cc._care_search_companies, cc._care_fetch_rating_detail
    cc._care_search_companies = lambda name: [
        {"CompanyID": "abc==", "CompanyName": "Godrej Finance Limited"},
        {"CompanyID": "xyz==", "CompanyName": "Godrej Finance Limited-Securitisation"},
    ]
    cc._care_fetch_rating_detail = lambda company_id: {
        "instruments": [{"instrument": "Long Term", "rating": "CARE AA+; Stable"}],
        "url": f"https://www.careratings.com/search?Id={company_id}",
    }
    try:
        result = cc._lookup_care_rating("Godrej Finance Limited")
    finally:
        cc._care_search_companies, cc._care_fetch_rating_detail = original_search, original_detail
    assert result["found"] is True, result
    assert result["company_name"] == "Godrej Finance Limited", result
    assert result["url"].endswith("abc=="), result
    print("test_care_matches_only_the_exact_entity_not_a_securitisation_tranche: PASS")


def test_care_reports_not_found_honestly():
    original = cc._care_search_companies
    cc._care_search_companies = lambda name: []
    try:
        result = cc._lookup_care_rating("Nonexistent Company Ltd")
    finally:
        cc._care_search_companies = original
    assert result["found"] is False, result
    assert "CARE" in result["note"], result
    print("test_care_reports_not_found_honestly: PASS")


def test_india_ratings_reports_not_found_honestly():
    original = cc._india_ratings_search_companies
    cc._india_ratings_search_companies = lambda name: []
    try:
        result = cc._lookup_india_ratings_rating("Nonexistent Company Ltd")
    finally:
        cc._india_ratings_search_companies = original
    assert result["found"] is False, result
    assert "India Ratings" in result["note"], result
    print("test_india_ratings_reports_not_found_honestly: PASS")


def test_india_ratings_matches_the_exact_issuer_by_name():
    """India Ratings' own search returns multiple issuers sharing a brand
    word (e.g. "Godrej Agrovet Limited" alongside "Godrej Properties
    Limited") -- exact matching must pick the right one, not the first."""
    original_search, original_detail = (
        cc._india_ratings_search_companies, cc._india_ratings_fetch_rating_detail,
    )
    cc._india_ratings_search_companies = lambda name: [
        {"issuerID": "14086", "name": "Godrej Agrovet Limited"},
        {"issuerID": "12230", "name": "Godrej Properties Limited"},
    ]
    cc._india_ratings_fetch_rating_detail = lambda issuer_id: {
        "instruments": [{"instrument": "Non-convertible debentures", "rating": "IND AA+ / Stable"}],
        "url": f"https://www.indiaratings.co.in/search/issuerid/{issuer_id}",
    }
    try:
        result = cc._lookup_india_ratings_rating("Godrej Properties Limited")
    finally:
        (cc._india_ratings_search_companies, cc._india_ratings_fetch_rating_detail) = (
            original_search, original_detail,
        )
    assert result["found"] is True, result
    assert result["company_name"] == "Godrej Properties Limited", result
    assert result["url"].endswith("/12230"), result
    print("test_india_ratings_matches_the_exact_issuer_by_name: PASS")


# --- the group check ------------------------------------------------------

def test_the_group_check_covers_entities_the_subject_check_cannot():
    """A project SPV is unrated by construction. Checking only the subject
    reports nothing while the parent that borrows carries a live rating."""
    calls = []

    def _fake(name):
        calls.append(name)
        return _rating(name) if "Builders" in name else {"ratings": [{"found": False}]}

    original = cc.lookup_credit_rating
    cc.lookup_credit_rating = _fake
    try:
        result = cc._safe_group_credit_ratings(
            {"companies": [{"name": "Pranami Builders Private Limited"},
                           {"name": "Some Logistics Private Limited"}]},
            "Pranami Neev Realty Limited",
        )
    finally:
        cc.lookup_credit_rating = original

    assert calls[0] == "Pranami Neev Realty Limited", calls
    assert len(result["rated"]) == 1, result["rated"]
    assert result["rated"][0]["company_name"] == "Pranami Builders Private Limited"
    assert result["unrated_count"] == 2, result
    print("test_the_group_check_covers_entities_the_subject_check_cannot: PASS")


def test_a_bounded_check_looks_at_the_likeliest_parent_first():
    """REGRESSION, and the reason a correct fix still produced a useless
    answer. The group list arrives in arbitrary registry order: on the real
    subject the first fourteen names were carbon and logistics companies
    and the two rated entities sat at 28 and 29, so a limit of fourteen
    found nothing. Entities sharing the subject's distinctive brand are the
    likeliest parents and are checked first."""
    checked = []

    def _fake(name):
        checked.append(name)
        # The SPV itself is unrated, which is the realistic case and the
        # whole reason a subject-only check reports nothing.
        rated = ("Pranami Builders", "Pranami Estates")
        return _rating(name) if any(r in name for r in rated) else {"ratings": [{"found": False}]}

    companies = [{"name": f"Carbon {i} Limited"} for i in range(20)]
    companies += [{"name": "Pranami Builders Private Limited"},
                  {"name": "Pranami Estates Pvt. Ltd."}]

    original = cc.lookup_credit_rating
    cc.lookup_credit_rating = _fake
    try:
        result = cc._safe_group_credit_ratings(
            {"companies": companies}, "Pranami Neev Realty Limited", limit=4)
    finally:
        cc.lookup_credit_rating = original

    assert checked[0] == "Pranami Neev Realty Limited", checked[:4]
    assert len(result["rated"]) == 2, result["rated"]
    names = {r["company_name"] for r in result["rated"]}
    assert names == {"Pranami Builders Private Limited", "Pranami Estates Pvt. Ltd."}, names
    print("test_a_bounded_check_looks_at_the_likeliest_parent_first: PASS")


def test_entities_past_the_limit_are_not_reported_as_unrated():
    """"Not checked" and "carries no rating" are different findings -- the
    same rule the group sweep and the charge watch already follow."""
    original = cc.lookup_credit_rating
    cc.lookup_credit_rating = lambda name: {"ratings": [{"found": False}]}
    try:
        result = cc._safe_group_credit_ratings(
            {"companies": [{"name": f"Co {i} Ltd"} for i in range(20)]}, "Subject Ltd", limit=3)
    finally:
        cc.lookup_credit_rating = original
    assert result["entities_checked"] == 3, result
    assert result["entities_total"] == 21, result
    assert any("were not checked at all" in n for n in result["notes"]), result["notes"]
    print("test_entities_past_the_limit_are_not_reported_as_unrated: PASS")


def test_unrated_is_explained_rather_than_left_looking_adverse():
    """Most private companies are unrated because they never sought a
    rating. Left bare, a list of "no rating" reads as a finding."""
    original = cc.lookup_credit_rating
    cc.lookup_credit_rating = lambda name: {"ratings": [{"found": False}]}
    try:
        result = cc._safe_group_credit_ratings({"companies": [{"name": "A Ltd"}]}, "B Ltd")
    finally:
        cc.lookup_credit_rating = original
    assert "not itself an adverse signal" in " ".join(result["notes"]), result["notes"]
    print("test_unrated_is_explained_rather_than_left_looking_adverse: PASS")


def test_a_failing_agency_never_sinks_the_group_pass():
    def _boom(name):
        raise ConnectionError("down")

    original = cc.lookup_credit_rating
    cc.lookup_credit_rating = _boom
    try:
        result = cc._safe_group_credit_ratings({"companies": [{"name": "A Ltd"}]}, "B Ltd")
    finally:
        cc.lookup_credit_rating = original
    assert result["rated"] == [], result
    print("test_a_failing_agency_never_sinks_the_group_pass: PASS")


# --- live -----------------------------------------------------------------

def test_live_crisil_returns_the_current_rating_not_a_stale_one():
    """The regression that motivated all of this. The web-search pass found
    a January 2022 "Issuer Not Cooperating" rationale and it was reported as
    the live position. The structured check must return what CRISIL says
    TODAY."""
    if not _LIVE:
        print("test_live_crisil_returns_the_current_rating_not_a_stale_one: SKIPPED (set CRISIL_LIVE=1)")
        return
    result = cc._lookup_crisil_rating("Pranami Builders Private Limited")
    assert result["found"] is True, result
    assert result["agency"] == "CRISIL", result
    ratings = " ".join(i["rating"] for i in result["instruments"])
    assert "Crisil BBB" in ratings, ratings
    assert "Issuer Not Cooperating" not in ratings, \
        "returned the 2022 position rather than the current one"
    print("test_live_crisil_returns_the_current_rating_not_a_stale_one: PASS")


def test_live_care_returns_a_real_rating():
    if not _LIVE:
        print("test_live_care_returns_a_real_rating: SKIPPED (set CRISIL_LIVE=1)")
        return
    result = cc._lookup_care_rating("Godrej Finance Limited")
    assert result["found"] is True, result
    assert result["agency"] == "CARE", result
    ratings = " ".join(i["rating"] for i in result["instruments"])
    assert "CARE" in ratings, ratings
    print("test_live_care_returns_a_real_rating: PASS")


def test_live_india_ratings_returns_a_real_rating():
    if not _LIVE:
        print("test_live_india_ratings_returns_a_real_rating: SKIPPED (set CRISIL_LIVE=1)")
        return
    result = cc._lookup_india_ratings_rating("Godrej Properties Limited")
    assert result["found"] is True, result
    assert result["agency"] == "India Ratings", result
    ratings = " ".join(i["rating"] for i in result["instruments"])
    assert "IND" in ratings, ratings
    print("test_live_india_ratings_returns_a_real_rating: PASS")


if __name__ == "__main__":
    test_a_legal_form_abbreviation_does_not_hide_a_rating()
    test_canonicalising_never_merges_two_different_companies()
    test_crisil_is_actually_in_the_agency_list()
    test_care_matches_only_the_exact_entity_not_a_securitisation_tranche()
    test_care_reports_not_found_honestly()
    test_india_ratings_reports_not_found_honestly()
    test_india_ratings_matches_the_exact_issuer_by_name()
    test_the_group_check_covers_entities_the_subject_check_cannot()
    test_a_bounded_check_looks_at_the_likeliest_parent_first()
    test_entities_past_the_limit_are_not_reported_as_unrated()
    test_unrated_is_explained_rather_than_left_looking_adverse()
    test_a_failing_agency_never_sinks_the_group_pass()
    test_live_crisil_returns_the_current_rating_not_a_stale_one()
    test_live_care_returns_a_real_rating()
    test_live_india_ratings_returns_a_real_rating()
    print("\nAll tests passed.")

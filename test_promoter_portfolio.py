"""
Standalone verification for promoter_portfolio.build_promoter_portfolio()'s
past_experiences fetch and on_time/delayed classification -- no real MahaRERA
session available in this environment (no cached guest token, no live
CAPTCHA-solving human), so resolver.search_promoters and
api_client.fetch_category are mocked instead of hit for real.

Run directly: python test_promoter_portfolio.py
"""

import requests
from unittest.mock import patch

import resolver
import promoter_portfolio


def _fake_candidate(reg_no, project_id, project_name):
    return resolver.ProjectCandidate(
        project_id=project_id,
        detail_url=f"https://maharerait.maharashtra.gov.in/public/project/view/{project_id}",
        reg_no=reg_no,
        project_name=project_name,
        promoter_name="Test Promoter Pvt Ltd",
        district="Pune",
        pincode="411001",
        last_modified="2026-01-01",
    )


def test_on_time_rate_math():
    """Project A: 3 classifiable past_experiences entries (2 on_time, 1
    delayed) plus one entry missing actualCompletionDate (must NOT count).
    Project B: past_experiences comes back as an empty list (0 entries).
    Portfolio-wide: on_time_rate_pct must be computed only over the 3
    classifiable entries -- 2/3 = 66.7%, not diluted by Project B's zero."""
    candidates = [
        _fake_candidate("P52100000001", "1001", "Project A"),
        _fake_candidate("P52100000002", "1002", "Project B"),
    ]

    past_experiences_by_project = {
        "1001": [
            {"originalProposedCompletionDate": "2024-06-30", "actualCompletionDate": "2024-05-15"},  # on_time
            {"originalProposedCompletionDate": "2023-01-31", "actualCompletionDate": "2023-01-31"},  # on_time (exact match)
            {"originalProposedCompletionDate": "2022-03-31", "actualCompletionDate": "2022-09-30"},  # delayed
            {"originalProposedCompletionDate": "2025-12-31", "actualCompletionDate": None},  # must be skipped
        ],
        "1002": [],
    }

    def fake_fetch_category(category, project_id, session, token, body=None):
        if category == "projects":
            return {"projectCurrentStatus": "Registered", "isProjectLapsed": 0, "userProfileId": 555}
        if category == "complaints":
            return {"complaintDetails": []}
        if category == "appeals":
            return []
        if category == "past_experiences":
            assert body == {"userProfileId": 555, "projectId": project_id}, f"unexpected body: {body}"
            return past_experiences_by_project[project_id]
        raise AssertionError(f"unexpected category fetched: {category}")

    with patch.object(resolver, "search_promoters", return_value=candidates), \
         patch.object(promoter_portfolio.api_client, "fetch_category", side_effect=fake_fetch_category):
        with requests.Session() as session:
            portfolio = promoter_portfolio.build_promoter_portfolio(
                "Test Promoter Pvt Ltd", session, token="fake-token", headless=True
            )

    totals = portfolio["totals"]
    assert totals["total_experience_entries_found"] == 3, totals
    assert totals["on_time_count"] == 2, totals
    assert totals["delayed_count"] == 1, totals
    assert totals["on_time_rate_pct"] == round(100 * 2 / 3, 1) == 66.7, totals
    assert any("self-reported" in note for note in portfolio["limitations"]), portfolio["limitations"]
    print("test_on_time_rate_math: PASS", totals)


def test_zero_entries_gives_none_not_zero():
    """No project in the portfolio has any classifiable past_experiences
    entry (missing dates, unparseable dates, or an empty/error response) --
    on_time_rate_pct must be None, never a fabricated 0.0 or ZeroDivisionError."""
    candidates = [_fake_candidate("P52100000003", "1003", "Project C")]

    def fake_fetch_category(category, project_id, session, token, body=None):
        if category == "projects":
            return {"projectCurrentStatus": "Registered", "isProjectLapsed": 0, "userProfileId": 999}
        if category == "complaints":
            return {"complaintDetails": []}
        if category == "appeals":
            return []
        if category == "past_experiences":
            # Present, but every entry is missing (or has unparseable) dates.
            return [
                {"originalProposedCompletionDate": None, "actualCompletionDate": None},
                {"originalProposedCompletionDate": "not-a-date", "actualCompletionDate": "also-not-a-date"},
            ]
        raise AssertionError(f"unexpected category fetched: {category}")

    with patch.object(resolver, "search_promoters", return_value=candidates), \
         patch.object(promoter_portfolio.api_client, "fetch_category", side_effect=fake_fetch_category):
        with requests.Session() as session:
            portfolio = promoter_portfolio.build_promoter_portfolio(
                "Test Promoter Pvt Ltd", session, token="fake-token", headless=True
            )

    totals = portfolio["totals"]
    assert totals["total_experience_entries_found"] == 0, totals
    assert totals["on_time_count"] == 0, totals
    assert totals["delayed_count"] == 0, totals
    assert totals["on_time_rate_pct"] is None, totals
    print("test_zero_entries_gives_none_not_zero: PASS", totals)


def test_total_area_developed_sums_land_area():
    """total_area_developed_lakh_sqft sums past_experiences.landArea (sqm)
    across every portfolio project's entries and converts to lakh sq ft --
    entries missing/zero landArea don't contribute or crash the sum."""
    candidates = [
        _fake_candidate("P52100000005", "1005", "Project E"),
        _fake_candidate("P52100000006", "1006", "Project F"),
    ]

    past_experiences_by_project = {
        "1005": [
            {"originalProposedCompletionDate": "2024-06-30", "actualCompletionDate": "2024-05-15", "landArea": 32917.0},
            {"originalProposedCompletionDate": None, "actualCompletionDate": None, "landArea": None},
        ],
        "1006": [
            {"originalProposedCompletionDate": "2023-01-31", "actualCompletionDate": "2023-01-31", "landArea": 10000.0},
        ],
    }

    def fake_fetch_category(category, project_id, session, token, body=None):
        if category == "projects":
            return {"projectCurrentStatus": "Registered", "isProjectLapsed": 0, "userProfileId": 1}
        if category == "complaints":
            return {"complaintDetails": []}
        if category == "appeals":
            return []
        if category == "past_experiences":
            return past_experiences_by_project[project_id]
        raise AssertionError(f"unexpected category fetched: {category}")

    with patch.object(resolver, "search_promoters", return_value=candidates), \
         patch.object(promoter_portfolio.api_client, "fetch_category", side_effect=fake_fetch_category):
        with requests.Session() as session:
            portfolio = promoter_portfolio.build_promoter_portfolio(
                "Test Promoter Pvt Ltd", session, token="fake-token", headless=True
            )

    totals = portfolio["totals"]
    expected_lakh_sqft = round((32917.0 + 10000.0) * 10.7639 / 100_000, 2)
    assert totals["total_area_developed_lakh_sqft"] == expected_lakh_sqft, totals
    assert totals["area_within_5km_lakh_sqft"] is None, "not computed -- no geocoding built yet, must stay None not a guessed 0"
    print("test_total_area_developed_sums_land_area: PASS", totals["total_area_developed_lakh_sqft"])


def test_geocode_query_prefers_pincode_over_noisy_address():
    """Regression test for a real live-run finding: MahaRERA's own
    past_experiences.address is often a full legal land description
    (survey numbers, stray commas) that Nominatim's free-form search
    fails to resolve at all, even when it contains the correct locality
    name -- but a bare 6-digit pincode extracted from that same string
    geocodes reliably. Pincode must be preferred whenever present."""
    noisy_but_has_pincode = (
        "Plot 1 of Survey nos 11/1A, 12/1, 12/2/1, 12/2/2, 12/2/3, 13/1B part and 13/2, "
        "Village Mamurdi, Service road to Pune Mumbai Expressway, Mamurdi, , Haveli, Pune, "
        "412101, MAHARASHTRA"
    )
    assert promoter_portfolio._geocode_query_for(noisy_but_has_pincode, "Pune") == "412101, India"
    assert promoter_portfolio._geocode_query_for("PUNE", "Pune") == "PUNE"  # no pincode -> raw address as-is
    assert promoter_portfolio._geocode_query_for(None, "Pune") == "Pune, Maharashtra, India"  # no address at all -> district
    assert promoter_portfolio._geocode_query_for("", "Pune") == "Pune, Maharashtra, India"
    print("test_geocode_query_prefers_pincode_over_noisy_address: PASS")


def test_extract_subject_project_location():
    """Parses the real partners.json shape (projectDetails.
    projectLegalLandAddressDetails.{locality, pinCode}) into a geocodable
    string, and returns None -- never a guessed/empty string -- for any
    malformed or missing shape."""
    real_shape = {
        "projectDetails": {
            "projectLegalLandAddressDetails": {"locality": "Mamurdi", "pinCode": "412101"},
        },
    }
    assert promoter_portfolio.extract_subject_project_location(real_shape) == "Mamurdi, 412101, Maharashtra, India"

    assert promoter_portfolio.extract_subject_project_location(None) is None
    assert promoter_portfolio.extract_subject_project_location({}) is None
    assert promoter_portfolio.extract_subject_project_location({"projectDetails": {}}) is None
    assert promoter_portfolio.extract_subject_project_location(
        {"projectDetails": {"projectLegalLandAddressDetails": {"locality": "", "pinCode": ""}}}
    ) is None
    print("test_extract_subject_project_location: PASS")


def test_area_within_5km_excludes_far_ungeocodable_and_self():
    """area_within_5km_lakh_sqft must: include an entry geocoded near the
    subject project, exclude one geocoded far away, exclude one that can't
    be geocoded at all (never guessed in or out), and exclude the subject
    project's own portfolio row entirely (it would otherwise sit at 0km
    from itself and trivially inflate both area totals with a
    self-reference). total_area_developed_lakh_sqft must still count the
    far/ungeocodable entries (that total doesn't require geocoding), but
    must still exclude the subject project's own entry.

    A self-reference is identified by the ENTRY'S OWN declared identity (its
    projectName, or its MahaRERA registration number), never by which
    registration it was fetched under. That distinction matters: the
    fetched-under rule silently discarded every genuine prior delivery for a
    single-project SPV, since there the only portfolio project IS the subject
    -- confirmed live on Pranami Bliss, whose real "Mall of Ranchi" delivery
    was being thrown away while still counting toward on_time_rate_pct."""
    subject_reg_no = "P52100019639"
    candidates = [
        _fake_candidate(subject_reg_no, "2000", "Godrej Park Greens (subject, self)"),
        _fake_candidate("P52100000020", "2001", "Nearby Project"),
        _fake_candidate("P52100000021", "2002", "Far Project"),
        _fake_candidate("P52100000022", "2003", "Ungeocodable Project"),
    ]
    past_experiences_by_project = {
        # A genuine self-reference: the promoter declared the subject project
        # itself as one of its own past experiences, named as such. Real
        # MahaRERA past_experiences entries always carry projectName
        # (confirmed live), which is what makes a self-reference identifiable
        # by the entry's OWN identity rather than by which registration it
        # was fetched under -- see the note in this test's docstring.
        "2000": [{"projectName": "Godrej Park Greens (subject, self)", "originalProposedCompletionDate": None, "actualCompletionDate": None, "landArea": 32917.0, "address": "Mamurdi"}],
        "2001": [{"projectName": "Prior Delivery Near Subject", "originalProposedCompletionDate": None, "actualCompletionDate": None, "landArea": 5000.0, "address": "Near Mamurdi"}],
        "2002": [{"projectName": "Prior Delivery Far Away", "originalProposedCompletionDate": None, "actualCompletionDate": None, "landArea": 8000.0, "address": "Nagpur"}],
        "2003": [{"projectName": "Prior Delivery Ungeocodable", "originalProposedCompletionDate": None, "actualCompletionDate": None, "landArea": 3000.0, "address": ""}],
    }
    subject_coords = (18.65, 73.75)
    geocode_by_query = {
        "Mamurdi, 412101, Maharashtra, India": subject_coords,  # subject project's own locality
        "Near Mamurdi": (18.65, 73.75),  # identical -> 0km, within 5km
        "Nagpur": (10.0, 73.75),  # ~960km away -> excluded
        # "Ungeocodable Project"'s entry has no address -> falls back to district "Pune",
        # deliberately not in this map, so _geocode returns None for it (unresolvable).
    }

    def fake_geocode(query):
        return geocode_by_query.get(query)

    def fake_fetch_category(category, project_id, session, token, body=None):
        if category == "projects":
            return {"projectCurrentStatus": "Registered", "isProjectLapsed": 0, "userProfileId": 1}
        if category == "complaints":
            return {"complaintDetails": []}
        if category == "appeals":
            return []
        if category == "past_experiences":
            return past_experiences_by_project[project_id]
        raise AssertionError(f"unexpected category fetched: {category}")

    subject_partners_data = {
        "projectDetails": {"projectLegalLandAddressDetails": {"locality": "Mamurdi", "pinCode": "412101"}},
    }

    with patch.object(resolver, "search_promoters", return_value=candidates), \
         patch.object(promoter_portfolio, "_geocode", side_effect=fake_geocode), \
         patch.object(promoter_portfolio.api_client, "fetch_category", side_effect=fake_fetch_category):
        with requests.Session() as session:
            portfolio = promoter_portfolio.build_promoter_portfolio(
                "Test Promoter Pvt Ltd", session, token="fake-token", headless=True,
                subject_project_partners_data=subject_partners_data, subject_reg_no=subject_reg_no,
            )

    totals = portfolio["totals"]
    # Only "Near Mamurdi" (5000 sqm) counts toward the 5km sum -- self (2000)
    # excluded, far (Nagpur) excluded, ungeocodable excluded.
    expected_5km = round(5000.0 * 10.7639 / 100_000, 2)
    assert totals["area_within_5km_lakh_sqft"] == expected_5km, totals
    # Total area developed counts Near + Far + Ungeocodable (5000+8000+3000),
    # excluding only the subject project's own self-reference (32917).
    expected_total = round((5000.0 + 8000.0 + 3000.0) * 10.7639 / 100_000, 2)
    assert totals["total_area_developed_lakh_sqft"] == expected_total, totals
    print("test_area_within_5km_excludes_far_ungeocodable_and_self: PASS", totals["area_within_5km_lakh_sqft"], totals["total_area_developed_lakh_sqft"])


def test_no_token_skips_past_experiences_honestly():
    """No session token available -> past_experiences must not be fetched
    at all (mirrors the existing appeals behavior), and each row must say
    so explicitly rather than silently showing zero entries."""
    candidates = [_fake_candidate("P52100000004", "1004", "Project D")]

    def fake_fetch_category(category, project_id, session, token, body=None):
        if category == "projects":
            return {"projectCurrentStatus": "Registered", "isProjectLapsed": 0, "userProfileId": 1}
        if category == "complaints":
            return {"complaintDetails": []}
        raise AssertionError(f"{category} should not be fetched without a token")

    with patch.object(resolver, "search_promoters", return_value=candidates), \
         patch.object(promoter_portfolio.api_client, "fetch_category", side_effect=fake_fetch_category):
        with requests.Session() as session:
            portfolio = promoter_portfolio.build_promoter_portfolio(
                "Test Promoter Pvt Ltd", session, token=None, headless=True
            )

    assert portfolio["projects"][0]["past_experience_fetch_error"] == "no session token available"
    assert portfolio["totals"]["on_time_rate_pct"] is None
    print("test_no_token_skips_past_experiences_honestly: PASS")


def test_single_project_spv_keeps_its_genuine_prior_delivery():
    """The exact regression this fix exists for, taken from real Pranami
    Bliss data. A single-project SPV's portfolio contains ONLY the subject
    registration, and that registration's past_experiences list carries a
    genuine, differently-named prior delivery ("Mall of Ranchi", 5462.75
    sqm, completed on time, not MahaRERA-registered since it's in
    Jharkhand). Its area MUST count: the old rule excluded any entry
    fetched under the subject's own registration, which for an SPV meant
    every entry, so on_time_rate_pct read 100.0 while
    total_area_developed_lakh_sqft read None -- two Developer Score
    sub-metrics unscored for no real reason."""
    subject_reg_no = "P51800077150"
    candidates = [_fake_candidate(subject_reg_no, "46590", "PRANAMI BLISS")]

    def fake_fetch_category(category, project_id, session, token, body=None):
        if category == "projects":
            return {"projectCurrentStatus": "Certificate Signed", "isProjectLapsed": 0, "userProfileId": 105868}
        if category == "complaints":
            return {"complaintDetails": []}
        if category == "appeals":
            return []
        if category == "past_experiences":
            return [{
                "userProfilePastExperienceId": 13710,
                "projectName": "Mall of Ranchi",
                "mahaRERARegistrationNumber": None,
                "landArea": 5462.75,
                "originalProposedCompletionDate": "2022-07-31",
                "actualCompletionDate": "2022-07-31",
                "address": "Ratu Road Ranchi Jharkhand 835222",
            }]
        raise AssertionError(f"unexpected category fetched: {category}")

    with patch.object(resolver, "search_promoters", return_value=candidates), \
         patch.object(promoter_portfolio.api_client, "fetch_category", side_effect=fake_fetch_category):
        with requests.Session() as session:
            portfolio = promoter_portfolio.build_promoter_portfolio(
                "Pranami Neev Realty Limited", session, token="fake-token", headless=True,
                subject_reg_no=subject_reg_no,
            )

    totals = portfolio["totals"]
    assert totals["on_time_count"] == 1, totals
    assert totals["on_time_rate_pct"] == 100.0, totals
    expected_area = round(5462.75 * 10.7639 / 100_000, 2)  # 0.59 lakh sq ft
    assert totals["total_area_developed_lakh_sqft"] == expected_area, totals
    print("test_single_project_spv_keeps_its_genuine_prior_delivery: PASS", totals["total_area_developed_lakh_sqft"])


def test_same_prior_delivery_across_two_registrations_counted_once():
    """A promoter's past-experience list is keyed on their userProfileId, not
    on the registration it was fetched under, so the same historical project
    comes back once per registration they hold. It must be counted exactly
    once -- both for area and for the on-time/delayed split, which the older
    code never deduplicated at all."""
    candidates = [
        _fake_candidate("P52100000030", "3000", "Project One"),
        _fake_candidate("P52100000031", "3001", "Project Two"),
    ]
    shared_entry = {
        "userProfilePastExperienceId": 999,
        "projectName": "The Only Prior Delivery",
        "mahaRERARegistrationNumber": None,
        "landArea": 4000.0,
        "originalProposedCompletionDate": "2021-01-01",
        "actualCompletionDate": "2021-01-01",
        "address": "Somewhere",
    }

    def fake_fetch_category(category, project_id, session, token, body=None):
        if category == "projects":
            return {"projectCurrentStatus": "Registered", "isProjectLapsed": 0, "userProfileId": 7}
        if category == "complaints":
            return {"complaintDetails": []}
        if category == "appeals":
            return []
        if category == "past_experiences":
            return [dict(shared_entry)]  # same entry returned under BOTH registrations
        raise AssertionError(f"unexpected category fetched: {category}")

    with patch.object(resolver, "search_promoters", return_value=candidates), \
         patch.object(promoter_portfolio.api_client, "fetch_category", side_effect=fake_fetch_category):
        with requests.Session() as session:
            portfolio = promoter_portfolio.build_promoter_portfolio(
                "Test Promoter Pvt Ltd", session, token="fake-token", headless=True
            )

    totals = portfolio["totals"]
    assert totals["total_experience_entries_found"] == 1, totals
    assert totals["on_time_count"] == 1, totals
    assert totals["total_area_developed_lakh_sqft"] == round(4000.0 * 10.7639 / 100_000, 2), totals
    print("test_same_prior_delivery_across_two_registrations_counted_once: PASS")


if __name__ == "__main__":
    test_on_time_rate_math()
    test_zero_entries_gives_none_not_zero()
    test_total_area_developed_sums_land_area()
    test_geocode_query_prefers_pincode_over_noisy_address()
    test_extract_subject_project_location()
    test_area_within_5km_excludes_far_ungeocodable_and_self()
    test_no_token_skips_past_experiences_honestly()
    test_single_project_spv_keeps_its_genuine_prior_delivery()
    test_same_prior_delivery_across_two_registrations_counted_once()
    print("\nAll tests passed.")

"""
Standalone verification for company_charter.py's two code-computed Maps
passes: _compute_landmark_distances() (the Key Landmarks table -- entirely
code-driven via geo_lookup.find_nearest_landmarks/driving_route, no model
involvement at all) and _verify_comparables_within_radius() (drops any
AI-proposed comparable that doesn't independently geocode-verify within
2km). No real Overpass/Nominatim/OSRM traffic -- geo_lookup.find_nearest_landmarks,
geo_lookup.driving_route, and geo_lookup.geocode are all mocked.

Run directly: python test_distance_refinement.py
"""

from unittest.mock import patch

import company_charter
import geo_lookup


def test_compute_landmark_distances_no_op_when_origin_ungeocodable():
    facts = {}
    with patch.object(geo_lookup, "find_nearest_landmarks") as mock_find:
        result = company_charter._compute_landmark_distances(facts, None)

    mock_find.assert_not_called()
    assert result["distances"] == []
    assert any("could not be geocoded" in g for g in result["gaps"])
    print("test_compute_landmark_distances_no_op_when_origin_ungeocodable: PASS")


def test_compute_landmark_distances_fills_category_and_driving_route():
    origin_coords = (18.5204, 73.8567)
    fake_landmarks = {
        "Airport": [{"name": "Test International Airport", "coords": (18.6, 73.9), "straight_line_km": 12.3}],
        "Metro station": [],  # genuinely none found within the widest radius
    }

    def fake_driving_route(origin, dest):
        assert origin == origin_coords
        return {"distance_km": 15.4, "duration_min": 22.0}

    with patch.object(geo_lookup, "find_nearest_landmarks", return_value=fake_landmarks), \
         patch.object(geo_lookup, "driving_route", side_effect=fake_driving_route):
        facts = company_charter._compute_landmark_distances({}, origin_coords)

    entries = facts["distances"]
    assert len(entries) == 2, entries
    airport, metro = entries
    assert airport["category"] == "Airport"
    assert airport["landmark"] == "Test International Airport"
    assert "15.4 km" in airport["distance_time"], airport["distance_time"]
    # Duration deliberately never shown -- OSRM's public server has no traffic
    # model (confirmed live: a real 9.7km Mumbai route came back as 10min,
    # ~58 km/h average), so a duration figure would misinform a reader.
    assert "22" not in airport["distance_time"], airport["distance_time"]
    assert "min" not in airport["distance_time"], airport["distance_time"]
    assert metro["category"] == "Metro station"
    assert metro["landmark"] == "None found"
    assert metro["distance_time"] == "Not found"
    print("test_compute_landmark_distances_fills_category_and_driving_route: PASS")


def test_compute_landmark_distances_says_cant_route_never_falls_back_to_straight_line():
    origin_coords = (18.5204, 73.8567)
    fake_landmarks = {
        "Hospital": [{"name": "Test Hospital", "coords": (18.55, 73.9), "straight_line_km": 7.7}],
    }

    with patch.object(geo_lookup, "find_nearest_landmarks", return_value=fake_landmarks), \
         patch.object(geo_lookup, "driving_route", return_value=None):
        facts = company_charter._compute_landmark_distances({}, origin_coords)

    entry = facts["distances"][0]
    assert entry["distance_time"] == "Can't route", entry["distance_time"]
    assert "7.7" not in entry["distance_time"]  # the straight-line number must never appear as if it were driving
    assert "7.7 km away" in entry["route_note"]  # still disclosed, but only in the note, not as the shown distance
    print("test_compute_landmark_distances_says_cant_route_never_falls_back_to_straight_line: PASS")


def _comparable(project, locality, distance_km="1.0", pincode=""):
    return {
        "project": project, "configuration": "2BHK", "pricing": "approx",
        "source": "web_search", "locality": locality, "pincode": pincode,
        "distance_km": distance_km,
    }


def test_comparable_geocode_query_prefers_pincode_over_locality_over_project():
    # Pincode wins even when locality is also given.
    assert company_charter._comparable_geocode_query(
        _comparable("Some Project", "Some Locality", pincode="400058")
    ) == "400058, India"
    # A pincode embedded in free text (not the field alone) is still pulled out.
    assert company_charter._comparable_geocode_query(
        _comparable("Some Project", "Some Locality", pincode="Near XYZ, 400058")
    ) == "400058, India"
    # No pincode -- falls back to locality.
    assert company_charter._comparable_geocode_query(
        _comparable("Some Project", "Some Locality")
    ) == "Some Locality"
    # Neither pincode nor locality -- falls back to the bare project name.
    assert company_charter._comparable_geocode_query(
        _comparable("Some Project", "")
    ) == "Some Project"
    print("test_comparable_geocode_query_prefers_pincode_over_locality_over_project: PASS")


def test_verify_comparables_uses_pincode_when_present_even_if_localities_collide():
    origin_coords = (18.5204, 73.8567)
    near_coords = (18.5300, 73.8567)   # ~1.07km -- within 2km
    far_coords = (18.6200, 73.8567)    # ~11km -- outside 2km
    # Both candidates share the SAME broad locality string, which would
    # otherwise collapse them onto the same point -- their own distinct
    # pincodes are what actually tells them apart.
    facts = {"comparables": [
        _comparable("Near Project", "Andheri West, Mumbai", pincode="400058"),
        _comparable("Far Project", "Andheri West, Mumbai", pincode="400099"),
    ]}

    def fake_geocode(query):
        assert "Andheri West" not in query, f"should have geocoded the pincode, not the locality: {query}"
        return {"400058, India": near_coords, "400099, India": far_coords}.get(query)

    with patch.object(geo_lookup, "geocode", side_effect=fake_geocode):
        result = company_charter._verify_comparables_within_radius(facts, origin_coords)

    kept = result["comparables"]
    assert len(kept) == 1 and kept[0]["project"] == "Near Project", kept
    print("test_verify_comparables_uses_pincode_when_present_even_if_localities_collide: PASS")


def test_verify_comparables_no_op_on_empty_list():
    facts = {"comparables": []}
    with patch.object(geo_lookup, "geocode") as mock_geocode:
        result = company_charter._verify_comparables_within_radius(facts, (18.5, 73.8))
    mock_geocode.assert_not_called()
    assert result["comparables"] == []
    print("test_verify_comparables_no_op_on_empty_list: PASS")


def test_verify_comparables_drops_all_when_origin_ungeocodable():
    facts = {"comparables": [_comparable("Some Project", "Some Locality")]}
    result = company_charter._verify_comparables_within_radius(facts, None)

    assert result["comparables"] == []
    assert any("could not be geocoded" in g and "1 candidate" in g for g in result["gaps"])
    print("test_verify_comparables_drops_all_when_origin_ungeocodable: PASS")


def test_verify_comparables_keeps_within_radius_drops_outside_and_ungeocodable():
    origin_coords = (18.5204, 73.8567)  # Pune center
    near_coords = (18.5300, 73.8567)     # ~1.07km north -- within 2km
    far_coords = (18.6200, 73.8567)      # ~11km north -- outside 2km
    facts = {"comparables": [
        _comparable("Near Project", "Near Locality"),
        _comparable("Far Project", "Far Locality"),
        _comparable("Unfindable Project", "Nowhere"),
    ]}

    def fake_geocode(query):
        # Keyed on the LOCALITY ALONE -- confirmed live against a real
        # project (2026-09-28) that combining "<project>, <locality>" into
        # one query reliably returns nothing, even for a real, well-known
        # locality, because Nominatim's structured search does not
        # gracefully ignore an unrecognised leading term. A query still
        # carrying "Near Project"/"Far Project"/"Unfindable Project" here
        # would be exactly that regression.
        return {"Near Locality": near_coords, "Far Locality": far_coords}.get(query)

    with patch.object(geo_lookup, "geocode", side_effect=fake_geocode):
        result = company_charter._verify_comparables_within_radius(facts, origin_coords)

    kept = result["comparables"]
    assert len(kept) == 1, kept
    assert kept[0]["project"] == "Near Project"
    assert kept[0]["distance_km"].startswith("1."), kept[0]["distance_km"]  # code-computed, not the model's guess

    gap_text = " ".join(result["gaps"])
    assert "Far Project" in gap_text and "outside the 2km radius" in gap_text
    assert "Unfindable Project" in gap_text and "could not be geocoded" in gap_text
    print("test_verify_comparables_keeps_within_radius_drops_outside_and_ungeocodable: PASS")


def test_verify_comparables_falls_back_to_project_name_when_locality_missing():
    origin_coords = (18.5204, 73.8567)
    near_coords = (18.5300, 73.8567)
    facts = {"comparables": [_comparable("Legacy Project", locality="")]}

    def fake_geocode(query):
        assert query == "Legacy Project", query  # no locality given -- must fall back, not send ", "
        return near_coords

    with patch.object(geo_lookup, "geocode", side_effect=fake_geocode):
        result = company_charter._verify_comparables_within_radius(facts, origin_coords)

    assert len(result["comparables"]) == 1
    print("test_verify_comparables_falls_back_to_project_name_when_locality_missing: PASS")


if __name__ == "__main__":
    test_compute_landmark_distances_no_op_when_origin_ungeocodable()
    test_compute_landmark_distances_fills_category_and_driving_route()
    test_compute_landmark_distances_says_cant_route_never_falls_back_to_straight_line()
    test_comparable_geocode_query_prefers_pincode_over_locality_over_project()
    test_verify_comparables_uses_pincode_when_present_even_if_localities_collide()
    test_verify_comparables_no_op_on_empty_list()
    test_verify_comparables_drops_all_when_origin_ungeocodable()
    test_verify_comparables_keeps_within_radius_drops_outside_and_ungeocodable()
    test_verify_comparables_falls_back_to_project_name_when_locality_missing()
    print("\nAll tests passed.")

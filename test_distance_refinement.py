"""
Standalone verification for company_charter.py's two Distances-table
precision upgrades and how they compose: _refine_distances_with_nominatim
(free, on by default, straight-line) tried first, then
_refine_distances_with_maps (opt-in, ToS-risky, driving route) only for
whatever the first pass couldn't resolve. No real Nominatim/Maps traffic --
geo_lookup.geocode and company_charter._lookup_maps_distance are mocked.

Run directly: python test_distance_refinement.py
"""

import os
from unittest.mock import patch

import company_charter
import geo_lookup


def _entry(landmark):
    return {
        "landmark": landmark,
        "distance_time": "approx. 12 min / 4 km (web_search estimate)",
        "route_note": "Estimated via web search, not a live driving-route lookup.",
    }


def test_nominatim_refine_computes_and_labels_a_straight_line_distance():
    # Pune railway station to a point exactly 0.5deg of latitude further
    # north/south is a well-known-shape haversine case; use two points with a
    # known separation instead of asserting an exact km, matching this repo's
    # existing style of full-precision-agnostic geocode fixtures.
    origin_coords = (18.5204, 73.8567)
    landmark_coords = (18.5300, 73.8567)  # ~1.07km due north
    facts = {"distances": [_entry("Test Landmark School")]}

    def fake_geocode(query):
        return {"Pune Test Locality, Maharashtra, India": origin_coords,
                "Test Landmark School": landmark_coords}.get(query)

    with patch.object(geo_lookup, "geocode", side_effect=fake_geocode):
        resolved = company_charter._refine_distances_with_nominatim(facts, "Pune Test Locality, Maharashtra, India")

    entry = facts["distances"][0]
    assert "Test Landmark School" in resolved, resolved
    assert "km (straight-line)" in entry["distance_time"], entry["distance_time"]
    assert "1.1" in entry["distance_time"] or "1.0" in entry["distance_time"], entry["distance_time"]
    assert "Nominatim" in entry["route_note"] and "not a driving route" in entry["route_note"], entry["route_note"]
    print("test_nominatim_refine_computes_and_labels_a_straight_line_distance: PASS")


def test_nominatim_refine_leaves_entry_untouched_when_landmark_ungeocodable():
    original = _entry("Unfindable Landmark")
    facts = {"distances": [dict(original)]}

    def fake_geocode(query):
        if query == "Some Origin, State, India":
            return (18.5, 73.8)
        return None  # landmark deliberately not in the map -- unresolvable

    with patch.object(geo_lookup, "geocode", side_effect=fake_geocode):
        resolved = company_charter._refine_distances_with_nominatim(facts, "Some Origin, State, India")

    assert resolved == set(), resolved
    assert facts["distances"][0] == original, facts["distances"][0]
    print("test_nominatim_refine_leaves_entry_untouched_when_landmark_ungeocodable: PASS")


def test_nominatim_refine_no_op_when_origin_itself_ungeocodable():
    original = _entry("Some Landmark")
    facts = {"distances": [dict(original)]}
    landmark_geocode_calls = []

    def fake_geocode(query):
        landmark_geocode_calls.append(query)
        return None

    with patch.object(geo_lookup, "geocode", side_effect=fake_geocode):
        resolved = company_charter._refine_distances_with_nominatim(facts, "Ungeocodable Origin")

    assert resolved == set()
    assert facts["distances"][0] == original
    # Only the origin should have been attempted -- no point spending a
    # rate-limited Nominatim call on a landmark when there's no origin to
    # measure it from.
    assert landmark_geocode_calls == ["Ungeocodable Origin"], landmark_geocode_calls
    print("test_nominatim_refine_no_op_when_origin_itself_ungeocodable: PASS")


def test_maps_refine_skips_landmarks_nominatim_already_resolved():
    original_distance_time = _entry("x")["distance_time"]
    facts = {"distances": [_entry("Already Resolved Landmark"), _entry("Still Needs Maps Landmark")]}
    maps_calls = []

    def fake_lookup_maps_distance(origin, landmark):
        maps_calls.append(landmark)
        return {"duration": "10 min", "distance": "3.0 km", "route": "Test Road"}

    with patch.object(company_charter, "_lookup_maps_distance", side_effect=fake_lookup_maps_distance), \
         patch.dict(os.environ, {company_charter._MAPS_SCRAPE_ENV_VAR: "1"}):
        company_charter._refine_distances_with_maps(facts, "Some Origin", skip={"Already Resolved Landmark"})

    assert maps_calls == ["Still Needs Maps Landmark"], maps_calls
    resolved_entry, maps_entry = facts["distances"]
    assert resolved_entry["distance_time"] == original_distance_time  # untouched, Maps never called for it
    assert "3.0 km" in maps_entry["distance_time"], maps_entry["distance_time"]
    print("test_maps_refine_skips_landmarks_nominatim_already_resolved: PASS")


def test_maps_refine_still_off_by_default_without_env_var():
    facts = {"distances": [_entry("Some Landmark")]}
    original = dict(facts["distances"][0])

    with patch.object(company_charter, "_lookup_maps_distance") as mock_lookup, \
         patch.dict(os.environ, {}, clear=False):
        os.environ.pop(company_charter._MAPS_SCRAPE_ENV_VAR, None)
        company_charter._refine_distances_with_maps(facts, "Some Origin")

    mock_lookup.assert_not_called()
    assert facts["distances"][0] == original
    print("test_maps_refine_still_off_by_default_without_env_var: PASS")


if __name__ == "__main__":
    test_nominatim_refine_computes_and_labels_a_straight_line_distance()
    test_nominatim_refine_leaves_entry_untouched_when_landmark_ungeocodable()
    test_nominatim_refine_no_op_when_origin_itself_ungeocodable()
    test_maps_refine_skips_landmarks_nominatim_already_resolved()
    test_maps_refine_still_off_by_default_without_env_var()
    print("\nAll tests passed.")

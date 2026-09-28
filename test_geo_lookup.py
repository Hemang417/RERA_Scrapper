"""
Standalone verification for geo_lookup.find_nearest_landmarks()'s own
ranking logic -- specifically _PREFER_MAPPED_FOOTPRINT (Mall ranks a
way/relation candidate, a real mapped building footprint, ahead of a bare
node before sorting by distance within each tier; confirmed live,
2026-09-28, that a real-world sample of well-known large Mumbai malls were
all way-tagged while smaller/less-recognized ones were node-tagged). No
real Overpass traffic -- geo_lookup._run_overpass_query is mocked.

Run directly: python test_geo_lookup.py
"""

from unittest.mock import patch

import geo_lookup


def _element(elem_type, name, lat, lon):
    # Matches real Overpass "out center" shape: a node carries flat lat/lon,
    # a way/relation carries them nested under "center" (see
    # geo_lookup._element_coords) -- a flat lat/lon on a way, like a naive
    # test fixture might use, is silently unresolvable and would make this
    # test pass for the wrong reason.
    if elem_type == "node":
        return {"type": elem_type, "lat": lat, "lon": lon, "tags": {"name": name}}
    return {"type": elem_type, "center": {"lat": lat, "lon": lon}, "tags": {"name": name}}


def test_mall_prefers_way_over_closer_node():
    origin = (19.0, 73.0)
    # The node is CLOSER than the way -- if this weren't a preferred-footprint
    # category, the node would rank first on distance alone. Mall must still
    # put the way first.
    close_node = _element("node", "Small Local Plaza", 19.001, 73.0)   # ~0.11km
    farther_way = _element("way", "Big Known Mall", 19.01, 73.0)        # ~1.11km

    def fake_query(query):
        return [close_node, farther_way] if "shop" in query else []

    with patch.object(geo_lookup, "_run_overpass_query", side_effect=fake_query):
        result = geo_lookup.find_nearest_landmarks(origin, n_per_category=2)

    mall = result["Mall"]
    assert [m["name"] for m in mall] == ["Big Known Mall", "Small Local Plaza"], mall
    assert mall[0]["geometry"] == "way" and mall[1]["geometry"] == "node"
    print("test_mall_prefers_way_over_closer_node: PASS")


def test_mall_still_keeps_a_node_when_too_few_ways_exist():
    origin = (19.0, 73.0)
    only_node = _element("node", "Only Mall Here", 19.005, 73.0)

    def fake_query(query):
        return [only_node] if "shop" in query else []

    with patch.object(geo_lookup, "_run_overpass_query", side_effect=fake_query):
        result = geo_lookup.find_nearest_landmarks(origin, n_per_category=2)

    assert [m["name"] for m in result["Mall"]] == ["Only Mall Here"]
    print("test_mall_still_keeps_a_node_when_too_few_ways_exist: PASS")


def test_other_categories_are_unaffected_by_the_footprint_preference():
    origin = (19.0, 73.0)
    close_node = _element("node", "Closer Hospital", 19.001, 73.0)
    farther_way = _element("way", "Farther Hospital", 19.01, 73.0)

    def fake_query(query):
        return [close_node, farther_way] if "amenity" in query and "hospital" in query else []

    with patch.object(geo_lookup, "_run_overpass_query", side_effect=fake_query):
        result = geo_lookup.find_nearest_landmarks(origin, n_per_category=2)

    # Hospital has no footprint preference -- plain distance order, node first.
    assert [h["name"] for h in result["Hospital"]] == ["Closer Hospital", "Farther Hospital"]
    print("test_other_categories_are_unaffected_by_the_footprint_preference: PASS")


if __name__ == "__main__":
    test_mall_prefers_way_over_closer_node()
    test_mall_still_keeps_a_node_when_too_few_ways_exist()
    test_other_categories_are_unaffected_by_the_footprint_preference()
    print("\nAll tests passed.")

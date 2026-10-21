"""
Standalone verification for geo_lookup.find_nearest_landmarks()'s own
ranking logic -- specifically _PREFER_MAPPED_FOOTPRINT (Mall ranks a
way/relation candidate, a real mapped building footprint, ahead of a bare
node before sorting by distance within each tier; confirmed live,
2026-09-28, that a real-world sample of well-known large Mumbai malls were
all way-tagged while smaller/less-recognized ones were node-tagged). No
real Overpass traffic -- geo_lookup._run_overpass_query is mocked.

Also covers geo_lookup.geocode()'s HERE Geocoding & Search API request/
response handling (request params, response-shape parsing, and the
never-guess failure contract: empty query, missing HERE_API_KEY, empty
`items`, and a request exception must all return None, never raise or
fabricate a coordinate) -- _here_api_key is mocked directly rather than
touching real env/.env state, and no real HERE traffic is made
(geo_lookup.requests.get is mocked).

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


class _FakeResponse:
    def __init__(self, json_body, status_ok=True):
        self._json_body = json_body
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise geo_lookup.requests.HTTPError("bad status")

    def json(self):
        return self._json_body


def test_geocode_returns_none_on_empty_query():
    with patch.object(geo_lookup, "_here_api_key", return_value="test-key"), \
         patch.object(geo_lookup.requests, "get") as mock_get:
        assert geo_lookup.geocode("") is None
        assert geo_lookup.geocode("   ") is None
    mock_get.assert_not_called()
    print("test_geocode_returns_none_on_empty_query: PASS")


def test_geocode_returns_none_when_api_key_missing():
    # Must never raise and never fall back to another service -- a missing
    # key is exactly the same "can't verify this" case as a failed request.
    with patch.object(geo_lookup, "_here_api_key", return_value=""), \
         patch.object(geo_lookup.requests, "get") as mock_get:
        assert geo_lookup.geocode("Andheri West, Mumbai") is None
    mock_get.assert_not_called()
    print("test_geocode_returns_none_when_api_key_missing: PASS")


def test_geocode_sends_expected_here_request_and_parses_lat_lng():
    fake_resp = _FakeResponse({"items": [{"position": {"lat": 19.1197, "lng": 72.8468}}]})
    with patch.object(geo_lookup, "_here_api_key", return_value="test-key"), \
         patch.object(geo_lookup.requests, "get", return_value=fake_resp) as mock_get:
        coords = geo_lookup.geocode("400058, India")

    assert coords == (19.1197, 72.8468), coords
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["q"] == "400058, India"
    assert kwargs["params"]["apiKey"] == "test-key"
    assert kwargs["params"]["in"] == "countryCode:IND"
    assert kwargs["params"]["limit"] == 1
    print("test_geocode_sends_expected_here_request_and_parses_lat_lng: PASS")


def test_geocode_returns_none_on_empty_items():
    fake_resp = _FakeResponse({"items": []})
    with patch.object(geo_lookup, "_here_api_key", return_value="test-key"), \
         patch.object(geo_lookup.requests, "get", return_value=fake_resp):
        assert geo_lookup.geocode("a nonsense query with no match") is None
    print("test_geocode_returns_none_on_empty_items: PASS")


def test_geocode_returns_none_on_request_exception_never_raises():
    def raise_connection_error(*args, **kwargs):
        raise geo_lookup.requests.exceptions.ConnectionError("network down")

    with patch.object(geo_lookup, "_here_api_key", return_value="test-key"), \
         patch.object(geo_lookup.requests, "get", side_effect=raise_connection_error):
        assert geo_lookup.geocode("Andheri West, Mumbai") is None
    print("test_geocode_returns_none_on_request_exception_never_raises: PASS")


def test_geocode_returns_none_on_bad_http_status():
    fake_resp = _FakeResponse({"items": []}, status_ok=False)
    with patch.object(geo_lookup, "_here_api_key", return_value="bad-key"), \
         patch.object(geo_lookup.requests, "get", return_value=fake_resp):
        assert geo_lookup.geocode("Andheri West, Mumbai") is None
    print("test_geocode_returns_none_on_bad_http_status: PASS")


if __name__ == "__main__":
    test_mall_prefers_way_over_closer_node()
    test_mall_still_keeps_a_node_when_too_few_ways_exist()
    test_other_categories_are_unaffected_by_the_footprint_preference()
    test_geocode_returns_none_on_empty_query()
    test_geocode_returns_none_when_api_key_missing()
    test_geocode_sends_expected_here_request_and_parses_lat_lng()
    test_geocode_returns_none_on_empty_items()
    test_geocode_returns_none_on_request_exception_never_raises()
    test_geocode_returns_none_on_bad_http_status()
    print("\nAll tests passed.")

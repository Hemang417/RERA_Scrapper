"""
Shared OpenStreetMap Nominatim geocoding + straight-line distance helpers.

Used by promoter_portfolio.py (resolving a locality to lat/lon for the
"area within 5km" Developer Score filter) and company_charter.py (the
Distances table's Nominatim precision upgrade, see
_refine_distances_with_nominatim). Both call sites can run within the same
pipeline invocation, so the module-global rate limiter lives HERE, once --
two independent per-module clocks could each honour their own ~1.1s
spacing while still landing two requests back-to-back across the module
boundary, breaching Nominatim's combined usage policy. Free, no API key,
no ToS ambiguity (unlike the Google Maps scrape in company_charter.py) --
requires only the ~1 req/s cap and a descriptive User-Agent, both below.

Never treat a failed/empty geocode as "0km away" or any other guessed
value -- every caller here must treat None as "can't compute this",
never as zero.
"""
import math
import time

import requests

import config

_GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
_GEOCODE_USER_AGENT = "MahaRERA-Scrapper-DueDiligence/1.0 (personal research tool, low-volume)"
_GEOCODE_MIN_INTERVAL_S = 1.1
_last_geocode_at = 0.0


def geocode(query: str) -> tuple | None:
    """Resolves a free-text address/locality/landmark string to (lat, lon)
    via Nominatim, rate-limited to Nominatim's own usage policy. Returns
    None -- never a guessed coordinate -- if the query is empty, the
    request fails, or nothing matches."""
    global _last_geocode_at
    query = (query or "").strip()
    if not query:
        return None

    elapsed = time.monotonic() - _last_geocode_at
    if elapsed < _GEOCODE_MIN_INTERVAL_S:
        time.sleep(_GEOCODE_MIN_INTERVAL_S - elapsed)
    _last_geocode_at = time.monotonic()

    try:
        resp = requests.get(
            _GEOCODE_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "in"},
            headers={"User-Agent": _GEOCODE_USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None


def haversine_km(a: tuple, b: tuple) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r_km = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_km * math.asin(math.sqrt(x))

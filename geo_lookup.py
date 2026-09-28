"""
Shared OpenStreetMap geocoding (Nominatim), POI discovery (Overpass) and
routing (OSRM) helpers -- all free, keyless, public OSM-ecosystem services,
no ToS ambiguity (unlike the old Google Maps scrape company_charter.py used
to carry for landmark distances).

Used by promoter_portfolio.py (resolving a locality to lat/lon for the
"area within 5km" Developer Score filter) and company_charter.py (geocoding
the subject project's own locality, then finding/measuring the "2 nearest
per category" Key Landmarks and verifying comparable projects sit within
2km -- see _compute_landmark_distances and _verify_comparables_within_radius).
All three services can be hit within the same pipeline invocation, so each
gets its own module-global rate limiter clock HERE, once -- two independent
per-module clocks could each honour their own spacing while still landing
requests back-to-back across a module boundary, breaching the free public
instance's usage policy.

Never treat a failed/empty geocode, Overpass lookup, or OSRM route as "0km
away", a guessed distance, or a silently-dropped landmark -- every caller
here must treat None/empty as "can't compute this" and say so plainly,
never approximate it as a real answer.
"""
import math
import time

import requests

import config

_GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
_GEOCODE_USER_AGENT = "MahaRERA-Scrapper-DueDiligence/1.0 (personal research tool, low-volume)"
_GEOCODE_MIN_INTERVAL_S = 1.1
_last_geocode_at = 0.0

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_MIN_INTERVAL_S = 1.0
_OVERPASS_TIMEOUT_S = 30
_last_overpass_at = 0.0

_OSRM_URL_TEMPLATE = "https://router.project-osrm.org/route/v1/driving/{}"
_OSRM_MIN_INTERVAL_S = 1.0
_last_osrm_at = 0.0

# Search radius rungs (metres) tried in order per category until 2 results
# are found -- "nearest metro station" for a project with none nearby should
# keep expanding rather than stop at an arbitrary cutoff; 160km is a sane
# outer bound (a state's width) past which "nearest" stops meaning anything
# useful and the category is honestly reported as not found. Public (no
# leading underscore): company_charter.py reports this bound in the "None
# found" case, rather than hardcoding its own copy of the number.
LANDMARK_RADIUS_STEPS_M = (3000, 5000, 10000, 20000, 40000, 80000, 160000)

# category -> Overpass QL element filters (unioned). Each gets "(around:R,
# lat,lon);" appended per radius rung. "Infrastructure" means major
# transport infra per this project's own definition (highways/flyovers/
# junctions/railway stations, NOT metro) -- deliberately excludes anything
# tagged station=subway/light_rail so it doesn't just duplicate the Metro
# station category; "!~" in Overpass QL matches when the key is absent too,
# so a plain mainline station with no `station` tag still counts.
LANDMARK_CATEGORIES = (
    # `aeroway=aerodrome` alone matches any airfield, including a private
    # gliderdrome/airstrip -- confirmed live (2026-09-28): "NDA Gliderdrome"
    # surfaced as a Pune project's "nearest airport", which misleads a
    # reader into thinking there's commercial air access nearby when there
    # isn't. Requiring an `iata` or `icao` code excludes those -- a real
    # gliderdrome/private airstrip has neither, while any airport actually
    # open to scheduled or general commercial traffic carries at least an
    # ICAO code.
    ("Airport", ('node["aeroway"="aerodrome"]["iata"]', 'node["aeroway"="aerodrome"]["icao"]',
                  'way["aeroway"="aerodrome"]["iata"]', 'way["aeroway"="aerodrome"]["icao"]')),
    ("School", ('node["amenity"="school"]',)),
    ("Metro station", ('node["railway"="station"]["station"~"subway|light_rail"]',
                        'node["station"="subway"]')),
    # shop=mall alone has no tag distinguishing a real large mall from a
    # small local shopping plaza -- unlike Hospital/Metro/Airport, whose
    # own tags are already unambiguous, so this can't get an airport-style
    # hard exclusion. See _PREFER_MAPPED_FOOTPRINT below for the (weaker,
    # preference-only) fix.
    ("Mall", ('node["shop"="mall"]', 'way["shop"="mall"]')),
    ("Hospital", ('node["amenity"="hospital"]', 'way["amenity"="hospital"]')),
    ("Religious place", ('node["amenity"="place_of_worship"]',)),
    ("Hotel", ('node["tourism"="hotel"]', 'way["tourism"="hotel"]')),
    ("Infrastructure", ('node["highway"="motorway_junction"]',
                         'node["railway"="station"]["station"!~"subway|light_rail"]')),
)

# Categories where a candidate mapped as a real building footprint (a
# `way`/`relation`, not a bare `node`) should be preferred over one that
# isn't -- confirmed live (2026-09-28, a real Andheri West/Goregaon query):
# every well-known large Mumbai mall in a sample (Oberoi, InOrbit, Infiniti,
# Citi, Evershine, Express Zone) was way-tagged, while bare nodes were
# smaller/less-recognized names (Crystal Plaza, Harmony Mall, Tirumala
# Shopping Center). This is a real, live-confirmed correlation, but only a
# PREFERENCE, not a hard exclusion like Airport's iata/icao requirement --
# a node-tagged mall is still kept if too few way-tagged ones exist nearby,
# since sparser OSM mapping in a smaller city shouldn't turn "Mall" into a
# false "None found". Only Mall needs this: every other category's own tag
# is already unambiguous (a small hospital is still genuinely a hospital;
# a mall-vs-plaza mismatch is closer to Airport's real-vs-toy problem).
_PREFER_MAPPED_FOOTPRINT = {"Mall"}


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


def _run_overpass_query(query: str) -> list | None:
    """POSTs one query to the free public Overpass API, rate-limited the
    same way geocode() is. Returns None -- never an empty list pretending
    to mean "nothing found" -- on any network/parse failure, so callers can
    tell "the server didn't answer" apart from "genuinely nothing there"."""
    global _last_overpass_at
    elapsed = time.monotonic() - _last_overpass_at
    if elapsed < _OVERPASS_MIN_INTERVAL_S:
        time.sleep(_OVERPASS_MIN_INTERVAL_S - elapsed)
    _last_overpass_at = time.monotonic()

    try:
        resp = requests.post(
            _OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": _GEOCODE_USER_AGENT},
            timeout=_OVERPASS_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()["elements"]
    except (requests.RequestException, ValueError, KeyError):
        return None


def _element_coords(element: dict) -> tuple | None:
    if element.get("type") == "node":
        return element.get("lat"), element.get("lon")
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return center["lat"], center["lon"]
    return None


def find_nearest_landmarks(origin_coords: tuple, n_per_category: int = 2) -> dict:
    """For each category in LANDMARK_CATEGORIES, finds the `n_per_category`
    nearest named OSM points of interest to `origin_coords` via the free
    Overpass API, expanding the search radius through
    _LANDMARK_RADIUS_STEPS_M until enough are found (or the widest radius
    is exhausted). Fully deterministic, code-computed -- no model
    involvement, so no hallucinated landmark names or distances.

    Returns {category: [{"name", "coords", "straight_line_km", "geometry"},
    ...]}, each list containing 0, 1, or n_per_category entries -- fewer
    than requested means genuinely fewer than that exist within the
    widest radius tried (or every Overpass call for that category
    failed), never a padded/guessed entry. Ranking is by straight-line
    distance only (Overpass has no routing concept) -- except for
    categories in _PREFER_MAPPED_FOOTPRINT, which rank a `way`/`relation`
    candidate ahead of any `node` before sorting by distance within each
    tier. driving_route() is a separate step for the entries actually
    selected."""
    results = {}
    if not origin_coords:
        return {category: [] for category, _ in LANDMARK_CATEGORIES}
    lat, lon = origin_coords

    for category, filters in LANDMARK_CATEGORIES:
        found = []
        prefer_footprint = category in _PREFER_MAPPED_FOOTPRINT
        for radius_m in LANDMARK_RADIUS_STEPS_M:
            clauses = "".join(f"{f}(around:{radius_m},{lat},{lon});" for f in filters)
            query = f"[out:json][timeout:{_OVERPASS_TIMEOUT_S - 5}];({clauses});out center 20;"
            elements = _run_overpass_query(query)
            if elements is None:
                continue  # transient failure this rung -- try the next, wider one

            by_name = {}
            for el in elements:
                name = (el.get("tags") or {}).get("name")
                coords = _element_coords(el)
                if not name or not coords or None in coords:
                    continue
                km = haversine_km(origin_coords, coords)
                if name not in by_name or km < by_name[name]["straight_line_km"]:
                    by_name[name] = {"name": name, "coords": coords, "straight_line_km": km,
                                      "geometry": el.get("type", "node")}

            if prefer_footprint:
                found = sorted(by_name.values(),
                                key=lambda c: (0 if c["geometry"] != "node" else 1, c["straight_line_km"]))
            else:
                found = sorted(by_name.values(), key=lambda c: c["straight_line_km"])
            if len(found) >= n_per_category:
                break
        results[category] = found[:n_per_category]
    return results


def driving_route(origin_coords: tuple, dest_coords: tuple) -> dict | None:
    """Real driving distance/duration between two points via the free,
    keyless public OSRM routing server. Returns None -- callers must show
    an explicit "can't route" rather than silently substituting a
    straight-line number that would look like a real driving distance but
    isn't -- if either point is missing or OSRM can't compute a route.

    `duration_min` is a pure free-flow estimate -- this public OSRM server
    has NO traffic model at all. Confirmed live (2026-09-28): a real
    9.7km Andheri West -> CSMIA route came back as 10 minutes, ~58 km/h
    average, nothing like real Mumbai traffic. Safe to treat `distance_km`
    (real road-network distance) as reliable; do not present
    `duration_min` to a reader as a real travel-time estimate -- callers
    that only want a trustworthy number should use distance_km alone (see
    company_charter._compute_landmark_distances, which does exactly this)."""
    global _last_osrm_at
    if not origin_coords or not dest_coords:
        return None

    elapsed = time.monotonic() - _last_osrm_at
    if elapsed < _OSRM_MIN_INTERVAL_S:
        time.sleep(_OSRM_MIN_INTERVAL_S - elapsed)
    _last_osrm_at = time.monotonic()

    lat1, lon1 = origin_coords
    lat2, lon2 = dest_coords
    url = _OSRM_URL_TEMPLATE.format(f"{lon1},{lat1};{lon2},{lat2}")
    try:
        resp = requests.get(
            url,
            params={"overview": "false"},
            headers={"User-Agent": _GEOCODE_USER_AGENT},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        route = data["routes"][0]
        return {"distance_km": route["distance"] / 1000.0, "duration_min": route["duration"] / 60.0}
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None

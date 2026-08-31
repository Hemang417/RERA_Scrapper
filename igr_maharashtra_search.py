"""
Human-in-the-loop search of IGR Maharashtra's free e-Search
(freesearchigrservice.maharashtra.gov.in) -- the registered-deed register
audited live 2026-09-01 (see build_data_coverage.py's CTS sheet, "Registered
deeds / parties / consideration" and "Equitable mortgages"). This is the
only independent, party-named check on a promoter's registered land dealings
this pipeline has found for Maharashtra: a real result carries the seller,
the purchaser, the full property description and the ACTUAL CONSIDERATION
AMOUNT in one row, confirmed live against Document Number search (SRO
"Joint S.R. Mumbai 9 (Andheri 2 (Andheri))", 2024, doc #100 -- a Leave &
License agreement naming Ramesh Babulal Shah as lessor and M/s Matushri
Impex as lessee, Rs 44,100/month plus Rs 5,29,200 advance).

SAME POLICY AS EVERY OTHER CAPTCHA-GATED MODULE HERE (up_captcha_search.py /
mahabhumi.py / gst_portal.py / session_auth.py): nothing in this file reads
or solves a CAPTCHA image. A real, VISIBLE browser opens, a human reads it
and types it in themselves, and this only reads the page AFTER that human
submits.

TWO SEARCH MODES, both confirmed live, ONE confirmed against a real
returned row:

  Document Number (search_by_document_number) -- Registration Type
  (eFiling/eRegistration/Regular/iSarita 2.0) -> District (all 37, genuinely
  statewide) -> SRO -> Year -> Doc No. THE MODE ACTUALLY CONFIRMED against a
  real result -- _parse_document_results below is pinned against that exact
  row's HTML.

  Property Details (search_by_property) -- Year -> District -> [Taluka ->]
  Village/Area -> Survey/CTS/Milkat/Gat/Plot No. THREE SEPARATE REGIONS,
  each its own field ids and cascade shape -- "mumbai" (2 districts, a
  free-text village autocomplete), "rest_of_maharashtra" (35 districts,
  REQUIRES a Taluka before Village cascades), "urban" (32 municipal
  corporation/council areas, District cascades straight to Area, no
  taluka). See the region-fields comment above search_by_property for the
  full live-confirmed detail, including why the SAME locality can sit in
  either the rural or the urban region depending on how it was annexed --
  Pune's Market Yard (Gulatekadi) is in "urban", not the rural taluka/
  village list a human would guess first. Form mechanics confirmed live
  for all three (the CAPTCHA gate accepts a correctly-typed answer --
  "Entered Correct Captcha" was seen), but no property number tried this
  pass belonged to a real registered parcel, so THE RESULT TABLE'S OWN
  SHAPE IS NOT CONFIRMED for any region. search_by_property returns
  whatever rendered rather than guessing a schema no live row has shown.

A GENUINE LIMITATION, from the site's own FAQ, not a gap in this script:
Power of Attorney and Will deeds are never returned by either search mode.

EQUITABLE MORTGAGES (Notice of Intimation) are not a separate filter here --
"Registration Type" is a filing CHANNEL (eFiling/eRegistration/Regular/
iSarita 2.0), not a deed-type category. A mortgage shows up as one more
possible DName value in the exact same result shape _parse_document_results
already reads, discoverable by document number like any other deed --
search_by_document_number is the same call for a mortgage as for a sale
deed, no separate function needed for it.

    python igr_maharashtra_search.py docno "<district>" "<SRO contains>" <year> <doc_no> [registration_type]
    python igr_maharashtra_search.py property <region> "<district>" "<village/area>" <property_no> ["<taluka>"]

Both need a human at the keyboard. `region` is one of mumbai /
rest_of_maharashtra / urban -- see the module comment above search_by_
property if you're not sure which one covers a given locality; `<taluka>`
is required (and only used) when region=rest_of_maharashtra. `district` is
matched against _DISTRICT_HINTS first (Mumbai, Mumbai Suburban, Pune are
spelled out, since those are what this pipeline's own subjects have needed
so far), then used as typed -- pass the exact Marathi label shown on the
portal for anything else.
"""

from __future__ import annotations

import sys
import time

from bs4 import BeautifulSoup

import config

_BASE_URL = "https://freesearchigrservice.maharashtra.gov.in/"

# The portal's own district dropdown is Marathi-only. Filled in as this
# pipeline's actual subjects have needed them -- not a full 37-district
# translation table, which nothing here has needed yet. Add one the same
# way: confirm the exact Marathi label live off the portal's own dropdown
# before trusting a translation, since a wrong guess would silently search
# the wrong district rather than fail loudly.
_DISTRICT_HINTS = {
    "mumbai": "मुंबई जिल्हा",
    "mumbai city": "मुंबई जिल्हा",
    "mumbai suburban": "मुंबई उपनगर जिल्हा",
    "mumbai suburb": "मुंबई उपनगर जिल्हा",
    "pune": "पुणे",
}

_REGISTRATION_TYPE_RADIO = {
    "efiling": "#rblDocType_0",
    "eregistration": "#rblDocType_1",
    "regular": "#rblDocType_2",
    "isarita": "#rblDocType_3",
    "isarita 2.0": "#rblDocType_3",
}


class CaptchaTimeoutError(Exception):
    pass


class BrowserClosedError(Exception):
    pass


def _launch(headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright is required. Run: pip install playwright && playwright install chromium"
        ) from e
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=headless)
    page = browser.new_context(viewport={"width": 1366, "height": 950}).new_page()
    return p, browser, page


def _settle(page, timeout_ms=15000):
    """After a script-driven change that MIGHT autopostback (ASP.NET
    WebForms style -- a real full-page reload, not a same-page AJAX
    refresh), wait for the network to go quiet. Never raises: a change that
    does NOT postback (pure client-side) just times out here harmlessly.

    15s, not the 6s up_captcha_search.py's own version of this defaults
    to: confirmed live this portal's own postbacks can take 10-12s+ to
    settle, well past that shorter default -- a caller that checked a
    dropdown's options right after a too-short timeout would find it
    still empty and misreport a genuine match as "not found"."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def _resolve_district(hint: str) -> str:
    return _DISTRICT_HINTS.get(hint.strip().casefold(), hint)


def _select_by_contains(page, selector: str, text_contains: str,
                         timeout_ms: int = 20000) -> str | None:
    """Selects the first <option> whose text contains `text_contains`
    (case-insensitive), returning the matched option's own text, or None if
    nothing matched within `timeout_ms`. Used for SRO (English, but long
    and inconsistently formatted -- "Joint S.R. Mumbai 9 (Andheri 2
    (Andheri))") where an exact label match is brittle.

    POLLS rather than reading the option list once: the same _settle()
    unreliability _fill_village_area works around applies to whatever
    postback populated this dropdown too (a district select, here), so a
    caller relying on _settle() alone before calling this could see an
    empty/stale option list on a page that would have had the real one a
    couple of seconds later."""
    needle = text_contains.strip().casefold()
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        for opt in page.query_selector_all(f"{selector} option"):
            label = opt.inner_text() or ""
            if needle in label.casefold():
                page.select_option(selector, value=opt.get_attribute("value"))
                return label
        page.wait_for_timeout(1000)
    return None


def _wait_for_human_submit(page, timeout_seconds, request_predicate, post_submit_marker=None):
    """Blocks until a request matching `request_predicate` fires, or
    `timeout_seconds` elapses -- the SAME shape as up_captcha_search.py's
    helper of the same name, because the failure modes are identical on an
    ASP.NET WebForms page: waiting for ANY navigation or ANY body-text
    change trips on things a human does before ever reaching Search (a
    district select's own postback, a CAPTCHA refresh click). The
    predicate must therefore name the SPECIFIC request the real Search
    button makes -- confirmed live here for both modes: property search
    POSTs the control name `btnSearch`, document-number search POSTs
    `btnSearchDoc`.
    """
    print(f"[INFO] Waiting up to {timeout_seconds}s for you to solve the CAPTCHA and click Search...")
    try:
        with page.expect_request(request_predicate, timeout=timeout_seconds * 1000):
            pass
    except Exception as e:
        if page.is_closed():
            raise BrowserClosedError("Browser window was closed before the CAPTCHA was solved.") from e
        raise CaptchaTimeoutError(
            f"No Search submit within {timeout_seconds}s -- the CAPTCHA wasn't solved in time."
        ) from e
    try:
        page.wait_for_load_state("load", timeout=8000)
    except Exception:
        pass
    _settle(page, timeout_ms=8000)
    if post_submit_marker:
        try:
            page.wait_for_selector(post_submit_marker, timeout=15000)
        except Exception:
            pass
    time.sleep(1.0)


def _wait_for_search_to_finish(page, timeout_seconds=120):
    """After a human submits, THIS PORTAL CAN GENUINELY TAKE MINUTES to
    render the real result -- its own FAQ says so outright ("Search result
    will take few minutes to show result"). _wait_for_human_submit only
    proves the Search POST fired and the page began reloading; it does
    NOT prove the result has actually rendered by the time it returns.

    CONFIRMED LIVE 2026-09-01, a real human CAPTCHA solve against
    region=urban/Pune/Gulatekadi/3223: reading raw_text right after
    _wait_for_human_submit returned captured the page BACK AT THE BLANK
    SEARCH FORM with the portal's own 'Please Wait.....' marker still in
    it -- a fully-formed, correct-length page that looks like an ordinary
    result to any check based on response size or exception-freedom alone.
    `found` was reported as unknown and the real result, whatever it was,
    was never actually seen.

    Polls for that literal marker to DISAPPEAR from the body text rather
    than trusting a fixed wait -- the loading text as this portal writes
    it (checked case-insensitively since capitalisation was not itself
    confirmed stable). Returns whether it actually cleared within
    `timeout_seconds`; a caller should still treat a raw_text capture
    taken after a False return as possibly incomplete, not as "no
    results" -- the two must not be conflated."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            text = page.inner_text("body")
        except Exception:
            text = ""
        if "please wait" not in text.casefold():
            return True
        page.wait_for_timeout(2000)
    return False


def _safe_read(read_fn, attempts=6, delay=1.0, min_length=0):
    """Retries `read_fn()` against Playwright's "page is navigating" race
    (the resulting page can still be in flight a moment after the search
    request fires) and against a read that succeeds but is suspiciously
    short (a mid-navigation blank interim document) -- the same two races
    up_captcha_search.py's helper of the same name guards, confirmed there
    against a real live CAPTCHA solve."""
    last_exc = None
    last_value = None
    for _ in range(attempts):
        try:
            value = read_fn()
        except Exception as e:  # noqa: BLE001 -- narrowed by the retry itself
            last_exc = e
            time.sleep(delay)
            continue
        if min_length and len(value) < min_length:
            last_value = value
            time.sleep(delay)
            continue
        return value
    if last_exc is not None:
        raise last_exc
    return last_value


# --- Document Number search -- CONFIRMED against a real returned row ------
#
# Live example (2026-09-01), the exact shape this parser is pinned against:
#   DocNo=100  DName="36-अ-लिव्ह अॅड लायसन्सेस" (Leave & License)
#   RDate=03/01/2024  SROName="सह दु.नि.मुंबई 9"
#   Seller Name={रमेशबबालालशाह}  Purchaser Name={"मे. मातुश्री इम्पेक्सतर्फे..."}
#   Property Description=<full address, area, CTS no., consideration text>
#   SROCode=323  Status=4  IndexII=<button>

def _parse_document_results(html: str) -> list[dict]:
    """The Document Number search's own results table -- {header: cell} per
    row, keyed on the exact headers confirmed live (DocNo/DName/RDate/
    SROName/Seller Name/Purchaser Name/Property Description/SROCode/
    Status/IndexII). Pure: HTML in, dicts out, no network, so it is
    testable against the real captured row above without a live portal.

    The Seller/Purchaser cells render their own curly braces ({name},
    {"name"}) -- the portal's own formatting for a party list, kept as-is
    rather than stripped, since a multi-party deed's braces are the only
    thing separating one party from the next.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = [tr.find_all(["td", "th"]) for tr in table.find_all("tr")]
        rows = [r for r in rows if r]
        if not rows:
            continue
        header = [c.get_text(" ", strip=True).casefold() for c in rows[0]]
        if not all(any(h in cell for cell in header) for h in ("docno", "seller", "purchaser")):
            continue
        out = []
        for cells in rows[1:]:
            texts = [c.get_text(" ", strip=True) for c in cells]
            if not any(texts):
                continue
            out.append({header[i]: texts[i] for i in range(min(len(header), len(texts)))})
        return out
    return []


def search_by_document_number(
    district: str,
    sro_contains: str,
    year: int | str,
    doc_number: int | str,
    registration_type: str = "Regular",
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at IGR Maharashtra's Document Number search,
    pre-fills Registration Type/District/SRO/Year/Doc No., then waits for a
    human to read the CAPTCHA and click Search.

    `district` is matched against _DISTRICT_HINTS first (see module
    docstring), then used as-is -- pass the exact Marathi label for a
    district not in that small table. `sro_contains` is a case-insensitive
    substring match against the SRO dropdown's own (English) option text,
    populated only after `district` is selected.

    Returns {"found", "rows", "raw_text", "url", "note"}. `rows` is []
    both when nothing rendered and when a results table rendered with zero
    entries -- `note` says which. Never raises for a normal empty result;
    only for a genuinely unreachable page or an unmatched district/SRO/
    registration type, which are configuration errors, not CAPTCHA
    outcomes.
    """
    registration_type_key = registration_type.strip().casefold()
    if registration_type_key not in _REGISTRATION_TYPE_RADIO:
        return {
            "found": False, "rows": [], "raw_text": "", "url": _BASE_URL,
            "note": f"Unknown registration_type {registration_type!r} -- expected one of "
                    f"{sorted(set(_REGISTRATION_TYPE_RADIO))}.",
        }

    p, browser, page = _launch(headless=False)
    try:
        page.goto(_BASE_URL, timeout=30000)
        # The landing page opens on the "Property Details" tab and shows a
        # "Search Flow" help modal over it -- confirmed live both are
        # present on a fresh load, in that order.
        try:
            page.click("text=Close", timeout=5000)
        except Exception:
            pass
        page.evaluate("__doPostBack('mnuSearchType','3')")  # switch to Document Number
        # Confirmed live this postback alone can take 10-12s+ to settle,
        # well past a plain _settle() call's own default timeout -- this
        # explicit wait is what actually matters here, not that helper.
        page.wait_for_selector("#rblDocType_0", timeout=25000)

        page.check(_REGISTRATION_TYPE_RADIO[registration_type_key])

        district_label = _resolve_district(district)
        page.select_option("#ddldistrictfordoc", label=district_label)
        _settle(page)  # district select postbacks to populate SRO

        matched_sro = _select_by_contains(page, "#ddlSROName", sro_contains)
        if matched_sro is None:
            return {
                "found": False, "rows": [], "raw_text": "", "url": page.url,
                "note": f"No SRO option for district {district_label!r} contained "
                        f"{sro_contains!r}.",
            }

        page.select_option("#ddlYearForDoc", label=str(year))
        page.fill("#txtDocumentNo", str(doc_number))

        print(f"\n[INFO] A browser window has opened at {_BASE_URL}")
        print(f"[INFO] Registration Type={registration_type}, District={district_label}, "
              f"SRO={matched_sro!r}, Year={year}, Doc No.={doc_number} are pre-filled.")
        print("[INFO] Please read the CAPTCHA shown there, type it into the box, and click Search.")
        try:
            _wait_for_human_submit(
                page, timeout_seconds,
                request_predicate=lambda req: req.method == "POST" and "btnSearchDoc" in (req.post_data or ""),
                post_submit_marker="#txtDocumentNo",
            )
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "rows": [], "raw_text": "", "url": page.url, "note": str(e)}

        finished = _wait_for_search_to_finish(page)
        html = _safe_read(page.content, min_length=5000)
        body_text = _safe_read(lambda: page.inner_text("body"), min_length=200)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))
        rows = _parse_document_results(html)
        if rows:
            note = ""
        elif not finished:
            note = (
                "The portal's own 'Please Wait.....' marker was still present when this was read -- "
                "the site's own FAQ warns results can take minutes, and this capture may be "
                "incomplete rather than a genuine empty result. Re-check raw_text/screenshot, or "
                "re-run with a longer wait, before concluding this document number doesn't exist."
            )
        else:
            note = (
                "No results table naming DocNo/Seller/Purchaser rendered after submit, and the "
                "'Please Wait.....' marker had cleared -- either the search did not actually land, "
                "or it landed and this document number does not exist for this SRO/year. Check "
                "raw_text/screenshot to tell which."
            )
        return {"found": bool(rows), "rows": rows, "raw_text": body_text, "url": page.url, "note": note}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


# --- Property Details search -- form mechanics confirmed, result shape NOT
#
# THREE REGIONS, discovered live 2026-09-01 -- NOT one form with a district
# dropdown, as the landing page's default (Mumbai) tab alone suggested. The
# three buttons across the top of Property Details (Mumbai / Rest of
# Maharashtra / Urban Areas in Rest of Maharashtra) each swap in a
# DIFFERENT set of field ids and a different cascade shape entirely:
#
#   mumbai -- #ddlDistrict (2 options: the two Mumbai districts only) ->
#     #txtAreaName, a free-text field whose own postback populates the
#     companion #ddlareaname select (see _fill_cascading_select's mumbai
#     branch). #txtAttributeValue / #txtImg / #btnSearch.
#
#   rest_of_maharashtra -- #ddlDistrict1 (35 options, genuinely statewide)
#     -> #ddltahsil (Taluka, a REQUIRED third level Mumbai's flow doesn't
#     have at all) -> #ddlvillage, a plain cascading <select>, no free text
#     involved. #txtAttributeValue1 / #txtImg1 / #btnSearch_RestMaha.
#     CONFIRMED LIVE against Pune: 14 real talukas, one of which (Pune
#     City) cascades to only 6 villages -- all OUTLYING areas annexed into
#     the city, not the old city core.
#
#   urban -- #ddlDistrictUrban (32 options -- municipal corporation/council
#     areas) -> #ddlareanameUrban, a plain cascading <select> straight off
#     the district, no taluka step at all. #txtAttributeValueUrban /
#     #txtImgUrban / #btnSearchUrban. CONFIRMED LIVE this is where Pune's
#     old-city core actually lives: selecting District="पुणे" here (the
#     SAME Marathi label as rest_of_maharashtra's district, despite being a
#     different <select> entirely) cascaded 47 real areas in ENGLISH,
#     including "Gulatekadi" -- the Market Yard locality neither the
#     rural-village Pune City taluka NOR any obvious substring match found.
#
# THE LESSON THIS COST: a locality that reads as part of "the city" to a
# human can sit in EITHER the rural or the urban region depending on
# whether it was annexed as a former village (rest_of_maharashtra) or was
# always inside the municipal corporation's own ward system (urban) -- and
# guessing wrong doesn't error, it just returns a real but WRONG village
# list with no locality that matches, which looks identical to "this
# locality genuinely isn't searchable here" unless both regions are tried.

_REGION_FIELDS = {
    "mumbai": {
        "year": "#ddlFromYear", "district": "#ddlDistrict",
        "property": "#txtAttributeValue", "captcha": "#txtImg",
        "search": "#btnSearch", "search_control_name": "btnSearch",
    },
    "rest_of_maharashtra": {
        "year": "#ddlFromYear1", "district": "#ddlDistrict1", "taluka": "#ddltahsil",
        "village": "#ddlvillage", "property": "#txtAttributeValue1", "captcha": "#txtImg1",
        "search": "#btnSearch_RestMaha", "search_control_name": "btnSearch_RestMaha",
    },
    "urban": {
        "year": "#ddlFromYearUrban", "district": "#ddlDistrictUrban", "village": "#ddlareanameUrban",
        "property": "#txtAttributeValueUrban", "captcha": "#txtImgUrban",
        "search": "#btnSearchUrban", "search_control_name": "btnSearchUrban",
    },
}

_REGION_TAB_BUTTON = {
    "mumbai": None,  # the landing page's own default tab -- no click needed
    "rest_of_maharashtra": "#btnOtherdistrictSearch",
    "urban": "#btnUrbansearch",
}


def _select_cascading(page, selector: str, label: str, timeout_ms: int = 25000) -> bool:
    """Selects the <option> whose text exactly matches `label` (case-
    insensitive), polling rather than reading the option list once.

    Confirmed live this portal's own "networkidle" signal can report a
    cascading dropdown idle well before it actually repopulates from the
    postback that just fired (some background connection on this page
    apparently never reads as fully idle) -- a caller trusting a fixed
    settle() wait alone found a stale/empty option list where a poll found
    the real one a few seconds later. Shared by every cascading select in
    this module (Rest of Maharashtra's taluka/village, Urban's district/
    area) -- each hits the identical race, confirmed independently for
    more than one of them.
    """
    needle = label.strip().casefold()
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        for opt in page.query_selector_all(f"{selector} option"):
            if (opt.inner_text() or "").strip().casefold() == needle:
                page.select_option(selector, value=opt.get_attribute("value"))
                return True
        page.wait_for_timeout(1000)
    return False


def _fill_mumbai_area(page, village: str, timeout_ms: int = 25000) -> bool:
    """Mumbai's OWN shape: #txtAreaName is free text whose own onchange
    handler fires `__doPostBack('txtAreaName','')` -- confirmed live that a
    plain fill() alone leaves #ddlareaname's options empty, since the
    site's autocomplete only resolves on a genuine change-triggered
    postback, not on keystrokes. Neither rest_of_maharashtra nor urban has
    an equivalent free-text step; their area/village selects cascade
    straight off a district or taluka select instead."""
    page.fill("#txtAreaName", village)
    page.evaluate("__doPostBack('txtAreaName','')")
    return _select_cascading(page, "#ddlareaname", village, timeout_ms)


def search_by_property(
    region: str,
    district: str,
    village: str,
    property_number: str,
    taluka: str | None = None,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at IGR Maharashtra's Property Details search
    under the given `region` ("mumbai" | "rest_of_maharashtra" | "urban" --
    see the module-level comment above for what each actually covers and
    why a locality can sit in either the rural or urban one), pre-fills
    District/[Taluka/]Village/Property No., then waits for a human to read
    the CAPTCHA and click Search. Year defaults to the portal's own
    current-year default -- not set here, since a promoter's registration
    could be any past year and there is no single sensible default.

    `taluka` is REQUIRED for region="rest_of_maharashtra" (that region's
    village select cascades off Taluka, not District directly) and ignored
    for the other two, which have no taluka step at all.

    UNLIKE search_by_document_number, the result table's own shape is NOT
    confirmed live for ANY region -- no property number tried during the
    audit that found this portal belonged to a real registered parcel, so
    no real row was ever seen to pin a parser against. This returns
    whatever the page's body text/HTML actually shows rather than guessing
    a schema; a caller needs to read raw_text (or the screenshot) to see
    what came back, and a proper _parse_property_results should be written
    the next time this runs against a real result -- not before.
    """
    if region not in _REGION_FIELDS:
        return {
            "found": False, "rows": [], "raw_text": "", "url": _BASE_URL,
            "note": f"Unknown region {region!r} -- expected one of {sorted(_REGION_FIELDS)}.",
        }
    if region == "rest_of_maharashtra" and not taluka:
        return {
            "found": False, "rows": [], "raw_text": "", "url": _BASE_URL,
            "note": "region='rest_of_maharashtra' requires `taluka` -- its village select "
                    "cascades off Taluka, not District directly.",
        }

    fields = _REGION_FIELDS[region]
    district_label = _resolve_district(district)

    p, browser, page = _launch(headless=False)
    try:
        page.goto(_BASE_URL, timeout=30000)
        try:
            page.click("text=Close", timeout=5000)
        except Exception:
            pass

        tab_button = _REGION_TAB_BUTTON[region]
        if tab_button:
            page.click(tab_button)
            # Confirmed live this switch alone can take well past a plain
            # _settle() call before the new region's own fields even exist
            # in the DOM -- wait for the district select itself, not a
            # generic idle signal.
            page.wait_for_selector(fields["district"], timeout=30000)

        page.select_option(fields["district"], label=district_label)
        _settle(page)
        page.wait_for_timeout(3000)  # see _select_cascading's own docstring on why

        if region == "rest_of_maharashtra":
            if not _select_cascading(page, fields["taluka"], taluka):
                return {
                    "found": False, "rows": [], "raw_text": "", "url": page.url,
                    "note": f"No taluka option for district {district_label!r} matched "
                            f"{taluka!r} exactly.",
                }
            village_matched = _select_cascading(page, fields["village"], village)
        elif region == "urban":
            village_matched = _select_cascading(page, fields["village"], village)
        else:  # mumbai
            village_matched = _fill_mumbai_area(page, village)

        if not village_matched:
            return {
                "found": False, "rows": [], "raw_text": "", "url": page.url,
                "note": f"No village/area option matching {village!r} appeared for district "
                        f"{district_label!r}"
                        + (f", taluka {taluka!r}" if region == "rest_of_maharashtra" else "")
                        + f" under region={region!r}. A locality can sit in a DIFFERENT region "
                          f"than expected -- e.g. Pune's Market Yard (Gulatekadi) is under "
                          f"region='urban', not the rural taluka/village list under "
                          f"region='rest_of_maharashtra' -- try the other region before "
                          f"assuming the locality isn't searchable at all. The portal's own FAQ "
                          f"also notes some names need an unusual spelling (e.g. 'Daadar' not "
                          f"'Dadar').",
            }

        page.fill(fields["property"], str(property_number))

        print(f"\n[INFO] A browser window has opened at {_BASE_URL}")
        print(f"[INFO] Region={region}, District={district_label}, "
              + (f"Taluka={taluka!r}, " if region == "rest_of_maharashtra" else "")
              + f"Village/Area={village!r}, Property No.={property_number} are pre-filled.")
        print("[INFO] Please read the CAPTCHA shown there, type it into the box, and click Search.")
        control_name = fields["search_control_name"]
        try:
            _wait_for_human_submit(
                page, timeout_seconds,
                request_predicate=lambda req: req.method == "POST" and control_name in (req.post_data or ""),
                post_submit_marker=fields["property"],
            )
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "rows": [], "raw_text": "", "url": page.url, "note": str(e)}

        finished = _wait_for_search_to_finish(page)
        body_text = _safe_read(lambda: page.inner_text("body"), min_length=200)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))
        note = (
            "Result table shape not confirmed live for this search mode -- see raw_text/screenshot "
            "for what actually came back, and use search_by_document_number instead where the "
            "document number is already known."
        )
        if not finished:
            note = (
                "The portal's own 'Please Wait.....' marker was STILL PRESENT when this was read -- "
                "confirmed live 2026-09-01 that reading too early here captures the page back at "
                "the blank search form, not a real result. This raw_text is likely incomplete; "
                "re-run with a longer wait or check the screenshot before drawing any conclusion "
                "from it. " + note
            )
        return {"found": None, "rows": [], "raw_text": body_text, "url": page.url, "note": note}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


if __name__ == "__main__":
    import os

    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    cmd = sys.argv[1]
    dump_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_igr_maharashtra_{cmd}_result.txt")
    if cmd == "docno" and len(sys.argv) >= 6:
        registration_type = sys.argv[6] if len(sys.argv) >= 7 else "Regular"
        result = search_by_document_number(
            sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], registration_type=registration_type,
        )
    elif cmd == "property" and len(sys.argv) >= 6:
        taluka = sys.argv[6] if len(sys.argv) >= 7 else None
        result = search_by_property(
            sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], taluka=taluka,
        )
    else:
        print(__doc__)
        raise SystemExit(2)
    print("\n--- RESULT ---")
    for k, v in result.items():
        if k == "raw_text":
            print(f"{k}: ({len(v)} chars, written to {dump_path})")
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(v)
        elif k == "rows":
            print(f"rows: {len(v)}")
            for row in v[:20]:
                print(f"  {row}")
        else:
            print(f"{k}: {v}")

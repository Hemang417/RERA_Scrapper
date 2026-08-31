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

  Property Details (search_by_property) -- Year -> District -> Village/Area
  (a free-text field that triggers its own ASP.NET postback to populate a
  companion dropdown -- NOT a plain <select>, see _fill_village_area) ->
  Survey/CTS/Milkat/Gat/Plot No. Form mechanics confirmed live (the village
  postback resolves, the CAPTCHA gate accepts a correctly-typed answer --
  "Entered Correct Captcha" was seen), but no property number tried this
  pass belonged to a real registered parcel, so THE RESULT TABLE'S OWN SHAPE
  IS NOT CONFIRMED for this mode. search_by_property returns whatever
  rendered rather than guessing a schema no live row has actually shown.

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
    python igr_maharashtra_search.py property "<district>" "<village/area>" <property_no>

Both need a human at the keyboard. `district` for docno mode is matched by
substring against the portal's own (Marathi-only) option text -- see
_DISTRICT_HINTS for the two Mumbai districts spelled out, since that pair is
where this pipeline's own subjects concentrate; anything else, pass the
exact Marathi label shown on the portal.
"""

from __future__ import annotations

import sys
import time

from bs4 import BeautifulSoup

import config

_BASE_URL = "https://freesearchigrservice.maharashtra.gov.in/"

# The portal's own district dropdown is Marathi-only. These are the two
# districts this pipeline's Maharashtra subjects concentrate in (Mumbai
# city and suburbs); anything else, the caller passes the exact Marathi
# label straight off the portal -- building a full 37-district translation
# table is not worth it for districts nothing here has ever needed yet.
_DISTRICT_HINTS = {
    "mumbai": "मुंबई जिल्हा",
    "mumbai city": "मुंबई जिल्हा",
    "mumbai suburban": "मुंबई उपनगर जिल्हा",
    "mumbai suburb": "मुंबई उपनगर जिल्हा",
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

        html = _safe_read(page.content, min_length=5000)
        body_text = _safe_read(lambda: page.inner_text("body"), min_length=200)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))
        rows = _parse_document_results(html)
        note = "" if rows else (
            "No results table naming DocNo/Seller/Purchaser rendered after submit -- either the "
            "search did not actually land, or it landed and this document number does not exist "
            "for this SRO/year. Check raw_text/screenshot to tell which."
        )
        return {"found": bool(rows), "rows": rows, "raw_text": body_text, "url": page.url, "note": note}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


# --- Property Details search -- form mechanics confirmed, result shape NOT

def _fill_village_area(page, village: str, timeout_ms: int = 25000) -> bool:
    """Sets the Village/Area free-text field and fires the ASP.NET postback
    its own onchange handler expects (`__doPostBack('txtAreaName','')`) --
    confirmed live that a plain fill() alone leaves the companion
    dropdown's options empty, since the site's autocomplete only resolves
    on a genuine change-triggered postback, not on keystrokes. Returns
    whether a matching area option appeared afterward.

    POLLS THE DROPDOWN DIRECTLY rather than calling _settle() and trusting
    it -- confirmed live this postback's own "networkidle" signal can fire
    well before the dropdown actually repopulates (some background
    connection on this page apparently never reads as fully idle), so a
    caller trusting _settle() alone found zero options where 25s of plain
    waiting found the real one. Polling the actual DOM condition is what
    the CALLER cares about, not a proxy for it.
    """
    page.fill("#txtAreaName", village)
    page.evaluate("__doPostBack('txtAreaName','')")
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        for opt in page.query_selector_all("#ddlareaname option"):
            if (opt.inner_text() or "").strip().casefold() == village.strip().casefold():
                page.select_option("#ddlareaname", value=opt.get_attribute("value"))
                return True
        page.wait_for_timeout(1000)
    return False


def search_by_property(
    district: str,
    village: str,
    property_number: str,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at IGR Maharashtra's Property Details search
    (Year defaults to the portal's own current-year default -- not set here,
    since a promoter's registration could be any past year and there is no
    single sensible default), pre-fills District/Village/Property No., then
    waits for a human to read the CAPTCHA and click Search.

    UNLIKE search_by_document_number, the result table's own shape is NOT
    confirmed live -- no property number tried during the audit that found
    this portal belonged to a real registered parcel, so no real row was
    ever seen to pin a parser against. This returns whatever the page's
    body text/HTML actually shows rather than guessing a schema; a caller
    needs to read raw_text (or the screenshot) to see what came back, and
    a proper _parse_property_results should be written the next time this
    runs against a real result -- not before.
    """
    district_label = _resolve_district(district)

    p, browser, page = _launch(headless=False)
    try:
        page.goto(_BASE_URL, timeout=30000)
        try:
            page.click("text=Close", timeout=5000)
        except Exception:
            pass
        # The landing page defaults to Property Details already; no tab
        # switch needed, unlike the Document Number search above.

        page.select_option("#ddlDistrict", label=district_label)
        _settle(page)
        # _settle's own networkidle wait is unreliable on this page (see
        # _fill_village_area) -- a flat floor here too, since a district
        # postback that hasn't actually landed server-side yet would make
        # the village postback right after it resolve against the WRONG
        # district's data rather than simply returning late.
        page.wait_for_timeout(3000)

        if not _fill_village_area(page, village):
            return {
                "found": False, "rows": [], "raw_text": "", "url": page.url,
                "note": f"No village/area option matching {village!r} appeared for district "
                        f"{district_label!r} -- the portal's own FAQ notes some names need an "
                        f"unusual spelling (e.g. 'Daadar' not 'Dadar').",
            }

        page.fill("#txtAttributeValue", str(property_number))

        print(f"\n[INFO] A browser window has opened at {_BASE_URL}")
        print(f"[INFO] District={district_label}, Village/Area={village!r}, "
              f"Property No.={property_number} are pre-filled.")
        print("[INFO] Please read the CAPTCHA shown there, type it into the box, and click Search.")
        try:
            _wait_for_human_submit(
                page, timeout_seconds,
                request_predicate=lambda req: req.method == "POST" and "btnSearch" in (req.post_data or ""),
                post_submit_marker="#txtAttributeValue",
            )
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "rows": [], "raw_text": "", "url": page.url, "note": str(e)}

        body_text = _safe_read(lambda: page.inner_text("body"), min_length=200)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))
        return {
            "found": None, "rows": [], "raw_text": body_text, "url": page.url,
            "note": "Result table shape not confirmed live for this search mode -- see raw_text/"
                    "screenshot for what actually came back, and use search_by_document_number "
                    "instead where the document number is already known.",
        }
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
    elif cmd == "property" and len(sys.argv) >= 5:
        result = search_by_property(sys.argv[2], sys.argv[3], sys.argv[4])
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

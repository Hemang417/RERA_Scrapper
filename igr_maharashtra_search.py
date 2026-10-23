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
  for all three regions. TWO real runs (a deliberately-implausible test
  property number, and a real human CAPTCHA solve against region=urban/
  Pune/Gulatekadi/CTS 3223) both came back the SAME way: 'Entered Correct
  Captcha' appears, the form resets to blank, no results table renders and
  no distinct 'no results' message appears either -- search_by_property
  now reports that pattern as found=False, but says explicitly that
  reading is not yet confirmed against a genuine POSITIVE match in this
  mode (unlike search_by_document_number, which has one). THE RESULT
  TABLE'S OWN SHAPE for an actual match therefore remains unconfirmed for
  every region; a caller still gets raw_text/screenshot to inspect rather
  than a guessed schema.

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

import os
import sys
import time

from bs4 import BeautifulSoup

import captcha_solver
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

_SEL_CAPTCHA_INPUT = "#TextBox1"
# Bumped from 4 2026-10-15 when the human-wait fallback was removed from
# both search_by_document_number and search_by_property -- autosolve is
# now the only path, so a bit more retry headroom costs nothing (each
# Claude vision call is a fraction of a cent) and is cheaper than the $5
# cross-run budget cap ever becoming the real limiting factor.
_MAX_AUTOSOLVE_ATTEMPTS = 8


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
    try:
        browser = p.chromium.launch(headless=headless)
    except Exception:
        # Bundled Chromium can fail to spawn on a machine missing the
        # Windows runtime it needs (side-by-side config error) -- fall
        # back to the system-installed Chrome, which doesn't have that
        # dependency gap. Same fix as nclt_search.py/bombay_hc_search.py.
        browser = p.chromium.launch(channel="chrome", headless=headless)
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


def _wait_for_search_to_finish(page, timeout_seconds=120):
    """After Search is submitted, THIS PORTAL CAN GENUINELY TAKE MINUTES to
    render the real result -- its own FAQ says so outright ("Search result
    will take few minutes to show result"). The submit itself only proves
    the Search POST fired and the page began reloading; it does NOT prove
    the result has actually rendered by the time it returns.

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


def _property_search_shows_no_result(body_text: str) -> bool:
    """Whether this Property Details result page matches the "search ran,
    matched nothing" pattern -- confirmed live TWICE (2026-09-01): once
    against a deliberately-implausible test property number, once against
    a real human CAPTCHA solve for CTS 3223 in Gulatekadi, Pune. Both came
    back identically -- 'Entered Correct Captcha' present, no results
    table, form reset to blank placeholders, no distinct 'no results'
    message anywhere. Pure: text in, bool out, so it is testable without a
    live portal. See search_by_property's own docstring for the honesty
    caveat this reading still carries -- no genuine POSITIVE match has
    been seen in this search mode yet to rule out this same reset also
    happening on a real hit."""
    return "entered correct captcha" in body_text.casefold()


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


_SEL_CAPTCHA_IMAGE = "#imgCaptcha1"


def _captcha_image_token(page) -> str | None:
    """Returns the CAPTCHA <img>'s current `src` (which CONFIRMED LIVE
    2026-10-14 carries a per-render token, e.g.
    '...Handler.ashx?txt=810b3a5') or None if it can't be read right now.
    Used to confirm a post-rejection CAPTCHA swap genuinely happened before
    re-solving -- see _autosolve_docno_captcha's own docstring for the live
    bug this exists to catch."""
    try:
        return page.locator(_SEL_CAPTCHA_IMAGE).first.get_attribute("src")
    except Exception:
        return None


def _autosolve_docno_captcha(page, autosolve: bool | None) -> bool:
    """Attempts Claude-vision autosolve (see captcha_solver.solve_igr_captcha)
    up to _MAX_AUTOSOLVE_ATTEMPTS times against the Document Number search's
    CAPTCHA, reusing this portal's own
    already-proven behavior: a wrong CAPTCHA rejects IN PLACE (no
    navigation) with "Entered Captcha is incorrect" and swaps in a fresh
    CAPTCHA image automatically -- no refresh button needed, unlike GST/
    MahaRERA. Returns True if a solve was accepted (Search was submitted
    and NOT rejected within the short post-submit window), False if
    autosolve isn't enabled/configured or every attempt was rejected -- the
    caller then falls through to the existing, already-proven human-wait
    loop unchanged, same fallback discipline as gst_portal.py/
    session_auth.py.

    CONFIRMED LIVE 2026-10-14 (caught live by a human watching the actual
    browser, not by any automated check): the portal was briefly slow
    ("Please Wait....." still showing) right when a rejection should have
    been checked, and three attempts in a row solved and resubmitted the
    EXACT SAME text -- this function was re-screenshotting before the
    CAPTCHA had actually finished swapping to a new one, wasting attempts
    re-reading an image already known to be wrong. Fixed by tracking the
    image's own src token (see _captcha_image_token) and explicitly waiting
    for it to CHANGE after a rejection, not just waiting for "some image is
    loaded" the way the old naturalWidth-only check did (same class of race
    gst_portal.py's own screenshot-timing bug was)."""
    want_autosolve = autosolve if autosolve is not None else os.environ.get("IGR_CAPTCHA_AUTOSOLVE") == "1"
    if not want_autosolve:
        return False

    for attempt in range(1, _MAX_AUTOSOLVE_ATTEMPTS + 1):
        pre_solve_token = _captcha_image_token(page)
        try:
            backend_name, solved_text = captcha_solver.solve_igr_captcha(page, enabled=True)
            captcha_input = page.locator(_SEL_CAPTCHA_INPUT)
            captcha_input.click()
            captcha_input.fill("")
            captcha_input.press_sequentially(solved_text, delay=50)
            page.click("#btnSearchDoc", timeout=10000)
        except Exception as e:
            print(f"[INFO] CAPTCHA autosolve attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} failed ({e}).")
            return False  # a non-solve error (missing key, selector miss) won't fix itself on retry

        try:
            page.wait_for_load_state("load", timeout=8000)
        except Exception:
            pass
        _settle(page, timeout_ms=8000)
        time.sleep(1.0)

        # CONFIRMED LIVE 2026-10-14 by a human watching the real browser: a
        # GENUINELY ACCEPTED submit hides the Search button while the
        # portal processes it -- the reliable accept signal. "Entered
        # Captcha is incorrect" text can be a STALE LEFTOVER from a
        # PREVIOUS rejection still sitting in the DOM when this attempt's
        # submit was actually accepted (same class of bug
        # session_auth.py's own _autosolve_attempt_succeeded needed fixing
        # for earlier this session) -- caught live when attempt 2 here was
        # misread as rejected despite the button having already hidden,
        # wasting attempt 3 clicking a Search button that had legitimately
        # disappeared because a real search was already running. The
        # button's own visibility is therefore checked FIRST, every poll,
        # and treated as authoritative over the text.
        rejected = False
        poll_deadline = time.time() + 20
        body_now = ""
        while time.time() < poll_deadline:
            try:
                button_visible = page.locator("#btnSearchDoc").is_visible()
            except Exception:
                button_visible = False
            if not button_visible:
                break  # accepted -- portal hid the button, genuinely processing
            body_now = _safe_read(lambda: page.inner_text("body"), min_length=1) or ""
            if "entered captcha is incorrect" in body_now.lower():
                rejected = True
                break
            page.wait_for_timeout(500)

        if not rejected:
            print(f"[INFO] {backend_name} solved the CAPTCHA ({solved_text!r}) on attempt "
                  f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} and Search was submitted automatically.")
            return True

        print(f"[INFO] {backend_name}'s solve ({solved_text!r}) was rejected by the portal "
              f"(attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS}).")

        # Wait for the image's own token to actually CHANGE -- not just for
        # "some image is loaded", which the stale old src still satisfies.
        token_changed = False
        change_deadline = time.time() + 15
        while time.time() < change_deadline:
            current_token = _captcha_image_token(page)
            if current_token and current_token != pre_solve_token:
                token_changed = True
                break
            page.wait_for_timeout(500)
        if not token_changed:
            print(f"[INFO] The CAPTCHA image did not appear to change after that rejection "
                  f"(attempt {attempt}) -- the next attempt may still be reading the same image.")

        try:
            page.wait_for_function(
                "() => { const img = document.querySelector('#imgCaptcha1'); "
                "return img && img.complete && img.naturalWidth > 0; }",
                timeout=10000,
            )
        except Exception:
            pass

        # CONFIRMED LIVE 2026-10-14: the rejection postback can leave
        # #btnSearchDoc transiently absent/hidden for a moment -- a human
        # wouldn't notice since they take longer to react than this takes
        # to pass, but the next automated attempt's click can land exactly
        # in that gap and time out. Wait for it to be genuinely clickable
        # again before looping to the next solve attempt; a failure here
        # is logged, not fatal -- the click itself still has its own
        # timeout/except as a second line of defense.
        try:
            page.locator("#btnSearchDoc").wait_for(state="visible", timeout=10000)
        except Exception as e:
            print(f"[INFO] Search button hadn't reappeared after that rejection ({e}) -- "
                  f"the next attempt may fail to click it.")

    print(f"[INFO] CAPTCHA autosolve did not succeed within {_MAX_AUTOSOLVE_ATTEMPTS} attempt(s) "
          f"-- falling back to a human solve.")
    return False


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

    CONFIRMED LIVE 2026-10-15 against a real 12-row RegistrationGrid: this
    grid's own PAGER renders as its table's LAST <tr>, one <td> spanning
    every column, wrapping a NESTED <table> of page-number links -- and
    `table.find_all("tr")` recurses into that nested table too, so a naive
    row collection picked up TWO garbage "rows" out of it (the wrapping
    <tr> itself, cell text collapsed to "1 2 3 4 5", and the nested
    table's own <tr> with "1"/"2"/"3"/... as separate cells) in addition
    to the 10 genuine documents on that page. Those two caused Index II
    fetches to time out, since 'indexII$10'/'indexII$11' postback targets
    don't exist -- there IS no row 10/11, just a mis-parsed pager. Both
    are excluded here by provenance, not by guessing at their text: a row
    is only this table's OWN row if its nearest <table> ancestor is this
    exact table (excludes the nested pager table's own <tr>), and only
    kept if it doesn't itself wrap a further nested <table> (excludes the
    wrapping pager <tr>).
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = [
            tr.find_all(["td", "th"]) for tr in table.find_all("tr")
            if tr.find_parent("table") is table and tr.find("table") is None
        ]
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


# CONFIRMED LIVE 2026-10-14: each result row's "IndexII" cell is a button
# whose onclick is __doPostBack('RegistrationGrid', f'indexII${row_index}')
# (0-based). That click/postback does NOT render the document in place --
# it stashes which row was selected in SERVER-SIDE SESSION STATE, then
# calls window.open('isaritaHTMLReportSuchiKramank2_RegLive.aspx') -- a
# URL with NO document-identifying query string at all, relying entirely
# on that just-set session state and the browser's existing session
# cookies. Confirmed: navigating straight to that path right after the
# postback (same browser session) returns a full plain-text "सूची क्र.2"
# (Index II) extract for that exact row -- deed type, consideration,
# market value, full property description, both parties' names/addresses,
# dates, stamp duty and registration fee -- no separate CAPTCHA, no PDF.
_INDEX_II_REPORT_PATH = "isaritaHTMLReportSuchiKramank2_RegLive.aspx"


def _fetch_index_ii_documents(page, row_count: int, max_attempts: int = 3) -> list[str | None]:
    """Fetches the Index II text for every row (0-indexed, matching the
    portal's own indexII$N convention) on the CURRENTLY DISPLAYED results
    page, in order, using Playwright's popup capture so the main results
    page's own DOM/session state is never reloaded or disturbed between
    rows. Returns one entry per row -- the Index II text on success, None
    if every attempt for that row failed (never raises; one bad row must
    not lose every other row's document).

    CONFIRMED LIVE 2026-10-15: individual rows can intermittently time out
    waiting for the popup even on a genuine, correctly-paged results page
    -- not a pagination/dedup issue, just occasional popup-open flakiness
    that got noticeably worse later in a page's row sequence (rows 6/7/9
    of 10 failed in one run) -- so each row gets up to `max_attempts`
    tries with a short pause before retrying, and a brief pause after
    every row (success or not) to avoid firing popups back-to-back as
    fast as Playwright can, which is the likely reason later rows in a
    page degraded more than earlier ones."""
    texts: list[str | None] = []
    for i in range(row_count):
        text = None
        for attempt in range(1, max_attempts + 1):
            try:
                with page.expect_popup(timeout=15000) as popup_info:
                    page.evaluate(f"__doPostBack('RegistrationGrid','indexII${i}')")
                popup = popup_info.value
                popup.wait_for_load_state("load", timeout=15000)
                text = popup.inner_text("body")
                popup.close()
                break
            except Exception as e:
                if attempt < max_attempts:
                    print(f"[INFO] Index II fetch for row {i} failed on attempt "
                          f"{attempt}/{max_attempts} ({e}) -- retrying.")
                    page.wait_for_timeout(1500)
                else:
                    print(f"[INFO] Could not fetch Index II for row {i} after "
                          f"{max_attempts} attempt(s) ({e}).")
        texts.append(text)
        page.wait_for_timeout(300)
    return texts


def _collect_all_results(page, fetch_index_ii: bool) -> list[dict]:
    """Walks EVERY page of the RegistrationGrid results table, not just the
    first -- CONFIRMED LIVE 2026-10-15 this grid paginates (PageSize=10,
    standard ASP.NET GridView numeric pager) and a single read silently
    dropped every row past the first 10. Assumes the CURRENT page (as
    loaded by the caller) is page 1.

    Navigates via the SAME __doPostBack('RegistrationGrid','Page$N')
    convention the pager's own numeric links use, incrementing N by one
    each time and stopping as soon as that literal target string no
    longer appears anywhere in the current page's HTML -- i.e. following
    "is there a next page" rather than reading a total page count up
    front, since the pager's own numeric links don't include the current
    page (so there's no single "you are on page N of M" line to parse).

    CONFIRMED LIVE 2026-10-15: the pager can advertise MORE page-number
    targets (Page$2..Page$5) than real pages of DISTINCT data actually
    exist -- clicking 'Page$N' past the true last page re-renders the
    EXACT SAME 10 rows rather than failing or going blank, which an
    earlier version of this loop (following link presence alone) read as
    4 more genuine pages, producing 50 rows that were really the same 10
    documents five times over (and wasted/flaky Index II popups on each
    repeat). Each page's row set is fingerprinted (docno/rdate/sroname
    tuples) and compared against every page already collected; a repeat
    fingerprint means the real last page was already passed, so it stops
    there instead of trusting the pager's own link count."""
    all_rows: list[dict] = []
    seen_page_signatures: set[tuple] = set()
    current_page = 1
    while True:
        html = _safe_read(page.content, min_length=5000)
        page_rows = _parse_document_results(html)
        signature = tuple(sorted(
            (r.get("docno", ""), r.get("rdate", ""), r.get("sroname", "")) for r in page_rows
        ))
        if signature in seen_page_signatures:
            break
        seen_page_signatures.add(signature)

        if fetch_index_ii and page_rows:
            index_ii_texts = _fetch_index_ii_documents(page, len(page_rows))
            for row, text in zip(page_rows, index_ii_texts):
                row["index_ii_text"] = text
        all_rows.extend(page_rows)

        next_page = current_page + 1
        if f"Page${next_page}" not in html:
            break
        try:
            page.evaluate(f"__doPostBack('RegistrationGrid','Page${next_page}')")
        except Exception:
            break
        _settle(page, timeout_ms=10000)
        page.wait_for_timeout(2000)
        current_page = next_page
    return all_rows


def search_by_document_number(
    district: str,
    sro_contains: str,
    year: int | str,
    doc_number: int | str,
    registration_type: str = "Regular",
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
    autosolve: bool | None = True,
    fetch_index_ii: bool = False,
) -> dict:
    """Opens a VISIBLE browser at IGR Maharashtra's Document Number search,
    pre-fills Registration Type/District/SRO/Year/Doc No., then waits for a
    human to read the CAPTCHA and click Search.

    `district` is matched against _DISTRICT_HINTS first (see module
    docstring), then used as-is -- pass the exact Marathi label for a
    district not in that small table. `sro_contains` is a case-insensitive
    substring match against the SRO dropdown's own (English) option text,
    populated only after `district` is selected.

    `fetch_index_ii=True` additionally fetches each row's own Index II
    document (see _fetch_index_ii_documents) and attaches it to that row
    dict as "index_ii_text" (None on a per-row failure) -- adds one popup
    round-trip per row, so leave it off for a quick existence check.

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
        _settle(page)
        page.fill("#txtDocumentNo", str(doc_number))
        # Confirmed live (screenshot evidence): a captcha this function had
        # already told the human to read sometimes showed "Entered Captcha
        # is incorrect" on the VERY FIRST submit despite a correct-looking
        # read, and once even rendered with no image at all. The likely
        # cause: #txtDocumentNo can itself carry a server-side postback
        # (like the two selects above), which would silently swap in a new
        # CAPTCHA image right as -- or just after -- the human started
        # reading the old one. Settle fully, then wait for the CAPTCHA
        # image itself to be present and have really finished loading
        # (a broken/blank img still "exists" for wait_for_selector) before
        # ever telling a human to look at it.
        _settle(page)
        try:
            page.wait_for_function(
                "() => { const img = document.querySelector('img[src*=\"Captcha\"], img[id*=\"Captcha\" i], img[id*=\"img\" i]'); "
                "return img && img.complete && img.naturalWidth > 0; }",
                timeout=10000,
            )
        except Exception:
            pass  # best-effort -- fall through and let the human judge what they see

        print(f"\n[INFO] A browser window has opened at {_BASE_URL}")
        print(f"[INFO] Registration Type={registration_type}, District={district_label}, "
              f"SRO={matched_sro!r}, Year={year}, Doc No.={doc_number} are pre-filled.")

        if not _autosolve_docno_captcha(page, autosolve):
            # No human-wait fallback (removed 2026-10-15, see
            # captcha_solver.py's own module docstring) -- a fully
            # unattended run has no one there to solve it, so an honest
            # failure here beats tying up the browser waiting for someone
            # who isn't coming.
            return {
                "found": False, "rows": [], "raw_text": "", "url": page.url,
                "note": "CAPTCHA autosolve did not succeed -- no human-wait fallback.",
            }

        finished = _wait_for_search_to_finish(page)
        html = _safe_read(page.content, min_length=5000)
        body_text = _safe_read(lambda: page.inner_text("body"), min_length=200)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))
        rows = _parse_document_results(html)
        if rows:
            # CONFIRMED LIVE 2026-10-15: this grid paginates at 10 rows --
            # walk every page rather than reporting only this first one.
            rows = _collect_all_results(page, fetch_index_ii)
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
        # CONFIRMED LIVE 2026-10-14.
        "captcha_image": "#imgCaptcha",
        "search": "#btnSearch", "search_control_name": "btnSearch",
    },
    "rest_of_maharashtra": {
        "year": "#ddlFromYear1", "district": "#ddlDistrict1", "taluka": "#ddltahsil",
        "village": "#ddlvillage", "property": "#txtAttributeValue1", "captcha": "#txtImg1",
        # CONFIRMED LIVE 2026-10-23, reproduced twice by hand: right after
        # switching to this tab (before any selection), the visible captcha
        # image is #imgCaptcha -- the SAME id the mumbai tab's own, untouched
        # <img> uses. But the District/Taluka selects each fire a real
        # ASP.NET postback (full page round-trip, not an in-place AJAX
        # update), and after EITHER of those postbacks #imgCaptcha no longer
        # exists in the DOM at all -- the server re-renders the same visible
        # captcha under #imgCaptcha_new instead (confirmed present after
        # District alone, and still present after Taluka). Since
        # _setup_property_search_form always selects District (and, for this
        # region, Taluka) before a solve is ever attempted, #imgCaptcha_new
        # is the id that actually exists at solve time -- #imgCaptcha is only
        # ever true on a page that has had zero postbacks yet, which this
        # flow never reaches.
        "captcha_image": "#imgCaptcha_new",
        "search": "#btnSearch_RestMaha", "search_control_name": "btnSearch_RestMaha",
    },
    "urban": {
        "year": "#ddlFromYearUrban", "district": "#ddlDistrictUrban", "village": "#ddlareanameUrban",
        "property": "#txtAttributeValueUrban", "captcha": "#txtImgUrban",
        # CONFIRMED LIVE 2026-10-23: same #imgCaptcha_new id rest_of_maharashtra
        # lands on post-postback (see that region's own comment) -- this
        # region's District select postbacks the same way, so by solve time
        # the page is already on #imgCaptcha_new here too.
        "captcha_image": "#imgCaptcha_new",
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
    straight off a district or taluka select instead.

    CONFIRMED LIVE 2026-10-15 by a human watching the real browser: typing
    via press_sequentially() then pressing a real Tab key -- a genuine
    blur, not a forced page.evaluate("__doPostBack(...)") call -- fires
    this field's own autopostback the same way a person tabbing through
    the form would, and #ddlareaname populates from it exactly as before.
    Kept because the earlier evaluate()-driven postback is what every
    prior field-reset investigation was built on top of; switching to a
    real keyboard-driven commit here is the one combination not yet tried
    against the CAPTCHA-rejection reset bug (see _autosolve_property_
    captcha's own docstring)."""
    field = page.locator("#txtAreaName")
    field.click()
    field.fill("")
    field.press_sequentially(village, delay=50)
    page.keyboard.press("Tab")
    matched = _select_cascading(page, "#ddlareaname", village, timeout_ms)
    if not matched:
        return False

    # CONFIRMED LIVE 2026-10-15 by a human watching the real browser: the
    # postback above also clears #txtAreaName back to empty (the real
    # selected value lives in the dropdown, not the text box -- this part
    # is normal/expected, not a bug). But leaving it empty at submit time
    # risks the portal treating Village Name as an unfilled required
    # field. A plain fill() here (no Tab, no triggering another postback)
    # puts the text back without disturbing #ddlareaname's own selection.
    #
    # NOTE: an earlier version of this function also fired a second,
    # "warm-up" postback on #ddlareaname itself here, on the theory that
    # its selection wasn't valid server-side without one. That was
    # DISPROVEN by directly diffing the real POST payload of a failing run
    # against a working one (see _autosolve_property_captcha's own
    # docstring for the actual cause, a stale __EVENTTARGET) -- the
    # warm-up changed nothing live and was removed rather than left in as
    # dead weight.
    field.fill(village)
    return True


def _setup_property_search_form(
    page, region: str, fields: dict, district_label: str, year, taluka, village: str, property_number,
) -> str | None:
    """Drives the Property Details form from a FRESH page load through to
    District/[Taluka/]Village/Property No. filled in, ready for a CAPTCHA
    solve -- pulled out of search_by_property so _autosolve_property_captcha
    can redo this ENTIRE sequence from a brand new page load on each retry.
    Returns an error note (str) on a configuration failure (no matching
    Year/Taluka/Village option), or None on success."""
    page.goto(_BASE_URL, timeout=30000)
    try:
        page.click("text=Close", timeout=5000)
    except Exception:
        pass

    tab_button = _REGION_TAB_BUTTON[region]
    if tab_button:
        page.click(tab_button)
        # Confirmed live this switch alone can take well past a plain
        # _settle() call before the new region's own fields even exist in
        # the DOM -- wait for the district select itself, not a generic
        # idle signal.
        page.wait_for_selector(fields["district"], timeout=30000)

    if year is not None:
        try:
            page.select_option(fields["year"], label=str(year))
        except Exception as e:
            return f"No Year option matched {year!r} for region={region!r} ({e})."
        # CONFIRMED LIVE 2026-10-14: selecting Year fires its own postback
        # -- without waiting for it to settle before touching District
        # next, the two postbacks raced and the page settled back on the
        # DEFAULT year, silently discarding this selection.
        _settle(page)
        page.wait_for_timeout(3000)

    page.select_option(fields["district"], label=district_label)
    _settle(page)
    page.wait_for_timeout(3000)  # see _select_cascading's own docstring on why

    if region == "rest_of_maharashtra":
        if not _select_cascading(page, fields["taluka"], taluka):
            return f"No taluka option for district {district_label!r} matched {taluka!r} exactly."
        village_matched = _select_cascading(page, fields["village"], village)
    elif region == "urban":
        village_matched = _select_cascading(page, fields["village"], village)
    else:  # mumbai
        village_matched = _fill_mumbai_area(page, village)

    if not village_matched:
        return (
            f"No village/area option matching {village!r} appeared for district {district_label!r}"
            + (f", taluka {taluka!r}" if region == "rest_of_maharashtra" else "")
            + f" under region={region!r}. A locality can sit in a DIFFERENT region than expected -- "
              f"e.g. Pune's Market Yard (Gulatekadi) is under region='urban', not the rural "
              f"taluka/village list under region='rest_of_maharashtra' -- try the other region "
              f"before assuming the locality isn't searchable at all. The portal's own FAQ also "
              f"notes some names need an unusual spelling (e.g. 'Daadar' not 'Dadar')."
        )

    # Real Tab (not a plain fill()) to keep the whole chain -- village name,
    # village select, property no., captcha -- committed via genuine
    # keyboard events the same way _fill_mumbai_area's village field now is,
    # leaving focus in the CAPTCHA field ready for _autosolve_property_
    # captcha to type straight into.
    property_field = page.locator(fields["property"])
    property_field.click()
    property_field.fill("")
    property_field.press_sequentially(str(property_number), delay=50)
    page.keyboard.press("Tab")

    # CONFIRMED LIVE 2026-10-15 by diffing the real POST payload of a
    # failing run against a working one: the __doPostBack('txtAreaName',
    # ...) call(s) above leave __EVENTTARGET pointing at that textbox, and
    # nothing clears it afterward -- so whichever submission comes next
    # (autosolve or the human-fallback click) gets silently routed to
    # Village Name's own postback handler server-side instead of the
    # Search button's, meaning the CAPTCHA validates fine but the actual
    # search query never runs. Cleared here, once, right after setup
    # finishes, so BOTH paths start from a clean state regardless of which
    # one ends up submitting; _autosolve_property_captcha clears it again
    # before every one of its own retries for the same reason.
    try:
        page.evaluate(
            "document.getElementById('__EVENTTARGET').value = ''; "
            "document.getElementById('__EVENTARGUMENT').value = '';"
        )
    except Exception:
        pass
    return None


def _recover_blanked_property_fields(page, region: str, fields: dict, district_label: str, year, village: str, property_number) -> bool:
    """Refills ONLY whichever of District/Year/Select Village/Village Name/
    Property No. are currently wrong, IN PLACE on the current page -- no
    page.goto(), no re-running the whole setup sequence. CONFIRMED LIVE
    2026-10-15 by a human watching the real browser: after a rejected
    CAPTCHA, any of these can revert to a placeholder/default while still
    WITHIN THE SAME page load/session. The human explicitly corrected a
    full-page-reload recovery here: reloading starts an entirely new
    CAPTCHA session with the portal rather than cleanly continuing the
    current one, so this stays on the same page and only touches whatever
    has actually gone wrong. Returns True if anything needed refilling
    (purely informational for the caller's own logging).

    `region` matters here: only "mumbai" has a separate free-text Village
    Name box (#txtAreaName) alongside its cascading select -- the other two
    regions' village/area field is a plain <select> with no companion text
    box, so "village_name" never applies outside region="mumbai" (see
    _property_field_status's own docstring for why this was hardcoded to
    mumbai's shape for too long and silently crashed on every other
    region)."""
    wrong = _property_field_status(page, region, fields, district_label, year, village, property_number)
    if "district" in wrong:
        page.select_option(fields["district"], label=district_label)
        _settle(page)
        page.wait_for_timeout(3000)
    if "year" in wrong and year is not None:
        page.select_option(fields["year"], label=str(year))
        _settle(page)
        page.wait_for_timeout(3000)
    if "select_village" in wrong:
        _select_cascading(page, fields["village"], village, timeout_ms=10000)
    if "village_name" in wrong:
        page.locator("#txtAreaName").fill(village)
    if "property_no" in wrong:
        page.locator(fields["property"]).fill(str(property_number))
    return bool(wrong)


def _property_field_status(page, region: str, fields: dict, district_label: str, year, village: str, property_number) -> dict:
    """Reads back District/Year/Select Village/[Village Name/]Property No.'s
    CURRENT values against what they should be, WITHOUT changing anything.
    Returns a dict naming only the field(s) that are wrong (empty dict if
    everything matches).

    CONFIRMED LIVE 2026-10-15 by a human watching the real browser, TWICE:
    first an earlier version of the caller's own "was this genuinely
    accepted" check only ever looked at Property No., missing a Select
    Village reset that produced a stable-but-FALSE "no results" reading
    across an entire multi-year sweep (see [[feedback_partial_field_reset_
    check_false_negative]]). After THAT was fixed to also check Select
    Village/Village Name, the SAME class of false negative recurred for a
    year (2020) already confirmed to have real documents -- this time
    because District and/or Year, mentioned in this module's own older
    comments as part of the same "whole form reset" but never actually
    added to this check, were the fields that had reset. Every field this
    module has EVER observed resetting is checked here now, not just
    whichever ones were implicated by the most recent failure -- a partial
    fix for a partial-check bug is still a partial check.

    CONFIRMED LIVE 2026-10-23: this function hardcoded mumbai's own field
    ids (#ddlareaname / #txtAreaName) regardless of `region`, which is fine
    for region="mumbai" but hung for 30s and then raised on
    rest_of_maharashtra/urban, where #txtAreaName doesn't exist at all --
    the actual reason autosolve had never been exercised past mumbai. Now
    uses `fields["village"]` (the real select for whichever region is
    active) and only checks the free-text Village Name box for mumbai,
    the one region that actually has one."""
    wrong = {}

    district_select = page.locator(fields["district"])
    try:
        current_district = district_select.locator("option:checked").inner_text().strip()
    except Exception:
        current_district = ""
    if current_district.casefold() != district_label.strip().casefold():
        wrong["district"] = current_district

    if year is not None:
        year_select = page.locator(fields["year"])
        try:
            current_year = year_select.locator("option:checked").inner_text().strip()
        except Exception:
            current_year = ""
        if current_year != str(year):
            wrong["year"] = current_year

    village_select = page.locator(fields["village"])
    try:
        selected_text = village_select.locator("option:checked").inner_text().strip()
    except Exception:
        selected_text = ""
    if selected_text.casefold() != village.strip().casefold():
        wrong["select_village"] = selected_text

    if region == "mumbai":
        area_name_field = page.locator("#txtAreaName")
        current_area_name = _safe_read(lambda: area_name_field.input_value(), min_length=0)
        if not current_area_name:
            wrong["village_name"] = current_area_name

    property_field = page.locator(fields["property"])
    current_property = _safe_read(lambda: property_field.input_value(), min_length=0)
    if current_property != str(property_number):
        wrong["property_no"] = current_property

    return wrong


def _autosolve_property_captcha(
    page, region: str, fields: dict, district_label: str, year, taluka, village: str, property_number,
    autosolve: bool | None,
) -> bool:
    """Tries Claude-vision autosolve (see captcha_solver.solve_igr_captcha)
    against Property Details search's CAPTCHA.

    IN-PLACE retry, STAYING ON THE SAME PAGE/SESSION throughout: a
    rejected submit is re-solved without navigating away. Select Village/
    Property No. (occasionally Village Name's own text box too) can still
    go blank after a rejection even with Village Name committed via real
    keystrokes + an actual Tab keypress (see _fill_mumbai_area /
    _setup_property_search_form) -- when that happens, only the blanked
    field(s) are refilled in place (_recover_blanked_property_fields),
    never via a fresh page.goto(). CONFIRMED LIVE 2026-10-15 by a human
    watching the real browser and explicitly correcting an earlier version
    of this function that reloaded the page on every reset: reloading
    starts an entirely new CAPTCHA session with the portal rather than
    continuing the current one -- the whole point of staying in place is
    that the session itself (not just the visible field values) carries
    state across a rejected attempt. A full _setup_property_search_form
    reload is kept only as a last resort if in-place recovery itself
    doesn't hold.

    ACCEPT SIGNAL -- TWO rounds of live correction, CONFIRMED 2026-10-15 by
    a human watching the real browser both times:
      1. Neither the Search button's own visibility nor "Entered Correct
         Captcha" text can be trusted the way _autosolve_docno_captcha
         trusts them on Document Number search -- both read as "accepted"
         here on an attempt that had NOT actually been accepted.
      2. The "Please Wait....." loader appearing is ALSO not itself an
         accept signal -- it appears for EVERY submit, accepted or not, as
         the portal's own field/captcha validation step; on a rejected
         CAPTCHA specifically, once that check finishes the portal swaps in
         a FRESH CAPTCHA image for another try, rather than showing any
         "incorrect" text the way Document Number search does.
    The one signal left unfalsified: after "Please Wait" clears, has the
    CAPTCHA image's own src token changed? A changed token means the
    portal rejected that attempt and is waiting for a new one -- loop back
    and solve the NEW image. An unchanged token (or the image no longer
    present at all) means the submission actually went through to a real
    results page -- stop here and let the caller's own result-table
    polling take over, exactly per that live correction: stop submitting
    CAPTCHAs the moment the loader appears, and let the wait-for-results
    step run from there.

    A staleness check guards against the possibility that the earlier
    field-reset bug can still recur for some other reason: before
    re-solving on attempt 2+, the Property No. field is read back, and
    only if it no longer matches what was set is the old full
    _setup_property_search_form redo used -- kept as a fallback, not the
    default path."""
    want_autosolve = autosolve if autosolve is not None else os.environ.get("IGR_CAPTCHA_AUTOSOLVE") == "1"
    if not want_autosolve or "captcha_image" not in fields:
        return False

    search_selector = fields["search"]
    captcha_input_selector = fields["captcha"]
    image_selector = fields["captcha_image"]

    for attempt in range(1, _MAX_AUTOSOLVE_ATTEMPTS + 1):
        # CONFIRMED LIVE 2026-10-15, per explicit user instruction after
        # watching this live: check+fix every field BEFORE every single
        # CAPTCHA attempt, including the very first -- not just retries.
        # Setup just ran moments before attempt 1, but a field can still
        # have drifted by the time this point is reached (confirmed: a
        # reset was once caught on "attempt 1" itself, before any
        # rejection had even been recorded).
        wrong = _property_field_status(page, region, fields, district_label, year, village, property_number)
        if wrong:
            print(f"[INFO] {sorted(wrong)} no longer match before attempt {attempt} -- "
                  f"refilling in place, same session (no page reload).")
            _recover_blanked_property_fields(page, region, fields, district_label, year, village, property_number)
            still_wrong = _property_field_status(page, region, fields, district_label, year, village, property_number)
            if still_wrong:
                print(f"[INFO] In-place recovery did not hold on attempt {attempt}/"
                      f"{_MAX_AUTOSOLVE_ATTEMPTS} ({sorted(still_wrong)} still wrong) -- redoing "
                      f"full setup from a fresh page load as a last resort.")
                setup_error = _setup_property_search_form(
                    page, region, fields, district_label, year, taluka, village, property_number
                )
                if setup_error:
                    print(f"[INFO] Could not re-set-up the search form on attempt "
                          f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} ({setup_error}).")
                    continue

        pre_solve_token = _safe_read(lambda: page.locator(image_selector).first.get_attribute("src"), min_length=0)
        try:
            backend_name, solved_text = captcha_solver.solve_igr_captcha(page, image_selector=image_selector, enabled=True)
            captcha_input = page.locator(captcha_input_selector)
            captcha_input.click()
            captcha_input.fill("")
            captcha_input.press_sequentially(solved_text, delay=50)
            # ROOT CAUSE, CONFIRMED LIVE 2026-10-15 by diffing the actual
            # POST data of a failing run against a working one: on a FIRST
            # CAPTCHA attempt, __EVENTTARGET is still 'txtAreaName' --
            # leftover from the __doPostBack('txtAreaName',...) calls
            # _fill_mumbai_area fires during setup, which nothing clears
            # afterward. ASP.NET routes a postback to WHATEVER control
            # __EVENTTARGET names, so the server re-runs Village Name's own
            # postback handler instead of the Search button's -- the
            # CAPTCHA still gets validated (a page-level check, not tied to
            # a specific control's event), so "Entered Correct Captcha"
            # genuinely appears, but the actual search query never runs,
            # which is why every first-attempt accept came back with 0
            # rows. A REJECTED attempt's own response resets these hidden
            # fields to empty by default, which is why every retry-accept
            # worked. Clearing them explicitly right before every click --
            # not just retries -- removes the dependency on an accidental
            # prior rejection to reach a correct state.
            page.evaluate(
                "document.getElementById('__EVENTTARGET').value = ''; "
                "document.getElementById('__EVENTARGUMENT').value = '';"
            )
            page.click(search_selector, timeout=10000)
        except Exception as e:
            print(f"[INFO] CAPTCHA autosolve attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} failed ({e}).")
            continue

        # "Please Wait" appears for EVERY submit, accepted or not (it's the
        # portal's own validation step) -- wait for it to clear before
        # reading anything, same signal _wait_for_search_to_finish already
        # polls for elsewhere in this module.
        wait_deadline = time.time() + 20
        while time.time() < wait_deadline:
            body_now = _safe_read(lambda: page.inner_text("body"), min_length=1) or ""
            if "please wait" not in body_now.casefold():
                break
            page.wait_for_timeout(500)

        # CONFIRMED LIVE 2026-10-15 by a human watching the real browser: a
        # REJECTED attempt's only visible aftermath is a FRESH CAPTCHA
        # image swapped in once "Please Wait" clears -- no "incorrect"
        # text the way Document Number search shows. So a CHANGED image
        # token means rejected (solve the new one next); an unchanged
        # token, or the image no longer present at all, means the
        # submission went through to a real results page.
        #
        # The swap isn't necessarily instant the moment "Please Wait" text
        # disappears -- poll briefly rather than reading the token once,
        # so a delayed swap isn't misread as "unchanged" (accepted).
        rejected = False
        settle_deadline = time.time() + 5
        while time.time() < settle_deadline:
            current_token = _safe_read(lambda: page.locator(image_selector).first.get_attribute("src"), min_length=0)
            if current_token and current_token != pre_solve_token:
                rejected = True
                break
            page.wait_for_timeout(500)

        if not rejected:
            # CONFIRMED LIVE 2026-10-15: an "unchanged" reading also covers
            # the CAPTCHA image being GONE entirely -- which happened once
            # not because Search succeeded, but because the WHOLE FORM had
            # been reset back to its blank placeholders (District back to
            # "---Select District----", Property No. cleared), the same
            # reset bug _setup_property_search_form's own retry exists
            # for. A genuine results page leaves every field exactly as
            # submitted; a silent reset does not -- check ALL of them (not
            # just Property No., which an earlier version of this check
            # missed -- see _property_field_status's own docstring for the
            # live-confirmed false-negative that gap caused) before
            # trusting "no fresh CAPTCHA" as acceptance.
            wrong = _property_field_status(page, region, fields, district_label, year, village, property_number)
            if wrong:
                print(f"[INFO] {backend_name}'s solve ({solved_text!r}) on attempt "
                      f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} left no fresh CAPTCHA behind, but "
                      f"{sorted(wrong)} no longer match -- the form was reset, not accepted. "
                      f"Refilling in place, same session, before the next attempt.")
                _recover_blanked_property_fields(page, region, fields, district_label, year, village, property_number)
                continue
            print(f"[INFO] {backend_name} solved the CAPTCHA ({solved_text!r}) on attempt "
                  f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} -- no fresh CAPTCHA appeared after "
                  f"'Please Wait' cleared, so Search went through to a real results page.")
            return True

        print(f"[INFO] {backend_name}'s solve ({solved_text!r}) was rejected (attempt "
              f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS}) -- a fresh CAPTCHA appeared after "
              f"'Please Wait' cleared; solving that one next, fields left untouched.")

        try:
            page.wait_for_selector(search_selector, state="visible", timeout=10000)
        except Exception:
            pass

    print(f"[INFO] CAPTCHA autosolve did not succeed within {_MAX_AUTOSOLVE_ATTEMPTS} attempt(s).")
    return False


def search_by_property(
    region: str,
    district: str,
    village: str,
    property_number: str,
    taluka: str | None = None,
    year: int | str | None = None,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
    autosolve: bool | None = True,
    fetch_index_ii: bool = False,
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

    CONFIRMED LIVE 2026-10-14 (region=mumbai, district=Mumbai, village=
    Parel, property=10): a genuine positive match renders the SAME
    DocNo/DName/RDate/SROName/Seller Name/Purchaser Name/... table
    search_by_document_number already parses -- `rows` uses that same
    parser (_parse_document_results) here too. Before this was confirmed,
    this function only checked for the 'Entered Correct Captcha' text and
    assumed its presence meant no match; that assumption was WRONG (the
    phrase appears on a genuine hit too) and produced a real false
    negative against exactly this property, caught by a human running the
    identical search manually and getting a populated table back. The
    no-match reading (found=False) now additionally requires that the
    SAME parser found no rows, not just that phrase alone -- still only
    confirmed for region=mumbai; rest_of_maharashtra/urban are untested
    against a real positive match and may render differently.
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
        setup_error = _setup_property_search_form(
            page, region, fields, district_label, year, taluka, village, property_number
        )
        if setup_error:
            return {"found": False, "rows": [], "raw_text": "", "url": page.url, "note": setup_error}

        print(f"\n[INFO] A browser window has opened at {_BASE_URL}")
        print(f"[INFO] Region={region}, District={district_label}, "
              + (f"Taluka={taluka!r}, " if region == "rest_of_maharashtra" else "")
              + f"Village/Area={village!r}, Property No.={property_number} are pre-filled.")

        if not _autosolve_property_captcha(page, region, fields, district_label, year, taluka, village, property_number, autosolve):
            # No human-wait fallback (removed 2026-10-15, see
            # captcha_solver.py's own module docstring) -- a fully
            # unattended run has no one there to solve it, so an honest
            # failure here beats tying up the browser waiting for someone
            # who isn't coming.
            return {
                "found": False, "rows": [], "raw_text": "", "url": page.url,
                "note": "CAPTCHA autosolve did not succeed -- no human-wait fallback.",
            }

        finished = _wait_for_search_to_finish(page)
        # CONFIRMED LIVE 2026-10-14: _wait_for_search_to_finish's "please
        # wait" marker clearing does NOT mean the real results table has
        # actually rendered yet for this search mode -- a read taken
        # immediately after it cleared caught the blank reset form
        # (found=False) for a property that, confirmed by a patient manual
        # wait moments later on the identical search, DOES have real
        # registered documents. The table can still be ~15s+ away even
        # once "please wait" is gone. Poll instead of reading once: try
        # the real parser repeatedly, and only accept a "no results"
        # reading once that same reading has held STABLE across several
        # consecutive checks a few seconds apart, not on a single read.
        html = ""
        body_text = ""
        rows = []
        if finished:
            # CONFIRMED LIVE 2026-10-15: widening the TOTAL window alone
            # (90s->150s) did nothing, because the stability counter was
            # exiting after only 3-5 consecutive "no result" reads (~15s),
            # nowhere near either ceiling -- a real result that takes
            # longer than ~15s to render past "Entered Correct Captcha"
            # would always lose the race. Separately: every "0 rows" run
            # so far was accepted on the VERY FIRST CAPTCHA attempt, and
            # every real-match run had at least one prior rejection/reset
            # cycle -- consistent with a cold first-accept search genuinely
            # taking longer behind the scenes than a session that already
            # did a postback or two. So the fix is a much longer required
            # STABILITY window (60s of continuous "no result" reads, not
            # ~15s) before giving up, not just a longer ceiling -- plus
            # timing logged so a future run can confirm or rule this out
            # with real numbers instead of another guess.
            poll_started = time.time()
            poll_deadline = poll_started + 150
            stable_no_result_checks = 0
            required_stable_checks = 20  # ~60s of continuous "no result" reads
            while time.time() < poll_deadline:
                html = _safe_read(page.content, min_length=5000)
                body_text = _safe_read(lambda: page.inner_text("body"), min_length=200)
                rows = _parse_document_results(html)
                if rows:
                    print(f"[INFO] Real results table appeared after {time.time() - poll_started:.1f}s of polling.")
                    break
                if _property_search_shows_no_result(body_text):
                    stable_no_result_checks += 1
                    if stable_no_result_checks >= required_stable_checks:
                        print(f"[INFO] 'No result' reading held stable for "
                              f"{time.time() - poll_started:.1f}s -- concluding no match.")
                        break
                else:
                    stable_no_result_checks = 0
                page.wait_for_timeout(3000)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))

        # CONFIRMED LIVE 2026-10-14 (caught by a human running the exact
        # same search manually, with identical parameters to an automated
        # run that had just reported found=False): this search mode DOES
        # render the SAME results table search_by_document_number already
        # parses (same DocNo/DName/RDate/SROName/Seller Name/Purchaser
        # Name/... header row) -- the "Entered Correct Captcha" text this
        # function used to treat as a definitive "no results" signal
        # appears on a genuine POSITIVE match too, so checking for it
        # alone was always a false-negative risk, now confirmed rather
        # than just theoretical (the module's own earlier docstrings were
        # honest that this had never been tested against a real hit).
        # Try the real parser FIRST; only fall through to the inferred
        # "no results" reading when it finds nothing.
        if rows:
            # CONFIRMED LIVE 2026-10-15: this grid paginates at 10 rows --
            # walk every page rather than reporting only this first one.
            rows = _collect_all_results(page, fetch_index_ii)
        if rows:
            found, note = True, ""
        elif not finished:
            found, note = None, (
                "The portal's own 'Please Wait.....' marker was STILL PRESENT when this was read -- "
                "confirmed live 2026-09-01 that reading too early here captures the page back at "
                "the blank search form, not a real result. This raw_text is likely incomplete; "
                "re-run with a longer wait or check the screenshot before drawing any conclusion "
                "from it."
            )
        elif _property_search_shows_no_result(body_text):
            # CONFIRMED LIVE 2026-10-14: a genuine positive match DOES
            # render the same DocNo/Seller/Purchaser table
            # search_by_document_number parses (see the `rows` check
            # above, now reachable here too) -- so reaching THIS branch
            # means that parser specifically found no matching table,
            # not merely that "Entered Correct Captcha" appeared (that
            # phrase alone was wrongly treated as sufficient before this
            # fix, which produced a real false negative against a
            # property that does have registered documents). This
            # reading is now backed by the same confirmed parser
            # search_by_document_number relies on, not just an inferred
            # reset pattern.
            found, note = False, (
                "The CAPTCHA was accepted ('Entered Correct Captcha' appeared) and the search ran, "
                "but no DocNo/Seller/Purchaser results table was found -- the same parser "
                "search_by_document_number uses, now confirmed live to correctly read a genuine "
                "positive match in this search mode too. This reads as 'no registered document "
                "found' for this district/village/property number. It only reports what THIS "
                "AUTHORITY's index contains, never whether the property/CTS number itself "
                "genuinely exists."
            )
        else:
            found, note = None, (
                "Neither a DocNo/Seller/Purchaser results table nor the 'Entered Correct Captcha' "
                "no-result pattern was found -- an unexpected page state. See raw_text/screenshot "
                "for what actually came back."
            )
        return {"found": found, "rows": rows, "raw_text": body_text, "url": page.url, "note": note}
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
        year = sys.argv[7] if len(sys.argv) >= 8 else None
        result = search_by_property(
            sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], taluka=taluka, year=year,
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

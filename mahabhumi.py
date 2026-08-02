"""
CTS -> land-record lookup via Maha Bhulekh (bhulekh.mahabhumi.gov.in)'s
Property Card (मालमत्ता पत्रक) search -- Maharashtra's official land-records
portal (NIC + Revenue Dept, Directorate of Land Records and Settlement
Commissioner). Confirmed real and live-tested this pass (not assumed from
documentation alone).

The site is a single Marathi-only ASP.NET WebForms page with a cascading
District -> Office (कार्यालय) -> Village (गाव) -> CTS Number flow. Live
mechanism, confirmed by driving the real page and inspecting its DOM:
  - District/Office/Village are plain <select> postbacks (ddlMainDist /
    ddlTalForAll / ddlVillForAll) -- each selection triggers an ASP.NET
    UpdatePanel partial postback that repopulates the next dropdown. NO
    CAPTCHA gates any of this; it is fully drivable headless.
  - Typing a CTS number into txtcsno and clicking btnsearchfind (शोधा) is
    ALSO CAPTCHA-free -- it resolves the typed number against the site's
    own list of valid CTS identifiers for that exact village, populating
    them into the ddlsurveyno dropdown (a CTS number can have multiple
    sub-divisions, e.g. "100/1", "100/2" -- callers must pick the exact
    one, never guess).
  - Only the FINAL step -- Mobile + Language + CAPTCHA + Submit
    (btnmainsubmit), which actually reveals the Property Card -- is
    CAPTCHA-gated. Unlike MahaRERA (session_auth.py), there is no evidence
    this site grants a CAPTCHA-free session after one solve: the CAPTCHA
    image visibly regenerates on every partial postback, so this is
    modeled as a per-lookup human step, not a cacheable session
    (token_cache.py's model does not apply here -- there is nothing
    reusable to cache).

Matching session_auth.py's own rule: nothing in this module solves or
reads the CAPTCHA image. A human must look at the real browser window this
opens and type it in themselves.

Also matching the ICRA/ZaubaCorp convention elsewhere in this codebase:
office and village names are NEVER fuzzy-matched or guessed across the
Marathi/English boundary -- picking the wrong village would misattribute
an entire legal land record to the wrong place, a serious factual error.
list_offices()/list_villages() return the site's own exact labels for a
caller (or a human) to choose from explicitly; fetch_property_card() only
accepts an exact label already confirmed this way.

CONFIRMED LIVE (this pass, a real CAPTCHA solve completed): the Property
Card the site reveals after Submit is NOT structured HTML text -- it's a
rendered document/image, so soup.get_text() on it yields little to nothing
useful. fetch_property_card() therefore also takes a full-page screenshot
and runs it through Tesseract OCR (same engine/fallback-path convention as
company_charter._extract_document_text), keeping the OCR'd text alongside
whatever structured fields/raw text soup parsing manages to find, so
nothing is silently lost if a future page happens to render differently.
"""

import os
import shutil
import time

import pytesseract
from PIL import Image

import config

# Mirrors company_charter.py's own Tesseract-path bootstrap exactly (same
# reasoning: pytesseract shells out to the tesseract binary by name, and on
# a machine where it's installed but not on PATH -- confirmed the actual
# state here -- OCR would otherwise fail silently with no usable text).
# Duplicated rather than imported from company_charter.py so this module
# still works OCR-capable when run standalone (`python mahabhumi.py ...`),
# not only when reached via company_charter.run_cts_lookup_standalone
# (whose own import of company_charter already sets this at import time).
if not shutil.which("tesseract"):
    for _candidate in (
        os.environ.get("TESSERACT_CMD"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if _candidate and os.path.exists(_candidate):
            pytesseract.pytesseract.tesseract_cmd = _candidate
            break

_BASE_URL = "https://bhulekh.mahabhumi.gov.in/"

_SEL_PROPERTY_CARD_RADIO = "#ContentPlaceHolder1_rbtnSelectType_2"
_SEL_CTS_RADIO = "#ContentPlaceHolder1_rbtnSearchType_0"
_SEL_DISTRICT = "#ContentPlaceHolder1_ddlMainDist"
_SEL_OFFICE = "#ContentPlaceHolder1_ddlTalForAll"
_SEL_VILLAGE = "#ContentPlaceHolder1_ddlVillForAll"
_SEL_CTS_INPUT = "#ContentPlaceHolder1_txtcsno"
_SEL_CTS_SEARCH_BTN = "#ContentPlaceHolder1_btnsearchfind"
_SEL_CTS_CANDIDATES = "#ContentPlaceHolder1_ddlsurveyno"
_SEL_MOBILE = "#ContentPlaceHolder1_txtmobile1"
_SEL_SUBMIT_BTN = "#ContentPlaceHolder1_btnmainsubmit"

# Maharashtra's 36 districts -- a fixed, hardcodable list (confirmed against
# the live ddlMainDist option set), mapped from common English spellings
# (including pre-rename aliases still used in older RERA/registry records)
# to the exact Marathi label the site requires for select_option(label=...).
_DISTRICT_NAME_MAP = {
    "akola": "अकोला",
    "amravati": "अमरावती",
    "ahilyanagar": "अहिल्यानगर", "ahmednagar": "अहिल्यानगर",
    "kolhapur": "कोल्हापूर",
    "gadchiroli": "गडचिरोली",
    "gondia": "गोंदिया",
    "chandrapur": "चंद्रपूर",
    "chhatrapati sambhajinagar": "छत्रपती संभाजीनगर", "aurangabad": "छत्रपती संभाजीनगर",
    "jalgaon": "जळगाव",
    "jalna": "जालना",
    "thane": "ठाणे",
    "dharashiv": "धाराशिव", "osmanabad": "धाराशिव",
    "dhule": "धुळे",
    "nagpur": "नागपूर",
    "nashik": "नाशिक", "nasik": "नाशिक",
    "nanded": "नांदेड",
    "nandurbar": "नंदुरबार",
    "parbhani": "परभणी",
    "palghar": "पालघर",
    "pune": "पुणे", "poona": "पुणे",
    "beed": "बीड", "bid": "बीड",
    "buldhana": "बुलढाणा", "buldana": "बुलढाणा",
    "bhandara": "भंडारा",
    "mumbai suburban": "मुंबई उपनगर",
    "mumbai city": "मुंबई शहर", "mumbai": "मुंबई शहर",
    "yavatmal": "यवतमाळ",
    "ratnagiri": "रत्नागिरी",
    "raigad": "रायगड", "raigarh": "रायगड",
    "latur": "लातूर",
    "wardha": "वर्धा",
    "washim": "वाशिम",
    "satara": "सातारा",
    "sangli": "सांगली",
    "sindhudurg": "सिंधुदुर्ग",
    "solapur": "सोलापूर", "sholapur": "सोलापूर",
    "hingoli": "हिंगोली",
}


class CaptchaTimeoutError(Exception):
    pass


class BrowserClosedError(Exception):
    pass


class AmbiguousSelectionError(Exception):
    """Raised instead of guessing when a caller-supplied label doesn't
    exactly match one of the site's own options."""

    def __init__(self, hint: str, options: list):
        self.hint = hint
        self.options = options
        super().__init__(
            f"{hint!r} is not an exact match against the site's own options. "
            f"Call the matching list_*() function and pass one of its returned "
            f"labels verbatim -- never guess a district/office/village."
        )


def resolve_district_label(district_name: str) -> str | None:
    """Maps a common English district name/alias to the exact Marathi
    label bhulekh.mahabhumi.gov.in requires. Returns None if not
    recognized -- callers should surface that as "not automatable" rather
    than falling back to a guess."""
    return _DISTRICT_NAME_MAP.get((district_name or "").strip().lower())


def _launch(headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright is required. Run: pip install playwright && playwright install chromium"
        ) from e
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=headless)
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    return p, browser, page


def _settle(page) -> None:
    """Bounded wait for an ASP.NET partial postback to settle when nothing
    downstream repopulates (so _wait_for_repopulated's polling doesn't
    apply) -- e.g. after selecting a village, or after checking the CTS
    radio reveals the number input. The page never reaches networkidle
    (something on it polls continuously), so a fixed wait is used instead,
    same as session_auth.py's SPA-bootstrap wait."""
    page.wait_for_timeout(1500)


def _wait_for_repopulated(page, selector: str, timeout_ms: int = 10000) -> None:
    """ASP.NET UpdatePanel postbacks repopulate a dependent <select> a
    moment after its parent changes -- polls for that rather than a fixed
    sleep, since the real delay varies with server load."""
    page.wait_for_function(
        "(sel) => { const el = document.querySelector(sel); return el && el.options.length > 1; }",
        arg=selector,
        timeout=timeout_ms,
    )


def _value_for_label(page, selector: str, label: str) -> str | None:
    """Finds the <option> value whose text matches `label` under
    whitespace-collapsing (confirmed live: some office labels contain a
    real, verbatim double space, e.g. "...भूमि  अभिलेख, कल्याण")."""
    return page.eval_on_selector_all(
        f"{selector} option",
        "(opts, label) => {"
        " const norm = s => s.trim().replace(/\\s+/g, ' ');"
        " const target = norm(label);"
        " const match = opts.find(o => norm(o.textContent) === target);"
        " return match ? match.value : null;"
        " }",
        label,
    )


def _select_option_exact(page, selector: str, label: str, options: list) -> None:
    if label not in options:
        raise AmbiguousSelectionError(label, options)
    # Not page.select_option(selector, label=label): confirmed live that
    # Playwright's own label= matching collapses internal whitespace before
    # comparing, so a label with a real double space never matches itself
    # even passed back byte-identical. Select by value instead, found via
    # our own whitespace-normalized text match.
    value = _value_for_label(page, selector, label)
    if value is None:
        raise AmbiguousSelectionError(label, options)
    page.select_option(selector, value=value)


# The site's own "nothing matched" message for the CTS-candidates dropdown
# -- not a real CTS number, but also doesn't contain "--" like the ordinary
# placeholder options, so it must be filtered explicitly (confirmed live:
# without this, a genuine zero-match search reported this string back as if
# it were one real candidate).
_CTS_NO_MATCH_TEXT = "न.भु.क्र./CTS नंबर उपलब्ध नाही"


def _read_options(page, selector: str) -> list:
    texts = page.locator(f"{selector} option").all_inner_texts()
    return [t.strip() for t in texts if t.strip() and "--" not in t and t.strip() != _CTS_NO_MATCH_TEXT]


def _open_property_card_search(page, district_label: str):
    page.goto(_BASE_URL, wait_until="domcontentloaded", timeout=config.SEARCH_TIMEOUT_MS)
    page.check(_SEL_PROPERTY_CARD_RADIO)
    # The radio switch is itself an ASP.NET partial postback (it swaps the
    # whole District/Office/Village/CTS field layout in) -- must settle
    # before touching ddlMainDist, or the district-select postback below
    # races it and ddlTalForAll never repopulates (confirmed live: an
    # unconditional 10s wait_for_function timeout without this).
    _settle(page)
    district_options = _read_options(page, _SEL_DISTRICT)
    _select_option_exact(page, _SEL_DISTRICT, district_label, district_options)
    _wait_for_repopulated(page, _SEL_OFFICE)


def list_offices(district_name: str) -> dict:
    """Live-fetches the real list of land-record offices (कार्यालय) for a
    district -- headless, no CAPTCHA, no human needed. Returns
    {"found": True, "district_label": ..., "offices": [label, ...]} or
    {"found": False, "note": "..."} if the district name isn't recognized
    or the site couldn't be reached."""
    district_label = resolve_district_label(district_name)
    if not district_label:
        return {"found": False, "note": f"district name {district_name!r} not recognized -- see _DISTRICT_NAME_MAP"}

    p = browser = None
    try:
        p, browser, page = _launch(headless=True)
        _open_property_card_search(page, district_label)
        offices = _read_options(page, _SEL_OFFICE)
        return {"found": True, "district_label": district_label, "offices": offices}
    except Exception as e:
        return {"found": False, "note": f"Mahabhumi office lookup could not run this pass: {e}"}
    finally:
        if browser:
            browser.close()
        if p:
            p.stop()


def list_villages(district_name: str, office_label: str) -> dict:
    """Live-fetches the real list of villages/localities (गाव) under a
    specific office -- headless, no CAPTCHA. `office_label` must be one of
    the exact strings returned by list_offices() (see AmbiguousSelectionError
    otherwise). Returns {"found": True, "villages": [label, ...]} or
    {"found": False, "note": "..."}."""
    district_label = resolve_district_label(district_name)
    if not district_label:
        return {"found": False, "note": f"district name {district_name!r} not recognized -- see _DISTRICT_NAME_MAP"}

    p = browser = None
    try:
        p, browser, page = _launch(headless=True)
        _open_property_card_search(page, district_label)
        office_options = _read_options(page, _SEL_OFFICE)
        _select_option_exact(page, _SEL_OFFICE, office_label, office_options)
        _wait_for_repopulated(page, _SEL_VILLAGE)
        villages = _read_options(page, _SEL_VILLAGE)
        return {"found": True, "villages": villages}
    except AmbiguousSelectionError as e:
        return {"found": False, "note": str(e), "options": e.options}
    except Exception as e:
        return {"found": False, "note": f"Mahabhumi village lookup could not run this pass: {e}"}
    finally:
        if browser:
            browser.close()
        if p:
            p.stop()


def _search_cts_candidates_once(district_label: str, office_label: str, village_label: str, cts_query: str) -> dict:
    p = browser = None
    try:
        p, browser, page = _launch(headless=True)
        _open_property_card_search(page, district_label)

        office_options = _read_options(page, _SEL_OFFICE)
        _select_option_exact(page, _SEL_OFFICE, office_label, office_options)
        _wait_for_repopulated(page, _SEL_VILLAGE)

        village_options = _read_options(page, _SEL_VILLAGE)
        _select_option_exact(page, _SEL_VILLAGE, village_label, village_options)
        _settle(page)  # village-select is also a postback; races the CTS radio-check below without this

        page.check(_SEL_CTS_RADIO)
        _settle(page)  # checking the radio itself reveals/resets the CTS input via another postback
        page.fill(_SEL_CTS_INPUT, str(cts_query).strip())
        page.click(_SEL_CTS_SEARCH_BTN)
        # A genuine no-match leaves ddlsurveyno at its placeholder-only state
        # forever, so waiting for it to repopulate would misreport "no
        # matches" as an error -- a fixed settle then reading whatever's
        # there treats an empty result as the valid outcome it is.
        _settle(page)

        candidates = _read_options(page, _SEL_CTS_CANDIDATES)
        return {"found": True, "candidates": candidates}
    finally:
        if browser:
            browser.close()
        if p:
            p.stop()


def search_cts_candidates(district_name: str, office_label: str, village_label: str, cts_query: str, max_attempts: int = 3) -> dict:
    """Live-resolves a typed CTS number against the site's own list of
    valid CTS identifiers for that exact village -- headless, no CAPTCHA.
    A CTS number can have multiple sub-divisions (e.g. "100/1", "100/2"),
    so this returns every match rather than assuming one. Retries up to
    `max_attempts` times on a transient timeout -- confirmed live: this
    site's ASP.NET postback timing is occasionally slow enough to blow the
    dropdown-repopulation wait on an otherwise-correct request, the same
    kind of flakiness already documented for MahaRERA's Orders/Judgments
    search (see search_maharera_judgments). Returns {"found": True,
    "candidates": ["100", ...]} (empty list if the site genuinely has none
    matching) or {"found": False, "note": "..."} only after every attempt
    fails or the district/office/village itself doesn't exactly match."""
    district_label = resolve_district_label(district_name)
    if not district_label:
        return {"found": False, "note": f"district name {district_name!r} not recognized -- see _DISTRICT_NAME_MAP"}

    last_error = None
    for _attempt in range(max_attempts):
        try:
            return _search_cts_candidates_once(district_label, office_label, village_label, cts_query)
        except AmbiguousSelectionError as e:
            return {"found": False, "note": str(e), "options": e.options}
        except Exception as e:
            last_error = e
    return {"found": False, "note": f"Mahabhumi CTS-number search could not run after {max_attempts} attempt(s): {last_error}"}


# Marathi Tesseract language pack (mar.traineddata) is NOT part of the
# machine-wide Tesseract install here (confirmed live: `tesseract
# --list-langs` only shows eng/osd), and this project's user has no write
# access to C:\Program Files\Tesseract-OCR\tessdata -- confirmed live,
# BUILTIN\Users only has ReadAndExecute there. Rather than requiring an
# admin-elevated install (which also wouldn't travel with this repo to
# another machine/office), mar.traineddata is fetched into a project-local
# tessdata/ directory instead (gitignored -- see .gitignore -- it's a ~3MB
# vendored binary asset, not source), pointed at via the TESSDATA_PREFIX
# environment variable rather than pytesseract's `config="--tessdata-dir
# ..."` -- confirmed live that pytesseract tokenizes `config` with
# shlex.split(..., posix=False) on Windows, which does NOT strip quote
# characters the way a real shell would, so a quoted path came through
# tesseract's argv with the literal quote marks still attached ("...\
# tessdata" as part of the filename) and failed to open. TESSDATA_PREFIX
# needs no quoting at all, so this sidesteps that bug entirely. Since
# --tessdata-dir/TESSDATA_PREFIX REPLACES Tesseract's whole search path
# rather than merging into it, eng.traineddata is copied in here too --
# otherwise the combined "mar+eng" lookup would fail with an unrelated
# "eng not found" error.
#
# To set this up on a fresh machine:
#   mkdir tessdata
#   curl -L -o tessdata/mar.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/mar.traineddata
#   copy "C:\Program Files\Tesseract-OCR\tessdata\eng.traineddata" tessdata\eng.traineddata
_TESSDATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tessdata")
if os.path.isdir(_TESSDATA_DIR):
    os.environ["TESSDATA_PREFIX"] = _TESSDATA_DIR


def _ocr_image(path: str) -> str:
    """Marathi ("mar") if that Tesseract language pack is available -- see
    _TESSDATA_DIR above for where this repo keeps it, since it isn't part
    of a standard Tesseract install. Without it, Devanagari text OCRs as
    garbled Latin-letter noise (English glyph-shape guesses for unfamiliar
    characters), confirmed live against a real Property Card. Falls back
    to English-only automatically when "mar" isn't available
    (TesseractError for an unknown language) rather than failing OCR
    entirely -- still recovers ASCII content (PU-ID numbers, dates) even
    without it, just not the Devanagari fields."""
    try:
        return pytesseract.image_to_string(Image.open(path), lang="mar+eng").strip()
    except pytesseract.TesseractError:
        return pytesseract.image_to_string(Image.open(path), lang="eng").strip()


def _scrape_result_page(page, screenshot_path: str | None = None) -> dict:
    """Best-effort extraction from whatever page the site shows after a
    successful CAPTCHA submission. Tries the two structural patterns already
    proven elsewhere in this codebase (ZaubaCorp's li.row pairs, and plain
    label/value tables) and keeps the raw page text either way.

    Confirmed live: page.content()/raw_text captured the ORIGINAL search-
    form page, not the Property Card -- while a full-page screenshot (taken
    on the same `page` at the same moment) correctly showed the real result
    (its OCR text contained an exact PU-ID number match against what was
    actually on screen). That mismatch is strong evidence the Property Card
    renders inside an <iframe>, which page.content() doesn't descend into
    but a pixel-level screenshot does -- so raw_text/fields now pull from
    EVERY frame on the page (page.frames always includes the main frame, so
    this is a no-op on a page with no iframes), not just the top-level
    document.

    When `screenshot_path` is given, this also takes a full-page screenshot
    and runs it through Tesseract OCR (see _ocr_image) -- confirmed the more
    reliable extraction path for this specific site. `ocr_text` is "" (not
    attempted) when screenshot_path is None, and an honest "[OCR
    unavailable: ...]" marker -- never a silent blank -- if the screenshot
    or OCR step itself fails."""
    from bs4 import BeautifulSoup

    raw_text_parts = []
    fields = {}
    for frame in page.frames:
        try:
            html = frame.content()
        except Exception:
            continue  # a detached/cross-origin frame mid-navigation -- skip it, not fatal
        soup = BeautifulSoup(html, "html.parser")
        raw_text_parts.append(soup.get_text("\n", strip=True))
        for li in soup.find_all("li", class_="row"):
            parts = li.find_all(["span", "label"])
            if len(parts) >= 2:
                fields[parts[0].get_text(strip=True)] = parts[1].get_text(strip=True)
    raw_text = "\n".join(part for part in raw_text_parts if part)

    ocr_text = ""
    if screenshot_path:
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            ocr_text = _ocr_image(screenshot_path)
        except Exception as e:
            ocr_text = f"[OCR unavailable: {e}]"

    return {"fields": fields, "raw_text": raw_text, "ocr_text": ocr_text, "url": page.url}


def fetch_property_card(
    district_name: str,
    office_label: str,
    village_label: str,
    cts_number: str,
    mobile: str,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    poll_interval: float = config.CAPTCHA_POLL_INTERVAL_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """`screenshot_path`, if given, is where the post-CAPTCHA result page's
    full-page screenshot (and its OCR'd text -- see _scrape_result_page) get
    saved; omit it to skip the screenshot/OCR step entirely (existing
    callers that don't pass it are unaffected).

    Opens a VISIBLE browser, drives every field up to and including the
    exact CTS number, then waits for a human to read the CAPTCHA, type it,
    and click Submit -- same human-in-the-loop contract as
    session_auth.acquire_token_via_browser, except here a fresh CAPTCHA
    solve is required on every call (see module note: no reusable session
    was found).

    `office_label`/`village_label` must be exact strings already confirmed
    via list_offices()/list_villages() -- this function does not guess.
    `cts_number` must be one exact candidate already confirmed via
    search_cts_candidates() when more than one existed for that village.

    Raises CaptchaTimeoutError if nobody submits within timeout_seconds,
    BrowserClosedError if the window is closed early, or
    AmbiguousSelectionError if office/village/cts_number isn't an exact
    match. Callers should treat all three as "no result this time" and
    degrade gracefully, same as the MahaRERA CAPTCHA flow."""
    district_label = resolve_district_label(district_name)
    if not district_label:
        return {"found": False, "note": f"district name {district_name!r} not recognized -- see _DISTRICT_NAME_MAP"}

    p, browser, page = _launch(headless=False)
    try:
        # The same postback occasionally times out here as in
        # search_cts_candidates (confirmed live: identical inputs succeed on
        # a retry with no code change) -- retried in place, on the SAME
        # visible window, since the human hasn't started looking at it yet
        # at this point; failing outright here would otherwise force them
        # to re-run the whole command for a purely transient hiccup.
        setup_error = None
        for _attempt in range(3):
            try:
                _open_property_card_search(page, district_label)

                office_options = _read_options(page, _SEL_OFFICE)
                _select_option_exact(page, _SEL_OFFICE, office_label, office_options)
                _wait_for_repopulated(page, _SEL_VILLAGE)

                village_options = _read_options(page, _SEL_VILLAGE)
                _select_option_exact(page, _SEL_VILLAGE, village_label, village_options)
                _settle(page)

                page.check(_SEL_CTS_RADIO)
                _settle(page)
                page.fill(_SEL_CTS_INPUT, str(cts_number).strip())
                page.click(_SEL_CTS_SEARCH_BTN)
                _settle(page)

                cts_options = _read_options(page, _SEL_CTS_CANDIDATES)
                _select_option_exact(page, _SEL_CTS_CANDIDATES, str(cts_number).strip(), cts_options)

                page.fill(_SEL_MOBILE, str(mobile).strip())
                setup_error = None
                break
            except AmbiguousSelectionError:
                raise
            except Exception as e:
                setup_error = e
        if setup_error:
            return {"found": False, "note": f"Mahabhumi setup could not complete after 3 attempts: {setup_error}"}

        print(f"[INFO] A browser window has opened at {_BASE_URL}")
        print("[INFO] Please read the CAPTCHA shown there, type it in, and click Submit.")
        print(f"[INFO] Waiting up to {timeout_seconds}s for you to finish...")

        elapsed = 0.0
        last_status_at = 0.0
        starting_url = page.url
        # Confirmed live (a real CAPTCHA solve, content changed in the SAME
        # window, no new tab, no download -- and a screenshot of the actual
        # result page): this site's post-submit transition is an ASP.NET
        # partial postback, same technology as every other dropdown on this
        # page -- which commonly means the submit button/panel gets hidden
        # via CSS rather than removed from the DOM, so a bare .count() == 0
        # check never fires even though a human plainly sees the page
        # change. The confirmed result page is real structured Devanagari
        # text (a table headed "मालमत्ता पत्रक" / "PU-ID: ..."), NOT a
        # scanned image, and ends with a "मागे जा" (Go Back) button that
        # only exists on that view -- the single most specific, lowest-
        # false-positive signal available, checked first. The other three
        # signals (URL change, submit button losing visibility, rendered
        # text growing substantially) stay as a fallback in case a future
        # variation of this page doesn't show that exact button.
        starting_visible_text_len = len(page.inner_text("body"))
        while elapsed < timeout_seconds:
            if page.is_closed():
                raise BrowserClosedError("Browser window was closed before the CAPTCHA was solved.")
            try:
                current_visible_text_len = len(page.inner_text("body"))
                page_changed = (
                    page.get_by_text("मागे जा").count() > 0
                    or page.get_by_text("PU-ID", exact=False).count() > 0
                    or page.url != starting_url
                    or not page.locator(_SEL_SUBMIT_BTN).is_visible()
                    or abs(current_visible_text_len - starting_visible_text_len) > 200
                )
            except Exception:
                # Confirmed live: the moment a human submits the CAPTCHA, the
                # resulting postback can destroy Playwright's execution
                # context mid-check ("Execution context was destroyed, most
                # likely because of a navigation") -- that error is itself
                # strong evidence something just happened past the CAPTCHA
                # gate, not a real failure. Treat it as "still settling":
                # wait one more poll interval and let the NEXT iteration's
                # checks run against the now-stable post-navigation page,
                # rather than surfacing this as a fatal "could not run this
                # pass" indistinguishable from a genuinely broken lookup.
                time.sleep(poll_interval)
                elapsed += poll_interval
                continue
            if page_changed:
                # Either a full postback navigated us on, or the form itself
                # was replaced by a results view -- either way, something
                # past the CAPTCHA gate happened. Scraping immediately after
                # can hit the exact same transient "execution context
                # destroyed" error if the page is still settling -- a couple
                # of short retries here is cheap insurance against losing an
                # entire human-in-the-loop CAPTCHA solve to one bad instant.
                scrape_error = None
                for _scrape_attempt in range(3):
                    try:
                        return {"found": True, **_scrape_result_page(page, screenshot_path)}
                    except Exception as e:
                        scrape_error = e
                        time.sleep(poll_interval)
                raise scrape_error

            time.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed - last_status_at >= 30:
                print(f"[INFO] Still waiting for the CAPTCHA to be solved ({int(elapsed)}s/{timeout_seconds}s)...")
                last_status_at = elapsed

        raise CaptchaTimeoutError(f"No result within {timeout_seconds}s -- the CAPTCHA wasn't solved in time.")
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


if __name__ == "__main__":
    import sys

    _USAGE = (
        "usage: python mahabhumi.py <district> <office_label> <village_label> <cts_number> [mobile]\n"
        "       python mahabhumi.py offices <district>\n"
        "       python mahabhumi.py villages <district> <office_label>"
    )

    # The offices/villages subcommands have their own (shorter) arg counts --
    # checking len(sys.argv) < 5 unconditionally, before dispatching on
    # sys.argv[1], meant `python mahabhumi.py offices <district>` (3 args)
    # always hit the usage-and-exit branch and never actually ran. Dispatch
    # on the subcommand name first; only the bare property-card form still
    # needs 5 args.
    if len(sys.argv) >= 2 and sys.argv[1] == "offices":
        if len(sys.argv) != 3:
            print(_USAGE, file=sys.stderr)
            sys.exit(2)
        print(list_offices(sys.argv[2]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "villages":
        if len(sys.argv) != 4:
            print(_USAGE, file=sys.stderr)
            sys.exit(2)
        print(list_villages(sys.argv[2], sys.argv[3]))
    elif len(sys.argv) < 5:
        print(_USAGE, file=sys.stderr)
        sys.exit(2)
    else:
        district, office, village, cts = sys.argv[1:5]
        mobile = sys.argv[5] if len(sys.argv) > 5 else input("Mobile number to submit: ").strip()
        print(fetch_property_card(district, office, village, cts, mobile))

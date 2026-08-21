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

import io
import os
import re
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
# Added by the site some time before 2026-08-20: a second "what are you
# searching by" dropdown that sits between the CTS radio and the number
# box. Until it is set, btnsearchfind posts back but ddlsurveyno is never
# populated -- so EVERY CTS lookup silently returned zero candidates,
# including numbers that certainly exist (confirmed live: CTS "1" in
# village gulTekdi returns 1/a, 1/k/1, ... once this is selected, and
# nothing at all when it is skipped). A missing dropdown is tolerated so
# this still works if the site reverts.
_SEL_SEARCH_TYPE_DDL = "#ContentPlaceHolder1_ddlSelectSearchType"
_SEL_CTS_SEARCH_BTN = "#ContentPlaceHolder1_btnsearchfind"
_SEL_CTS_CANDIDATES = "#ContentPlaceHolder1_ddlsurveyno"
_SEL_MOBILE = "#ContentPlaceHolder1_txtmobile1"
_SEL_SUBMIT_BTN = "#ContentPlaceHolder1_btnmainsubmit"
# The card renders in whichever language THIS dropdown holds, and every
# label on it changes with the choice -- so it silently decides whether
# parse_property_card can read anything at all. Confirmed live 2026-08-21:
# a human picked "English" at the CAPTCHA step and every table field came
# back empty from a card that had rendered perfectly, because the parser
# matches Marathi labels ("Area Sq.Mt.." is not "क्षेत्र चौ.मी.").
# The site itself settles which language is authoritative -- its own
# disclaimer says the transliterated text is "prone to occasional
# inconsistencies" and that the Marathi content "will be considered as
# sacrosanct" -- so the code sets it rather than leaving it to whoever is
# at the keyboard alongside the CAPTCHA.
# How long to keep waiting for the card after a weak "something moved"
# signal before scraping whatever is on screen. Covers the "Processing ,
# Please wait.." overlay without ever discarding a human's CAPTCHA solve.
_WEAK_SIGNAL_GRACE_SECONDS = 45.0
# How long _scrape_result_page waits for the card to appear in the DOM
# before reading whatever is there anyway (never silently -- see the note
# parse_property_card returns).
_CARD_DOM_WAIT_SECONDS = 20.0
_SEL_LANGUAGE = "#ContentPlaceHolder1_ddllangforAll"
# WHY ENGLISH, given the portal calls its Marathi text sacrosanct.
# Because in Marathi the card is not text at all: it is a base64 JPEG in an
# <img> src (confirmed live 2026-08-21 over five CAPTCHA solves, every one
# of which returned a perfect card and zero parseable rows). Reading it
# would need a Marathi Tesseract pack this machine does not have, and OCR
# of a watermarked Devanagari table is far less reliable than parsing the
# English HTML the same portal serves.
#
# The transliteration risk is real but bounded, and it does NOT touch the
# values diligence turns on: CTS number, area, dates and mutation numbers
# are digits, identical in both renderings. Only Marathi WORDS are
# transliterated (a holder name, a tenure class) -- and for those the
# authoritative Marathi card image is saved alongside every capture by
# save_embedded_card_image. The goal here is extracted data, with the
# original kept as evidence; it is not to produce a legal copy.
_CARD_LANGUAGE = "English"

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


def _settle(page, ms: int = 3000) -> None:
    """Bounded wait for an ASP.NET partial postback to settle when nothing
    downstream repopulates (so _wait_for_repopulated's polling doesn't
    apply) -- e.g. after selecting a village, or after checking the CTS
    radio reveals the number input. The page never reaches networkidle
    (something on it polls continuously), so a fixed wait is used instead,
    same as session_auth.py's SPA-bootstrap wait.

    Raised from 1500ms to 3000ms on 2026-08-20: the site had slowed enough
    that the shorter wait let the next step run against a half-applied
    postback. Prefer _wait_for_repopulated or _wait_for_cts_results wherever
    there IS something specific to poll for; this is the fallback."""
    page.wait_for_timeout(ms)


def _wait_for_cts_results(page, timeout_ms: int = 25000) -> None:
    """Polls until the CTS search has actually produced a result.

    A completed search leaves ddlsurveyno holding EITHER real candidates OR
    the site's own "no CTS number available" entry, so in both cases it goes
    from zero options to at least one. Waiting a fixed interval instead was
    the bug that made every CTS lookup in the repo report zero candidates:
    the read happened before the postback landed, and an empty dropdown is
    indistinguishable from a genuine no-match (confirmed live 2026-08-20 -
    CTS "1" in village gulTekdi returned nothing on a fixed wait and 17 real
    sub-divisions once polled for)."""
    page.wait_for_function(
        "(sel) => { const el = document.querySelector(sel); return el && el.options.length > 0; }",
        arg=_SEL_CTS_CANDIDATES,
        timeout=timeout_ms,
    )


def _wait_for_repopulated(page, selector: str, timeout_ms: int = 30000) -> None:
    # 10s was too tight. Confirmed live 2026-08-21: on a slow afternoon the
    # portal took 13s just to serve the search form, so all three setup
    # attempts blew this wait and the run died BEFORE the human was ever
    # shown a CAPTCHA. The cost of waiting longer is seconds; the cost of
    # giving up early is the whole lookup.
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


def _select_cts_search_type(page) -> None:
    """Sets the search-by dropdown to its CTS-number option, if present.

    See _SEL_SEARCH_TYPE_DDL: skipping this makes every CTS search return an
    empty candidate list rather than an error, which reads as "this number
    does not exist" when in fact nothing was ever searched."""
    if not page.query_selector(_SEL_SEARCH_TYPE_DDL):
        return
    values = page.eval_on_selector_all(
        _SEL_SEARCH_TYPE_DDL + " option",
        "opts => opts.filter(o => o.value && !o.value.includes('--')).map(o => o.value)",
    )
    if not values:
        return
    page.select_option(_SEL_SEARCH_TYPE_DDL, value=values[0])
    _settle(page)


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
        _select_cts_search_type(page)
        page.fill(_SEL_CTS_INPUT, str(cts_query).strip())
        page.click(_SEL_CTS_SEARCH_BTN)
        # A genuine no-match still populates ddlsurveyno, with the site's own
        # "no CTS number available" entry, so this polls for the search having
        # landed at all and _read_options filters that entry out afterwards.
        _wait_for_cts_results(page)

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


# ---------------------------------------------------------------------------
# Property Card field extraction.
#
# WHAT THE CARD ACTUALLY IS, and why this replaces the OCR workaround. The
# Property Card is a rendered HTML page -- crisp text in real <table>
# elements, with the "For View Only - Not For Legal Purpose" watermark as a
# light overlay behind it. It is NOT a scanned image. OCR was only ever
# needed because page.content() was reading the search form instead of the
# card (the card lives in an iframe), and a screenshot was the only thing
# that saw the real page.
#
# So the right extraction is to parse the tables. OCR stays as a fallback,
# but on a machine without the Marathi language pack it produces garbled
# Latin guesses at Devanagari and cannot be relied on -- see _ocr_image.
#
# THE PARSER MATCHES ON LABEL TEXT, NEVER ON POSITION. The card has three
# shapes: a header table of survey/area/tenure columns, a block of
# label-and-value rows, and the फेरफार (mutation) table. Column counts vary
# between cards, so every field is found by its own Marathi label.

# Marathi label -> the English key this pipeline uses. The labels are what
# the Maharashtra Land Records rules print on Form D; they do not change
# between districts.
_CARD_LABELS = {
    "\u0928\u0917\u0930 \u092d\u0942\u092e\u093e\u092a\u0928 \u0915\u094d\u0930\u092e\u093e\u0902\u0915": "city_survey_number",
    "\u0936\u093f\u091f \u0928\u0902\u092c\u0930": "sheet_number",
    "\u092a\u094d\u0932\u0949\u091f \u0928\u0902\u092c\u0930": "plot_number",
    "\u0915\u094d\u0937\u0947\u0924\u094d\u0930": "area_sq_m",
    "\u0927\u093e\u0930\u0923\u093e\u0927\u093f\u0915\u093e\u0930": "tenure",
    "\u0939\u0915\u094d\u0915\u093e\u091a\u093e \u092e\u0942\u0933 \u0927\u093e\u0930\u0915": "original_holder",
    "\u092a\u091f\u094d\u091f\u0947\u0926\u093e\u0930": "lessee",
    "\u0907\u0924\u0930 \u092d\u093e\u0930": "encumbrance",
    "\u0907\u0924\u0930 \u0936\u0947\u0930\u0947": "other_remarks",
    "\u0917\u093e\u0935": "village",
    "\u091c\u093f\u0932\u094d\u0939\u093e": "district",
    "\u0935\u0930\u094d\u0937": "holder_year",
}

# The SAME card in the portal's machine-transliterated English rendering.
# Needed because the Marathi card is a JPEG (see save_embedded_card_image)
# and cannot be parsed at all without a Marathi OCR pack, which this
# machine does not have. Taken verbatim from a live capture on 2026-08-21,
# including "Tennure" and the double-dotted "Area Sq.Mt.." -- those are the
# portal's own spellings and must not be tidied up here.
#
# Ordered most-specific-first: matching is by substring, and "Lessee"
# occurs inside the mutation header "New Holder (H), Lessee(L) or
# Encumbrances(E)".
_CARD_LABELS_EN = {
    "Other Encumbrances": "encumbrance",
    "Other Remarks": "other_remarks",
    "Name of the Holder": "original_holder",
    "Area Sq.Mt": "area_sq_m",
    "Sheet Number": "sheet_number",
    "Plot Number": "plot_number",
    "Village/peth": "village",
    "Easements": "easements",
    "Tennure": "tenure",
    "CTS No": "city_survey_number",
    "Taluka": "office",
    "District": "district",
    "Lessee": "lessee",
    "varsh": "holder_year",
}

# Mutation-table column labels.
# CORRECTED against the real card 2026-08-21. The live card's third column
# is Khand Kramank (Vol.No.), NOT Pherphar Kramank -- the mutation number
# is written INSIDE the attestation cell ("ferafar kran. 599 pramane"),
# which is why _mutation_number_from exists. The Pherphar key is kept for
# any card that does carry it as its own column.
_MUTATION_COLUMNS = {
    "\u0926\u093f\u0928\u093e\u0902\u0915": "date",
    "\u0935\u094d\u092f\u0935\u0939\u093e\u0930": "transaction",
    "\u092b\u0947\u0930\u092b\u093e\u0930": "mutation_number",
    "\u0916\u0902\u0921 \u0915\u094d\u0930\u092e\u093e\u0902\u0915": "volume_number",
    "\u0928\u0935\u093f\u0928 \u0927\u093e\u0930\u0915": "new_holder",
    "\u0938\u093e\u0915\u094d\u0937\u093e\u0902\u0915\u0928": "attestation",
}

_MUTATION_COLUMNS_EN = {
    "Date": "date",
    "Transaction": "transaction",
    "Vol.No": "volume_number",
    "New Holder": "new_holder",
    "Attestation": "attestation",
}

_MUTATION_NO_RE = re.compile(
    r"(?:\u092b\u0947\u0930\u092b\u093e\u0930|ferafar)[^0-9]{0,20}([0-9]{1,6})", re.I)


def _mutation_number_from(entry: dict) -> str:
    """The mutation (pherphar) number, wherever the card put it. On the real
    card it is prose inside the attestation cell, not its own column."""
    for value in entry.values():
        found = _MUTATION_NO_RE.search(value or "")
        if found:
            return found.group(1)
    return ""

# Both renderings of the same card. English first: its order is
# significant (substring matching), and the two alphabets never collide.
_ALL_CARD_LABELS = {**_CARD_LABELS_EN, **_CARD_LABELS}
_ALL_MUTATION_COLUMNS = {**_MUTATION_COLUMNS_EN, **_MUTATION_COLUMNS}

_PU_ID_RE = re.compile(r"PU[\s-]?ID\s*[:\-]?\s*(\d{6,})", re.I)


_CARD_MARKERS = ("PU-ID", "PU_ID", "pu-id", "मागे जा")


def _card_frame_text(page) -> str:
    """The first frame whose HTML actually carries the Property Card, or "".

    page.get_by_text() searches the MAIN frame only. The card renders in a
    child frame on this site (the documented reason page.content() once
    returned the search form while a screenshot showed the card), so a
    main-frame-only check can never see it -- confirmed live 2026-08-21:
    a fully rendered Marathi card sat on screen, screenshotted correctly,
    while every strong signal read False and raw_text stayed at the bare
    3694-character form."""
    for frame in page.frames:
        try:
            html = frame.content()
        except Exception:
            continue
        if any(marker in html for marker in _CARD_MARKERS):
            return html
    return ""


# Keys the card writes as "Label : value" inside a single cell.
_SAME_CELL_KEYS = {"village", "office", "district", "holder_year"}


def _same_cell_value(text: str, label: str) -> str:
    """The value from a "Label : value" cell, or "".

    Handles the card's "Taluka / C.T.S.Office : ..." where punctuation sits
    between the label and its colon, and stops at the next label when one
    cell carries several pairs -- otherwise the village reads as "ambivli
    Taluka / C.T.S.Office : andheri District : mumbai upanagar", swallowing
    its neighbours. The live card puts each in its own cell, but the same
    block is rendered as one cell elsewhere on the portal."""
    index = text.find(label)
    if index < 0:
        return ""
    remainder = text[index + len(label):]
    if ":" not in remainder:
        return ""
    value = remainder.split(":", 1)[1]
    cut = len(value)
    for other in _ALL_CARD_LABELS:
        if other == label:
            continue
        position = value.find(other)
        if 0 <= position < cut:
            cut = position
    return value[:cut].strip()


def _cell_text(cell) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def parse_property_card(html: str) -> dict:
    """The Property Card's fields, from its own HTML.

    Pure: HTML in, dict out, no browser -- so it is testable against a
    saved capture rather than only against a live CAPTCHA-gated session.

    Returns {} when the HTML carries no card at all, which is how a caller
    tells "the card was not on this page" from "the card was there and this
    field was blank". A blank field on a real card is a FINDING: an empty
    इतर भार row means no encumbrance is recorded, and that is exactly
    what a reader needs to know.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)
    fields, mutations = {}, []

    pu_id = _PU_ID_RE.search(text)
    if pu_id:
        fields["pu_id"] = pu_id.group(1)

    for table in soup.find_all("table"):
        # NESTING IS HANDLED PER-ROW, NOT PER-TABLE, and both halves matter.
        #
        # get_text() on a cell that wraps another table returns every inner
        # label run together, so a wrapper row makes every label match at
        # position 0: all eight header fields once came back holding the
        # single string "Village/peth : ambivli Taluka / C.T.S.Office :
        # ...", reported as the area, the tenure and the district alike.
        #
        # But skipping any table that CONTAINS a table is too blunt -- the
        # real card puts the village/taluka/district table INSIDE the same
        # table that carries the CTS/Area/Tennure header, so that dropped
        # every header field. Exactly the JHARERA nested-table lesson.
        #
        # find_all("tr") also descends into nested tables, so rows must be
        # restricted to this table's own.
        rows = [tr for tr in table.find_all("tr")
                if tr.find_parent("table") is table and not tr.find("table")]
        if not rows:
            continue
        header_cells = [_cell_text(c) for c in rows[0].find_all(["th", "td"])]

        # Shape 1: the mutation table, matched on its own column labels.
        # The mutation table, in either rendering of the card.
        if any(any(k in h for k in ("\u0926\u093f\u0928\u093e\u0902\u0915", "Date")) for h in header_cells) and \
           any(any(k in h for k in ("\u0935\u094d\u092f\u0935\u0939\u093e\u0930", "Transaction")) for h in header_cells):
            index = {}
            for position, header in enumerate(header_cells):
                for label, key in _ALL_MUTATION_COLUMNS.items():
                    if label in header:
                        index[key] = position
            for row in rows[1:]:
                cells = [_cell_text(c) for c in row.find_all(["td", "th"])]
                if not any(cells):
                    continue
                entry = {key: (cells[pos] if pos < len(cells) else "")
                         for key, pos in index.items()}
                if not entry.get("mutation_number"):
                    entry["mutation_number"] = _mutation_number_from(entry)
                if any(entry.values()):
                    mutations.append(entry)
            continue

        # Shape 2: a header row of column labels over one value row.
        #
        # Only a row of real <th> cells counts as a header. A label-and-value
        # row (<td>label</td><td>value</td>) also has its label in the first
        # cell, so treating it as a header made the parser read the NEXT
        # row's first cell as the value: "हक्काचा मूळ धारक" came back as
        # "वर्ष:", the label of the row below it, instead of the holder's
        # name.
        matched = {}
        if not rows[0].find_all("th"):
            header_cells = []
        for position, header in enumerate(header_cells):
            for label, key in _ALL_CARD_LABELS.items():
                if label in header and key not in matched:
                    matched[key] = position
        if matched and len(rows) > 1:
            values = [_cell_text(c) for c in rows[1].find_all(["td", "th"])]
            for key, position in matched.items():
                if position < len(values) and key not in fields:
                    fields[key] = values[position]
            continue

        # Shape 3: label-and-value rows.
        for row in rows:
            cells = [_cell_text(c) for c in row.find_all(["td", "th"])]

            # 3a: label and value inside ONE cell -- "Village/peth :
            # ambivli". Restricted to the keys that really are written that
            # way, because a cell like "Name of the Holder : varsh : 1964"
            # would otherwise yield "varsh : 1964" as the holder's name.
            for cell in cells:
                for label, key in _ALL_CARD_LABELS.items():
                    if key not in _SAME_CELL_KEYS or key in fields:
                        continue
                    value = _same_cell_value(cell, label)
                    if value:
                        fields[key] = value

            # 3b: label in one cell, value in the NEXT. The label is NOT
            # always in cells[0]: the real card's label block opens each row
            # with an empty spacer <td>, which is why the holder, lessee,
            # encumbrance and remarks rows all read as blank.
            if len(cells) < 2:
                continue
            for position, cell in enumerate(cells[:-1]):
                for label, key in _ALL_CARD_LABELS.items():
                    if label in cell and key not in fields:
                        fields[key] = cells[position + 1]

    if not fields and not mutations:
        return {}

    # A CARD THAT IS THERE BUT UNREADABLE IS NOT A CARD THAT IS ABSENT.
    # The PU-ID is matched by regex over the whole page, so it survives any
    # language; every other field is matched by its Marathi label. PU-ID
    # present with nothing else means a real card rendered and this parser
    # could not read a single row of it -- the exact shape of the 2026-08-21
    # English-transliteration run. Reporting that as an ordinary sparse
    # result would let a wrong-language capture reach the Charter looking
    # like a plot with no owner, no area and no encumbrance recorded.
    result = {"fields": fields, "mutations": mutations}
    if list(fields) == ["pu_id"] and not mutations:
        result["note"] = (
            "A Property Card rendered (PU-ID " + fields["pu_id"] + ") but none of "
            "its labelled rows could be read. The most likely cause is that the "
            "card was requested in a language other than Marathi: the portal "
            "transliterates every label, and this parser matches the Marathi "
            "wording the Land Records rules print on Form D. Re-run with the "
            "language left at Marathi. Treat this as NO READING TAKEN, never as "
            "an absence of owner, encumbrance or mutation entries."
        )
    return result


_CARD_IMAGE_MIN_BYTES = 20000


def save_embedded_card_image(page, directory: str) -> dict:
    """Save the card image the result page embeds as a data: URI.

    CONFIRMED LIVE 2026-08-21, and it overturns what this module used to
    say about itself. In Marathi -- the language the portal calls
    sacrosanct -- the Property Card is NOT markup at all. It is a single
    base64 JPEG inlined into an <img> src, which is why PU-ID appears in no
    frame's HTML, why a screenshot always showed a card the DOM did not,
    and why OCR was ever in this file. The English card, by contrast, is
    real HTML tables (machine transliterated, and the portal warns that
    transliteration is "prone to occasional inconsistencies").

    So the image is the authoritative artifact whatever language is used,
    and it is worth keeping in both: it is the evidence behind whatever the
    parser reports. Returns {} when the page embeds no such image."""
    try:
        sources = page.evaluate(
            """() => Array.from(document.querySelectorAll('img'))
                 .map(e => e.src || '')
                 .filter(s => s.startsWith('data:image'))"""
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

    import base64

    best = None
    for src in sources:
        head, _, b64 = src.partition(",")
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        # The CAPTCHA is also a data: image on this page. The card is an
        # order of magnitude larger; size is what separates them.
        if len(raw) < _CARD_IMAGE_MIN_BYTES:
            continue
        if best is None or len(raw) > len(best[1]):
            best = (head.split("/")[1].split(";")[0], raw)
    if not best:
        return {}
    extension, raw = best
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"property_card.{extension}")
    with open(path, "wb") as fh:
        fh.write(raw)
    return {"path": path, "bytes": len(raw)}


def _dom_inventory(page) -> dict:
    """What kinds of embedded object are on the result page, and where they
    point. Diagnostic only -- it never decides anything, it just makes the
    next failure readable without spending another human CAPTCHA solve."""
    try:
        return page.evaluate(
            """() => {
                const src = sel => Array.from(document.querySelectorAll(sel))
                    .map(e => e.src || e.data || e.getAttribute('src') || '(none)');
                return {
                    iframe: src('iframe'), embed: src('embed'),
                    object: src('object'), img: src('img').slice(0, 25),
                    canvas: document.querySelectorAll('canvas').length,
                };
            }"""
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _scrape_result_page(page, screenshot_path: str | None = None, captured: dict | None = None) -> dict:
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

    # WAIT FOR THE CARD TO REACH THE DOM BEFORE READING IT.
    # Confirmed live 2026-08-21: this function read every frame FIRST and
    # took the screenshot AFTER, so a card that landed in between produced
    # the exact contradiction seen that day -- a screenshot showing a
    # complete Marathi Property Card beside a raw_text holding nothing but
    # the 3694-character search form. Reading the DOM is what the parser
    # depends on; the screenshot is only evidence. So poll for the card,
    # then read both from the same moment.
    for _ in range(int(_CARD_DOM_WAIT_SECONDS / 0.5)):
        if _card_frame_text(page):
            break
        time.sleep(0.5)

    raw_text_parts = []
    fields = {}
    mutations = []
    notes = []
    for frame in page.frames:
        try:
            html = frame.content()
        except Exception:
            continue  # a detached/cross-origin frame mid-navigation -- skip it, not fatal
        soup = BeautifulSoup(html, "html.parser")
        raw_text_parts.append(soup.get_text("\n", strip=True))
        # The Property Card, parsed from its own tables. This is the real
        # extraction path: the card is rendered HTML, not a scan, and the
        # docstring above promised table support that was never written --
        # so `fields` came back {} on every lookup this repo has ever done.
        card = parse_property_card(html)
        if card:
            fields.update(card["fields"])
            if card["mutations"]:
                mutations.extend(card["mutations"])
            if card.get("note") and card["note"] not in notes:
                notes.append(card["note"])
        # The li.row shape, kept as a fallback for any page that uses it.
        for li in soup.find_all("li", class_="row"):
            parts = li.find_all(["span", "label"])
            if len(parts) >= 2:
                fields.setdefault(parts[0].get_text(strip=True), parts[1].get_text(strip=True))
    raw_text = "\n".join(part for part in raw_text_parts if part)

    if not fields.get("pu_id") and not mutations:
        notes.append(
            "The Property Card was not present in any frame's HTML when this "
            "page was read. If the saved screenshot shows a card, the reading "
            "raced the page: the card arrived after the DOM was read. This is "
            "NO READING TAKEN, not an empty record."
        )

    ocr_text = ""
    if screenshot_path:
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            ocr_text = _ocr_image(screenshot_path)
        except Exception as e:
            ocr_text = f"[OCR unavailable: {e}]"

    # A note here means the card was ON the page and could not be read --
    # see parse_property_card. It must reach the caller, not be inferred
    # from an empty `fields`.
    if mutations or len(fields) > 1:
        notes = [n for n in notes if "none of" not in n]
    card_html = _card_frame_text(page)
    if card_html and screenshot_path:
        try:
            path = os.path.join(os.path.dirname(screenshot_path), "card_page.html")
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write(card_html)
        except Exception:
            pass  # diagnostics must never break a capture

    card_image = {}
    if screenshot_path:
        card_image = save_embedded_card_image(page, os.path.dirname(screenshot_path))
        if card_image.get("path"):
            notes.append(
                "The card image the page embedded was saved to "
                f"{card_image['path']} -- this is the authoritative record; "
                "in Marathi it is the ONLY form the card takes."
            )

    diagnostics = {"dom": _dom_inventory(page)}
    if captured is not None:
        diagnostics["responses"] = captured.get("responses", [])[-60:]
        saved = []
        for i, pdf in enumerate(captured.get("pdfs", [])):
            if not screenshot_path:
                continue
            path = os.path.join(os.path.dirname(screenshot_path), f"card_{i}.pdf")
            try:
                with open(path, "wb") as fh:
                    fh.write(pdf["body"])
                saved.append({"url": pdf["url"], "path": path, "bytes": len(pdf["body"])})
            except Exception as e:
                saved.append({"url": pdf["url"], "error": str(e)})
        diagnostics["pdfs"] = saved
        if saved:
            notes.append(
                f"{len(saved)} PDF response(s) captured from the result page and saved "
                "beside the screenshot. If the card is not in the DOM, it is in there."
            )
    return {"fields": fields, "mutations": mutations, "raw_text": raw_text,
            "ocr_text": ocr_text, "url": page.url, "notes": notes,
            "card_image": card_image, "diagnostics": diagnostics}


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
    captured = {"responses": [], "pdfs": []}

    def _on_response(response):
        # The card may never enter the DOM (confirmed live 2026-08-21: a
        # fully rendered Marathi card, screenshotted correctly, absent from
        # every frame's HTML). If the site delivers it as a document rather
        # than as markup, this is the only place it can be caught.
        try:
            content_type = (response.header_value("content-type") or "")
        except Exception:
            content_type = ""
        captured["responses"].append({"url": response.url[:300], "content_type": content_type})
        if "pdf" in content_type.lower() or response.url.lower().endswith(".pdf"):
            try:
                captured["pdfs"].append({"url": response.url, "body": response.body()})
            except Exception:
                pass

    page.on("response", _on_response)
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
                _select_cts_search_type(page)
                page.fill(_SEL_CTS_INPUT, str(cts_number).strip())
                page.click(_SEL_CTS_SEARCH_BTN)
                _wait_for_cts_results(page)

                cts_options = _read_options(page, _SEL_CTS_CANDIDATES)
                _select_option_exact(page, _SEL_CTS_CANDIDATES, str(cts_number).strip(), cts_options)

                page.fill(_SEL_MOBILE, str(mobile).strip())
                # Set BEFORE the human takes over -- see _SEL_LANGUAGE.
                page.select_option(_SEL_LANGUAGE, label=_CARD_LANGUAGE)
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
        print(f"[INFO] Language is already set to {_CARD_LANGUAGE} -- please leave it alone;")
        print("[INFO] the Marathi card is served as an image and cannot be parsed.")
        print(f"[INFO] Waiting up to {timeout_seconds}s for you to finish...")

        elapsed = 0.0
        last_status_at = 0.0
        weak_signal_seconds = 0.0
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
                # STRONG signals: the card itself is on screen. Nothing else
                # on this site shows a "मागे जा" (Go Back) button or a PU-ID.
                card_on_screen = bool(_card_frame_text(page))
                # WEAK signals: something moved. Confirmed live 2026-08-21 --
                # these ALL fire during the site's "Processing , Please
                # wait.." overlay, which hides the submit button and changes
                # the rendered text length while the card is still being
                # fetched. Treating them as equal to the strong signals
                # scraped the overlay instead of the card and threw away a
                # human CAPTCHA solve: raw_text came back as the bare search
                # form, no PU-ID, no rows.
                something_moved = (
                    page.url != starting_url
                    or not page.locator(_SEL_SUBMIT_BTN).is_visible()
                    or abs(current_visible_text_len - starting_visible_text_len) > 200
                )
                page_changed = card_on_screen or (
                    something_moved and weak_signal_seconds >= _WEAK_SIGNAL_GRACE_SECONDS
                )
                if something_moved and not card_on_screen:
                    # Give the card time to arrive rather than scraping the
                    # loading state. Still bounded: on expiry we scrape
                    # whatever is there, so a solve is never lost outright,
                    # and parse_property_card reports an unreadable result
                    # rather than an empty one.
                    weak_signal_seconds += poll_interval
                elif card_on_screen:
                    _settle(page, 1500)  # let the last rows paint
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
                        return {"found": True, **_scrape_result_page(page, screenshot_path, captured)}
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

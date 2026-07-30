"""
Live GST portal lookup -- Search Taxpayer by PAN (enumerate every GSTIN
registered under a PAN across every state) and Search Taxpayer by GSTIN
(SHOW FILING TABLE -- the actual GSTR-1/GSTR-3B return-filing history).

CONFIRMED LIVE (read-only page/DOM inspection this pass -- no CAPTCHA
solved, no data submitted, matching session_auth.py/mahabhumi.py's own
"never solve or read the CAPTCHA image, a human must" policy):
  - Search by PAN: https://services.gst.gov.in/services/searchtpbypan --
    the PAN input is #for_gstin, the submit button is #lotsearch (a
    shared Angular component/template with the GSTIN search page below,
    just relabeled).
  - Search by GSTIN/UIN: https://services.gst.gov.in/services/searchtp --
    same #for_gstin / #lotsearch, plus a "SHOW FILING TABLE" button,
    #filingTable, present in the DOM (its own tutorial documentation --
    tutorial.gst.gov.in -- confirms it requires picking a financial year
    afterward, then shows per-return filing details).
  - Both pages genuinely have a CAPTCHA widget ($scope.captchaObj in
    their own Angular controllers -- searchtpCtrl1.0.js /
    searchtpbypanctrl1.0.js) -- confirmed via a live console error
    (captchaObj.refresh is not a function) in the sandboxed browser this
    was researched in, where the widget's own init call threw before it
    could render -- so its actual markup was NOT observable this pass.
    Submitting a search without a valid captcha token was confirmed live
    to fail with a generic {"errorCode": "SWEB_9000"} response from the
    portal's own API (services/api/search/taxpayerDetails and
    services/api/get/gstndtls) -- consistent with a real CAPTCHA gate,
    not some other block. A standard, unrestricted Playwright Chromium
    browser (same as every other CAPTCHA-gated site in this pipeline)
    is expected to render the widget normally; this module's CAPTCHA-wait
    step and its result-page selectors are accordingly the same kind of
    best-effort, NOT YET independently confirmed past the CAPTCHA gate
    that mahabhumi.py's own module note used for the exact same reason --
    this needs one real human-in-the-loop live test to pin down the
    financial-year selector and the two results pages' real DOM shape,
    same as every other site here.

Matching every other CAPTCHA-gated module in this pipeline: nothing here
solves or reads the CAPTCHA image. A human must look at the real browser
window this opens and type it in themselves.

    python gst_portal.py pan <PAN>
    python gst_portal.py filing <GSTIN>
"""

from __future__ import annotations

import os
import shutil
import time

import pytesseract
from PIL import Image

import config
import gst_compliance

_BASE_URL = "https://services.gst.gov.in/"
_SEARCH_BY_PAN_URL = "https://services.gst.gov.in/services/searchtpbypan"
_SEARCH_BY_GSTIN_URL = "https://services.gst.gov.in/services/searchtp"

_SEL_INPUT = "#for_gstin"
_SEL_SEARCH_BTN = "#lotsearch"
_SEL_FILING_TABLE_BTN = "#filingTable"

# Same Tesseract-path bootstrap as mahabhumi.py, for the same reason:
# pytesseract shells out to the tesseract binary by name, and on a
# machine where it's installed but not on PATH, OCR would otherwise fail
# silently. Reuses the SAME project-local tessdata/ directory mahabhumi.py
# already fetches mar.traineddata into (this module only needs English,
# but TESSDATA_PREFIX is a single, process-wide setting, and doesn't hurt
# to point at a directory that also has eng.traineddata in it).
if not shutil.which("tesseract"):
    for _candidate in (
        os.environ.get("TESSERACT_CMD"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if _candidate and os.path.exists(_candidate):
            pytesseract.pytesseract.tesseract_cmd = _candidate
            break
_TESSDATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tessdata")
if os.path.isdir(_TESSDATA_DIR):
    os.environ.setdefault("TESSDATA_PREFIX", _TESSDATA_DIR)


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
    page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
    return p, browser, page


def _ocr_screenshot(page, screenshot_path: str) -> str:
    """Same reasoning as mahabhumi.py's _ocr_image: a best-effort fallback
    for whenever the results page doesn't parse cleanly via BeautifulSoup
    -- never raises, an honest "[OCR unavailable: ...]" marker instead."""
    try:
        page.screenshot(path=screenshot_path, full_page=True)
        return pytesseract.image_to_string(Image.open(screenshot_path)).strip()
    except Exception as e:
        return f"[OCR unavailable: {e}]"


def _wait_for_captcha_solve(page, timeout_seconds: int, poll_interval: float, success_texts: tuple):
    """Shared CAPTCHA-wait loop for both search flows below -- same
    hard-won robustness as mahabhumi.py's fetch_property_card (built and
    live-fixed earlier this session against a structurally similar
    ASP.NET/ASPX-style postback site): checks for any of `success_texts`
    appearing (the most specific, lowest-false-positive signal), a URL
    change, or a substantial jump in rendered visible text, and treats a
    mid-postback "execution context destroyed" error as evidence
    something just happened rather than a fatal failure. NOT YET
    confirmed live past an actual CAPTCHA solve (see module docstring) --
    the exact success_texts to look for here are this module's own best
    guess (GST-portal-specific labels its documented UI is known to show),
    to be corrected by a real live test the same way mahabhumi.py's
    detection was."""
    elapsed = 0.0
    last_status_at = 0.0
    starting_url = page.url
    try:
        starting_visible_text_len = len(page.inner_text("body"))
    except Exception:
        # A transient read failure this early (before the loop's own
        # per-iteration protection even applies) must not crash the whole
        # wait -- 0 is a safe fallback baseline, since any real content
        # appearing later will still trip the "substantial jump" check.
        starting_visible_text_len = 0
    print(f"[INFO] Waiting up to {timeout_seconds}s for you to finish...")
    while elapsed < timeout_seconds:
        if page.is_closed():
            raise BrowserClosedError("Browser window was closed before the CAPTCHA was solved.")
        try:
            body_text = page.inner_text("body")
            changed = (
                any(text in body_text for text in success_texts)
                or page.url != starting_url
                or abs(len(body_text) - starting_visible_text_len) > 200
            )
        except Exception:
            time.sleep(poll_interval)
            elapsed += poll_interval
            continue
        if changed:
            return
        time.sleep(poll_interval)
        elapsed += poll_interval
        if elapsed - last_status_at >= 30:
            print(f"[INFO] Still waiting for the CAPTCHA to be solved ({int(elapsed)}s/{timeout_seconds}s)...")
            last_status_at = elapsed
    raise CaptchaTimeoutError(f"No result within {timeout_seconds}s -- the CAPTCHA wasn't solved in time.")


def search_gstins_by_pan(
    pan: str,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    poll_interval: float = config.CAPTCHA_POLL_INTERVAL_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at the Search-by-PAN page, fills in `pan`,
    then waits for a human to solve the CAPTCHA and click Search. Returns
    {"found": bool, "gstins": [str, ...], "raw_text": str, "ocr_text": str,
    "url": str, "note": str}. The GSTIN list is extracted with
    gst_compliance's own validate_gstin as a filter over every "word" on
    the results page -- a deliberately loose, best-effort extraction (see
    module docstring: the real results-page markup isn't independently
    confirmed live yet), safe because validate_gstin's format check makes
    a false-positive match extremely unlikely."""
    import re

    p, browser, page = _launch(headless=False)
    try:
        page.goto(_SEARCH_BY_PAN_URL, timeout=30000)
        page.wait_for_selector(_SEL_INPUT, timeout=15000)
        page.fill(_SEL_INPUT, pan.strip().upper())

        print(f"\n[INFO] A browser window has opened at {_SEARCH_BY_PAN_URL}")
        print("[INFO] Please read the CAPTCHA shown there, type it in, and click Search.")
        try:
            _wait_for_captcha_solve(
                page, timeout_seconds, poll_interval,
                success_texts=("GSTIN", "Legal Name", "State Jurisdiction", "No records found"),
            )
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "gstins": [], "note": str(e)}

        body_text = page.inner_text("body")
        candidates = set(re.findall(r"\b[0-9]{2}[A-Za-z]{5}[0-9]{4}[A-Za-z][1-9A-Za-z]Z[0-9A-Za-z]\b", body_text))
        gstins = sorted(g.upper() for g in candidates if gst_compliance.validate_gstin(g))

        ocr_text = _ocr_screenshot(page, screenshot_path) if screenshot_path else ""
        return {
            "found": bool(gstins), "gstins": gstins, "raw_text": body_text, "ocr_text": ocr_text,
            "url": page.url,
            "note": "" if gstins else "No GSTIN-shaped text found on the results page -- confirm the PAN and re-check manually.",
        }
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


def fetch_gstin_filing_table(
    gstin: str,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    poll_interval: float = config.CAPTCHA_POLL_INTERVAL_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at the Search-by-GSTIN page, fills in
    `gstin`, waits for a human to solve the CAPTCHA and click Search, then
    clicks "SHOW FILING TABLE" (#filingTable). Returns {"found": bool,
    "raw_text": str, "ocr_text": str, "url": str, "note": str} -- does NOT
    yet parse the filing table into gst_compliance.py's structured record
    shape (form/period_start/period_end/filing_date), since that table's
    real column layout isn't independently confirmed live yet (see module
    docstring). raw_text/ocr_text are kept in full specifically so a human
    can read the real filing dates directly until that parsing step is
    built and confirmed against a real result."""
    p, browser, page = _launch(headless=False)
    try:
        page.goto(_SEARCH_BY_GSTIN_URL, timeout=30000)
        page.wait_for_selector(_SEL_INPUT, timeout=15000)
        page.fill(_SEL_INPUT, gstin.strip().upper())

        print(f"\n[INFO] A browser window has opened at {_SEARCH_BY_GSTIN_URL}")
        print("[INFO] Please read the CAPTCHA shown there, type it in, and click Search.")
        try:
            _wait_for_captcha_solve(
                page, timeout_seconds, poll_interval,
                success_texts=("Legal Name", "Registration Effective", "Constitution of Business", "No records found"),
            )
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "note": str(e)}

        try:
            page.click(_SEL_FILING_TABLE_BTN, timeout=10000)
            page.wait_for_timeout(2000)
        except Exception as e:
            body_text = page.inner_text("body")
            ocr_text = _ocr_screenshot(page, screenshot_path) if screenshot_path else ""
            return {
                "found": True, "raw_text": body_text, "ocr_text": ocr_text, "url": page.url,
                "note": f"Registration details found, but clicking SHOW FILING TABLE did not work as expected: {e}. "
                "Read raw_text/ocr_text directly, or check the browser manually.",
            }

        body_text = page.inner_text("body")
        ocr_text = _ocr_screenshot(page, screenshot_path) if screenshot_path else ""
        return {"found": True, "raw_text": body_text, "ocr_text": ocr_text, "url": page.url, "note": ""}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3 or sys.argv[1] not in ("pan", "filing"):
        print("usage: python gst_portal.py pan <PAN>", file=sys.stderr)
        print("       python gst_portal.py filing <GSTIN>", file=sys.stderr)
        sys.exit(2)

    if sys.argv[1] == "pan":
        print(search_gstins_by_pan(sys.argv[2]))
    else:
        print(fetch_gstin_filing_table(sys.argv[2]))

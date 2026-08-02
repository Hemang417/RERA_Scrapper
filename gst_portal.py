"""
Live GST portal lookup -- Search Taxpayer by PAN (enumerate every GSTIN
registered under a PAN across every state) and Search Taxpayer by GSTIN
(SHOW FILING TABLE -- the actual GSTR-1/GSTR-3B return-filing history).

CONFIRMED LIVE end-to-end (real human-in-the-loop CAPTCHA solve, real
result pages read -- against Pranami Neev Realty Limited, PAN AANCP0234D):
  - Search by PAN: https://services.gst.gov.in/services/searchtpbypan --
    PAN input #for_gstin, submit button #lotsearch (a shared Angular
    component/template with the GSTIN search page below, just relabeled).
    Confirmed result-page shape: a results table listing GSTIN/UIN,
    Status, State per registration under that PAN.
  - Search by GSTIN/UIN: https://services.gst.gov.in/services/searchtp --
    same #for_gstin / #lotsearch, then registration details render
    (Legal Name, Effective Date of registration, Constitution of
    Business, etc.). Clicking "SHOW FILING TABLE" (#filingTable) does
    NOT itself reveal any filing rows -- it reveals a "Financial Year"
    <select> (the LAST <select> on the page) plus its own SEARCH button
    (the LAST "SEARCH"-labelled button); picking a year and clicking
    that button is what actually renders the per-return rows, one
    "Filing details for {FORM}" block per form filed that year (GSTR3B,
    GSTR-1/IFF, and for some years CMP08/GSTR9/GSTR9C/GSTR1A too), each
    with its own "Financial Year / Tax Period / Date of filing / Status"
    table -- see `parse_filing_table` below, which turns this into
    gst_compliance.py's structured record shape.
  - Both pages have a real CAPTCHA widget; a human types it in and
    clicks Search in the real, visible browser this opens -- nothing
    here solves or reads the CAPTCHA image itself, same policy as every
    other CAPTCHA-gated module in this pipeline (session_auth.py /
    mahabhumi.py).

    python gst_portal.py pan <PAN>
    python gst_portal.py filing <GSTIN>
"""

from __future__ import annotations

import os
import re
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
_FY_RE = re.compile(r"^\d{4}-\d{4}$")

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
    years: tuple[str, ...] | None = None,
) -> dict:
    """Opens a VISIBLE browser at the Search-by-GSTIN page, fills in
    `gstin`, waits for a human to solve the CAPTCHA and click Search, then
    clicks "SHOW FILING TABLE" (#filingTable). Returns {"found": bool,
    "by_year": {"2022-2023": {"raw_text": str}, ...}, "ocr_text": str,
    "url": str, "note": str}.

    CONFIRMED LIVE (previously an unconfirmed guess -- see module
    docstring): clicking #filingTable does NOT itself reveal filing rows.
    It reveals a "Financial Year" <select> plus its own SEARCH button;
    a year must be picked and that button clicked before any per-return
    row appears. This function now does that for every year in `years`
    (default: every year the dropdown actually offers), one at a time in
    the SAME browser session so a single CAPTCHA solve covers every year
    rather than needing one per year. Still does not parse the resulting
    text into gst_compliance.py's structured record shape (form/
    period_start/period_end/filing_date) -- that table's real column
    layout inside `by_year[...]["raw_text"]` needs one confirmed read
    before a parser is worth writing; a human reads it directly for now."""
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
            page.wait_for_timeout(1500)
        except Exception as e:
            body_text = page.inner_text("body")
            ocr_text = _ocr_screenshot(page, screenshot_path) if screenshot_path else ""
            return {
                "found": True, "raw_text": body_text, "ocr_text": ocr_text, "url": page.url,
                "note": f"Registration details found, but clicking SHOW FILING TABLE did not work as expected: {e}. "
                "Read raw_text/ocr_text directly, or check the browser manually.",
            }

        try:
            year_select = page.locator("select").last
            year_select.wait_for(timeout=5000)
            available_years = [y.strip() for y in year_select.locator("option").all_inner_texts() if _FY_RE.match(y.strip())]
        except Exception:
            available_years = []

        if not available_years:
            body_text = page.inner_text("body")
            ocr_text = _ocr_screenshot(page, screenshot_path) if screenshot_path else ""
            return {
                "found": True, "raw_text": body_text, "ocr_text": ocr_text, "url": page.url,
                "note": "SHOW FILING TABLE was clicked but no Financial Year selector was found on the page -- "
                "read raw_text/ocr_text directly, or check the browser manually.",
            }

        wanted_years = [y for y in (years or available_years) if y in available_years] or available_years
        by_year = {}
        for year in wanted_years:
            try:
                year_select.select_option(label=year)
                page.locator("button:has-text('SEARCH')").last.click(timeout=5000)
                page.wait_for_timeout(1500)
                by_year[year] = {"raw_text": page.inner_text("body")}
            except Exception as e:
                by_year[year] = {"raw_text": "", "note": f"Could not fetch this year: {e}"}

        ocr_text = _ocr_screenshot(page, screenshot_path) if screenshot_path else ""
        return {"found": True, "by_year": by_year, "ocr_text": ocr_text, "url": page.url, "note": ""}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


# ---------------------------------------------------------------------------
# Confirmed-live parser: turns fetch_gstin_filing_table's `by_year` raw text
# into gst_compliance.py's structured record shape ({"form", "period_start",
# "period_end", "filing_date"}, all as "YYYY-MM-DD" strings -- the exact
# shape run_gst_compliance_check reads from gst_filing_input.json). Built
# against Pranami Neev Realty Limited's real filing history (76 periods,
# 2022-08 through 2026-06), the first real read past the CAPTCHA gate.
# ---------------------------------------------------------------------------
_MONTH_NUMBERS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1
)}
# Only these two forms have a due-date rule in gst_compliance.due_date --
# it raises ValueError for anything else. CMP08 (composition, quarterly),
# GSTR9/GSTR9C (annual), and GSTR1A (amendment) all appear in real filing
# tables but are deliberately NOT parsed into scoreable records here; they
# have no statutory due-date rule implemented, so scoring them would be a
# guess, not a computation.
_SCOREABLE_FORMS = {"GSTR3B": "GSTR-3B", "GSTR-1/IFF": "GSTR-1"}
_FILING_ROW_RE = re.compile(r"(\d{4}-\d{4})\t([A-Za-z]+)\t(\d{2}/\d{2}/\d{4})\tFiled")


def _month_period(month_name: str, financial_year: str):
    """A financial year "2024-2025" runs April 2024 -> March 2025; a tax
    period's own month name (not the calendar year) is all the portal
    gives per row, so the FY's start year applies to Apr-Dec and the FY's
    end year applies to Jan-Mar."""
    import calendar
    import datetime as _dt

    fy_start_year = int(financial_year.split("-")[0])
    month_num = _MONTH_NUMBERS[month_name]
    year = fy_start_year if month_num >= 4 else fy_start_year + 1
    last_day = calendar.monthrange(year, month_num)[1]
    return _dt.date(year, month_num, 1), _dt.date(year, month_num, last_day)


def parse_filing_table(by_year: dict) -> list[dict]:
    """Turns fetch_gstin_filing_table's `by_year` dict into a deduplicated,
    sorted list of {"form", "period_start", "period_end", "filing_date"}
    records (dates as "YYYY-MM-DD" strings), ready to drop straight into
    gst_filing_input.json. Only monthly GSTR-3B/GSTR-1 periods are
    included (see _SCOREABLE_FORMS); quarterly/annual forms are skipped,
    not guessed at. Deduplicates across years since the portal shows a
    period under whichever financial year it falls in -- overlap is not
    expected, but a defensive dedupe costs nothing."""
    seen = set()
    records = []
    for block in by_year.values():
        text = block.get("raw_text") or ""
        for section in re.split(r"Filing details for ", text)[1:]:
            form_label = section.split("\n", 1)[0].strip()
            form = _SCOREABLE_FORMS.get(form_label)
            if not form:
                continue
            for row_fy, month_name, filing_date_raw in _FILING_ROW_RE.findall(section):
                if month_name not in _MONTH_NUMBERS:
                    continue  # a quarterly ("Jul-Sep") or "Annual" period under a scoreable form label -- not expected, skip rather than guess
                period_start, period_end = _month_period(month_name, row_fy)
                day, month, year = filing_date_raw.split("/")
                filing_date = f"{year}-{month}-{day}"
                key = (form, period_start.isoformat())
                if key in seen:
                    continue
                seen.add(key)
                records.append({
                    "form": form,
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "filing_date": filing_date,
                })
    records.sort(key=lambda r: (r["form"], r["period_start"]))
    return records


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

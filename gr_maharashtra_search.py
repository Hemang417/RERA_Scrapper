"""
Maharashtra Government Resolutions (GR) portal search -- gr.maharashtra.gov.in,
CAPTCHA-gated. Confirmed real and live-tested this pass against a genuine hit:
searching Department="Urban Development" + a date range found the actual GR
(CID-1812/CR-274/UD-10, a CIDCO land-compensation/resettlement scheme file
number this pipeline needed to trace back to its published text for a Navi
Mumbai project's land-record note).

WHY THIS MODULE EXISTS, SEPARATE FROM mahabhumi.py'S CTS LOOKUP: a project's
land/title documents sometimes cite a reference that LOOKS like it could be a
CTS/survey number but is actually a Government Resolution file number
(Marathi convention: "<dept-code>-<serial>/<प्र.क्र.|CR>-<serial>/<dept-
abbrev>-<serial>", e.g. "CID-1812/CR-274/UD-10") -- a policy document, not a
land-parcel identifier, and Mahabhumi's CTS/Property-Card search will never
resolve it (CONFIRMED LIVE: Mahabhumi's own Property-Card office list for
Panvel taluka does not even cover the village this GR's own subject-matter
project sits in -- see _looks_like_gr_citation's own docstring for the
format this module watches for). See company_charter.run_gr_citation_lookup
for where this gets wired into the Charter pipeline's land-record section.

CLAUDE-VISION AUTOSOLVE (added 2026-10-26, see captcha_solver.solve_gr_captcha):
unlike this module's original design, a real, VISIBLE browser opens but
nothing waits on a human any more -- Claude vision reads #SitePH_ImgCaptcha
and this module fills it in and clicks शोधा (Search) itself, same
zero-human-intervention policy session_auth.py/up_captcha_search.py/
ts_rera_client.py already use.

CONFIRMED LIVE 2026-10-26 by deliberately submitting a wrong answer (twice:
with no filters, and again with a real keyword filled in) -- and this is
the one genuinely tricky part of autosolving this specific portal: UNLIKE
UP-RERA's "Invalid Captcha!!!" text, this portal shows NO distinguishing
rejection message at all. A wrong CAPTCHA just redisplays the page's own
default, UNFILTERED recent-GRs listing -- and that listing renders INSIDE
#SitePH_dgvDocuments itself (confirmed by direct DOM inspection: 11 rows
present, same table id search_gr() already parses for a real result), not
some other element -- so table-presence or row-count alone cannot tell a
genuinely filtered result apart from "the search never actually ran". The
one signal that does: #SitePH_ucPaging_lblTotalRecords (the "एकूण बाबी : N"
total) only changes from its pre-submit baseline when a filter genuinely
took effect, since a wrong CAPTCHA always redisplays the exact same
all-departments total. _solve_captcha_and_search below records that total
before the first attempt and, after each attempt, accepts the result only
when the confirmed-empty "माहिती उपलब्ध नाही" text appears OR the total
has moved from that baseline -- otherwise it refreshes the CAPTCHA and
tries again. (A real search that happens to return exactly the same count
as the unfiltered baseline would be misread as a wrong CAPTCHA and retried
needlessly -- accepted as a known, very unlikely edge case given this
module is only ever used for a narrow citation-keyword search, never an
unfiltered one.)

Mechanism, confirmed live by direct DOM inspection (classic ASP.NET
WebForms, same full-page-postback style as nclt_search.py / up_captcha_
search.py's pre-autosolve incarnation, NOT an AJAX partial postback):
  - Department dropdown: #SitePH_ddlDepartmentType (English labels shown in
    this module's own _DEPARTMENTS map are the ones confirmed relevant to
    land/CIDCO matters; the live page has ~50 options total).
  - Keyword (free-text, matched against the GR's own subject/body -- the
    right field for an old-style citation): #SitePH_txtKeywords, max 50
    chars.
  - #SitePH_txtGRNo ("सांकेतांक क्रमांक" / Code Number) is a DIFFERENT field
    for the newer SYSTEM-GENERATED 18-char tracking code (e.g.
    "202610161136400117"), not an old department-style citation -- its own
    on-page hint says "partial code allowed e.g. YYYYMMDD". Using it for a
    citation like "CID-1812/CR-274/UD-10" silently returns nothing; this
    module's search_gr() only ever writes to the keyword field.
  - #SitePH_txtFromDate / #SitePH_txtToDate: CONFIRMED LIVE BUG -- setting
    these via a raw value assignment (page.fill) does NOT register with the
    site's own onkeyup/onchange `validateDateFormat` handler or the jQuery
    `.datepicker()` it's bound to, and submitting with a value set that way
    throws a client-side validation alert() and the WHOLE FORM resets to
    blank on the resulting reload -- losing every other filled field too,
    not just the date. The fix (used throughout _set_date_via_picker below):
    open the picker (click the 📅 button) and drive ITS OWN year/month
    selects plus a day click, exactly the sequence a human would use.
  - #SitePH_ImgCaptcha loads from a dedicated endpoint
    (Site/Information/captcha.aspx), re-rendering on every full postback --
    not a per-request guid in the src the way UP-RERA's CaptchaImage.axd is,
    so there is no "did the token change" signal available here. That turns
    out not to matter: _solve_captcha_and_search only ever clicks the real
    Search button itself (never the separate refresh icon, see below), and
    a fresh image is already waiting after each such click since the whole
    page reloads regardless of whether the answer was right.
  - A separate "refresh CAPTCHA" icon also exists next to the image
    (`<input type="image" onclick="document.getElementById(
    'form1').submit();">`, no id/name -- alt="Refresh Captcha" is the one
    stable selector) and does its own full page postback, exactly like the
    real Search button -- this is precisely the "captcha-refresh click
    masquerading as a human submit" bug class nclt_search.py's own
    docstring warns about, confirmed here by reading the actual onclick
    handler. Not used by this module at all: with no human in the loop to
    mistakenly click it, and Search's own postback already refreshing the
    image, there is nothing left for it to do here.
  - Results render into #SitePH_dgvDocuments, a plain server-rendered
    <table> (not a grid widget), with a stable column shape confirmed live:
    Label6=department, Label1=title/subject, Label2=the system GR code,
    Label7=GR date, lblFileSize=size in KB, and an <a id="..._a1_N"> whose
    href is the PDF's relative path. The confirmed-live empty-result marker
    is the exact text "माहिती उपलब्ध नाही." ("Information not available.").

    python gr_maharashtra_search.py "1812" --department "Urban Development"
    python gr_maharashtra_search.py "" --department "Urban Development" \\
        --from-date 2017-09-22 --to-date 2017-09-22
"""

from __future__ import annotations

import os
import re
import sys
import time

from bs4 import BeautifulSoup

import captcha_solver
import config

_SEARCH_URL = "https://gr.maharashtra.gov.in/1145/Government-Resolutions"

_SEL_DEPARTMENT = "#SitePH_ddlDepartmentType"
_SEL_KEYWORD = "#SitePH_txtKeywords"
_SEL_FROM_DATE = "#SitePH_txtFromDate"
_SEL_FROM_DATE_CAL_BTN = ".friendlyDate .calendarButton"  # first match = From-date's own button
_SEL_TO_DATE = "#SitePH_txtToDate"
_SEL_GR_CODE = "#SitePH_txtGRNo"
_SEL_CAPTCHA_INPUT = "#SitePH_txtimgcode"
_SEL_SUBMIT_BTN = "#SitePH_btnSearch"
_SEL_RESULTS_TABLE = "#SitePH_dgvDocuments"
_SEL_TOTAL_RECORDS = "#SitePH_ucPaging_lblTotalRecords"
_EMPTY_RESULT_TEXT = "माहिती उपलब्ध नाही"
# Same cap as every other portal's Claude-vision autosolve loop in this
# codebase (session_auth.py, up_captcha_search.py, ...).
_MAX_AUTOSOLVE_ATTEMPTS = 8

# Department value -> the live <select>'s own option value, confirmed live
# 2026-10-16 by direct DOM inspection. Only the ones plausibly relevant to a
# land/title/CIDCO citation are named here; see this module's own docstring
# for the full department list if a caller needs one not yet added.
_DEPARTMENTS = {
    "Urban Development": "25",            # नगर विकास विभाग -- CIDCO/Navi Mumbai/municipal matters
    "Revenue and Forest": "19",            # महसूल व वन विभाग
    "Revenue (incl. Land Acquisition)": "48",  # महसूल विभाग(भूसंपादन सह)विभाग (पासुन २४-जून-२०२६)
    "Housing": "09",                       # गृहनिर्माण विभाग
    "General Administration": "07",        # सामान्य प्रशासन विभाग
    "Public Works": "18",                  # सार्वजनिक बांधकाम विभाग
}

# Confirmed live 2026-10-16: a value set via page.fill() on the date inputs
# never reaches the site's own datepicker-bound validation, and submitting
# anyway throws a client-side alert() that resets the WHOLE form (not just
# the date) on reload. Every date must go through the real picker widget.
_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class CaptchaTimeoutError(Exception):
    pass


class BrowserClosedError(Exception):
    pass


class UnknownDepartmentError(Exception):
    pass


def _looks_like_gr_citation(value: str) -> bool:
    """Whether `value` has the shape of an old-style Maharashtra GR file
    number -- "<dept-code>-<serial>/<प्र.क्र.|CR|C.R.>-<serial>/<dept-
    abbrev>-<serial>", e.g. "CID-1812/CR-274/UD-10" or "CID-1812/C.R.274/
    UD-10" -- as opposed to a genuine CTS/survey/gat number, which is just a
    bare number or number/subdivision (e.g. "602", "88/1"). Used by
    company_charter.run_gr_citation_lookup to decide whether a value parked
    in a project's land_identification field should be routed to THIS
    search instead of (or in addition to) Mahabhumi's CTS lookup -- a
    code-computed, deterministic classification, never a guess about what
    the value actually refers to."""
    return bool(re.match(
        r"^[A-Za-z]{2,6}[-.]?\s*\d{2,6}\s*/\s*(?:C\.?\s*R\.?|प्र\.?\s*क्र\.?)\s*[-.]?\s*\d{2,6}\s*/\s*[A-Za-z]{2,6}[-.]?\s*\d{1,4}$",
        value.strip(),
    ))


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
        # dependency gap. Same fix as every other browser-driven module here.
        browser = p.chromium.launch(channel="chrome", headless=headless)
    page = browser.new_context(viewport={"width": 1366, "height": 950}).new_page()
    return p, browser, page


def _settle(page, timeout_ms=6000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def _set_date_via_picker(page, date_input_selector: str, calendar_button_selector: str, iso_date: str) -> None:
    """Sets `date_input_selector` to `iso_date` (YYYY-MM-DD) by driving the
    real jQuery UI datepicker -- opening it, selecting year/month from ITS
    OWN dropdowns, then clicking the matching day cell -- never by filling
    the text field directly. See this module's own docstring for why a
    direct fill breaks the whole form rather than just this one field."""
    year, month, day = iso_date.split("-")
    month_abbr = _MONTH_ABBR[int(month) - 1]
    day_num = int(day)

    page.click(calendar_button_selector)
    picker = page.locator(".ui-datepicker:visible").last
    picker.locator(".ui-datepicker-year").select_option(year)
    picker.locator(".ui-datepicker-month").select_option(label=month_abbr)
    # The day cells re-render after each select_option postback above; find
    # the exact-text, non-"ui-state-disabled" link for this day (a leading
    # blank/disabled cell from the previous month can share the same number).
    day_cell = picker.locator(f"a.ui-state-default:text-is('{day_num}')").first
    day_cell.click()


def _solve_captcha_and_search(page, timeout_seconds: int) -> None:
    """Fills #SitePH_ImgCaptcha's answer in via Claude vision and clicks
    शोधा (Search) itself, retrying against a FRESH CAPTCHA up to
    _MAX_AUTOSOLVE_ATTEMPTS times. No separate refresh-icon click is needed
    between attempts -- #SitePH_ImgCaptcha re-renders on every full postback
    (confirmed live, including a plain Search click with no captcha-refresh
    involved at all), so clicking Search again already hands the next
    attempt a brand new image.

    CONFIRMED LIVE 2026-10-26: this portal gives no rejection text at all
    (see module docstring), so a wrong answer is recognized by
    #SitePH_ucPaging_lblTotalRecords staying exactly at its pre-submit
    baseline, not by any error message. Raises CaptchaTimeoutError if no
    attempt within the budget produces a result distinguishable from that
    baseline, or BrowserClosedError if the window closes first."""
    try:
        baseline_total = _safe_read(lambda: page.inner_text(_SEL_TOTAL_RECORDS), attempts=3, delay=0.5)
    except Exception:
        baseline_total = None

    print(f"\n[INFO] A browser window has opened at {_SEARCH_URL}")
    print(f"[INFO] Solving the CAPTCHA via Claude vision (up to {_MAX_AUTOSOLVE_ATTEMPTS} attempts)...")
    loop_deadline = time.time() + timeout_seconds
    for attempt in range(1, _MAX_AUTOSOLVE_ATTEMPTS + 1):
        if page.is_closed():
            raise BrowserClosedError("Browser window was closed before the CAPTCHA was solved.")
        if time.time() > loop_deadline:
            raise CaptchaTimeoutError(
                f"No confirmed result within {timeout_seconds}s -- the CAPTCHA wasn't solved in time."
            )
        try:
            backend_name, solved_text = captcha_solver.solve_gr_captcha(page, enabled=True)
            page.fill(_SEL_CAPTCHA_INPUT, solved_text)
            page.click(_SEL_SUBMIT_BTN)
        except Exception as e:
            print(f"[INFO] CAPTCHA autosolve attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} failed ({e}).")
            continue

        try:
            page.wait_for_load_state("load", timeout=15000)
        except Exception:
            pass
        _settle(page, timeout_ms=8000)
        time.sleep(1.0)

        body_text = _safe_read(lambda: page.inner_text("body"), min_length=50)
        if _EMPTY_RESULT_TEXT in body_text:
            print(f"[OK] {backend_name}'s solve ({solved_text!r}) was accepted on attempt "
                  f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} (confirmed empty result).")
            return
        try:
            current_total = page.inner_text(_SEL_TOTAL_RECORDS)
        except Exception:
            current_total = None
        if current_total is not None and current_total != baseline_total:
            print(f"[OK] {backend_name}'s solve ({solved_text!r}) was accepted on attempt "
                  f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} (total moved from {baseline_total!r} to "
                  f"{current_total!r}).")
            return

        print(f"[INFO] {backend_name}'s solve ({solved_text!r}) on attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} "
              f"is indistinguishable from the unfiltered default listing -- treating as a wrong CAPTCHA.")

    raise CaptchaTimeoutError(
        f"CAPTCHA autosolve did not produce a result distinguishable from the unfiltered default "
        f"listing (total stayed at {baseline_total!r}) within {_MAX_AUTOSOLVE_ATTEMPTS} attempt(s)."
    )


def _safe_read(read_fn, attempts=6, delay=1.0, min_length=0):
    """Same retry shape as nclt_search.py's helper of the same name -- see
    that module's docstring for the confirmed-live race this guards
    against."""
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


def _parse_results_rows(html: str, base_url: str) -> list[dict]:
    """Rows of #SitePH_dgvDocuments, confirmed live column shape: department
    name, title/subject, the system GR code, the GR date, file size in KB,
    and the PDF's absolute download URL (resolved against `base_url` since
    the <a> href on the live page is relative)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id="SitePH_dgvDocuments")
    if table is None:
        return []
    out = []
    for row in table.find_all("tr")[1:]:  # skip the header row
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        link = cells[6].find("a")
        href = link.get("href") if link else None
        pdf_url = _urljoin(base_url, href) if href else None
        out.append({
            "department": cells[1].get_text(" ", strip=True),
            "title": cells[2].get_text(" ", strip=True),
            "gr_code": cells[3].get_text(" ", strip=True),
            "gr_date": cells[4].get_text(" ", strip=True),
            "size_kb": cells[5].get_text(" ", strip=True),
            "pdf_url": pdf_url,
        })
    return out


def _urljoin(base_url: str, href: str) -> str:
    from urllib.parse import urljoin
    return urljoin(base_url, href)


def search_gr(
    keyword: str = "",
    department: str = "Urban Development",
    from_date: str | None = None,
    to_date: str | None = None,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at the Maharashtra GR portal's search form,
    pre-fills `department` (a key into _DEPARTMENTS -- raises
    UnknownDepartmentError if it isn't one), `keyword` (the right field for
    an old-style file-number citation -- see module note on why NOT the
    Code Number field), and `from_date`/`to_date` (ISO "YYYY-MM-DD",
    optional, driven through the real datepicker), then solves the CAPTCHA
    via Claude vision and clicks शोधा (Search) itself (see
    _solve_captcha_and_search).

    CONFIRMED LIVE 2026-10-16: a genuine zero-result search renders the
    exact text "माहिती उपलब्ध नाही." ("Information not available.") where
    the results table would be -- used below to recognize a confirmed empty
    result, distinct from the table simply not having rendered yet.

    Returns {"found", "rows", "raw_text", "url", "note"}. `rows` is a list
    of {"department", "title", "gr_code", "gr_date", "size_kb", "pdf_url"}."""
    if department not in _DEPARTMENTS:
        raise UnknownDepartmentError(f"Unknown department {department!r} -- must be one of {sorted(_DEPARTMENTS)}.")

    p, browser, page = _launch(headless=False)
    try:
        page.goto(_SEARCH_URL, timeout=30000)
        page.wait_for_selector(_SEL_DEPARTMENT, timeout=15000)
        page.select_option(_SEL_DEPARTMENT, value=_DEPARTMENTS[department])

        if keyword:
            page.fill(_SEL_KEYWORD, keyword)
        if from_date:
            _set_date_via_picker(page, _SEL_FROM_DATE, _SEL_FROM_DATE_CAL_BTN, from_date)
        if to_date:
            # The To-date's own calendar button is the SECOND ".calendarButton"
            # on the page (From-date's is first) -- confirmed live, both
            # share the same CSS class with no distinguishing id prefix.
            to_date_cal_btn = page.locator(".friendlyDate .calendarButton").nth(1)
            page.click(_SEL_TO_DATE)  # harmless focus; the button click below does the real work
            to_date_cal_btn.click()
            picker = page.locator(".ui-datepicker:visible").last
            year, month, day = to_date.split("-")
            picker.locator(".ui-datepicker-year").select_option(year)
            picker.locator(".ui-datepicker-month").select_option(label=_MONTH_ABBR[int(month) - 1])
            picker.locator(f"a.ui-state-default:text-is('{int(day)}')").first.click()

        print(f"[INFO] Department ({department}) and keyword ({keyword!r}) are pre-filled.")
        try:
            _solve_captcha_and_search(page, timeout_seconds)
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "rows": [], "raw_text": "", "url": page.url, "note": str(e)}

        html = _safe_read(page.content, min_length=500)
        body_text = _safe_read(lambda: page.inner_text("body"), min_length=50)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))

        if _EMPTY_RESULT_TEXT in body_text:
            return {"found": False, "rows": [], "raw_text": body_text, "url": page.url, "note": ""}

        rows = _parse_results_rows(html, page.url)
        note = "" if rows else (
            "Neither the confirmed 'माहिती उपलब्ध नाही' (no results) text nor a parseable "
            "#SitePH_dgvDocuments table appeared after submit -- inconclusive, not a confirmed "
            "clean search. Check raw_text or the screenshot directly."
        )
        return {"found": bool(rows), "rows": rows, "raw_text": body_text, "url": page.url, "note": note}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


def download_gr_pdf(pdf_url: str, dest_path: str, timeout: int = 30) -> bool:
    """Downloads a GR's PDF directly over plain HTTP -- CONFIRMED LIVE this
    pass that these files need no session/cookie/CAPTCHA at all, unlike the
    search itself (a public static upload path). Returns whether the save
    succeeded; never raises, same "a download failure is a gap, not a
    crash" discipline as every other optional document fetch here."""
    try:
        import requests
        r = requests.get(pdf_url, timeout=timeout, verify=False)
        r.raise_for_status()
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception:
        return False


def extract_pdf_text(pdf_path: str) -> str:
    """Full text of every page, joined with a '--- PAGE n ---' marker --
    CONFIRMED LIVE this pass that these GR PDFs carry a real embedded text
    layer (not scanned images), so this is a direct extraction, never OCR.
    Returns "" on any failure (corrupt/missing file) rather than raising --
    a failed extraction is a gap for the caller to note, not a crash."""
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        parts = []
        for i, page in enumerate(doc):
            parts.append(f"--- PAGE {i + 1} ---\n{page.get_text()}")
        return "\n\n".join(parts)
    except Exception:
        return ""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("keyword", help="Free-text keyword, e.g. a GR file-number fragment like '1812'. May be empty ('') when searching by date only.")
    parser.add_argument("--department", default="Urban Development", choices=sorted(_DEPARTMENTS))
    parser.add_argument("--from-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--to-date", default=None, help="YYYY-MM-DD")
    args = parser.parse_args()

    _result = search_gr(args.keyword, args.department, args.from_date, args.to_date)
    print("\n--- RESULT ---")
    for _k, _v in _result.items():
        if _k == "raw_text":
            print(f"{_k}: ({len(_v)} chars)")
        elif _k == "rows":
            print(f"rows: {len(_v)}")
            for _row in _v[:20]:
                print(f"  {_row}")
        else:
            print(f"{_k}: {_v}")

"""
NCLT (National Company Law Tribunal) case status -- party-name search,
CAPTCHA-gated. Confirmed real and live-tested this pass (DOM structure and
the genuine no-match state; see module note on `search_party` for what
still hasn't been live-confirmed).

The site (efiling.nclt.gov.in/nclt/public/case_status.php) is a plain PHP
form, not ASP.NET WebForms. Confirmed live by direct DOM inspection:
  - "SEARCH BY" has a PARTY NAME WISE option (value="party_wise") that,
    once selected, triggers a real full-page POST (change() ->
    document.frm.submit()) revealing the party-name field -- this is NOT
    an AJAX partial update.
  - The bench selector (name="schemaname") and the party-name field
    (name="namee" -- yes, misspelled on the real site) do not themselves
    postback; they're read directly off the DOM at submit time.
  - Only the final SEARCH click is CAPTCHA-gated (name="answer"), and it
    too is a real full-page POST (submitForm() -> document.frm.submit()),
    not AJAX -- same mechanism as up_captcha_search.py's UP-RERA projects
    search, which is why this module borrows its exact
    _wait_for_human_submit/_safe_read helpers rather than mahabhumi.py's
    (that site's ASP.NET UpdatePanel partial-postback model doesn't apply
    here).

SAME POLICY AS EVERY OTHER CAPTCHA-GATED MODULE HERE (gst_portal.py /
mahabhumi.py / up_captcha_search.py / session_auth.py): nothing in this
file reads or solves a CAPTCHA image. A real, VISIBLE browser opens, a
human reads it and types it in themselves, and this only reads the page
AFTER that human submits.

16 benches nationwide (see _BENCHES). A Maharashtra-based promoter's
filings are most plausibly under NCLT, Mumbai Bench -- the default here --
but a promoter with pan-India group entities could have proceedings under
any other bench too; this module only ever checks the ONE bench a caller
names, same "one lookup, one CAPTCHA" discipline as CTS/GST, and a caller
that wants broader coverage pays one more CAPTCHA solve per additional
bench.

    python nclt_search.py "<party name>" [bench name, default Mumbai]
"""

from __future__ import annotations

import sys
import time

from bs4 import BeautifulSoup

import config

_CASE_STATUS_URL = "https://efiling.nclt.gov.in/nclt/public/case_status.php"

# value -> matches the real <option value="N"> codes, confirmed live.
_BENCHES = {
    "Ahmedabad": "1", "Allahabad": "2", "Bengaluru": "3", "Chandigarh": "4",
    "Chennai": "5", "Guwahati": "6", "Hyderabad": "7", "Kolkata": "8",
    "Mumbai": "9", "Principal": "10", "New Delhi": "11", "Jaipur": "12",
    "Amaravati": "13", "Cuttack": "14", "Kochi": "15", "Indore": "16",
}

_SEL_BENCH = 'select[name="schemaname"]'
_SEL_SEARCH_BY = 'select[name="search_by"]'
_SEL_PARTY_NAME = 'input[name="namee"]'


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


def _settle(page, timeout_ms=6000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def _wait_for_human_submit(page, timeout_seconds, request_predicate, post_submit_marker=None):
    """Same shape as up_captcha_search._wait_for_human_submit (see that
    module's own docstring for the two live bugs -- a body-text-length
    delta and a captcha-refresh click both masquerading as "the human
    submitted" -- that make a SPECIFIC request predicate, not a generic
    page-change check, the only reliable signal here too)."""
    print(f"[INFO] Waiting up to {timeout_seconds}s for you to solve the CAPTCHA and click SEARCH...")
    try:
        with page.expect_request(request_predicate, timeout=timeout_seconds * 1000):
            pass
    except Exception as e:
        if page.is_closed():
            raise BrowserClosedError("Browser window was closed before the CAPTCHA was solved.") from e
        raise CaptchaTimeoutError(
            f"No SEARCH submit within {timeout_seconds}s -- the CAPTCHA wasn't solved in time."
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
    """Same retry shape as up_captcha_search._safe_read -- see that
    module's docstring for the confirmed-live race this guards against
    (a mid-navigation blank interim document that reads successfully but
    near-empty, rather than raising)."""
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


def _parse_case_rows(html: str) -> list[dict]:
    """Any table whose header row names a case/diary/filing-number-shaped
    column, as a list of {header: cell} dicts.

    NOT YET CONFIRMED LIVE against a real match (this pass only confirmed
    the genuine no-match state -- see search_party's docstring): treat an
    empty return here as "this shape hasn't been proven against a real hit
    yet," not as proof of a clean result -- search_party's own `note`
    carries that distinction, same discipline as
    up_captcha_search.search_appeals's own unconfirmed-shape caveat."""
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = [tr.find_all(["td", "th"]) for tr in table.find_all("tr")]
        rows = [r for r in rows if r]
        if not rows:
            continue
        header = [c.get_text(" ", strip=True).casefold() for c in rows[0]]
        joined = " | ".join(header)
        if "case" not in joined and "diary" not in joined and "filing" not in joined:
            continue
        out = []
        for cells in rows[1:]:
            texts = [c.get_text(" ", strip=True) for c in cells]
            if not any(texts):
                continue
            out.append({header[i]: texts[i] for i in range(min(len(header), len(texts)))})
        return out
    return []


def search_party(
    party_name: str,
    bench: str = "Mumbai",
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at NCLT's Case Status Report, switches
    SEARCH BY to PARTY NAME WISE, picks `bench` (default Mumbai) and
    pre-fills the party name, then waits for a human to read the CAPTCHA
    and click SEARCH.

    CONFIRMED LIVE this pass: a non-matching party name returns the exact
    page text 'No Record Found In Case Detail.' -- used below to recognize
    a genuine zero-result search. A MATCHING result's table shape has NOT
    been confirmed live yet (this pass never had a real CAPTCHA solved
    against a party actually on record) -- when neither the confirmed
    no-match text nor a parseable results table appears, this returns
    found=False with a non-empty `note` flagging the result as
    inconclusive rather than a confirmed clean check, per this codebase's
    "no finding never means no check" convention (see guardrails.md).
    `raw_text` and an optional screenshot are always returned so a human
    can read an actual hit directly until a live one confirms the header
    names _parse_case_rows should look for.

    Returns {"found", "rows", "raw_text", "url", "note"}."""
    if bench not in _BENCHES:
        return {
            "found": False, "rows": [], "raw_text": "", "url": "",
            "note": f"Unknown bench {bench!r} -- must be one of {sorted(_BENCHES)}.",
        }

    p, browser, page = _launch(headless=False)
    try:
        page.goto(_CASE_STATUS_URL, timeout=30000)
        page.wait_for_selector(_SEL_SEARCH_BY, timeout=15000)
        page.select_option(_SEL_SEARCH_BY, value="party_wise")
        page.evaluate("change()")  # real full-page POST, confirmed live -- see module note
        page.wait_for_selector(_SEL_PARTY_NAME, timeout=15000)

        page.select_option(_SEL_BENCH, value=_BENCHES[bench])
        page.fill(_SEL_PARTY_NAME, party_name)

        print(f"\n[INFO] A browser window has opened at {_CASE_STATUS_URL}")
        print(f"[INFO] Bench ({bench}) and party name ({party_name!r}) are pre-filled. "
              f"Please read the CAPTCHA and click SEARCH.")
        try:
            _wait_for_human_submit(
                page, timeout_seconds,
                request_predicate=lambda req: (
                    req.method == "POST" and "case_status.php" in req.url
                    and "party_wise" in (req.post_data or "")
                ),
                post_submit_marker=_SEL_SEARCH_BY,
            )
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "rows": [], "raw_text": "", "url": page.url, "note": str(e)}

        html = _safe_read(page.content, min_length=500)
        body_text = _safe_read(lambda: page.inner_text("body"), min_length=50)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))

        if "no record found" in body_text.casefold():
            return {"found": False, "rows": [], "raw_text": body_text, "url": page.url, "note": ""}

        rows = _parse_case_rows(html)
        note = "" if rows else (
            "Page did not show the confirmed 'No Record Found In Case Detail.' text, but no results "
            "table with a recognizable case/diary/filing-number column could be parsed either -- "
            "inconclusive, not a confirmed clean check. Check raw_text or the screenshot directly; "
            "this results shape has not been confirmed live yet."
        )
        return {"found": bool(rows), "rows": rows, "raw_text": body_text, "url": page.url, "note": note}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    _party_name = sys.argv[1]
    _bench = sys.argv[2] if len(sys.argv) > 2 else "Mumbai"
    _result = search_party(_party_name, _bench)
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

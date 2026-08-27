"""
Human-in-the-loop CAPTCHA search for the two UP pages this pipeline never
posts to unsolved: UP-RERA's own project search (View_projects.aspx --
states/adapter_uttarpradesh.py deliberately does not define
search_promoter_projects, see group_sweep._CANNOT_SEARCH["UP"]) and
UP-REAT's appeals judgement search (efilingreat.up.gov.in -- Partial in the
2026-08-26 coverage audit for exactly this reason).

SAME POLICY AS EVERY OTHER CAPTCHA-GATED MODULE HERE (gst_portal.py /
mahabhumi.py / session_auth.py): nothing in this file reads or solves a
CAPTCHA image. A real, VISIBLE browser opens, a human reads it and types it
in themselves, and this only reads the page AFTER that human submits.

    python up_captcha_search.py projects "<promoter name>" "<District>"
    python up_captcha_search.py appeals "<party name>"

Both need a human at the keyboard.

FIRST LIVE ATTEMPT (2026-08-26) closed the browser on the human before they
had touched the CAPTCHA. The bug: "wait for the page to change" was judged
by a body-text-length delta, and this page's own promoter dropdown renders
~2,300 option strings into inner_text -- trivially enough incidental reflow
to trip a 200-character threshold on its own. Fixed by waiting for the one
event that can ONLY mean the human submitted: a real page navigation (this
portal's Search button does a traditional synchronous form POST, not an
AJAX partial update).
"""

from __future__ import annotations

import sys
import time

from bs4 import BeautifulSoup

import config

_PROJECTS_URL = "https://www.up-rera.in/View_projects.aspx"
_APPEALS_URL = "https://efilingreat.up.gov.in/upreat/judgement.php"

_SEL_DISTRICT = "#ctl00_ContentPlaceHolder1_DdlprojectDistrict"
_SEL_PROMOTER = "#ctl00_ContentPlaceHolder1_ddl_prm"
_SEL_CAPTCHA_INPUT = "#ctl00_ContentPlaceHolder1_txtcap"


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
    """After a script-driven select_option that MIGHT autopostback (ASP.NET
    WebForms style -- a real full-page reload, not a same-page AJAX
    refresh), wait for the network to go quiet. Never raises: a select
    that does NOT postback (pure client-side) just times out here
    harmlessly.
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def _wait_for_human_submit(page, timeout_seconds, request_predicate, post_submit_marker=None):
    """Blocks until a request matching `request_predicate` fires, or
    `timeout_seconds` elapses. The predicate is what actually distinguishes
    "the human clicked Search" from every OTHER thing this page can do
    while they're reading the CAPTCHA.

    TWO BUGS FOUND LIVE getting here, both the same species: something else
    on the page also fires a request/navigation, and a generic "wait for
    activity" check can't tell it apart from the real submit.
      1. Waiting for ANY page change (a body-text-length delta) tripped on
         the promoter dropdown's own ~2,300 rendered option strings, before
         the human had touched anything.
      2. Waiting for ANY navigation tripped on clicking "refresh CAPTCHA" --
         a plain-looking image button that is ALSO a full ASP.NET postback,
         since it needs a fresh guid from the server. A human who read an
         unclear CAPTCHA and refreshed it once closed the browser on
         themselves before ever reaching Search.
    The caller's predicate must therefore name the SPECIFIC request the
    real Search submit makes and nothing else does -- confirmed live per
    page (see the two call sites): the projects page's Search button POSTs
    its own ASP.NET control name; UP-REAT's is pure AJAX
    (`fn_search_by_case_dfr()`) to `ajax_judgement.php` with
    `action=case_status_search`, while its own CAPTCHA-refresh icon only
    ever GETs `captcha.php` -- confirmed by watching both live before ever
    asking a human to solve anything.
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
    # Whatever happens next -- a full page reload (projects) or an AJAX
    # response repainting part of the page (appeals) -- let it settle.
    # wait_for_load_state("load") returns immediately when no new
    # navigation occurs, so this is safe for both shapes.
    try:
        page.wait_for_load_state("load", timeout=8000)
    except Exception:
        pass
    _settle(page, timeout_ms=8000)
    if post_submit_marker:
        # A THIRD race, found live: expect_request()'s __exit__ only proves
        # the request FIRED, not that the resulting page has finished
        # rendering. For a real full-page reload this leaves a window where
        # the DOM is a blank interim document -- page.content()/inner_text
        # either raise "page is navigating" or, worse, succeed against that
        # blank page and silently return almost nothing. Waiting for one
        # element that's on the resulting page NO MATTER what it says
        # (found or empty) is what actually proves rendering is done.
        try:
            page.wait_for_selector(post_submit_marker, timeout=15000)
        except Exception:
            pass
    time.sleep(1.0)


def _safe_read(read_fn, attempts=6, delay=1.0, min_length=0):
    """Retries `read_fn()` (e.g. page.content / page.inner_text) against
    Playwright's "Unable to retrieve content because the page is
    navigating and changing the content" -- a real transient race, not a
    coding error: _wait_for_human_submit only waits for the SEARCH
    REQUEST to fire, and the resulting navigation can still be in flight
    a moment after that (confirmed live: the first successful CAPTCHA
    solve after the request-predicate fix hit exactly this).

    ALSO retries a read that succeeds but is suspiciously short --
    `min_length`, when given. Confirmed live as a SEPARATE failure mode
    from the one above: a mid-navigation blank interim document doesn't
    always raise on read, it can return a real (non-exception) but
    near-empty string, which silently passed as "the search returned zero
    rows" the first time this ran. Re-raises whatever the last attempt
    raised once attempts are exhausted, or returns the last (too-short)
    result if every attempt merely stayed short without erroring.
    """
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


def _parse_results_table(html: str) -> list[dict]:
    """Any table whose header row names a registration-number-shaped column
    AND a project-name-shaped column -- the shape a results grid would have
    if one rendered -- as a list of {header: cell} dicts. Empty list means
    either no such table rendered (the search never actually landed) or it
    rendered with zero data rows (a genuine empty result); `raw_text` is
    kept alongside so a caller can tell the two apart rather than trusting
    this alone.

    CONFIRMED LIVE 2026-08-26 that the real header reads 'Reg.Number', not
    'Registration Number' -- an early version of this check required the
    literal word 'registration' and silently returned zero rows against a
    results grid that had, in fact, rendered UPRERAPRJ14636 correctly.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = [tr.find_all(["td", "th"]) for tr in table.find_all("tr")]
        rows = [r for r in rows if r]
        if not rows:
            continue
        header = [c.get_text(" ", strip=True).casefold() for c in rows[0]]
        joined = " | ".join(header)
        has_reg_col = "registration" in joined or ("reg" in joined and "number" in joined)
        if not has_reg_col or "project" not in joined:
            continue
        out = []
        for cells in rows[1:]:
            texts = [c.get_text(" ", strip=True) for c in cells]
            if not any(texts):
                continue
            out.append({header[i]: texts[i] for i in range(min(len(header), len(texts)))})
        return out
    return []


def search_projects_by_promoter(
    promoter_name_contains: str,
    district: str,
    promoter_option_value: str | None = None,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at UP-RERA's project search, picks `district`
    and the promoter matching `promoter_name_contains` (or the exact
    `promoter_option_value` if you already have the UPRERAPRM id), then
    waits for a human to read the CAPTCHA and click Search.

    District is mandatory per states/uttarpradesh.py's own notes -- this
    is a ONE-district search, not a statewide sweep. Confirming this works
    at all (a solved CAPTCHA really does return this promoter's OTHER
    projects) is the point; a real sweep would still need one CAPTCHA solve
    per district, which is why group_sweep does not attempt it.

    Returns {"found", "rows", "raw_text", "url", "note"}. `rows` is [] both
    when nothing rendered and when a results table rendered with zero
    entries -- `note` says which.
    """
    p, browser, page = _launch(headless=False)
    try:
        page.goto(_PROJECTS_URL, timeout=30000)
        page.wait_for_selector(_SEL_DISTRICT, timeout=15000)
        page.select_option(_SEL_DISTRICT, label=district)
        _settle(page)  # the district select may itself postback

        if promoter_option_value:
            page.select_option(_SEL_PROMOTER, value=str(promoter_option_value))
        else:
            matched = None
            for opt in page.query_selector_all(f"{_SEL_PROMOTER} option"):
                if promoter_name_contains.casefold() in (opt.inner_text() or "").casefold():
                    matched = opt.get_attribute("value")
                    break
            if matched is None:
                return {
                    "found": False, "rows": [], "raw_text": "", "url": page.url,
                    "note": (f"No promoter option in this page's dropdown (district={district}) "
                              f"matched {promoter_name_contains!r}. The promoter list may be "
                              f"scoped to the selected district, or the name is spelled "
                              f"differently there."),
                }
            page.select_option(_SEL_PROMOTER, value=matched)
        _settle(page)  # the promoter select may ALSO postback on its own

        print(f"\n[INFO] A browser window has opened at {_PROJECTS_URL}")
        print("[INFO] District and promoter are pre-selected. Please read the CAPTCHA "
              "shown there, type it into the 'Enter Captcha' box, and click Search.")
        try:
            _wait_for_human_submit(
                page, timeout_seconds,
                request_predicate=lambda req: req.method == "POST" and "btnSearch" in (req.post_data or ""),
                post_submit_marker=_SEL_CAPTCHA_INPUT,
            )
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "rows": [], "raw_text": "", "url": page.url, "note": str(e)}

        html = _safe_read(page.content, min_length=20000)
        body_text = _safe_read(lambda: page.inner_text("body"), min_length=2000)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))
        rows = _parse_results_table(html)
        note = "" if rows else (
            "No results table naming both 'Registration' and 'Project' rendered after submit -- "
            "either the search did not actually land, or it landed and returned zero rows. "
            "Check raw_text/screenshot to tell which."
        )
        return {"found": bool(rows), "rows": rows, "raw_text": body_text, "url": page.url, "note": note}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


def search_appeals(
    party_name: str,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at UP-REAT's judgement search and switches
    'Search By' to Free Text -- the only mode the 2026-08-26 audit found
    with a party-name field. Field ids confirmed live via a headless probe
    (no CAPTCHA involved, since that probe never submits): the AJAX call
    behind 'Free Text' injects `#text_name` (the party/free-text field) and
    `#answer` (the CAPTCHA), and the Search button is
    `<button onclick="fn_search_by_case_dfr()">`, which POSTs to
    `ajax_judgement.php` with `action=case_status_search` -- distinct from
    the CAPTCHA-refresh icon, which only ever GETs `captcha.php`.

    Returns {"found", "raw_text", "url", "note"}. Unlike the projects
    search, no known table shape exists yet to parse into `rows` -- that's
    the natural next step once a live read shows what the results actually
    look like.
    """
    p, browser, page = _launch(headless=False)
    try:
        page.goto(_APPEALS_URL, timeout=30000)
        page.wait_for_selector("#search_by", timeout=15000)
        page.select_option("#search_by", value="5")  # Free Text
        page.wait_for_selector("#text_name", timeout=10000)  # the AJAX-injected fields have landed

        filled = False
        try:
            page.fill("#text_name", party_name)
            filled = True
        except Exception:
            pass

        print(f"\n[INFO] A browser window has opened at {_APPEALS_URL}")
        print("[INFO] 'Search By' is set to Free Text.")
        if filled:
            print(f"[INFO] The party-name field was pre-filled with {party_name!r} -- please "
                  f"check it, read the CAPTCHA, and click Search.")
        else:
            print(f"[INFO] Could not auto-fill the party-name field -- please type "
                  f"{party_name!r} into it yourself, read the CAPTCHA, and click Search.")
        try:
            _wait_for_human_submit(
                page, timeout_seconds,
                request_predicate=lambda req: (
                    req.method == "POST" and "ajax_judgement.php" in req.url
                    and "case_status_search" in (req.post_data or "")
                ),
                post_submit_marker="#search_by",  # persists across the AJAX repaint either way
            )
        except (CaptchaTimeoutError, BrowserClosedError) as e:
            return {"found": False, "raw_text": "", "url": page.url, "note": str(e)}

        body_text = _safe_read(lambda: page.inner_text("body"), min_length=300)
        if screenshot_path:
            _safe_read(lambda: page.screenshot(path=screenshot_path, full_page=True))
        # CONFIRMED LIVE 2026-08-26: UP-REAT's own empty-result text is "No
        # Data Found", not "No record" -- the first live run reported
        # found=True against exactly this negative result.
        lowered = body_text.casefold()
        return {
            "found": "no data found" not in lowered and "no record" not in lowered,
            "raw_text": body_text, "url": page.url, "note": "",
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
    dump_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"_up_captcha_{cmd}_result.txt")
    if cmd == "projects" and len(sys.argv) >= 4:
        result = search_projects_by_promoter(sys.argv[2], sys.argv[3])
    elif cmd == "appeals" and len(sys.argv) >= 3:
        result = search_appeals(sys.argv[2])
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

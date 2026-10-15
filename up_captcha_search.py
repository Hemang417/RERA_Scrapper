"""
Claude-vision CAPTCHA autosolve for the two UP pages this pipeline never
posted to unsolved before 2026-10-15: UP-RERA's own project search
(View_projects.aspx -- states/adapter_uttarpradesh.py deliberately does not
define search_promoter_projects, see group_sweep._CANNOT_SEARCH["UP"]) and
UP-REAT's appeals judgement search (efilingreat.up.gov.in -- Partial in the
2026-08-26 coverage audit for exactly this reason).

SAME POLICY AS EVERY OTHER CAPTCHA-GATED MODULE IN THIS CODEBASE
(gst_portal.py / mahabhumi.py / session_auth.py / ts_rera_client.py): Claude
vision solves the CAPTCHA and this module submits it itself, no human-wait
fallback.

    python up_captcha_search.py projects "<promoter name>" "<District>"
    python up_captcha_search.py appeals "<party name>"

FIRST LIVE ATTEMPT (2026-08-26, back when this was still human-in-the-loop)
closed the browser on the human before they had touched the CAPTCHA. The
bug: "wait for the page to change" was judged by a body-text-length delta,
and this page's own promoter dropdown renders ~2,300 option strings into
inner_text -- trivially enough incidental reflow to trip a 200-character
threshold on its own. Fixed at the time by waiting for a real page
navigation instead; superseded 2026-10-15 by the autosolve loop below, which
doesn't need to distinguish "the human hasn't acted yet" from anything,
since nothing is waiting on a human any more.

CONFIRMED LIVE 2026-10-15 (projects page, by deliberately submitting a wrong
CAPTCHA once to observe it): a rejected CAPTCHA re-renders the SAME search
form with the text "Invalid Captcha!!!" and a freshly regenerated image
(this is a full synchronous WebForms POST, so the whole page -- including a
new CaptchaImage.axd guid -- reloads regardless of accept/reject; the guid
changing is NOT by itself a useful accept/reject signal here, unlike IGR's
or Mahabhumi's own AJAX partial postbacks). District/Promoter dropdown
selections were confirmed to SURVIVE both an accepted and a rejected submit
unchanged -- no field-reset bug on this portal.
"""

from __future__ import annotations

import sys
import time

from bs4 import BeautifulSoup

import captcha_solver
import config

_PROJECTS_URL = "https://www.up-rera.in/View_projects.aspx"
_APPEALS_URL = "https://efilingreat.up.gov.in/upreat/judgement.php"

_SEL_DISTRICT = "#ctl00_ContentPlaceHolder1_DdlprojectDistrict"
_SEL_PROMOTER = "#ctl00_ContentPlaceHolder1_ddl_prm"
_SEL_CAPTCHA_INPUT = "#ctl00_ContentPlaceHolder1_txtcap"
_SEL_SUBMIT_BTN = "#ctl00_ContentPlaceHolder1_btnSearch"
# No id on this <img> -- ASP.NET's CaptchaImage.axd handler control renders
# a bare <img src="CaptchaImage.axd?guid=...">, matched by src pattern.
_SEL_CAPTCHA_IMAGE = "img[src*='CaptchaImage']"

_SEL_APPEALS_CAPTCHA_INPUT = "#answer"
_SEL_APPEALS_SUBMIT_BTN = "button[onclick='fn_search_by_case_dfr()']"
_SEL_APPEALS_CAPTCHA_IMAGE = "img.captcha"

# Same cap as every other portal's Claude-vision autosolve loop.
_MAX_AUTOSOLVE_ATTEMPTS = 8
# How long one attempt gets to settle (full page reload on the projects
# page, AJAX repaint on appeals) before its result is read.
_AUTOSOLVE_SETTLE_SECONDS = 2.0


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


def _safe_read(read_fn, attempts=6, delay=1.0, min_length=0):
    """Retries `read_fn()` (e.g. page.content / page.inner_text) against
    Playwright's "Unable to retrieve content because the page is
    navigating and changing the content" -- a real transient race, not a
    coding error: clicking Search fires the real request, but the resulting
    navigation can still be in flight a moment later (confirmed live: the
    first successful CAPTCHA solve after the request-predicate fix hit
    exactly this, back when this read a human's own submit rather than its
    own).

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
    solves the CAPTCHA via Claude vision and clicks Search itself.

    District is mandatory per states/uttarpradesh.py's own notes -- this
    is a ONE-district search, not a statewide sweep. Confirming this works
    at all (a solved CAPTCHA really does return this promoter's OTHER
    projects) is the point; a real sweep would still need one CAPTCHA solve
    per district, which is why group_sweep does not attempt it.

    Returns {"found", "rows", "raw_text", "url", "note"}. `rows` is [] both
    when nothing rendered and when a results table rendered with zero
    entries -- `note` says which. Raises nothing: a CAPTCHA that's never
    accepted or a closed window both fold into {"found": False, ...}, same
    as the no-matching-promoter path above.
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
        print(f"[INFO] District and promoter are pre-selected. Solving the CAPTCHA via Claude "
              f"vision (up to {_MAX_AUTOSOLVE_ATTEMPTS} attempts)...")

        body_text = ""
        accepted = False
        loop_deadline = time.time() + timeout_seconds
        browser_closed_early = False
        for attempt in range(1, _MAX_AUTOSOLVE_ATTEMPTS + 1):
            if page.is_closed():
                browser_closed_early = True
                break
            if time.time() > loop_deadline:
                print(f"[INFO] Overall timeout_seconds={timeout_seconds}s budget exhausted before "
                      f"attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} -- stopping.")
                break
            try:
                backend_name, solved_text = captcha_solver.solve_up_rera_captcha(page, enabled=True)
                page.fill(_SEL_CAPTCHA_INPUT, solved_text)
                page.click(_SEL_SUBMIT_BTN)
            except Exception as e:
                print(f"[INFO] CAPTCHA autosolve attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} failed ({e}).")
                continue

            # CONFIRMED LIVE 2026-10-15 by deliberately submitting a wrong
            # CAPTCHA once: this is a full synchronous WebForms POST, so the
            # whole page reloads either way (including a freshly regenerated
            # CaptchaImage.axd guid regardless of accept/reject -- NOT a
            # useful signal here, unlike IGR's/Mahabhumi's own AJAX partial
            # postbacks). The one reliable signal is the exact text the
            # portal shows on a rejected submit: "Invalid Captcha!!!".
            try:
                page.wait_for_load_state("load", timeout=15000)
            except Exception:
                pass
            _settle(page, timeout_ms=8000)
            time.sleep(_AUTOSOLVE_SETTLE_SECONDS)
            body_text = _safe_read(lambda: page.inner_text("body"), min_length=2000)
            if "invalid captcha" in body_text.casefold():
                print(f"[INFO] {backend_name}'s solve ('{solved_text}') on attempt "
                      f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} was rejected ('Invalid Captcha!!!') -- "
                      f"trying again.")
                continue
            print(f"[OK] {backend_name}'s solve ('{solved_text}') was accepted on attempt "
                  f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS}.")
            accepted = True
            break

        if browser_closed_early:
            return {"found": False, "rows": [], "raw_text": "", "url": "",
                    "note": "Browser window was closed before the CAPTCHA was solved."}
        if not accepted:
            return {
                "found": False, "rows": [], "raw_text": "", "url": page.url,
                "note": f"CAPTCHA was not accepted after {_MAX_AUTOSOLVE_ATTEMPTS} Claude-vision attempts.",
            }

        html = _safe_read(page.content, min_length=20000)
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
    """Opens a VISIBLE browser at UP-REAT's judgement search, switches
    'Search By' to Free Text -- the only mode the 2026-08-26 audit found
    with a party-name field -- then solves the CAPTCHA via Claude vision and
    clicks Search itself. Field ids confirmed live via a headless probe (no
    CAPTCHA involved, since that probe never submits): the AJAX call behind
    'Free Text' injects `#text_name` (the party/free-text field) and
    `#answer` (the CAPTCHA), and the Search button is
    `<button onclick="fn_search_by_case_dfr()">`, which POSTs to
    `ajax_judgement.php` with `action=case_status_search` -- distinct from
    the CAPTCHA-refresh icon, which only ever GETs `captcha.php`.

    Returns {"found", "raw_text", "url", "note"}. Unlike the projects
    search, no known table shape exists yet to parse into `rows` -- that's
    the natural next step once a live read shows what the results actually
    look like. Raises nothing: a CAPTCHA that's never accepted or a closed
    window both fold into {"found": False, ...}, same as every other
    failure path here.
    """
    p, browser, page = _launch(headless=False)
    try:
        page.goto(_APPEALS_URL, timeout=30000)
        page.wait_for_selector("#search_by", timeout=15000)
        page.select_option("#search_by", value="5")  # Free Text
        page.wait_for_selector("#text_name", timeout=10000)  # the AJAX-injected fields have landed

        try:
            page.fill("#text_name", party_name)
        except Exception as e:
            return {"found": False, "raw_text": "", "url": page.url,
                    "note": f"Could not fill the party-name field: {e}"}

        print(f"\n[INFO] A browser window has opened at {_APPEALS_URL}")
        print(f"[INFO] 'Search By' is set to Free Text, party name pre-filled with {party_name!r}.")
        print(f"[INFO] Solving the CAPTCHA via Claude vision (up to {_MAX_AUTOSOLVE_ATTEMPTS} attempts)...")

        body_text = ""
        accepted = False
        loop_deadline = time.time() + timeout_seconds
        for attempt in range(1, _MAX_AUTOSOLVE_ATTEMPTS + 1):
            if page.is_closed():
                return {"found": False, "raw_text": "", "url": "",
                        "note": "Browser window was closed before the CAPTCHA was solved."}
            if time.time() > loop_deadline:
                print(f"[INFO] Overall timeout_seconds={timeout_seconds}s budget exhausted before "
                      f"attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} -- stopping.")
                break
            try:
                if page.input_value("#text_name") != party_name:
                    page.fill("#text_name", party_name)
                backend_name, solved_text = captcha_solver.solve_up_reat_captcha(page, enabled=True)
                page.fill(_SEL_APPEALS_CAPTCHA_INPUT, solved_text)
                page.click(_SEL_APPEALS_SUBMIT_BTN)
            except Exception as e:
                print(f"[INFO] CAPTCHA autosolve attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} failed ({e}).")
                continue

            # This is a pure AJAX repaint (no navigation) -- settle briefly,
            # then check for the exact rejection text confirmed live
            # 2026-10-15 by deliberately submitting a wrong CAPTCHA:
            # "Captch Value is incorrect/Empty, kindly try again" (the
            # typo is the site's own).
            time.sleep(_AUTOSOLVE_SETTLE_SECONDS)
            body_text = _safe_read(lambda: page.inner_text("body"), min_length=300)
            if "captch value is incorrect" in body_text.casefold():
                print(f"[INFO] {backend_name}'s solve ('{solved_text}') on attempt "
                      f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} was rejected -- trying again.")
                continue
            print(f"[OK] {backend_name}'s solve ('{solved_text}') was accepted on attempt "
                  f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS}.")
            accepted = True
            break

        if not accepted:
            return {
                "found": False, "raw_text": "", "url": page.url,
                "note": f"CAPTCHA was not accepted after {_MAX_AUTOSOLVE_ATTEMPTS} Claude-vision attempts.",
            }

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

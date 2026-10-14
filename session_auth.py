"""
Captures a MahaRERA guest access token by opening a real, visible browser on
the project's public detail page and waiting for a human to solve the
CAPTCHA gate shown there.

By default, nothing here solves the CAPTCHA itself -- a person must read and
type it in the browser window that pops up. This only automates the
plumbing around that: launching the browser, waiting for the site to mint
the token into sessionStorage once the CAPTCHA is passed, reading it out,
and caching it -- the same token a human would otherwise copy manually via
DevTools > Application > Session Storage.

Autosolve (CapSolver, see captcha_solver.solve_maharera_captcha) is an
explicit opt-in on top of that (autosolve=True, the default for this
function, or MAHARERA_CAPTCHA_AUTOSOLVE=1) -- same policy-reversal caveat as
gst_portal.py/captcha_solver.py: this automates a step every other
CAPTCHA-gated module here deliberately leaves to a human (see
guardrails.md's "HUMAN budget" framing). Autosolve ALWAYS falls back to the
proven human-wait loop below on any failure -- a wrong solve, a missing
CAPSOLVER_API_KEY, a selector miss -- so a human can always still finish the
job in the same already-open browser.
"""

import os
import time

import captcha_solver
import config
import token_cache


class CaptchaTimeoutError(Exception):
    pass


class BrowserClosedError(Exception):
    pass


_TOKEN_JS = """
() => {
    const raw = sessionStorage.getItem('tokens');
    if (!raw) return null;
    try {
        const parsed = JSON.parse(raw);
        return parsed.accessToken || null;
    } catch (e) {
        return null;
    }
}
"""

# CONFIRMED LIVE 2026-10-14 against maharerait.maharashtra.gov.in's project
# detail page (see captcha_solver.py's own confirmed-selector note for the
# CAPTCHA canvas itself): input has no id, only name="captcha"; submit is
# the only button with class "next"; refresh is <a class="cpt-btn">. A
# wrong submit pops a Bootstrap-style error modal ("Error!", class
# "alert-danger") whose own overlay intercepts pointer events on the rest
# of the page -- CONFIRMED LIVE 2026-10-14, this blocked the refresh-button
# click outright until the modal's own close button
# (aria-label="Close", class "btn-close") is clicked first.
_SEL_CAPTCHA_INPUT = 'input[name="captcha"]'
_SEL_CAPTCHA_SUBMIT_BTN = "button.next"
_SEL_CAPTCHA_REFRESH_BTN = "a.cpt-btn"
_SEL_ERROR_MODAL_CLOSE_BTN = "button.btn-close"
_MAX_AUTOSOLVE_ATTEMPTS = 4


def _read_token(page):
    try:
        return page.evaluate(_TOKEN_JS)
    except Exception as e:
        raise BrowserClosedError(f"Browser window closed or unreachable: {e}") from e


def _try_autosolve(page, autosolve: bool | None) -> str | None:
    """Attempts CapSolver autosolve up to _MAX_AUTOSOLVE_ATTEMPTS times,
    refreshing the CAPTCHA between wrong guesses -- same retry discipline as
    gst_portal.py's _solve_captcha_and_submit, adapted to this portal's much
    more reliable success signal: the real accessToken appearing in
    sessionStorage once the portal actually accepts the solve, rather than a
    fragile page-text match. Returns the token on success, or None if
    autosolve isn't enabled/configured or every attempt fails -- the caller
    then proceeds to the existing human-wait loop unchanged."""
    want_autosolve = autosolve if autosolve is not None else os.environ.get("MAHARERA_CAPTCHA_AUTOSOLVE") == "1"
    if not want_autosolve:
        return None

    for attempt in range(1, _MAX_AUTOSOLVE_ATTEMPTS + 1):
        try:
            backend_name, solved_text = captcha_solver.solve_maharera_captcha(page, enabled=True)
        except Exception as e:
            print(f"[INFO] CAPTCHA autosolve attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} failed ({e}).")
            break  # a non-solve error (missing key, selector miss, etc.) won't fix itself on retry

        try:
            # Real per-character keystrokes, not a raw value-set -- same
            # reasoning as gst_portal.py's own fix: a framework's form
            # validation can otherwise never register a programmatically
            # set value as "touched and valid".
            captcha_input = page.locator(_SEL_CAPTCHA_INPUT)
            captcha_input.click()
            captcha_input.fill("")
            captcha_input.press_sequentially(solved_text, delay=50)
            page.click(_SEL_CAPTCHA_SUBMIT_BTN, timeout=10000)
        except Exception as e:
            print(f"[INFO] CAPTCHA autosolve attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} failed ({e}).")
            break

        # Give the portal a few seconds to actually mint the token after a
        # correct submit before concluding this attempt failed.
        for _ in range(10):
            token = _read_token(page)
            if token:
                print(f"[INFO] {backend_name} solved the CAPTCHA ({solved_text!r}) on attempt "
                      f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} and the session was captured automatically.")
                return token
            page.wait_for_timeout(500)

        print(f"[INFO] {backend_name}'s solve ({solved_text!r}) was rejected by the portal "
              f"(attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS}).")
        if attempt < _MAX_AUTOSOLVE_ATTEMPTS:
            try:
                # The error modal's own overlay intercepts clicks on
                # everything behind it -- dismiss it first (best-effort: it
                # may not always appear) or the refresh click below hangs
                # until its own timeout for no reason.
                try:
                    page.click(_SEL_ERROR_MODAL_CLOSE_BTN, timeout=2000)
                except Exception:
                    pass
                page.click(_SEL_CAPTCHA_REFRESH_BTN, timeout=5000)
                page.wait_for_timeout(1000)  # let the new image actually render before re-screenshotting
            except Exception as e:
                print(f"[INFO] Could not refresh the CAPTCHA for another attempt ({e}).")
                break

    print(f"[INFO] CAPTCHA autosolve did not succeed within {_MAX_AUTOSOLVE_ATTEMPTS} attempt(s) "
          f"-- falling back to a human solve.")
    return None


def acquire_token_via_browser(
    project_id: str,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    poll_interval: float = config.CAPTCHA_POLL_INTERVAL_SECONDS,
    autosolve: bool | None = True,
) -> str:
    """Opens a visible browser on the project's public detail page, tries
    CapSolver autosolve (see _try_autosolve; pass autosolve=False to skip
    straight to the human wait), then waits for a human to solve the
    CAPTCHA gate shown there, then reads and caches the resulting guest
    accessToken.

    Raises CaptchaTimeoutError if nobody solves it within timeout_seconds, or
    BrowserClosedError if the window is closed early. Callers should treat
    both as "no token available this time" and fall back gracefully rather
    than aborting the whole run.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright is required. Run: pip install playwright && playwright install chromium"
        ) from e

    url = config.DETAIL_VIEW_URL_TEMPLATE.format(project_id)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False)
        except Exception:
            # Bundled Chromium can fail to spawn on a machine missing the
            # Windows runtime it needs (side-by-side config error) -- fall
            # back to the system-installed Chrome, which doesn't have that
            # dependency gap. Same fix as nclt_search.py/bombay_hc_search.py.
            browser = p.chromium.launch(channel="chrome", headless=False)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=config.SEARCH_TIMEOUT_MS)
            page.wait_for_timeout(1000)  # let the SPA bootstrap

            token = _read_token(page)
            if token:
                token_cache.save(token)
                return token

            token = _try_autosolve(page, autosolve)
            if token:
                token_cache.save(token)
                return token

            print(f"[INFO] A browser window has opened at {url}")
            print("[INFO] Please solve the CAPTCHA shown there and click Submit.")
            print(f"[INFO] Waiting up to {timeout_seconds}s for you to finish...")

            elapsed = 0.0
            last_status_at = 0.0
            while elapsed < timeout_seconds:
                token = _read_token(page)
                if token:
                    token_cache.save(token)
                    print("[OK] Session captured -- continuing automatically.")
                    return token

                time.sleep(poll_interval)
                elapsed += poll_interval
                if elapsed - last_status_at >= 30:
                    print(
                        f"[INFO] Still waiting for the CAPTCHA to be solved "
                        f"({int(elapsed)}s/{timeout_seconds}s)..."
                    )
                    last_status_at = elapsed

            raise CaptchaTimeoutError(
                f"No session captured within {timeout_seconds}s -- the CAPTCHA wasn't solved in time."
            )
        finally:
            try:
                browser.close()
            except Exception:
                pass

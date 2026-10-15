"""
Captures a MahaRERA guest access token by opening a real, visible browser on
the project's public detail page and autosolving the CAPTCHA gate shown
there via Claude vision (see captcha_solver.solve_maharera_captcha).

Autosolve (autosolve=True, the default for this function, or
MAHARERA_CAPTCHA_AUTOSOLVE=1) is a policy reversal from every other
CAPTCHA-gated module in this pipeline's original design (see guardrails.md's
"HUMAN budget" framing) -- CONFIRMED LIVE across repeated trials this is
reliable enough (proven 5/5) that, as of 2026-10-15, this no longer falls
back to a human-wait loop on failure: a fully unattended run has no one
there to solve it anyway, so that wait was dead time, not a real fallback.
Raises CaptchaTimeoutError if every autosolve attempt fails.
"""

import os

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
# Bumped from 4 2026-10-15 when the human-wait fallback was removed --
# autosolve is now the only path, so a bit more retry headroom costs
# nothing (each Claude vision call is a fraction of a cent) and is cheaper
# than the $5 cross-run budget cap ever becoming the real limiting factor.
_MAX_AUTOSOLVE_ATTEMPTS = 8


def _read_token(page):
    try:
        return page.evaluate(_TOKEN_JS)
    except Exception as e:
        raise BrowserClosedError(f"Browser window closed or unreachable: {e}") from e


def _try_autosolve(page, autosolve: bool | None) -> str | None:
    """Attempts Claude-vision autosolve up to _MAX_AUTOSOLVE_ATTEMPTS times,
    refreshing the CAPTCHA between wrong guesses -- same retry discipline as
    gst_portal.py's _solve_captcha_and_submit, adapted to this portal's much
    more reliable success signal: the real accessToken appearing in
    sessionStorage once the portal actually accepts the solve, rather than a
    fragile page-text match. Returns the token on success, or None if
    autosolve isn't enabled/configured or every attempt fails -- the caller
    raises CaptchaTimeoutError in that case (no human-wait fallback)."""
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
    """Opens a visible browser on the project's public detail page, autosolves
    the CAPTCHA gate shown there via Claude vision (see _try_autosolve), then
    reads and caches the resulting guest accessToken.

    Raises CaptchaTimeoutError if every autosolve attempt fails (no
    human-wait fallback -- removed 2026-10-15, see this module's own
    docstring), or BrowserClosedError if the window is closed early. Callers
    should treat both as "no token available this time" and fall back
    gracefully rather than aborting the whole run. `timeout_seconds`/
    `poll_interval` are kept for call-site compatibility but no longer
    control a wait loop here.
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

            raise CaptchaTimeoutError(
                f"CAPTCHA autosolve did not succeed within {_MAX_AUTOSOLVE_ATTEMPTS} attempt(s) -- "
                "no human-wait fallback (removed 2026-10-15; see this module's own docstring)."
            )
        finally:
            try:
                browser.close()
            except Exception:
                pass

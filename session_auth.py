"""
Captures a MahaRERA guest access token by opening a real, visible browser on
the project's public detail page and waiting for a human to solve the
CAPTCHA gate shown there.

Nothing here solves the CAPTCHA itself -- a person must read and type it in
the browser window that pops up. This only automates the plumbing around
that: launching the browser, waiting for the site to mint the token into
sessionStorage once the CAPTCHA is passed, reading it out, and caching it --
the same token a human would otherwise copy manually via DevTools >
Application > Session Storage.
"""

import time

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


def _read_token(page):
    try:
        return page.evaluate(_TOKEN_JS)
    except Exception as e:
        raise BrowserClosedError(f"Browser window closed or unreachable: {e}") from e


def acquire_token_via_browser(
    project_id: str,
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    poll_interval: float = config.CAPTCHA_POLL_INTERVAL_SECONDS,
) -> str:
    """Opens a visible browser on the project's public detail page, waits for
    a human to solve the CAPTCHA gate shown there, then reads and caches the
    resulting guest accessToken.

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
        browser = p.chromium.launch(headless=False)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=config.SEARCH_TIMEOUT_MS)
            page.wait_for_timeout(1000)  # let the SPA bootstrap

            token = _read_token(page)
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

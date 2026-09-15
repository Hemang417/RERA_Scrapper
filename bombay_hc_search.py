"""
Bombay High Court case status -- party-name search, via the national
eCourts High Courts Services portal (hcservices.ecourts.gov.in), CAPTCHA
-gated. Confirmed real and live-tested this pass (DOM structure, the
request the Go button actually fires, and the genuine wrong-CAPTCHA
response shape; see module note on `search_party` for what still hasn't
been live-confirmed).

Two things make this heavier than every other CAPTCHA-gated portal in this
codebase, confirmed live by direct DOM/network inspection -- NOT
guessed from documentation:
  1. The "Case Status : Search by Petitioner/Respondent" form requires a
     mandatory Registration Year (#rgyearP) alongside the Party Name --
     there is no open-ended "search every year" option. A thorough check
     needs one CAPTCHA solve PER YEAR, not one total.
  2. Bombay High Court itself is split into 7 separate benches on this
     portal (Appellate Side, Original Side, Aurangabad, Nagpur, Kolhapur,
     Goa, Special Court/TORTS) -- a promoter's litigation could be under
     any of them, and each is a SEPARATE search (separate court_complex_code
     selection, separate CAPTCHA).
This module only ever runs ONE (bench, year) search per call -- callers
(see company_charter._safe_bombay_hc_check) decide how many bench/year
combinations are worth the CAPTCHA cost and loop accordingly.

Mechanism, confirmed live: unlike NCLT (plain form POST, full reload) or
Maha Bhulekh (ASP.NET UpdatePanel partial postback), this portal's "Go"
button (onclick="funShowRecords('CSpartyName');") fires a jQuery AJAX POST
to hcservices/cases_qry/index_qry.php?action_code=showRecords and repaints
part of the page from the JSON response -- so this module captures that
response directly (page.expect_response), not the page's own HTML.
CONFIRMED LIVE: an incorrect CAPTCHA gets back exactly {"Error":"ERROR_VAL"}
(and the page separately shows "THERE IS AN ERROR") -- there is no
ambiguity here the way there was on NCLT's party-name search; the CAPTCHA
is genuinely server-validated. A CORRECT solve's response shape for an
actual hit has NOT been confirmed live yet (this pass never had a real
CAPTCHA solved) -- see search_party's own docstring.

The Highcourt/Bench selects (#sess_state_code / #court_complex_code) are
driven by their visible label text, not raw values -- confirmed live that
those values are composite/internal-coded strings (state_code~x,
code1@code2), and label-based selection is both more robust to internal
ID changes and matches how a human actually uses the page (same
"never guess an internal code" reasoning that keeps CTS office/village
picks label-based too).

SAME POLICY AS EVERY OTHER CAPTCHA-GATED MODULE HERE (gst_portal.py /
mahabhumi.py / up_captcha_search.py / nclt_search.py / session_auth.py):
nothing in this file reads or solves a CAPTCHA image. A real, VISIBLE
browser opens, a human reads it and types it in themselves, and this only
reads the response AFTER that human submits.

    python bombay_hc_search.py "<party name>" <year> [bench label,
        default "Original Side,Bombay"]
"""

from __future__ import annotations

import json
import sys
import time

import config

_MAIN_URL = "https://hcservices.ecourts.gov.in/hcservices/main.php"

_SEL_HIGHCOURT = "#sess_state_code"
_SEL_BENCH = "#court_complex_code"
_SEL_PARTY_NAME = "#petres_name"
_SEL_YEAR = "#rgyearP"
_SEL_CAPTCHA = "#captcha"
# funShowRecords('CSpartyName') is this exact form's own Go button --
# distinct from every other tab's Go/Search button on the same page.
_SEL_GO_BUTTON = "input[onclick=\"funShowRecords('CSpartyName');\"]"

# Every bench under Bombay High Court on this portal, confirmed live via
# the #court_complex_code option list. "Original Side,Bombay" is the
# commercial/company-litigation bench and the most likely home for a
# RERA promoter's civil litigation; "Appellate Side,Bombay" is the other
# Bombay-city bench. Aurangabad/Nagpur/Kolhapur/Goa cover the rest of
# Maharashtra + Goa and are not searched by default.
BENCHES = (
    "Original Side,Bombay",
    "Appellate Side,Bombay",
    "Bench at Aurangabad",
    "Bench at Nagpur",
    "Bombay High Court,Bench at Kolhapur",
    "High court of Bombay at Goa",
    "Special Court (TORTS) Bombay",
)


class CaptchaTimeoutError(Exception):
    pass


class BrowserClosedError(Exception):
    pass


class CaptchaRejectedError(Exception):
    """The AJAX response was the confirmed-live {"Error": "ERROR_VAL"}
    shape -- the CAPTCHA was read and rejected server-side, not a timeout
    and not a genuine (even if empty) result. Distinct from
    CaptchaTimeoutError so a caller can tell "never solved" from "solved
    wrong" -- both are still "this lookup did not complete", never a
    clean check, but the second means the human DID engage."""


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


def _open_party_name_form(page, bench: str):
    """Navigates to the Case Status tab, selects Bombay High Court + the
    given bench (by label, confirmed live to trigger the Party Name tab's
    fields/captcha to render), and returns once #petres_name exists.
    Raises ValueError if `bench` isn't one of BENCHES -- never guesses a
    close match, same reasoning as CTS office/village selection."""
    if bench not in BENCHES:
        raise ValueError(f"Unknown bench {bench!r} -- must be one of {BENCHES}.")

    page.goto(_MAIN_URL, timeout=30000)
    # "Case Status" sidebar entry -- confirmed live as the second icon,
    # matched by its own link text rather than a hardcoded coordinate.
    page.click("text=Case Status", timeout=15000)
    # A stray "Please Select Highcourt and Bench.." modal can already be
    # open at this point (confirmed live, harmless) -- dismiss it if so.
    try:
        page.click("#bs_alert button", timeout=2000)
    except Exception:
        pass
    page.wait_for_selector(_SEL_HIGHCOURT, timeout=15000)
    page.select_option(_SEL_HIGHCOURT, label="Bombay High Court")
    _settle(page)
    page.wait_for_selector(_SEL_BENCH, timeout=15000)
    page.select_option(_SEL_BENCH, label=bench)
    _settle(page)
    page.wait_for_selector(_SEL_PARTY_NAME, timeout=15000)


def search_party(
    party_name: str,
    year: str | int,
    bench: str = "Original Side,Bombay",
    status: str = "Both",
    timeout_seconds: int = config.CAPTCHA_TIMEOUT_SECONDS,
    screenshot_path: str | None = None,
) -> dict:
    """Opens a VISIBLE browser at eCourts' Case Status : Search by
    Petitioner/Respondent form for Bombay High Court, pre-fills `bench`,
    `party_name`, `year` (the mandatory Registration Year -- see module
    note) and `status` ("Pending"/"Disposed"/"Both"), then waits for a
    human to read the CAPTCHA and click Go.

    CONFIRMED LIVE this pass: a wrong CAPTCHA gets back exactly
    {"Error": "ERROR_VAL"} from the AJAX endpoint, raised here as
    CaptchaRejectedError so a caller never mistakes "captcha rejected" for
    "confirmed zero results". A CORRECT solve's response shape for an
    actual hit has NOT been confirmed live yet -- `raw_response` is always
    returned (parsed JSON if the body was valid JSON, else the raw text)
    so a human/caller can read a real hit directly until a live one
    confirms the shape to parse into structured rows, same discipline as
    nclt_search.search_party and up_captcha_search.search_appeals's own
    unconfirmed-shape caveats.

    Returns {"found", "rows", "raw_response", "url", "note"}. `rows` is
    always [] until that live confirmation exists."""
    status_radio = {"Pending": "#radP", "Disposed": "#radD", "Both": "#radB"}.get(status)
    if status_radio is None:
        return {
            "found": False, "rows": [], "raw_response": None, "url": "",
            "note": f"Unknown status {status!r} -- must be Pending, Disposed, or Both.",
        }

    p, browser, page = _launch(headless=False)
    try:
        _open_party_name_form(page, bench)

        page.fill(_SEL_PARTY_NAME, party_name)
        page.fill(_SEL_YEAR, str(year))
        page.check(status_radio)

        print(f"\n[INFO] A browser window has opened at {_MAIN_URL}")
        print(f"[INFO] Bombay High Court / {bench}, party name ({party_name!r}), year ({year}), "
              f"status ({status}) are pre-filled. Please read the CAPTCHA and click Go.")
        print(f"[INFO] Waiting up to {timeout_seconds}s for you to solve the CAPTCHA and click Go...")
        try:
            with page.expect_response(
                lambda resp: "index_qry.php" in resp.url and "action_code=showRecords" in resp.url,
                timeout=timeout_seconds * 1000,
            ) as resp_info:
                page.click(_SEL_GO_BUTTON, timeout=5000)
        except Exception as e:
            if page.is_closed():
                raise BrowserClosedError("Browser window was closed before the CAPTCHA was solved.") from e
            raise CaptchaTimeoutError(
                f"No search response within {timeout_seconds}s -- the CAPTCHA wasn't solved in time."
            ) from e

        response = resp_info.value
        body_text = _read_body_safely(response)
        if screenshot_path:
            try:
                page.screenshot(path=screenshot_path, full_page=True)
            except Exception:
                pass

        try:
            parsed = json.loads(body_text)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if isinstance(parsed, dict) and parsed.get("Error"):
            raise CaptchaRejectedError(
                f"The site rejected the CAPTCHA (response: {parsed!r}) -- solved wrong, or the "
                f"session expired. Not a confirmed result either way; re-run to try again."
            )

        note = (
            "No confirmed-live parser exists yet for a genuine hit's response shape (this pass never "
            "had a real CAPTCHA solved against a party actually on record) -- check raw_response "
            "directly. A response containing an explicit zero-results indicator would need a live hit "
            "to compare against before this can honestly report found=False as a confirmed clean check."
        )
        return {"found": False, "rows": [], "raw_response": parsed if parsed is not None else body_text,
                "url": page.url, "note": note}
    except (CaptchaTimeoutError, BrowserClosedError, CaptchaRejectedError) as e:
        return {"found": False, "rows": [], "raw_response": None, "url": page.url if not page.is_closed() else "",
                "note": str(e)}
    finally:
        try:
            browser.close()
        except Exception:
            pass
        p.stop()


def _read_body_safely(response, attempts: int = 4, delay: float = 0.5) -> str:
    """response.text() can race a still-in-flight body the same way a
    mid-navigation page read can elsewhere in this codebase (see
    up_captcha_search._safe_read's own docstring) -- retried defensively
    even though this hasn't been observed to fail live yet, since an AJAX
    response body is normally fully buffered by the time expect_response
    resolves."""
    last_exc = None
    for _ in range(attempts):
        try:
            return response.text()
        except Exception as e:  # noqa: BLE001
            last_exc = e
            time.sleep(delay)
    if last_exc is not None:
        raise last_exc
    return ""


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    _party_name = sys.argv[1]
    _year = sys.argv[2]
    _bench = sys.argv[3] if len(sys.argv) > 3 else "Original Side,Bombay"
    _result = search_party(_party_name, _year, _bench)
    print("\n--- RESULT ---")
    for _k, _v in _result.items():
        print(f"{_k}: {_v}")

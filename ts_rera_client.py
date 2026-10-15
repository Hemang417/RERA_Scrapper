"""
Telangana RERA (TS-RERA) project search + detail fetch.

Unlike MahaRERA (resolver.py / session_auth.py), TS-RERA gates the project
SEARCH itself behind a CAPTCHA -- rerait.telangana.gov.in/SearchList/Search
has no token-free lookup at all, not even by exact registration number.
There is also no equivalent of MahaRERA's 9 category API endpoints: a
project's entire public record (promoter org, partners, project info, bank
accounts, land, land-owner/investor promoters, building/unit detail) lives on
one server-rendered "PrintPreview" page reached from the search results'
"View" link.

CAPTCHA solving is Claude-vision-only (captcha_solver.solve_ts_rera_captcha),
same zero-human-intervention policy as every other portal in this codebase.
This automates the plumbing around that: launching the browser, pre-filling
the project name, solving the CAPTCHA and submitting itself, then (if one
candidate is chosen) opening its PrintPreview page and parsing the parts of
it that matter for a Company Charter.

Standalone use:
    python ts_rera_client.py "constella"
"""

import re
import sys
import time

import captcha_solver

TG_RERA_SEARCH_URL = "https://rerait.telangana.gov.in/SearchList/Search"

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_POLL_INTERVAL_SECONDS = 1.0

_SEL_PROJECT_INPUT = "input[placeholder='Project Name']"
_SEL_CAPTCHA_IMAGE = "#captchaImage"
_SEL_CAPTCHA_INPUT = "#Captcha"
_SEL_SUBMIT_BTN = "#btnSearch"
# Same cap as every other portal's Claude-vision autosolve loop.
_MAX_AUTOSOLVE_ATTEMPTS = 8
# How long a single attempt gets to show a results transition (either "No
# Records Found" or a populated table -- see _try_extract_results) before
# giving up on it and trying again. This is a traditional full-page POST
# (id="btnSearch" is type=submit, not an AJAX button), so a rejected CAPTCHA
# means the server simply re-renders the SAME search form -- nothing here
# ever transitions to "Search Result"/"No Records Found" text, which is
# exactly what _try_extract_results returning None (not [] or a list) across
# this whole window means: reuse it as the accept/reject signal directly
# rather than inventing a second one.
_AUTOSOLVE_ATTEMPT_TIMEOUT_SECONDS = 15.0


class TSReraTimeoutError(Exception):
    pass


class TSReraNotFoundError(Exception):
    pass


class TSReraBrowserClosedError(Exception):
    pass


def search_and_fetch(
    project_name: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    auto_open_single_match: bool = True,
) -> dict:
    """Opens a visible browser on TS-RERA's search page, pre-fills
    `project_name`, and waits for a human to solve the CAPTCHA and click
    Search. Returns:

        {
            "candidates": [{"project_name", "promoter_name", "last_modified", "view_href"}, ...],
            "detail": {...} | None,   # populated iff exactly one candidate and auto_open_single_match
        }

    Raises TSReraTimeoutError if the CAPTCHA isn't accepted within
    _MAX_AUTOSOLVE_ATTEMPTS or the overall `timeout_seconds` budget runs out
    first, TSReraNotFoundError if the search legitimately returns zero
    rows."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright is required. Run: pip install playwright && playwright install chromium"
        ) from e

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
            page.goto(TG_RERA_SEARCH_URL, wait_until="domcontentloaded", timeout=60000)
            try:
                page.fill("input[placeholder='Project Name']", project_name)
            except Exception:
                pass  # non-fatal -- human can type it themselves too

            print(f"[INFO] A browser window has opened at {TG_RERA_SEARCH_URL}")
            print(f"[INFO] Project Name has been pre-filled with '{project_name}' where possible.")
            print(f"[INFO] Solving the CAPTCHA via Claude vision (up to {_MAX_AUTOSOLVE_ATTEMPTS} attempts)...")

            candidates = None
            loop_deadline = time.time() + timeout_seconds
            for attempt in range(1, _MAX_AUTOSOLVE_ATTEMPTS + 1):
                if page.is_closed():
                    raise TSReraBrowserClosedError("Browser window was closed before the CAPTCHA was solved.")
                if time.time() > loop_deadline:
                    print(f"[INFO] Overall timeout_seconds={timeout_seconds}s budget exhausted "
                          f"before attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} -- stopping.")
                    break

                # Cheap insurance against the project-name field being reset
                # by the same full-page re-render a rejected CAPTCHA causes
                # -- not yet confirmed live to happen here, but costs
                # nothing to guard against given it's a known pattern on
                # every ASP.NET-family portal this codebase has automated so
                # far.
                try:
                    if page.input_value(_SEL_PROJECT_INPUT) != project_name:
                        page.fill(_SEL_PROJECT_INPUT, project_name)
                except Exception:
                    pass

                try:
                    backend_name, solved_text = captcha_solver.solve_ts_rera_captcha(page, enabled=True)
                    captcha_input = page.locator(_SEL_CAPTCHA_INPUT)
                    captcha_input.click()
                    captcha_input.fill("")
                    captcha_input.press_sequentially(solved_text, delay=50)
                    page.click(_SEL_SUBMIT_BTN, timeout=10000)
                except Exception as e:
                    print(f"[INFO] CAPTCHA autosolve attempt {attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} failed ({e}).")
                    continue

                # This is a traditional full-page POST (btnSearch is
                # type=submit) -- a rejected CAPTCHA just re-renders the SAME
                # search form with a fresh image (standard MVC validation-
                # failure behavior), which never produces "Search Result" or
                # "No Records Found" text. _try_extract_results returning
                # None across this whole window IS the reject signal; no
                # separate CAPTCHA-image-token check is needed (and none is
                # possible here -- see captcha_solver.py's own note that this
                # <img>'s src string never changes regardless of content).
                deadline = time.time() + _AUTOSOLVE_ATTEMPT_TIMEOUT_SECONDS
                while time.time() < deadline:
                    candidates = _try_extract_results(page)
                    if candidates is not None:
                        break
                    time.sleep(poll_interval)

                if candidates is not None:
                    print(f"[OK] {backend_name}'s solve ('{solved_text}') was accepted on attempt "
                          f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS}.")
                    break
                print(f"[INFO] {backend_name}'s solve ('{solved_text}') on attempt "
                      f"{attempt}/{_MAX_AUTOSOLVE_ATTEMPTS} was not accepted within "
                      f"{_AUTOSOLVE_ATTEMPT_TIMEOUT_SECONDS:.0f}s -- trying again.")

            if candidates is None:
                raise TSReraTimeoutError(
                    f"CAPTCHA was not accepted after {_MAX_AUTOSOLVE_ATTEMPTS} Claude-vision attempts."
                )
            if not candidates:
                raise TSReraNotFoundError(f"TS-RERA search for '{project_name}' returned zero results.")

            print(f"[OK] Found {len(candidates)} candidate(s).")
            for i, c in enumerate(candidates, start=1):
                print(f"  {i}. {c['project_name']} -- {c['promoter_name']} (modified {c['last_modified']})")

            detail = None
            if auto_open_single_match and len(candidates) == 1 and candidates[0]["view_href"]:
                print("[INFO] Exactly one match -- opening its PrintPreview record...")
                page.goto(candidates[0]["view_href"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(500)
                detail = _parse_print_preview(page)

            return {"candidates": candidates, "detail": detail}
        except Exception as e:
            if "Target page, context or browser has been closed" in str(e):
                raise TSReraBrowserClosedError(f"Browser window closed or unreachable: {e}") from e
            raise
        finally:
            try:
                browser.close()
            except Exception:
                pass


def fetch_detail_by_url(print_preview_url: str, timeout_seconds: int = 60) -> dict:
    """Opens a PrintPreview URL directly (reusing a still-valid session/link
    captured earlier, e.g. from a prior search_and_fetch candidate) and
    parses it. Only useful within the same short-lived session the URL's
    encrypted `q` token was issued for -- it is not a stable, reusable link."""
    from playwright.sync_api import sync_playwright

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
            page.goto(print_preview_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            page.wait_for_timeout(500)
            return _parse_print_preview(page)
        finally:
            try:
                browser.close()
            except Exception:
                pass


def _try_extract_results(page) -> list[dict] | None:
    """Returns None while the search hasn't been submitted yet (or the
    CAPTCHA is still showing), [] if the site explicitly says no records
    found, or a populated candidate list once results render."""
    try:
        body_text = page.inner_text("body")
    except Exception:
        return None

    if "No Records Found" in body_text and "Search Result" not in body_text:
        return []
    if "Search Result" not in body_text:
        return None

    rows = page.query_selector_all("table tr")
    candidates = []
    for row in rows:
        cells = row.query_selector_all("td")
        if len(cells) < 5:
            continue
        try:
            project_name = cells[1].inner_text().strip()
            promoter_name = cells[2].inner_text().strip()
            last_modified = cells[4].inner_text().strip()
        except Exception:
            continue
        if not project_name or project_name.lower() == "project name":
            continue
        view_href = None
        view_link = cells[3].query_selector("a")
        if view_link:
            href = view_link.get_attribute("href")
            if href:
                view_href = href if href.startswith("http") else f"https://rerait.telangana.gov.in{href}"
        candidates.append(
            {
                "project_name": project_name,
                "promoter_name": promoter_name,
                "last_modified": last_modified,
                "view_href": view_href,
            }
        )
    return candidates if candidates else None


# --- PrintPreview parsing -----------------------------------------------
#
# The PrintPreview page is one long server-rendered "as submitted" promoter
# application, not a stable JSON API -- there is no public TS-RERA equivalent
# of MahaRERA's category endpoints. Per-unit building/floor tables run to
# hundreds of near-identical rows for a large project and add little Charter
# value, so they are deliberately NOT parsed row-by-row here; only the
# declared summary figures (total units, approved built-up area) are kept.
# `raw_text` is always preserved so nothing is silently lost.

_FIELD_PATTERNS = {
    "promoter_org_name": r"Promoter Information - Organization\nName\n(.+)",
    "promoter_pan": r"PAN Number\n(.+)",
    "organization_type": r"Organization Type\n(.+)",
    "gstin": r"GST Number\n(.+)",
    "authority_name": r"Authority Name\n(.+)",
    "plan_approval_number": r"Plan Approval Number\n(.+)",
    "project_name": r"Project Name\n(.+)",
    "project_status": r"Project Status\n(.+)",
    "approved_date": r"Approved Date\n(.+)",
    "proposed_date_of_completion": r"Proposed Date of Completion\n(.+)",
    "litigations_related_to_project": r"Litigations related to the project \?\n(.+)",
    "project_type": r"Project Type\n(.+)",
    "total_area_sqmt": r"Total Area\(In sqmts\)\n(.+)",
    "net_area_sqmt": r"Net Area\(In sqmts\)\n(.+)",
    "total_building_units_approved_plan": r"Total Building Units \(as per approved plan\)\n(.+)",
    "approved_built_up_area_sqmt": r"Approved Built up Area \(In Sqmts\)\n(.+)",
    "mortgage_area_sqmt": r"Mortgage Area \(In Sqmts\)\n(.+)",
    "collection_bank_name": r"Collection Account of the Project \(100%\)\nBank Name\n(.+)",
    "collection_ifsc": r"Collection Account of the Project \(100%\)[\s\S]{0,120}?IFSC Code\n(.+)",
}


def _parse_print_preview(page) -> dict:
    raw_text = page.inner_text("body")

    parsed = {"raw_text": raw_text}
    for key, pattern in _FIELD_PATTERNS.items():
        m = re.search(pattern, raw_text)
        parsed[key] = m.group(1).strip() if m else None

    parsed["members"] = _parse_member_table(page)
    parsed["land_owner_investor_promoters"] = _parse_land_owner_table(page)
    parsed["has_zero_progress_signal"] = bool(
        re.search(r"Percentage of Work\n(?:.|\n){0,400}?\t0\n", raw_text)
    )
    return parsed


def _parse_member_table(page) -> list:
    members = []
    try:
        header = page.query_selector("text=Member Name")
        if not header:
            return members
        table = header.evaluate_handle("el => el.closest('table')")
        table = table.as_element()
        if not table:
            return members
        for row in table.query_selector_all("tr")[1:]:
            cells = [c.inner_text().strip() for c in row.query_selector_all("td")]
            if len(cells) >= 2 and cells[0]:
                members.append({"name": cells[0], "designation": cells[1]})
    except Exception:
        pass
    return members


def _parse_land_owner_table(page) -> list:
    owners = []
    try:
        header = page.query_selector("text=Promoter\\(Land Owner\\/ Investor\\) Type")
        if not header:
            return owners
        table = header.evaluate_handle("el => el.closest('table')")
        table = table.as_element()
        if not table:
            return owners
        for row in table.query_selector_all("tr")[1:]:
            cells = [c.inner_text().strip() for c in row.query_selector_all("td")]
            if len(cells) >= 4 and cells[0]:
                owners.append(
                    {
                        "project_name": cells[0],
                        "promoter_name": cells[1],
                        "type": cells[2],
                        "agreement_type": cells[3],
                    }
                )
    except Exception:
        pass
    return owners


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ts_rera_client.py <project name>")
        sys.exit(1)
    result = search_and_fetch(sys.argv[1])
    import json

    print(json.dumps(result, indent=2, ensure_ascii=False))

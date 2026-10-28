"""
CTS -> DCPR-2034 zoning/reservation lookup via BMC's own DP Remarks 2034
portal (dpremarks.mcgm.gov.in/dp2034) -- Brihanmumbai Municipal Corporation's
official Development Plan GIS viewer. Confirmed real and live-tested this
pass (not assumed from documentation alone): HTTPS resolves fine despite the
earlier MahaRERA-style SSL issues seen on other maharashtra.gov.in hosts.

ONLY covers plots inside BMC's own municipal limits -- the 24 BMC wards
(A, B, C, ... R/C). It does NOT cover: other Maharashtra municipal
corporations (each runs its own separate DP portal, not this one), or MMR
areas outside any municipal corporation (those fall under MMRDA's Regional
Plan, which -- confirmed live this pass -- has no plot-level query tool at
all, just policy/history pages; that case stays a manual/human lookup, same
as CTS/GR today).

Mechanism, confirmed by driving the real page and inspecting its DOM/JS:
  - It's a Dojo (dijit) + Esri JS API webapp, NOT ASP.NET postbacks like
    mahabhumi.py's site. Ward -> Village/Division -> CTS/CS/FP are three
    cascading dijit ComboBox autocomplete textboxes (#SelectWard1,
    #SelectVillage, #SelectCTS), each backed by a client-side
    dojo.data.ItemFileReadStore (the full ward list, and each ward's village
    list, are already loaded into the page -- confirmed no XHR fires on
    typing; filtering happens entirely in the browser).
  - NO CAPTCHA anywhere in this flow -- fully drivable headless, unlike
    mahabhumi.py's CTS/Property-Card search (CAPTCHA-gated at the final
    step) or the IGR/GST portals.
  - Each ComboBox's filtering is wired to the input's native `input`/`keyup`
    events, not a framework-specific API -- confirmed live that dispatching
    real DOM events (not Playwright's higher-level fill(), which doesn't
    always trigger dijit's own listener) reliably opens the suggestion
    list. Selecting the exact CTS number zooms the map to that parcel and
    opens a "Plot Info" popup (div#noc) containing the fields this lookup
    reports: Ward Name, Village/Division, CTS/CS/FP No., Reservation(s),
    Designation(s), DP Road(s), SM Sheet, EP Sheet -- plus a second
    "NOC Requirement" sub-tab (Highway/Coast/Flamingo ESZ/Heritage Buffer/
    Airport authority flags), captured here too since it's free once the
    popup is open and is directly relevant to CRZ/heritage/airport-funnel
    red flags a promoter's own filing would never volunteer.

Same discipline as mahabhumi.py and igr_maharashtra_search.py: ward/village
labels are NEVER fuzzy-matched or guessed -- list_wards()/list_villages()
return the site's own exact labels for a caller (or a human) to choose from
explicitly; search_cts_candidates()/fetch_plot_info() only accept an exact
CTS number already confirmed against the site's own candidate list for that
village (a CTS number can have multiple sub-divisions, e.g. "525/1A/1" vs
"525/1A2" vs "525A" -- picking the wrong one misattributes the zoning read
to the wrong parcel entirely).
"""

import re

import config

_BASE_URL = "https://dpremarks.mcgm.gov.in/dp2034/"

_SEL_WARD = "#SelectWard1"
_SEL_VILLAGE = "#SelectVillage"
_SEL_CTS = "#SelectCTS"
_POPUP = "#noc"

_FIRE_TYPE_JS = """
(args) => {
  const [id, text] = args;
  const el = document.getElementById(id);
  el.focus();
  el.value = text;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: text.slice(-1)}));
}
"""


class AmbiguousSelectionError(Exception):
    """Raised instead of guessing when a caller-supplied ward/village/CTS
    label doesn't exactly match one of the site's own autocomplete options."""

    def __init__(self, hint: str, options: list):
        self.hint = hint
        self.options = options
        super().__init__(
            f"{hint!r} is not an exact match against the site's own options ({options}). "
            f"Pass one of these verbatim -- never guess a ward/village/CTS number."
        )


class BrowserClosedError(Exception):
    pass


def _launch(headless: bool = True):
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
        # Same bundled-Chromium-missing-runtime fallback as mahabhumi.py /
        # nclt_search.py / bombay_hc_search.py.
        browser = p.chromium.launch(channel="chrome", headless=headless)
    page = browser.new_context(viewport={"width": 1024, "height": 768}).new_page()
    return p, browser, page


def _real_option_selector(field_id: str) -> str:
    # Dojo mints the popup as <field_id>_popup, and the real, selectable
    # choices inside it as <field_id>_popup0, _popup1, ... -- confirmed live
    # that the SAME popup also always contains two permanently-present but
    # hidden [role=option] rows ("Previous choices" / "More choices",
    # ids *_popup_prev / *_popup_next) for paging a long list. Waiting on
    # the bare [role='option'] selector resolves to whichever matches
    # first in DOM order, which is one of those two hidden rows, so a
    # visibility wait on it times out even once real suggestions are
    # showing. Matching only the numbered ids avoids that trap entirely.
    return f"[id^='{field_id}_popup'][id*='popup0'], [id^='{field_id}_popup'][id*='popup1'], [id^='{field_id}_popup'][id*='popup2']"


def _autocomplete_options(page, field_id: str) -> list:
    """Reads whatever real suggestion rows the dijit ComboBox is currently
    showing for `field_id` (see _real_option_selector for why the plain
    [role='option'] selector can't be used directly). Deliberately uses
    page.evaluate rather than a Playwright locator here: the bare
    `[id^=...]` prefix also matches the popup's own outer container
    (id exactly "<field_id>_popup", holding ALL rows' text concatenated
    together), and `:not()` can't itself exclude "ends with a digit" --
    filtering in JS on a numeric suffix is simpler than chaining regex-free
    CSS selectors for it."""
    return page.evaluate(
        """(fieldId) => {
            const re = new RegExp('^' + fieldId + '_popup[0-9]+$');
            return Array.from(document.querySelectorAll(`[id^="${fieldId}_popup"]`))
                .filter(el => re.test(el.id))
                .map(el => el.textContent.trim())
                .filter(Boolean);
        }""",
        field_id,
    )


def _fire_type_and_wait(page, selector: str, text: str, timeout_ms: int = 8000) -> list:
    field_id = selector.lstrip("#")
    page.evaluate(_FIRE_TYPE_JS, [field_id, text])
    page.wait_for_selector(_real_option_selector(field_id), state="visible", timeout=timeout_ms)
    return _autocomplete_options(page, field_id)


def _select_autocomplete_option(page, label: str) -> None:
    page.get_by_text(label, exact=True).first.click()
    # Confirmed live: clicking a suggestion closes this dropdown and primes
    # the NEXT field's cascading list asynchronously -- firing that next
    # field's input/keyup events immediately (no settle) sometimes lands
    # before the click's own selection handler finishes, leaving the next
    # dropdown empty and the visibility wait timing out.
    page.wait_for_timeout(500)


def _open_search_panel(page) -> None:
    """The three CTS/CS fields (#SelectWard1/#SelectVillage/#SelectCTS) sit
    inside a "Search" flyout panel that starts hidden -- confirmed live that
    #SelectWard1 is present in the DOM from page load but not visible (and
    so not interactable) until the top-nav "Search" link is clicked."""
    page.click("#search")
    page.wait_for_selector(_SEL_WARD, state="visible", timeout=10000)


def list_wards() -> dict:
    """Live-fetches BMC's 24 ward labels (A, B, C, ... R/C) -- headless, no
    CAPTCHA. Returns {"found": True, "wards": [label, ...]} or
    {"found": False, "note": "..."}."""
    p = browser = None
    try:
        p, browser, page = _launch(headless=True)
        page.goto(_BASE_URL, wait_until="load", timeout=config.SEARCH_TIMEOUT_MS)
        _open_search_panel(page)
        options = _fire_type_and_wait(page, _SEL_WARD, "")
        return {"found": True, "wards": options}
    except Exception as e:
        return {"found": False, "note": f"BMC DP Remarks ward lookup could not run this pass ({type(e).__name__})."}
    finally:
        if browser:
            browser.close()
        if p:
            p.stop()


def list_villages(ward_label: str) -> dict:
    """Live-fetches the village/division labels under one BMC ward --
    headless, no CAPTCHA. `ward_label` must be one of list_wards()'s own
    exact strings. Returns {"found": True, "villages": [label, ...]} or
    {"found": False, "note": "..."}."""
    p = browser = None
    try:
        p, browser, page = _launch(headless=True)
        page.goto(_BASE_URL, wait_until="load", timeout=config.SEARCH_TIMEOUT_MS)
        _open_search_panel(page)

        ward_options = _fire_type_and_wait(page, _SEL_WARD, ward_label)
        if ward_label not in ward_options:
            raise AmbiguousSelectionError(ward_label, ward_options)
        _select_autocomplete_option(page, ward_label)

        village_options = _fire_type_and_wait(page, _SEL_VILLAGE, "")
        return {"found": True, "villages": village_options}
    except AmbiguousSelectionError as e:
        return {"found": False, "note": str(e), "options": e.options}
    except Exception as e:
        return {"found": False, "note": f"BMC DP Remarks village lookup could not run this pass ({type(e).__name__})."}
    finally:
        if browser:
            browser.close()
        if p:
            p.stop()


def search_cts_candidates(ward_label: str, village_label: str, cts_query: str) -> dict:
    """Resolves a typed CTS/CS/FP number fragment against the site's own
    candidates for one ward+village (headless, no CAPTCHA) -- a CTS number
    can have multiple sub-divisions (e.g. "525/1A/1", "525/1A2", "525A"),
    and the caller must pick the exact one before calling fetch_plot_info().
    Returns {"found": True, "candidates": [label, ...]} or
    {"found": False, "note": "..."}."""
    p = browser = None
    try:
        p, browser, page = _launch(headless=True)
        page.goto(_BASE_URL, wait_until="load", timeout=config.SEARCH_TIMEOUT_MS)
        _open_search_panel(page)

        ward_options = _fire_type_and_wait(page, _SEL_WARD, ward_label)
        if ward_label not in ward_options:
            raise AmbiguousSelectionError(ward_label, ward_options)
        _select_autocomplete_option(page, ward_label)

        village_options = _fire_type_and_wait(page, _SEL_VILLAGE, village_label)
        if village_label not in village_options:
            raise AmbiguousSelectionError(village_label, village_options)
        _select_autocomplete_option(page, village_label)

        cts_options = _fire_type_and_wait(page, _SEL_CTS, cts_query)
        return {"found": True, "candidates": cts_options}
    except AmbiguousSelectionError as e:
        return {"found": False, "note": str(e), "options": e.options}
    except Exception as e:
        return {"found": False, "note": f"BMC DP Remarks CTS candidate search could not run this pass ({type(e).__name__})."}
    finally:
        if browser:
            browser.close()
        if p:
            p.stop()


_FIELD_PATTERN = re.compile(
    r"(Ward Name|Village/Division|CTS/CS/FP No\.|Reservation\(s\)|Designation\(s\)|DP Road\(s\)|SM Sheet|EP Sheet)-\s*([^\n]*)"
)
_NOC_PATTERN = re.compile(r"(Highway|Coast|Flamingo ESZ|Heritage Buffer|Airport):\s*([^\n]*)")


def _parse_popup_text(raw_text: str) -> dict:
    fields = {label: value.strip() for label, value in _FIELD_PATTERN.findall(raw_text)}
    noc_flags = {label: value.strip() for label, value in _NOC_PATTERN.findall(raw_text)}
    return {"fields": fields, "noc_requirement_flags": noc_flags}


def fetch_plot_info(ward_label: str, village_label: str, cts_number: str) -> dict:
    """Fetches BMC's own "Plot General Information" for an EXACT CTS/CS/FP
    number already confirmed via search_cts_candidates() -- headless, no
    CAPTCHA, no human-wait needed (unlike mahabhumi.fetch_property_card).

    Returns on success:
      {"found": True,
       "fields": {"Ward Name": ..., "Village/Division": ..., "CTS/CS/FP No.": ...,
                  "Reservation(s)": ..., "Designation(s)": ..., "DP Road(s)": ...,
                  "SM Sheet": ..., "EP Sheet": ...},
       "noc_requirement_flags": {"Highway": ..., "Coast": ..., "Flamingo ESZ": ...,
                                  "Heritage Buffer": ..., "Airport": ...},
       "url": _BASE_URL}
    or {"found": False, "note": "..."} if the ward/village/CTS doesn't match
    exactly, or the portal couldn't be reached this pass.

    Does NOT itself decide whether a promoter's claimed FSI/zone is
    consistent with what this returns -- that comparison belongs to the
    caller (company_charter.run_dcpr_zoning_check), same separation of
    concerns as run_cts_land_lookup vs mahabhumi.fetch_property_card."""
    p = browser = None
    try:
        p, browser, page = _launch(headless=True)
        page.goto(_BASE_URL, wait_until="load", timeout=config.SEARCH_TIMEOUT_MS)
        _open_search_panel(page)

        ward_options = _fire_type_and_wait(page, _SEL_WARD, ward_label)
        if ward_label not in ward_options:
            raise AmbiguousSelectionError(ward_label, ward_options)
        _select_autocomplete_option(page, ward_label)

        village_options = _fire_type_and_wait(page, _SEL_VILLAGE, village_label)
        if village_label not in village_options:
            raise AmbiguousSelectionError(village_label, village_options)
        _select_autocomplete_option(page, village_label)

        cts_options = _fire_type_and_wait(page, _SEL_CTS, cts_number)
        if cts_number not in cts_options:
            raise AmbiguousSelectionError(cts_number, cts_options)
        _select_autocomplete_option(page, cts_number)

        page.wait_for_selector(_POPUP, timeout=15000)
        page.wait_for_function(
            "(sel) => { const el = document.querySelector(sel); return el && el.textContent.includes('Plot General Information'); }",
            arg=_POPUP,
            timeout=15000,
        )
        raw_text = page.locator(_POPUP).inner_text()
        parsed = _parse_popup_text(raw_text)
        if not parsed["fields"]:
            return {"found": False, "note": "Plot Info popup opened but no recognizable fields were parsed out of it -- the page layout may have changed."}

        return {"found": True, **parsed, "url": _BASE_URL}
    except AmbiguousSelectionError as e:
        return {"found": False, "note": str(e), "options": e.options}
    except Exception as e:
        return {"found": False, "note": f"BMC DP Remarks plot-info lookup could not run this pass ({type(e).__name__})."}
    finally:
        if browser:
            browser.close()
        if p:
            p.stop()


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("wards", help="List BMC's 24 ward labels")

    p_villages = sub.add_parser("villages", help="List villages/divisions under a ward")
    p_villages.add_argument("ward", help="Exact ward label from `wards`, e.g. K/E")

    p_cts = sub.add_parser("cts", help="Resolve a CTS number fragment to exact candidates")
    p_cts.add_argument("ward")
    p_cts.add_argument("village", help="Exact village label from `villages`")
    p_cts.add_argument("cts_query", help="e.g. 525")

    p_plot = sub.add_parser("plot", help="Fetch Plot General Information for an exact CTS number")
    p_plot.add_argument("ward")
    p_plot.add_argument("village")
    p_plot.add_argument("cts_number", help="Exact candidate from `cts`, e.g. 525/1A2")

    args = parser.parse_args()

    if args.command == "wards":
        result = list_wards()
    elif args.command == "villages":
        result = list_villages(args.ward)
    elif args.command == "cts":
        result = search_cts_candidates(args.ward, args.village, args.cts_query)
    else:
        result = fetch_plot_info(args.ward, args.village, args.cts_number)

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("found") else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
Resolves MahaRERA registration numbers / project names to the portal's
internal numeric project id(s), using the fully public "Search Project" box
on https://maharera.maharashtra.gov.in -- no login, no account, no CAPTCHA.

Registration-number search reliably returns exactly one match. Free-text
project-name search can return many -- search_projects() returns every match
found on the results page; resolve_project_id() (kept for the exact reg-no
callers) is a thin wrapper that picks the single result.

Every match is read off its "View Details" link/href WITHOUT ever clicking
it: clicking triggers a "you are about to leave this site" JS confirm()
dialog and then loads the actual project detail page, which is gated by a
real, homegrown CAPTCHA (see session_auth.py) -- reading the href attribute
directly avoids all of that for this id-resolution step.
"""

import re
from dataclasses import dataclass, field

import config


class ProjectNotFoundError(Exception):
    pass


@dataclass
class ProjectCandidate:
    project_id: str
    detail_url: str
    reg_no: str | None = None
    project_name: str | None = None
    promoter_name: str | None = None
    district: str | None = None
    pincode: str | None = None
    last_modified: str | None = None
    raw_text: str = field(default="", repr=False)


_REG_NO_RE = re.compile(r"P\d{11}")
_PINCODE_RE = re.compile(r"\b\d{6}\b")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

# Walks up from a "View Details" link looking for the ancestor element whose
# text contains the card's registration number -- that's the whole result
# card. Bounded to 8 levels so it can't runaway to the full page body.
_CARD_ANCESTOR_WALK_JS = """
el => {
    let n = el;
    for (let i = 0; i < 8; i++) {
        if (!n.parentElement) break;
        n = n.parentElement;
        if (/P\\d{11}/.test(n.innerText)) return n.innerText;
    }
    return el.closest('div')?.innerText || el.innerText || '';
}
"""


def _parse_candidate_text(raw_text: str) -> dict:
    """Best-effort field extraction from a result card's innerText. Field
    labels/layout weren't independently re-verified beyond what was observed
    live, so every field is optional -- raw_text is always kept so nothing is
    silently lost if a specific pattern misses."""
    fields_ = {"raw_text": raw_text}

    reg_match = _REG_NO_RE.search(raw_text)
    if reg_match:
        fields_["reg_no"] = reg_match.group(0)

    pincode_match = _PINCODE_RE.search(raw_text)
    if pincode_match:
        fields_["pincode"] = pincode_match.group(0)

    date_match = _DATE_RE.search(raw_text)
    if date_match:
        fields_["last_modified"] = date_match.group(0)

    district_match = re.search(r"District\s*\n\s*([^\n]+)", raw_text)
    if district_match:
        fields_["district"] = district_match.group(1).strip()

    # Observed layout: "# <reg_no>" line, then project name, then promoter
    # name on the next two lines.
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    reg_line_idx = next((i for i, ln in enumerate(lines) if _REG_NO_RE.search(ln)), None)
    if reg_line_idx is not None:
        if reg_line_idx + 1 < len(lines):
            fields_["project_name"] = lines[reg_line_idx + 1]
        if reg_line_idx + 2 < len(lines):
            fields_["promoter_name"] = lines[reg_line_idx + 2]

    return fields_


def _run_search(page, query: str, tab_selector: str | None, input_selector: str, submit_selector: str) -> None:
    page.goto(config.SEARCH_URL, wait_until="domcontentloaded", timeout=config.SEARCH_TIMEOUT_MS)
    if tab_selector:
        page.click(tab_selector)
        page.wait_for_timeout(300)  # let the Bootstrap tab panel become visible/interactive
    page.fill(input_selector, query)
    page.click(submit_selector)
    page.wait_for_load_state("domcontentloaded", timeout=config.SEARCH_TIMEOUT_MS)


def _search(
    query: str,
    headless: bool,
    tab_selector: str | None,
    input_selector: str,
    submit_selector: str,
) -> list[ProjectCandidate]:
    """Runs a search against one of MahaRERA's search tabs (Projects or
    Promoters -- both render result cards in the same format) and returns
    every match found on the results page. Empty list if nothing matched."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "playwright is required. Run: pip install playwright && playwright install chromium"
        ) from e

    candidates: list[ProjectCandidate] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_context(viewport={"width": 1920, "height": 1080}).new_page()
        try:
            _run_search(page, query, tab_selector, input_selector, submit_selector)

            links = page.locator(config.VIEW_DETAILS_LINK_SELECTOR)
            try:
                links.first.wait_for(state="attached", timeout=config.SEARCH_TIMEOUT_MS)
            except Exception:
                return []

            for i in range(links.count()):
                link = links.nth(i)
                href = link.get_attribute("href")
                if not href:
                    continue
                match = re.search(r"/public/project/view/(\d+)", href)
                if not match:
                    continue

                try:
                    raw_text = link.evaluate(_CARD_ANCESTOR_WALK_JS)
                except Exception:
                    raw_text = ""

                parsed = _parse_candidate_text(raw_text) if raw_text else {"raw_text": ""}
                candidates.append(
                    ProjectCandidate(
                        project_id=match.group(1),
                        detail_url=href,
                        reg_no=parsed.get("reg_no"),
                        project_name=parsed.get("project_name"),
                        promoter_name=parsed.get("promoter_name"),
                        district=parsed.get("district"),
                        pincode=parsed.get("pincode"),
                        last_modified=parsed.get("last_modified"),
                        raw_text=parsed.get("raw_text", ""),
                    )
                )
        finally:
            browser.close()

    # De-duplicate by project_id -- defensive, in case a card ever renders
    # more than one element matching the "View Details" text selector.
    seen = set()
    unique = []
    for c in candidates:
        if c.project_id in seen:
            continue
        seen.add(c.project_id)
        unique.append(c)
    return unique


def search_projects(query: str, headless: bool = True) -> list[ProjectCandidate]:
    """Searches MahaRERA's public "Search Project" box for `query` (a
    registration number or free-text project name) and returns every match
    found on the results page."""
    return _search(query, headless, None, config.SEARCH_INPUT_SELECTOR, config.SEARCH_SUBMIT_SELECTOR)


def search_promoters(query: str, headless: bool = True) -> list[ProjectCandidate]:
    """Searches MahaRERA's public "Promoters" tab for `query` (a promoter
    name) and returns every project registered under a matching promoter --
    i.e. that promoter's full RERA-registered project portfolio. Same result
    shape as search_projects(); confirmed live that this tab renders result
    cards identically to the Projects tab."""
    return _search(
        query,
        headless,
        config.PROMOTERS_TAB_SELECTOR,
        config.PROMOTER_NAME_INPUT_SELECTOR,
        config.PROMOTER_SEARCH_SUBMIT_SELECTOR,
    )


def resolve_project_id(reg_no: str, headless: bool = True) -> tuple[str, str]:
    """Returns (project_id, public_detail_url) for an exact registration
    number. Thin wrapper around search_projects() -- an exact reg no should
    match exactly one project."""
    candidates = search_projects(reg_no, headless=headless)
    if not candidates:
        raise ProjectNotFoundError(
            f"No 'View Details' result for registration number '{reg_no}'. "
            f"Double-check it's correct."
        )
    return candidates[0].project_id, candidates[0].detail_url

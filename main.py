"""
CLI entrypoint.

    python main.py P51800012345
    python main.py "Kalpataru Height"
    python main.py P51800012345 --verify

No login or CAPTCHA is required for the search step or for the 'projects'/
'complaints' categories. The other 7 categories need a short-lived guest
session that MahaRERA gates behind a CAPTCHA on the project detail page --
by default this script opens a real, visible browser and waits for you to
solve it there; --no-auto-auth skips that and sticks to the 2 free
categories (or pass --token to supply a session you already captured
yourself).

After the scrape, this always runs the agentic deep-research pass (see
deep_research.py) to populate the Market Research and Promoter External
Profile sections, then always generates a Company Charter docx (see
company_charter.py) into output/company_charters/ -- no flag to disable
either. Requires ANTHROPIC_API_KEY to be set; a failure in either is never
fatal to the run, it just leaves that piece unpopulated (retry standalone
with `python deep_research.py <REG_NO>` / `python company_charter.py
<REG_NO>`).
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

import requests

import api_client
import company_charter
import config
import deep_research
import discover
import promoter_portfolio as promoter_portfolio_mod
import report
import resolver
import run_archive
import session_auth
import token_cache


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch a MahaRERA project's full details into a PDF report.")
    parser.add_argument(
        "query",
        help="MahaRERA project registration number (e.g. P51800012345) or a free-text project name.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Sanity-check each configured category endpoint against a real project instead of building a PDF.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help=(
            "Show the browser window during project search (useful for debugging). "
            "The CAPTCHA session browser is always shown regardless of this flag -- "
            "see --no-auto-auth to disable it entirely."
        ),
    )
    parser.add_argument(
        "--token",
        default=None,
        help=(
            "Optional guest access token to use instead of an auto-captured or cached one. "
            "Copy it yourself from your own browser: open the project's public page on "
            "maharerait.maharashtra.gov.in, DevTools > Application > Session Storage > "
            "'tokens' > accessToken. Valid for roughly 100 minutes."
        ),
    )
    parser.add_argument(
        "--no-auto-auth",
        action="store_true",
        help=(
            "Don't automatically open a browser to solve the CAPTCHA session. Only 'projects' "
            "and 'complaints' (which need no token at all) will be fetched, unless --token is given."
        ),
    )
    parser.add_argument(
        "--captcha-timeout",
        type=int,
        default=config.CAPTCHA_TIMEOUT_SECONDS,
        help=f"Seconds to wait for the CAPTCHA to be solved before giving up (default: {config.CAPTCHA_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--output-dir",
        default=config.OUTPUT_ROOT,
        help=f"Root output directory (default: {config.OUTPUT_ROOT})",
    )
    return parser.parse_args()


def _describe(c: "resolver.ProjectCandidate") -> str:
    bits = [c.reg_no or "(unknown reg no)", "--", c.project_name or "(unknown name)"]
    extra = " ".join(x for x in (c.promoter_name, c.district, c.pincode) if x)
    if extra:
        bits.append(f"[{extra}]")
    return " ".join(bits)


def _resolve(query: str, headed: bool) -> tuple[str, str, str]:
    """Returns (project_id, detail_url, reg_no_for_output_dir)."""
    is_reg_no = bool(re.match(r"^P\d{11}$", query, re.IGNORECASE))

    if is_reg_no:
        print(f"[INFO] Resolving project ID for {query} via public search (no login)...")
        try:
            project_id, detail_url = resolver.resolve_project_id(query, headless=not headed)
        except resolver.ProjectNotFoundError as e:
            print(f"[ERROR] {e}")
            sys.exit(1)
        print(f"[OK] Resolved to internal project ID: {project_id} ({detail_url})")
        return project_id, detail_url, query

    print(f"[INFO] Searching MahaRERA for project name '{query}' (no login)...")
    candidates = resolver.search_projects(query, headless=not headed)

    if not candidates:
        print(f"[ERROR] No projects found matching '{query}'. Double-check the spelling.")
        sys.exit(1)

    if len(candidates) == 1:
        chosen = candidates[0]
    else:
        print(f"\n[INFO] Found {len(candidates)} projects matching '{query}':\n")
        for i, c in enumerate(candidates, start=1):
            print(f"  {i}. {_describe(c)}")
        print()

        if not sys.stdin.isatty():
            print(
                "[ERROR] Multiple matches and no interactive terminal to pick one -- "
                "re-run with the exact registration number shown above."
            )
            sys.exit(2)

        chosen = None
        while chosen is None:
            try:
                choice = input(f"Enter a number (1-{len(candidates)}): ").strip()
            except EOFError:
                print(
                    "\n[ERROR] No input available to pick one -- "
                    "re-run with the exact registration number shown above."
                )
                sys.exit(2)
            if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                chosen = candidates[int(choice) - 1]
            else:
                print("Not a valid choice, try again.")

    reg_no = chosen.reg_no or chosen.project_id
    print(f"[OK] Selected: {_describe(chosen)} (internal project ID: {chosen.project_id})")
    return chosen.project_id, chosen.detail_url, reg_no


_AUTH_SOURCE_LABELS = {
    "explicit": "manually supplied --token",
    "cached": "reused cached session",
    "fresh_browser": "freshly solved CAPTCHA session",
    "none": "no session (only free categories)",
}


def ensure_token(
    project_id: str, explicit_token: str | None, no_auto_auth: bool, captcha_timeout: int
) -> tuple[str | None, str]:
    """Returns (token, auth_source) where auth_source is one of
    'explicit', 'cached', 'fresh_browser', or 'none'."""
    if explicit_token:
        return explicit_token, "explicit"

    if no_auto_auth:
        return None, "none"

    cached = token_cache.load_valid()
    if cached:
        print(f"[OK] Using cached session ({token_cache.minutes_left()} min left) -- no browser needed.")
        return cached, "cached"

    print("[INFO] No fresh session cached -- opening a browser for you to solve the CAPTCHA...")
    try:
        token = session_auth.acquire_token_via_browser(project_id, timeout_seconds=captcha_timeout)
        return token, "fresh_browser"
    except (session_auth.CaptchaTimeoutError, session_auth.BrowserClosedError) as e:
        print(f"[WARN] Couldn't capture a session ({e}). Continuing with only the free categories.")
        return None, "none"


def _extract_promoter_name(category_data: dict) -> str | None:
    """projects.promoterName is null in practice on every sample seen -- the
    real promoter name lives on partners.promoterDetails.promoterName
    (confirmed live), with stray trailing whitespace to strip."""
    partners = category_data.get("partners")
    if isinstance(partners, dict):
        details = partners.get("promoterDetails")
        if isinstance(details, dict) and details.get("promoterName"):
            return details["promoterName"].strip()
    projects_data = category_data.get("projects")  # defensive fallback only
    if isinstance(projects_data, dict) and projects_data.get("promoterName"):
        return projects_data["promoterName"].strip()
    return None


def main() -> int:
    args = parse_args()
    query = args.query.strip()

    project_id, detail_url, reg_no = _resolve(query, headed=args.headed)

    token, auth_source = ensure_token(project_id, args.token, args.no_auto_auth, args.captcha_timeout)
    print(f"[INFO] Auth source: {_AUTH_SOURCE_LABELS[auth_source]}")

    project_out_dir = os.path.join(args.output_dir, reg_no)
    raw_dir = os.path.join(project_out_dir, "raw")
    documents_dir = os.path.join(project_out_dir, "documents")

    if args.verify:
        os.makedirs(project_out_dir, exist_ok=True)
        print("\n[INFO] Verifying category endpoints...")
        discover.verify_endpoints(project_id, raw_dir, token)
        return 0

    # Snapshot whatever a previous run left behind before this run starts
    # overwriting it, so two runs of the same project can be diffed instead
    # of the second silently clobbering the first. Must read prior_research
    # BEFORE archiving moves research/deep_research.json out from under
    # project_out_dir.
    prior_research = run_archive.load_prior_research(reg_no, args.output_dir)
    archive_dir = run_archive.archive_previous_run(reg_no, args.output_dir)
    prior_manifest = run_archive.load_prior_manifest(archive_dir)
    prior_documents_dir = run_archive.prior_documents_dir(archive_dir)
    prior_complaint_orders_manifest = run_archive.load_prior_complaint_orders_manifest(archive_dir)
    prior_complaint_orders_dir = run_archive.prior_complaint_orders_dir(archive_dir)
    if archive_dir:
        print(f"[INFO] Archived previous run to {archive_dir}")

    os.makedirs(project_out_dir, exist_ok=True)

    print("\n[INFO] Fetching all categories...")
    errors = {}
    category_data = api_client.fetch_all_categories(project_id, raw_dir, token, errors_out=errors)

    gated_failed = [
        cat
        for cat, err in errors.items()
        if cat not in config.NO_AUTH_CATEGORIES and err.status_code in (401, 403)
    ]
    retried = False
    if gated_failed and auth_source != "none":
        print(
            f"\n[WARN] {len(gated_failed)} categor(ies) came back unauthorized despite having a "
            f"session -- refreshing it and retrying: {', '.join(gated_failed)}"
        )
        token_cache.invalidate()
        try:
            token = session_auth.acquire_token_via_browser(project_id, timeout_seconds=args.captcha_timeout)
            auth_source = "fresh_browser"
            retried = True
            retry_data = api_client.fetch_all_categories(project_id, raw_dir, token, categories=gated_failed)
            category_data.update(retry_data)
        except (session_auth.CaptchaTimeoutError, session_auth.BrowserClosedError) as e:
            print(f"[WARN] Retry session capture failed ({e}) -- leaving those categories as failed.")

    print("\n[INFO] Downloading documents...")
    documents_manifest = api_client.download_documents(
        category_data.get("documents"),
        documents_dir,
        token,
        project_id,
        prior_manifest=prior_manifest,
        prior_documents_dir=prior_documents_dir,
    )
    downloaded = sum(1 for d in documents_manifest if d["status"] == "downloaded")
    reused = sum(1 for d in documents_manifest if d["status"] == "reused")
    print(
        f"[OK] {downloaded + reused}/{len(documents_manifest)} document(s) available "
        f"({downloaded} downloaded, {reused} reused from the previous run)."
    )

    print("\n[INFO] Downloading complaint order PDFs...")
    complaint_orders_dir = os.path.join(project_out_dir, "complaint_orders")
    try:
        complaint_orders_manifest = api_client.download_complaint_orders(
            category_data.get("complaints"),
            complaint_orders_dir,
            token,
            project_id,
            prior_manifest=prior_complaint_orders_manifest,
            prior_documents_dir=prior_complaint_orders_dir,
        )
        co_downloaded = sum(1 for d in complaint_orders_manifest if d["status"] == "downloaded")
        co_reused = sum(1 for d in complaint_orders_manifest if d["status"] == "reused")
        print(
            f"[OK] {co_downloaded + co_reused}/{len(complaint_orders_manifest)} complaint order(s) available "
            f"({co_downloaded} downloaded, {co_reused} reused from the previous run)."
        )
    except Exception as e:
        # New, still-narrow feature -- a DMS outage or an unexpected
        # complaints.json shape here must not take down an otherwise-good run.
        print(f"[WARN] Complaint order download failed ({e}) -- continuing without it.")
        complaint_orders_manifest = []

    with open(os.path.join(project_out_dir, "complaint_orders_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(complaint_orders_manifest, f, indent=2, ensure_ascii=False)

    promoter_name = _extract_promoter_name(category_data)
    portfolio = None
    if promoter_name:
        print(f"\n[INFO] Building promoter portfolio for '{promoter_name}'...")
        try:
            with requests.Session() as portfolio_session:
                portfolio = promoter_portfolio_mod.build_promoter_portfolio(
                    promoter_name, portfolio_session, token, headless=not args.headed
                )
            promoter_dir = os.path.join(project_out_dir, "promoter")
            os.makedirs(promoter_dir, exist_ok=True)
            with open(os.path.join(promoter_dir, "portfolio.json"), "w", encoding="utf-8") as f:
                json.dump(portfolio, f, indent=2, ensure_ascii=False)
            t = portfolio["totals"]
            print(
                f"[OK] Promoter portfolio: {t['total_projects']} project(s), "
                f"{t['total_complaints']} complaint(s), {t['total_appeals']} appeal(s)."
            )
        except Exception as e:
            # Broad on purpose: this opens its own Playwright browser and
            # must never be fatal to the main run.
            print(f"[WARN] Promoter portfolio build failed ({e}) -- continuing without it.")
    else:
        print("\n[WARN] Could not determine promoter name -- skipping promoter portfolio.")

    print("\n[INFO] Running agentic deep research (market + promoter profile)...")
    try:
        research_data = deep_research.run_deep_research(
            reg_no, category_data, args.output_dir, prior_research=prior_research
        )
        gap_count = sum(len(research_data.get(key, {}).get("gaps", [])) for key in deep_research.RESEARCH_KEYS)
        reused_note = " (reused prior confirmed sources -- only open gaps re-attempted)" if research_data.get("_reused_prior") else ""
        print(f"[OK] Deep research complete{reused_note} ({gap_count} unresolved gap(s) across all sections).")
    except Exception as e:
        # Never fatal: a missing ANTHROPIC_API_KEY, rate limit, or network
        # hiccup here must not take down an otherwise-successful RERA scrape.
        print(f"[WARN] Deep research failed ({e}) -- continuing without it.")
        research_data = None

    # No automated review-fetching mechanism exists in this pipeline (see
    # company_charter.run_review_authenticity_triage's own module note) --
    # this only picks up reviews a user has separately collected and saved
    # here, e.g. via the Browser pane against a review site.
    reviews_path = os.path.join(project_out_dir, "reviews.json")
    reviews = None
    if os.path.exists(reviews_path):
        with open(reviews_path, "r", encoding="utf-8") as f:
            reviews = json.load(f)
        print(f"[INFO] Found {reviews_path} -- running review-authenticity triage on {len(reviews)} review(s).")

    print("\n[INFO] Generating Company Charter...")
    charter_facts = None
    try:
        charter_path, charter_facts = company_charter.run_company_charter(
            reg_no, category_data, documents_manifest, documents_dir, research_data, args.output_dir,
            complaint_orders_manifest=complaint_orders_manifest, complaint_orders_dir=complaint_orders_dir,
            reviews=reviews,
        )
        print(f"[OK] Company Charter written to {charter_path}")
    except Exception as e:
        # Same policy as deep research: a missing API key, template mismatch,
        # or a corrupt/unreadable document must not take down the scrape.
        print(f"[WARN] Company Charter generation failed ({e}) -- continuing without it.")
        charter_path = None

    # Persisted so finalize_report.py can rebuild the PDF later with zero
    # network calls (these were previously only ever held in memory).
    with open(os.path.join(project_out_dir, "documents_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(documents_manifest, f, indent=2, ensure_ascii=False)
    with open(os.path.join(project_out_dir, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "reg_no": reg_no,
                "project_id": project_id,
                "auth_source": auth_source,
                "promoter_name": promoter_name,
                "company_charter_path": charter_path,
                "generated_at": datetime.now().isoformat(),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    pdf_path = os.path.join(project_out_dir, f"{reg_no}_summary.pdf")
    print(f"\n[INFO] Building PDF report -> {pdf_path}")
    report.build_pdf(
        reg_no,
        project_id,
        category_data,
        documents_manifest,
        pdf_path,
        auth_source=auth_source,
        promoter_portfolio=portfolio,
        research_data=research_data,
        charter_facts=charter_facts,
    )

    failed = [cat for cat, data in category_data.items() if data is None]
    unconfirmed = [cat for cat in config.CATEGORY_ORDER if config.CATEGORY_ENDPOINTS[cat]["status"] != "confirmed"]

    print("\n" + "=" * 60)
    print("  Run summary")
    print("=" * 60)
    print(f"  auth source:         {_AUTH_SOURCE_LABELS[auth_source]}{' (after retry)' if retried else ''}")
    for cat in config.CATEGORY_ORDER:
        data = category_data.get(cat)
        if cat == "documents":
            count = f"{downloaded} downloaded, {reused} reused / {len(documents_manifest)} found"
        elif data is None:
            count = "FAILED"
        elif isinstance(data, list):
            count = f"{len(data)} record(s)"
        elif isinstance(data, dict):
            count = "1 record" if data else "empty"
        else:
            count = "empty"
        print(f"  {cat:<20} {count}")

    print(f"  {'promoter_profile':<20} {'built (' + str(portfolio['totals']['total_projects']) + ' project(s))' if portfolio else 'not available'}")
    print(f"  {'market_research':<20} {'populated' if research_data else 'FAILED this run -- see warning above, or retry with: python deep_research.py ' + reg_no}")
    print(f"  {'company_charter':<20} {'written (' + charter_path + ')' if charter_path else 'FAILED this run -- see warning above, or retry with: python company_charter.py ' + reg_no}")

    if failed:
        print(f"\n[WARN] {len(failed)} categor(ies) failed to fetch: {', '.join(failed)}")
    if unconfirmed:
        print(f"[WARN] {len(unconfirmed)} categor(ies) use observed-but-not-individually-verified "
              f"endpoints: {', '.join(unconfirmed)}")
        print("        Run with --verify to sanity-check them.")

    print(f"\nPDF report: {pdf_path}")
    print(f"Documents:  {documents_dir}")
    print(f"Raw JSON:   {raw_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

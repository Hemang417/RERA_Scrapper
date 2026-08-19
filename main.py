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
import time
from datetime import datetime

import requests

import api_client
import company_charter
import config
import deep_research
import discover
import gst_intake
import promoter_portfolio as promoter_portfolio_mod
import report
import resolver
import run_archive
import session_auth
import states
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
    parser.add_argument(
        "--state",
        default=None,
        choices=sorted(states.PROFILES),
        help=(
            "Which state RERA to query. Inferred from the registration-number format "
            "when omitted. Maharashtra and Telangana share the same P+11-digit shape, "
            "so that case is settled by an OBSERVED district-code convention and "
            "announced -- pass --state explicitly to override it."
        ),
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help=(
            "Skip the public search-box resolution step entirely and use this internal "
            "numeric project ID directly -- for when MahaRERA's own search page returns "
            "'No Records Found' for a registration number that demonstrably still exists "
            "(confirmed live: the direct getProjectGeneralDetailsByProjectId API can still "
            "return full data for a project the search box can't currently find -- a site-"
            "side search-index gap, not a sign the project itself is gone). Only meaningful "
            "when `query` is a registration number, since that's the only case this script "
            "would otherwise need to resolve via search."
        ),
    )
    gst = parser.add_mutually_exclusive_group()
    gst.add_argument(
        "--gstin",
        default=None,
        help=(
            "This promoter's GSTIN (e.g. 27AANCP0234D1ZO). Runs the GST filing-history "
            "intake as part of the pipeline: every GSTIN registered under the same PAN is "
            "discovered, each one's filing table is fetched, and the result feeds the "
            "Company Charter's GST Compliance score. Opens a visible browser and needs a "
            "human to solve a CAPTCHA -- once for the PAN search, then once per GSTIN "
            "found. Omit to skip GST entirely, exactly as today."
        ),
    )
    gst.add_argument(
        "--pan",
        default=None,
        help=(
            "This promoter's PAN (e.g. AANCP0234D). Same GST intake as --gstin, starting "
            "from the PAN instead. MahaRERA's own document set always includes a PAN card "
            "and never a GSTIN, so this is usually the one you have."
        ),
    )
    return parser.parse_args()


# _describe / _resolve / ensure_token / _extract_promoter_name moved to
# states/adapter_maharashtra.py -- they were MahaRERA portal logic, not
# orchestration. _AUTH_SOURCE_LABELS moved with them and is re-exported
# here for the run summary below.
from states.adapter_maharashtra import _AUTH_SOURCE_LABELS  # noqa: E402


def _run_gst_intake_step(gst_identifier: str | None, reg_no: str, output_dir: str) -> str:
    """Runs the opt-in GST filing-history intake and returns a one-line status
    for the run summary.

    NEVER raises. The intake opens its own browser, depends on a live
    third-party portal, and needs a human to solve a CAPTCHA -- any of which
    can fail on a run that is otherwise completely fine. A GST failure costs
    the Charter one unscored sub-metric (GST Compliance); it must never cost
    the whole scrape. Same policy as the promoter portfolio and deep research
    steps either side of it."""
    if not gst_identifier:
        print("\n[INFO] No --gstin/--pan supplied -- skipping GST filing-history intake.")
        return "not requested (pass --gstin or --pan to enable)"

    print(f"\n[INFO] Running GST filing-history intake for {gst_identifier}...")
    try:
        result = gst_intake.run_intake(gst_identifier, reg_no, output_dir)
    except Exception as e:
        # Broad on purpose -- see this function's own docstring.
        print(f"[WARN] GST intake failed ({e}) -- continuing without GST filing data.")
        return f"FAILED this run ({e})"

    print(
        f"[OK] GST intake: {result['period_count']} scoreable filing period(s) for "
        f"{result['primary_gstin']}."
    )
    return f"{result['period_count']} period(s) for {result['primary_gstin']}"


class CliReporter:
    """ProgressReporter for the command line.

    The adapter cannot print directly and cannot call input(): app.py drives
    the same acquire() through a Streamlit reporter, which re-runs
    top-to-bottom and cannot block on stdin."""

    def info(self, msg: str) -> None:
        print(f"[INFO] {msg}")

    def warn(self, msg: str) -> None:
        print(f"[WARN] {msg}")

    def ok(self, msg: str) -> None:
        print(f"[OK] {msg}")

    def choose(self, prompt: str, options: list) -> int | None:
        """Index chosen, or None for "cannot ask".

        None on a non-TTY preserves main()'s previous behaviour exactly: it
        returned exit code 2 rather than hanging on a blocked read or
        silently picking the first match."""
        if not sys.stdin.isatty():
            return None
        print(f"\n{prompt}")
        for i, option in enumerate(options, start=1):
            print(f"  {i}. {option}")
        while True:
            raw = input(f"Enter 1-{len(options)} (or blank to abort): ").strip()
            if not raw:
                return None
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw) - 1
            print("  Not a valid choice.")


def main() -> int:
    # Full pipeline start -- fed into company_charter.run_company_charter so
    # the Charter's own version-log line reports true end-to-end run time
    # (scrape + CAPTCHA wait + deep research + charter generation), not just
    # the charter-build step in isolation.
    pipeline_start_time = time.time()
    args = parse_args()
    query = args.query.strip()

    # Which state's RERA this is. --state wins; otherwise inferred from the
    # registration-number format, with the Maharashtra/Telangana collision
    # settled by an announced, documented tiebreak (states.resolve_state).
    reporter = CliReporter()
    candidates, candidates_note = states.candidate_profiles(query, args.state)
    if candidates_note:
        reporter.info(candidates_note)
    profile = candidates[0]

    def _archive_previous_run(reg_no: str) -> dict:
        """Called by the adapter once the project resolves and before it
        writes anything. Archiving is keyed on reg_no and is entirely
        state-neutral, so it stays here rather than inside an adapter.

        Must read prior_research BEFORE archiving moves
        research/deep_research.json out from under project_out_dir."""
        nonlocal prior_research
        prior_research = run_archive.load_prior_research(reg_no, args.output_dir)
        archive_dir = run_archive.archive_previous_run(reg_no, args.output_dir)
        if archive_dir:
            print(f"[INFO] Archived previous run to {archive_dir}")
        return {
            "manifest": run_archive.load_prior_manifest(archive_dir),
            "documents_dir": run_archive.prior_documents_dir(archive_dir),
            "complaint_orders_manifest": run_archive.load_prior_complaint_orders_manifest(archive_dir),
            "complaint_orders_dir": run_archive.prior_complaint_orders_dir(archive_dir),
        }

    prior_research = None
    ctx = states.AcquisitionContext(
        output_dir=args.output_dir,
        reporter=reporter,
        headed=args.headed,
        explicit_token=args.token,
        no_auto_auth=args.no_auto_auth,
        captcha_timeout=args.captcha_timeout,
        project_id_override=args.project_id,
        on_resolved=_archive_previous_run,
    )

    if args.verify:
        # Developer diagnostic, and category-API-only -- it checks that the
        # endpoint table still matches what the portal actually serves.
        if not any(c.can(states.CAP_CATEGORY_API) for c in candidates):
            print(f"[ERROR] --verify needs per-category endpoints, which {profile.rera_acronym} does not expose.")
            return 2
        # Same probe ladder as the main path, but stopping at resolve+auth:
        # --verify only needs a real project on that authority to check its
        # endpoint table against, not a full scrape.
        verify_adapter = None
        for candidate in candidates:
            if not candidate.can(states.CAP_CATEGORY_API) or not candidate.can(states.CAP_LOOKUP_BY_REG_NO):
                continue
            try:
                verify_adapter = states.get_adapter(candidate.code)
            except NotImplementedError:
                continue
            try:
                project_id, _detail_url, reg_no, token, _auth = verify_adapter.resolve_and_auth(query, ctx)
            except states.StateAcquisitionError:
                verify_adapter = None
                continue
            profile = candidate
            break
        if verify_adapter is None:
            print(f"[ERROR] '{query}' was not found on any authority with verifiable endpoints.")
            return 2
        print(f"[INFO] State: {profile.state_name} ({profile.rera_acronym})")
        os.makedirs(os.path.join(args.output_dir, reg_no), exist_ok=True)
        print("[INFO] Verifying category endpoints...")
        verify_adapter.verify_endpoints(project_id, os.path.join(args.output_dir, reg_no, "raw"), token)
        return 0

    # Cleared before any Claude API call this run might make -- this process
    # could otherwise carry token counts over from an unrelated earlier
    # project (matters more for app.py's long-lived Streamlit process than
    # this one-shot CLI, but scoping it identically in both keeps
    # write_usage_log's report accurate to just THIS run).
    deep_research.reset_usage_log()

    # ASK THE PORTALS instead of guessing which state this is.
    #
    # Most registration formats identify their authority outright, so
    # `candidates` is one entry and this loop runs once -- the ordinary
    # resolve, unchanged. Where two authorities share a format (MahaRERA and
    # TG-RERA both issue P + 11 digits), each is tried in turn and the first
    # one that actually HAS the project wins.
    #
    # This costs nothing in the common case: resolving the project on a
    # portal is work acquire() does anyway, so a successful probe IS the
    # resolve. Only a miss costs an extra lookup, and a miss is exactly the
    # case where guessing would have produced a confident wrong answer
    # against the wrong authority.
    acquired = None
    attempted = []
    for candidate in candidates:
        if len(candidates) > 1 and not candidate.can(states.CAP_LOOKUP_BY_REG_NO):
            # Some authorities cannot be searched by registration number at
            # all -- TG-RERA's public record does not even display one. Skip
            # rather than pretend, and say so if nothing else matches.
            attempted.append((candidate, "cannot be searched by registration number"))
            continue
        try:
            adapter = states.get_adapter(candidate.code)
        except NotImplementedError as e:
            attempted.append((candidate, "no acquisition adapter yet"))
            if len(candidates) == 1:
                print(f"[ERROR] {e}")
                return 1
            continue

        if len(candidates) > 1:
            reporter.info(f"Looking for {query} on {candidate.rera_acronym}...")
        try:
            acquired = adapter.acquire(query, ctx)
        except states.StateAcquisitionError as e:
            attempted.append((candidate, str(e)))
            continue
        profile = candidate
        if len(candidates) > 1:
            # A fact now, not a guess -- the authority's own search found it.
            reporter.ok(f"Confirmed on {profile.rera_acronym}: {query} is a {profile.state_name} project.")
        break

    if acquired is None:
        print(f"[ERROR] '{query}' could not be found on any authority whose registration format it matches.")
        for candidate, why in attempted:
            print(f"          - {candidate.rera_acronym}: {why}")
        unsearchable = [c for c, _ in attempted if not c.can(states.CAP_LOOKUP_BY_REG_NO)]
        if unsearchable:
            names = ", ".join(c.rera_acronym for c in unsearchable)
            print(
                f"        {names} cannot be looked up by registration number -- its public "
                f"record does not expose one. Re-run with the PROJECT NAME instead, and "
                f"--state to pick the authority."
            )
        return 1

    print(f"[INFO] State: {profile.state_name} ({profile.rera_acronym})")

    reg_no = acquired.reg_no
    project_id = acquired.project_id
    token = None  # every token-using stage now lives behind the adapter
    auth_source = acquired.auth_source
    category_data = acquired.category_data
    documents_manifest = acquired.documents_manifest
    complaint_orders_manifest = acquired.complaint_orders_manifest
    promoter_name = acquired.promoter_name
    portfolio = acquired.promoter_portfolio

    project_out_dir = os.path.join(args.output_dir, reg_no)
    raw_dir = os.path.join(project_out_dir, "raw")
    documents_dir = acquired.documents_dir or os.path.join(project_out_dir, "documents")
    complaint_orders_dir = acquired.complaint_orders_dir or os.path.join(project_out_dir, "complaint_orders")

    # A state that cannot do something says so, rather than rendering an
    # empty section that reads like a clean check.
    for note in acquired.notes:
        print(f"[INFO] {note}")


    # Opt-in, and placed here on purpose: this is the last step that needs a
    # human at the keyboard (a CAPTCHA solve per portal lookup), so it sits
    # beside the other browser work rather than after deep research, which
    # runs unattended for minutes. Writes gst_filing_input.json, which
    # run_gst_compliance_check picks up during Charter generation below.
    gst_status = _run_gst_intake_step(args.gstin or args.pan, reg_no, args.output_dir)

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
            reviews=reviews, promoter_portfolio=portfolio, pipeline_start_time=pipeline_start_time,
            state_profile=profile,
        )
        external_charter_path = charter_path.replace("_Internal.docx", "_External.docx")
        print(f"[OK] Company Charter (Internal) written to {charter_path}")
        print(f"[OK] Company Charter (External) written to {external_charter_path}")
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
                # Which state this run was about. finalize_report and the
                # module CLIs read it back so a re-render months later still
                # produces the right state's labels. Absent in every tree
                # written before this field existed -- get_profile(None)
                # treats that as Maharashtra, which is what those runs were.
                "state": profile.code,
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
        state_profile=profile,
    )

    failed = [cat for cat, data in category_data.items() if data is None]
    unconfirmed = [cat for cat in config.CATEGORY_ORDER if config.CATEGORY_ENDPOINTS[cat]["status"] != "confirmed"]

    print("\n" + "=" * 60)
    print("  Run summary")
    print("=" * 60)
    print(f"  auth source:         {_AUTH_SOURCE_LABELS[auth_source]}")
    not_published = getattr(acquired, "categories_not_published", set()) or set()
    for cat in config.CATEGORY_ORDER:
        data = category_data.get(cat)
        if data is None and cat in not_published:
            # Not a failure -- this authority has no such endpoint. Saying
            # "FAILED" here would invite a pointless retry and imply the
            # data exists somewhere.
            print(f"  {cat:<20} not published by {profile.rera_acronym}")
            continue
        if cat == "documents":
            _dl = sum(1 for d in documents_manifest if d["status"] == "downloaded")
            _re = sum(1 for d in documents_manifest if d["status"] == "reused")
            count = f"{_dl} downloaded, {_re} reused / {len(documents_manifest)} found"
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
    print(f"  {'gst_filing_intake':<20} {gst_status}")
    print(f"  {'company_charter':<20} {'written (' + charter_path + ')' if charter_path else 'FAILED this run -- see warning above, or retry with: python company_charter.py ' + reg_no}")

    usage = deep_research.write_usage_log(args.output_dir, reg_no)
    total = usage["total"]
    print(f"  {'claude_api_usage':<20} {total['calls']} call(s), {total['input_tokens'] + total['output_tokens']:,} token(s), ${total['cost_usd']:.4f}")
    for label, bucket in sorted(usage["by_label"].items(), key=lambda kv: -kv[1]["cost_usd"]):
        print(f"    {label:<26} {bucket['calls']:>3} call(s)  {bucket['input_tokens'] + bucket['output_tokens']:>8,} tok  ${bucket['cost_usd']:.4f}")
    print(f"  (full breakdown: {os.path.join(project_out_dir, 'usage_summary.json')})")

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

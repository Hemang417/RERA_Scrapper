"""
Streamlit UI for the MahaRERA scraper.

    streamlit run app.py

Wraps the same functions main.py's CLI uses (resolver/session_auth/
api_client/promoter_portfolio/report/finalize_report/deep_research/
company_charter) -- no logic is duplicated, this is presentation only. The
CAPTCHA step still opens a real, separate Chromium window on this machine
(Playwright can't render inside a Streamlit page); solve it there and this
page will pick back up once it's captured.

Every run also fires the agentic deep-research pass and Company Charter
generation automatically (see deep_research.py / company_charter.py) -- no
toggle to disable either. Requires ANTHROPIC_API_KEY; a failure in either
is never fatal to the run, it just leaves that piece unpopulated (use the
Attach Research tab to recover deep research; re-run `python
company_charter.py <REG_NO>` for the charter).
"""

import asyncio
import base64
import json
import os
import platform
import re
import sys

import requests
import streamlit as st

# Streamlit's Tornado server sets the Selector event-loop policy on Windows
# (process-global, not per-thread) for its own websocket handling. Playwright's
# sync API spins up its own asyncio loop internally to launch the browser as a
# subprocess, which Windows' SelectorEventLoop cannot do (NotImplementedError
# from asyncio.base_events._make_subprocess_transport) -- only ProactorEventLoop
# supports subprocess creation on Windows. Tornado's own loop is already
# constructed by the time this script runs per-session, so reasserting the
# Proactor policy here doesn't disturb it -- it only affects loops created
# afterward, which is exactly what Playwright needs.
if platform.system() == "Windows" and sys.version_info >= (3, 8):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import company_charter
import config
import deep_research
import finalize_report
import report
import resolver
import run_archive
import states
import token_cache

st.set_page_config(page_title="MahaRERA Scraper", page_icon="\U0001F3E2", layout="wide")

_AUTH_SOURCE_LABELS = {
    "explicit": "manually supplied token",
    "cached": "reused cached session",
    "fresh_browser": "freshly solved CAPTCHA session",
    "none": "no session (free categories only)",
}


def _describe(c: resolver.ProjectCandidate) -> str:
    bits = [c.reg_no or "(unknown reg no)", "—", c.project_name or "(unknown name)"]
    extra = ", ".join(x for x in (c.promoter_name, c.district, c.pincode) if x)
    if extra:
        bits.append(f"[{extra}]")
    return " ".join(bits)


def _project_out_dir(reg_no: str) -> str:
    return os.path.join(config.OUTPUT_ROOT, reg_no)


def _pdf_preview(pdf_path: str, height: int = 700) -> None:
    with open(pdf_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    st.components.v1.html(
        f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="{height}" '
        f'style="border:1px solid #4443"></iframe>',
        height=height + 10,
    )


# _extract_promoter_name moved to states/adapter_maharashtra.py along with
# the rest of the acquisition logic this file used to duplicate.


class StreamlitReporter:
    """ProgressReporter for the Streamlit UI.

    choose() always returns None -- "cannot ask". Streamlit re-runs the
    whole script on every interaction, so a blocking prompt inside
    acquire() would deadlock. Multi-candidate selection is handled by the
    st.radio + session_state flow in the resolve phase instead, and
    acquire() is only ever called once a project is already resolved."""

    def info(self, msg: str) -> None:
        log(msg)

    def warn(self, msg: str) -> None:
        log(f"WARN: {msg}")
        st.warning(msg)

    def ok(self, msg: str) -> None:
        log(msg)

    def choose(self, prompt: str, options: list) -> int | None:
        return None


def log(msg: str) -> None:
    st.session_state.log.append(msg)


# ---------------------------------------------------------------------------
# Sidebar -- run options
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Options")
    headed_search = st.checkbox("Show search browser (headed)", value=False, help="Debugging only -- the search step never needs a CAPTCHA.")
    no_auto_auth = st.checkbox("Skip CAPTCHA / auto-auth", value=False, help="Only fetch the 2 free categories (projects, complaints); never opens a browser.")
    captcha_timeout = st.number_input("CAPTCHA timeout (seconds)", min_value=60, max_value=600, value=config.CAPTCHA_TIMEOUT_SECONDS, step=30)
    explicit_token = st.text_input("Manual session token (optional)", type="password", help="Paste a token you already captured yourself to skip auto-auth entirely.")
    st.caption(f"Cached session: {token_cache.minutes_left()} min left" if token_cache.minutes_left() > 0 else "Cached session: none / expired")
    use_maps_scrape = st.checkbox(
        "Enable Maps-scrape distances (experimental)",
        value=False,
        help="Company Charter only: replaces web-search distance estimates with a live-scraped "
        "Google Maps driving route per landmark. Off by default -- this scrapes Google's "
        "consumer UI (not their paid Distance Matrix API), so it may not comply with Google's "
        "Terms of Service and can break without warning if Google changes their page. Falls "
        "back to the web-search estimate on any failure.",
    )

st.title("\U0001F3E2 MahaRERA Project Scraper")

tab_run, tab_browse, tab_research = st.tabs(["Run Scraper", "Browse Reports", "Attach Research"])

# ---------------------------------------------------------------------------
# Tab 1: Run Scraper
# ---------------------------------------------------------------------------
with tab_run:
    query = st.text_input("Project name or MahaRERA registration number", placeholder="e.g. P51800002451 or Pranami Bliss")

    col_search, col_reset = st.columns([1, 1])
    search_clicked = col_search.button("Search / Resolve", type="primary", disabled=not query.strip())
    if col_reset.button("Reset"):
        st.session_state.candidates = None
        st.session_state.resolved = None
        st.session_state.run_result = None
        st.session_state.log = []
        st.rerun()

    # Which authority this session is querying. app.py resolves the project
    # interactively (its own st.radio flow) BEFORE acquire() is called, so
    # by the time we get there the project is already pinned to one portal
    # -- there is nothing left to probe. main.py's probe ladder applies to
    # the CLI, where a bare registration number arrives with no UI step.
    profile = states.get_profile(states.DEFAULT_STATE_CODE)

    if search_clicked:
        q = query.strip()
        st.session_state.candidates = None
        st.session_state.resolved = None
        st.session_state.run_result = None
        is_reg_no = bool(re.match(r"^P\d{11}$", q, re.IGNORECASE))
        with st.spinner(f"Searching MahaRERA for '{q}'..."):
            if is_reg_no:
                try:
                    project_id, detail_url = resolver.resolve_project_id(q, headless=not headed_search)
                    st.session_state.resolved = {"project_id": project_id, "detail_url": detail_url, "reg_no": q}
                except resolver.ProjectNotFoundError as e:
                    st.error(str(e))
            else:
                candidates = resolver.search_projects(q, headless=not headed_search)
                if not candidates:
                    st.error(f"No projects found matching '{q}'. Double-check the spelling.")
                elif len(candidates) == 1:
                    c = candidates[0]
                    st.session_state.resolved = {
                        "project_id": c.project_id, "detail_url": c.detail_url, "reg_no": c.reg_no or c.project_id,
                    }
                else:
                    st.session_state.candidates = candidates

    if st.session_state.candidates:
        st.info(f"Found {len(st.session_state.candidates)} matching projects -- pick one:")
        labels = [_describe(c) for c in st.session_state.candidates]
        choice = st.radio("Matches", labels, index=None, label_visibility="collapsed")
        if choice is not None and st.button("Use this project"):
            c = st.session_state.candidates[labels.index(choice)]
            st.session_state.resolved = {"project_id": c.project_id, "detail_url": c.detail_url, "reg_no": c.reg_no or c.project_id}
            st.session_state.candidates = None
            st.rerun()

    if st.session_state.resolved and not st.session_state.candidates:
        r = st.session_state.resolved
        st.success(f"Resolved: reg no **{r['reg_no']}**, internal project ID **{r['project_id']}**")
        st.caption(r["detail_url"])

        if st.button("Fetch RERA data + build report", type="primary"):
            project_id, reg_no = r["project_id"], r["reg_no"]
            project_out_dir = _project_out_dir(reg_no)
            raw_dir = os.path.join(project_out_dir, "raw")

            # This process stays alive across multiple runs (unlike main.py's
            # one-shot CLI), so the usage log must be cleared per-run here --
            # otherwise a second project's usage_summary.json would include
            # token counts left over from whatever was run before it in this
            # same Streamlit session.
            deep_research.reset_usage_log()

            # A one-slot mutable rather than `nonlocal`. This block runs at
            # MODULE level -- Streamlit executes the script top-to-bottom --
            # so there is no enclosing function for `nonlocal` to bind to,
            # and it is a compile-time SyntaxError rather than something that
            # shows up only when the branch is taken. main.py's identical
            # callback sits inside def main(), which is why it works there.
            _prior = {"research": None}

            def _archive_previous_run(reg_no: str) -> dict:
                """Same callback contract main.py uses -- see
                states.AcquisitionContext.on_resolved. Archiving is keyed on
                reg_no and is state-neutral, so it stays with the caller."""
                _prior["research"] = run_archive.load_prior_research(reg_no)
                archive_dir = run_archive.archive_previous_run(reg_no)
                if archive_dir:
                    st.caption(f"Archived previous run to `{archive_dir}`")
                return {
                    "manifest": run_archive.load_prior_manifest(archive_dir),
                    "documents_dir": run_archive.prior_documents_dir(archive_dir),
                    "complaint_orders_manifest": run_archive.load_prior_complaint_orders_manifest(archive_dir),
                    "complaint_orders_dir": run_archive.prior_complaint_orders_dir(archive_dir),
                }

            # ONE call, the same one main.py makes. Everything that used to
            # be duplicated here -- auth, the 401/403 retry, the 9 category
            # fetches, document and complaint-order downloads, the promoter
            # portfolio -- now lives in states/adapter_maharashtra.py, so the
            # CLI and the UI cannot drift apart again.
            #
            # project_id_override carries the project this UI already
            # resolved interactively across reruns, so acquire() does not
            # redo a search that has already happened.
            ctx = states.AcquisitionContext(
                output_dir=config.OUTPUT_ROOT,
                reporter=StreamlitReporter(),
                headed=headed_search,
                explicit_token=explicit_token.strip() or None,
                no_auto_auth=no_auto_auth,
                captcha_timeout=int(captcha_timeout),
                project_id_override=project_id,
                on_resolved=_archive_previous_run,
            )
            adapter = states.get_adapter(profile.code)
            with st.spinner("Fetching RERA data (categories, documents, promoter portfolio)..."):
                try:
                    acquired = adapter.acquire(reg_no, ctx)
                except states.StateAcquisitionError as e:
                    st.error(str(e))
                    st.stop()

            category_data = acquired.category_data
            documents_manifest = acquired.documents_manifest
            complaint_orders_manifest = acquired.complaint_orders_manifest
            documents_dir = acquired.documents_dir
            complaint_orders_dir = acquired.complaint_orders_dir
            promoter_name = acquired.promoter_name
            portfolio = acquired.promoter_portfolio
            auth_source = acquired.auth_source
            for note in acquired.notes:
                st.info(note)


            # --- deep research (market + promoter profile) ---
            research_data = None
            with st.spinner("Running agentic deep research (market + promoter profile) -- this calls the Claude API and can take several minutes..."):
                try:
                    research_data = deep_research.run_deep_research(reg_no, category_data, prior_research=_prior["research"])
                    if research_data.get("_reused_prior"):
                        st.caption("Reused prior confirmed research sources -- only previously-open gaps were re-attempted.")
                except Exception as e:
                    st.warning(f"Deep research failed ({e}) -- continuing without it. Retry later from the Attach Research tab or `python deep_research.py {reg_no}`.")

            # --- company charter ---
            charter_path = None
            charter_facts = None
            if use_maps_scrape:
                os.environ[company_charter._MAPS_SCRAPE_ENV_VAR] = "1"
            else:
                os.environ.pop(company_charter._MAPS_SCRAPE_ENV_VAR, None)
            spinner_msg = "Generating Company Charter docx -- this calls the Claude API and can take several minutes"
            if use_maps_scrape:
                spinner_msg += " (Maps-scrape enabled: also launches a headless browser per landmark, adding time)"
            # No automated review-fetching mechanism exists in this pipeline --
            # this only picks up reviews a user has separately collected and
            # saved here (e.g. via the Browser pane against a review site).
            reviews_path = os.path.join(project_out_dir, "reviews.json")
            reviews = json.load(open(reviews_path, encoding="utf-8")) if os.path.exists(reviews_path) else None
            with st.spinner(spinner_msg + "..."):
                try:
                    charter_path, charter_facts = company_charter.run_company_charter(
                        reg_no, category_data, documents_manifest, documents_dir, research_data,
                        complaint_orders_manifest=complaint_orders_manifest, complaint_orders_dir=complaint_orders_dir,
                        reviews=reviews, promoter_portfolio=portfolio,
                    )
                    st.success(f"Company Charter written to `{charter_path}`")
                except Exception as e:
                    st.warning(f"Company Charter generation failed ({e}) -- continuing without it. Retry later with `python company_charter.py {reg_no}`.")

            with open(os.path.join(project_out_dir, "documents_manifest.json"), "w", encoding="utf-8") as f:
                json.dump(documents_manifest, f, indent=2, ensure_ascii=False)
            from datetime import datetime as _dt
            with open(os.path.join(project_out_dir, "run_meta.json"), "w", encoding="utf-8") as f:
                json.dump({"reg_no": reg_no, "project_id": project_id, "auth_source": auth_source, "promoter_name": promoter_name, "company_charter_path": charter_path, "generated_at": _dt.now().isoformat()}, f, indent=2, ensure_ascii=False)

            pdf_path = os.path.join(project_out_dir, f"{reg_no}_summary.pdf")
            with st.spinner("Building PDF report..."):
                report.build_pdf(reg_no, project_id, category_data, documents_manifest, pdf_path, auth_source=auth_source, promoter_portfolio=portfolio, research_data=research_data, charter_facts=charter_facts)

            usage = deep_research.write_usage_log(config.OUTPUT_ROOT, reg_no)

            st.session_state.run_result = {
                "reg_no": reg_no, "project_id": project_id, "auth_source": auth_source, "retried": retried,
                "category_data": category_data, "errors": errors, "documents_manifest": documents_manifest,
                "downloaded": downloaded, "reused": reused, "portfolio": portfolio, "research_data": research_data,
                "pdf_path": pdf_path, "documents_dir": documents_dir, "raw_dir": raw_dir,
                "charter_path": charter_path, "usage": usage,
            }
            st.rerun()

    # --- results ---
    if st.session_state.run_result:
        res = st.session_state.run_result
        st.divider()
        st.subheader(f"Results — {res['reg_no']}")

        m1, m2, m3, m4, m5 = st.columns(5)
        fetched_ok = sum(1 for cat in config.CATEGORY_ORDER if res["category_data"].get(cat) is not None)
        m1.metric("Categories fetched", f"{fetched_ok}/9")
        m2.metric("Documents", f"{res['downloaded'] + res.get('reused', 0)}/{len(res['documents_manifest'])}")
        m3.metric("Auth source", _AUTH_SOURCE_LABELS[res["auth_source"]] + (" (retried)" if res["retried"] else ""))
        m4.metric("Promoter portfolio", f"{res['portfolio']['totals']['total_projects']} project(s)" if res["portfolio"] else "n/a")
        usage_total = (res.get("usage") or {}).get("total")
        m5.metric("Claude API cost", f"${usage_total['cost_usd']:.4f}" if usage_total else "n/a")

        if usage_total:
            with st.expander(f"Claude API usage detail ({usage_total['calls']} call(s), {usage_total['input_tokens'] + usage_total['output_tokens']:,} token(s))"):
                by_label = res["usage"]["by_label"]
                rows = [
                    {
                        "Purpose": label, "Calls": b["calls"], "Turns": b["turns"],
                        "Input tokens": b["input_tokens"], "Output tokens": b["output_tokens"],
                        "Cost ($)": round(b["cost_usd"], 4),
                    }
                    for label, b in sorted(by_label.items(), key=lambda kv: -kv[1]["cost_usd"])
                ]
                st.dataframe(rows, hide_index=True, use_container_width=True)

        cat_rows = []
        for cat in config.CATEGORY_ORDER:
            data = res["category_data"].get(cat)
            if cat == "documents":
                count = f"{res['downloaded']} downloaded, {res.get('reused', 0)} reused / {len(res['documents_manifest'])} found"
            elif data is None:
                count = "FAILED"
            elif isinstance(data, list):
                count = f"{len(data)} record(s)"
            elif isinstance(data, dict):
                count = "1 record" if data else "empty"
            else:
                count = "empty"
            cat_rows.append({"Category": cat, "Result": count})
        st.dataframe(cat_rows, hide_index=True, use_container_width=True)

        if res["portfolio"]:
            with st.expander("Promoter portfolio detail"):
                st.json(res["portfolio"])

        with open(res["pdf_path"], "rb") as f:
            st.download_button("\U0001F4C4 Download PDF report", f, file_name=os.path.basename(res["pdf_path"]), mime="application/pdf")
        _pdf_preview(res["pdf_path"])

        if res.get("charter_path") and os.path.exists(res["charter_path"]):
            with open(res["charter_path"], "rb") as f:
                st.download_button(
                    "\U0001F4C4 Download Company Charter (docx)", f,
                    file_name=os.path.basename(res["charter_path"]),
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
        else:
            st.caption("Company Charter not available for this run -- see warnings above.")

        if res["documents_manifest"]:
            available_count = res["downloaded"] + res.get("reused", 0)
            with st.expander(f"Available documents ({available_count})"):
                for idx, d in enumerate(res["documents_manifest"]):
                    if d["status"] in ("downloaded", "reused"):
                        path = os.path.join(res["documents_dir"], d["saved_filename"])
                        if os.path.exists(path):
                            label = d["saved_filename"] + (" (reused)" if d["status"] == "reused" else "")
                            with open(path, "rb") as f:
                                # key includes idx, not just path -- two manifest entries can
                                # share a saved_filename (fixed at the source in api_client.py
                                # going forward, but older manifests may still have duplicates)
                                st.download_button(label, f, file_name=d["saved_filename"], key=f"{idx}:{path}")
                    else:
                        st.caption(f"❌ {d['label']} -- {d['status']}")

        with st.expander("Raw category JSON"):
            for cat in config.CATEGORY_ORDER:
                st.markdown(f"**{cat}**")
                st.json(res["category_data"].get(cat))

# ---------------------------------------------------------------------------
# Tab 2: Browse Reports
# ---------------------------------------------------------------------------
with tab_browse:
    if not os.path.isdir(config.OUTPUT_ROOT):
        st.info("No reports generated yet.")
    else:
        reg_nos = sorted(
            d for d in os.listdir(config.OUTPUT_ROOT)
            if os.path.isdir(os.path.join(config.OUTPUT_ROOT, d))
        )
        if not reg_nos:
            st.info("No reports generated yet.")
        else:
            chosen_reg_no = st.selectbox("Previously scraped project", reg_nos)
            if chosen_reg_no:
                out_dir = _project_out_dir(chosen_reg_no)
                meta_path = os.path.join(out_dir, "run_meta.json")
                if os.path.exists(meta_path):
                    with open(meta_path, encoding="utf-8") as f:
                        st.json(json.load(f))
                pdf_path = os.path.join(out_dir, f"{chosen_reg_no}_summary.pdf")
                if os.path.exists(pdf_path):
                    with open(pdf_path, "rb") as f:
                        st.download_button("\U0001F4C4 Download PDF", f, file_name=os.path.basename(pdf_path), mime="application/pdf", key="browse_dl")
                    _pdf_preview(pdf_path)
                else:
                    st.warning("No PDF found for this project yet.")

# ---------------------------------------------------------------------------
# Tab 3: Attach Research (wraps finalize_report.py)
# ---------------------------------------------------------------------------
with tab_research:
    st.caption(
        "The Run Scraper tab now runs deep research automatically via the Claude API. Use this tab only "
        "to manually override it -- e.g. re-attach a hand-edited `deep_research.json`, or recover a run "
        "where the automatic pass failed (missing ANTHROPIC_API_KEY, rate limit, etc.) -- then rebuild the "
        "PDF with zero network calls."
    )
    if not os.path.isdir(config.OUTPUT_ROOT):
        st.info("No reports generated yet.")
    else:
        reg_nos = sorted(d for d in os.listdir(config.OUTPUT_ROOT) if os.path.isdir(os.path.join(config.OUTPUT_ROOT, d)))
        if reg_nos:
            target_reg_no = st.selectbox("Project to attach research to", reg_nos, key="research_reg_no")
            uploaded = st.file_uploader("deep_research.json", type=["json"])
            if uploaded and st.button("Save + rebuild PDF"):
                try:
                    parsed = json.loads(uploaded.read().decode("utf-8"))
                except json.JSONDecodeError as e:
                    st.error(f"Not valid JSON: {e}")
                else:
                    research_dir = os.path.join(_project_out_dir(target_reg_no), "research")
                    os.makedirs(research_dir, exist_ok=True)
                    with open(os.path.join(research_dir, "deep_research.json"), "w", encoding="utf-8") as f:
                        json.dump(parsed, f, indent=2, ensure_ascii=False)
                    pdf_path = finalize_report.rebuild(target_reg_no)
                    st.success(f"Rebuilt {pdf_path}")
                    _pdf_preview(pdf_path)
        else:
            st.info("No reports generated yet.")

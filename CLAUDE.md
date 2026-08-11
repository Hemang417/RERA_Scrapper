# RERA Scrapper — pipeline map

**Content rules live in `rules.md`, not here.** Read it before generating,
editing or reviewing any Charter text. This file is the flow: what runs, in
what order, what is optional, and what must not be skipped.

## Entry point

`python main.py <REG_NO|project name> [--gstin X | --pan Y] [--headed] [--token T] [--no-auto-auth] [--project-id N] [--output-dir D]`

Everything below runs from `main.py::main()` in this order. Stages marked
**[opt-in]** do nothing unless asked; **[never fatal]** log a warning and let
the run continue. Nothing else may swallow an error.

## Stages

1. **Resolve** `_resolve()` — reg-no or free-text name to internal `project_id`
   via Playwright search. `--project-id` skips this when MahaRERA's own search
   cannot find a project that still exists.
2. **Auth** `ensure_token()` — opens a visible browser for a human CAPTCHA
   solve. `--token` supplies one manually; `--no-auto-auth` limits the run to
   the two endpoints needing no token.
3. **Archive** `run_archive` — loads prior research and manifests, then moves
   the previous run aside so documents can be reused rather than refetched.
4. **Scrape** `api_client.fetch_all_categories()` — 9 category endpoints into
   `output/<reg_no>/raw/`. Token-gated failures are retried once with a fresh
   session.
5. **Documents** `api_client.download_documents()`, then
   `download_complaint_orders()` **[never fatal]** into `output/<reg_no>/`.
6. **Promoter portfolio** `promoter_portfolio.build_promoter_portfolio()`
   **[never fatal]** — opens its own browser; needs a promoter name from
   `partners.promoterDetails`.
7. **GST intake** `_run_gst_intake_step()` **[opt-in] [never fatal]** — needs
   `--gstin` or `--pan`. Enumerates every GSTIN under the PAN, fetches each
   filing table, writes `gst_filing_input.json`. One human CAPTCHA solve per
   lookup, which is why it sits here beside the other browser work and not
   after deep research.
8. **Deep research** `deep_research.run_deep_research()` **[never fatal]** —
   agentic web search, market plus promoter. Unattended, minutes long.
9. **Charter** `company_charter.run_company_charter()` **[never fatal]** — see
   below.
10. **Report** `report.build_pdf()` — the RERA project report. A different
    document from the Charter; do not confuse them.
11. **Usage log** `deep_research.write_usage_log()` — per-label cost to
    `usage_summary.json`.

## Inside `run_company_charter()`

1. Assemble facts (`_run_charter_pass`, registry and insolvency checks,
   document grounding). Section B of `rules.md` is injected into every such call.
2. `run_cts_land_lookup()` **[opt-in]** and `run_gst_compliance_check()`
   **[opt-in]** — each does nothing without its human-supplied input file.
3. `_record_source_hits_and_promote()` — cross-run source-trust bookkeeping.
4. `_normalize_misfiled_facts()` then `run_finding_research()` **[never fatal]**
   — per-finding deep research. Enriches confirmed findings only, never gaps.
   A failed call keeps the original text; it must never delete a finding.
5. `_fill_template()` **Internal first, then External.** Internal renders on the
   real facts dict and computes the scores that get persisted; External renders
   from `_externalized_facts_copy()`. Inside each: normalize, scrub clean
   checks, sanitize process text, then the hard gate
   `_verify_external_document_quality()`, which blocks the save.
6. `run_claude_md_document_review()` **[never fatal]** — re-reads both saved
   documents and audits them against `rules.md` via the API. Advisory: it
   reports and writes a review JSON, and never blocks the PDF.
7. `_convert_docx_to_pdf()` on both. **The PDF is the deliverable.**
8. Restore scrubbed and sanitized text, then persist `.facts.json`. The record
   keeps what the page drops.

## Before calling a run done

- Correct builder: `company_charter.py::_fill_template` via
  `run_company_charter`. `charter_report.py` is a different document for a
  different request; `charter_document.py` builds nothing at all any more.
- A real monitor-flag resolution pass: flags re-checked against current facts,
  not carried over stale.
- The review stage ran, or its failure was reported rather than passed over.
- Output only in `output/company_charters/`.
- Any script hitting `_fill_template` directly must call `_convert_docx_to_pdf`
  itself; `run_company_charter` is the only path that does it for you.

## Other entry points

`company_charter.py <REG_NO>` (charter only) · `deep_research.py <REG_NO>` ·
`gst_intake.py <PAN|GSTIN> <REG_NO>` · `cts_resolve.py` (human-in-the-loop land
records) · `charter_report.py` via `run_charter_pipeline.py` or `build_report.py`
· `executive_briefing.py` · `finalize_report.py` (rebuild PDF, no API calls).

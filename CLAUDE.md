# RERA Scrapper — pipeline map

**Two companion files. Read the relevant one before you touch anything.**

- **`rules.md`** — what the documents may and may not say. Read before
  generating, editing or reviewing any Charter text. Parsed at runtime and
  injected into API calls, so it is live configuration, not just prose.
- **`guardrails.md`** — every gate, fallback, bound and never-fatal wrapper,
  by symbol. Read before changing, removing or adding one. Several look
  redundant and are not; each records why it exists.

This file is the flow: what runs, in what order, what is optional, and what
must not be skipped.

## Entry point

`python main.py <REG_NO|project name> [--state MH|TG] [--gstin X | --pan Y] [--headed] [--token T] [--no-auto-auth] [--project-id N] [--output-dir D]`

Everything below runs from `main.py::main()` in this order. Stages marked
**[opt-in]** do nothing unless asked; **[never fatal]** log a warning and let
the run continue. Nothing else may swallow an error.

## Stages

0. **State** `states.candidate_profiles()` — `--state` wins, else every
   authority whose reg-no format matches. MahaRERA and TG-RERA share
   `P\d{11}`, so both are **probed in turn and the one that actually has the
   project wins**; the district-code convention only orders the attempts.
1-6. **Acquire** `states.get_adapter(code).acquire()` — resolve, auth, scrape,
   documents, complaint orders, promoter portfolio, behind one call. MahaRERA's
   is `states/adapter_maharashtra.py`. A state declares what it HAS
   (`profile.capabilities`); what it lacks returns empty plus an honest
   `notes` sentence, never a stub. Archiving stays with the caller via
   `ctx.on_resolved`. `app.py` calls the same method — enforced by a test.
7. **GST intake** `_run_gst_intake_step()` **[opt-in] [never fatal]** — needs
   `--gstin` or `--pan`. Enumerates every GSTIN under the PAN, fetches each
   filing table, writes `gst_filing_input.json`. One human CAPTCHA solve per
   lookup, which is why it sits beside the other browser work, not after (8).
8. **Deep research** `deep_research.run_deep_research()` **[never fatal]** —
   agentic web search. Unattended, minutes long.
9. **Charter** `company_charter.run_company_charter()` **[never fatal]**, below.
10. **Report** `report.build_pdf()` — the RERA project report, a different
    document from the Charter; do not confuse them.
11. **Usage log** `write_usage_log()` — per-label cost to `usage_summary.json`.

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
   from `_externalized_facts_copy()`. Inside each: preflight, normalize, scrub
   clean checks, sanitize process text, then `_verify_external_document_quality()`,
   which blocks the save.
6. `run_claude_md_document_review()` — audits both saved documents against
   `rules.md`. **Strict: no PDF unless they can be SHOWN to comply**, and a
   review that could not run is a failure. `CHARTER_ALLOW_UNCHECKED=1` overrides.
7. `_convert_docx_to_pdf()` on both. **The PDF is the deliverable.**
8. Restore scrubbed and sanitized text, then persist `.facts.json`. The record
   keeps what the page drops.

## How the rules get enforced

Rendering is pure code, so most of `rules.md` is enforced by passes, not by a
model reading it. Four mechanisms, in order: **preflight** (`_preflight_rules`,
the first thing `_fill_template` does), **prompt** (Section B into every content
call, C only into External ones), **deterministic passes**, and **gates**
(`_verify_external_document_quality` blocks a bad save). Section A never reaches
an API call.

See `guardrails.md` for the full map of all four.

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

`company_charter.py <REG_NO>` · `deep_research.py <REG_NO>` · `gst_intake.py
<PAN|GSTIN> <REG_NO>` · `cts_resolve.py` (human-in-the-loop land records) ·
`charter_report.py` via `run_charter_pipeline.py` / `build_report.py` ·
`executive_briefing.py` · `finalize_report.py` (rebuild PDF, no API calls) ·
`ts_rera_client.py <name>` (Telangana RERA search+detail, standalone, not
wired into main.py -- CAPTCHA-gates its own search, human-in-the-loop).

# RERA Scrapper — pipeline map

**Two companion files. Read the relevant one before you touch anything.**

- **`rules.md`** — what the documents may and may not say. Read before
  generating, editing or reviewing any Charter text. Parsed at runtime and
  injected into API calls: live configuration, not just prose.
- **`guardrails.md`** — every gate, fallback, bound and never-fatal wrapper, by
  symbol. Read before changing one. Several look redundant and are not.

This file is the flow: what runs, in what order, what is optional, what must
not be skipped.

## Entry point

`python main.py <REG_NO|project name> [--state MH|GJ|KA|TG|JH|WB|UP|TN|HR|DL] [--group-sweep] [--group-gst] [--group-litigation]
[--group-enforcement] [--group-financial-disclosure] [--gstin X | --pan Y] [--headed] [--token T] [--no-auto-auth]
[--project-id N] [--output-dir D]`

Everything below runs from `main.py::main()` in this order. Stages marked
**[opt-in]** do nothing unless asked; **[never fatal]** log a warning and let
the run continue. Nothing else may swallow an error.

## Stages

0. **State** `states.candidate_profiles()` — `--state` wins, else every
   authority whose reg-no format matches. MahaRERA and TG-RERA share
   `P\d{11}`, so both are **probed and whichever actually has the project
   wins**; the district-code convention only orders the attempts.
1-6. **Acquire** `states.get_adapter(code).acquire()` — resolve, auth, scrape,
   documents, complaint orders, promoter portfolio, behind one call. Ten states:
   MH, GJ, KA, TG, JH, WB, UP, TN, HR, DL. A state declares what it HAS
   (`profile.capabilities`); what it lacks returns empty plus an honest
   `notes` sentence, never a stub. Archiving stays with the caller via
   `ctx.on_resolved`. `app.py` calls the same method — enforced by a test.
7. **GST intake** `_run_gst_intake_step()` **[opt-in] [never fatal]** — needs
   `--gstin` or `--pan`. Enumerates every GSTIN under the PAN, fetches each
   filing table, writes `gst_filing_input.json`. One human CAPTCHA solve per
   lookup, hence its position beside the other browser work, not after (8).
8. **Deep research** `deep_research.run_deep_research()` **[never fatal]** —
   agentic web search, unattended, minutes long.
9. **Charter** `company_charter.run_company_charter()` **[never fatal]**, below.
10. **Report** `report.build_pdf()` — the RERA project report, NOT the Charter.
11. **Usage log** `write_usage_log()` — per-label cost to `usage_summary.json`.

## Inside `run_company_charter()`

1. Assemble facts (`_run_charter_pass`, registry and insolvency checks,
   document grounding). Section B of `rules.md` is injected into every such call.
2. `run_cts_land_lookup()`, `run_igr_registered_deed_check()`, and
   `run_gst_compliance_check()` **[opt-in]** — each does nothing without its
   human-supplied input file, unless a human is actually at this terminal
   (`sys.stdin.isatty()`), in which case CTS walks them through office →
   village → CTS-number → mobile inline instead of stopping to wait for a
   separate `cts_resolve.py` session (never auto-picks office/village —
   Marathi labels don't reliably match RERA's own text), and IGR asks
   directly for a district/SRO/year/document-number (no candidate list to
   pick from — a document number is either already in hand, from e.g. the
   project's own Title Report, or not knowable at all). IGR is Maharashtra
   -only, checked explicitly (`facts["state"]["code"] == "MH"`) since unlike
   CTS it has no district-hint step that would naturally fail elsewhere.
2b. **Identity and group passes** — code-computed, never model-authored, each
   with its own Charter section: `_safe_promoter_identity()` (PAN off the filed
   card), `_safe_charge_movement()` (borrowing moved since last run),
   `_safe_state_footprint()` (registered vs built), `_safe_group_rera_sweep()`
   **[`--group-sweep`]**, `_safe_group_gst()` **[`--group-gst`]** (PAN-keyed, so
   most of a group is unreachable), `_safe_group_litigation()`
   **[`--group-litigation`]** (case law + K-RERA orders; every hit a NAME match),
   `_safe_group_enforcement()` **[`--group-enforcement`]** (defaulter/cancellation/
   penalty/rejection/surrender registers across UP-RERA, HARERA, TNRERA,
   Delhi-RERA, WBRERA, JHARERA; name match, same discipline as the litigation
   sweep — WBRERA's own register names only the project, never the promoter, so
   its hits are weaker evidence than the others), `_safe_group_financial_disclosure()`
   **[`--group-financial-disclosure`]** (balance sheet/P&L/ITR documents found on
   OTHER group entities' Gujarat/Jharkhand/Haryana/West Bengal/Uttar Pradesh/Tamil
   Nadu projects — the six portals with a searchable document list, of which only
   the first four have ever produced a live-confirmed hit — via `--group-sweep`'s
   already-opened projects — needs `--group-sweep` on or there is nothing to check),
   `_safe_nclt_check()` (direct NCLT Case Status portal query by party name, NCLT,
   Mumbai Bench only — 15 other benches nationwide not checked; a real CAPTCHA
   solve, so only runs when a human is at this terminal), `_safe_bombay_hc_check()`
   (direct eCourts Case Status query for Original Side + Appellate Side, Bombay,
   last 5 registration years — the portal requires a mandatory year per search, so
   this is 10 separate CAPTCHA-gated lookups; always asks explicit human consent
   first, on top of the terminal check, given that cost — 5 other benches covering
   Aurangabad/Nagpur/Kolhapur/Goa/Special Court not checked). Both are DIRECT
   court/tribunal queries, distinct from `_safe_group_litigation()`'s indirect
   Indian Kanoon name search just above.
   Each reports its own coverage: no finding never means no check (`guardrails.md`).
   `_append_promoter_profile_section()` consolidates the above into one
   scannable overview — corporate identity, a Brief Profile narrative (from
   `deep_research`'s `promoter_external`, persisted into `facts["promoter_
   external_research"]` rather than only used as a transient prompt hint),
   a Business Ventures & Network summary, a Trust Signals checklist
   (`_promoter_trust_signals()` — Clean/Flagged/Not checked/Not applicable
   per already-run check, deliberately NOT a numeric 1-4 score), and the
   Registered-Deed Corroboration result if IGR ran. Cross-references the
   Group Companies section's own table/director-relationship diagram rather
   than re-rendering them. States outright, rather than estimating, what
   genuinely isn't available from any public source: personal net worth,
   family, personal investment portfolios, and private-entity valuations.
3. `_record_source_hits_and_promote()` — cross-run source-trust bookkeeping.
4. `_normalize_misfiled_facts()` then `run_finding_research()` **[never fatal]**
   — per-finding deep research. Confirmed findings only, never gaps. A failed
   call keeps the original text; it must never delete a finding.
5. `_fill_template()` **Internal first, then External.** Internal renders on the
   real facts dict and computes the scores that get persisted; External renders
   from `_externalized_facts_copy()`. Inside each: preflight, normalize, scrub
   clean checks, sanitize process text, then `_verify_external_document_quality()`,
   which blocks the save.
6. `run_claude_md_document_review()` — audits both saved documents against
   `rules.md`. **Strict: no PDF unless SHOWN to comply**; a review that could
   not run is a failure. `CHARTER_ALLOW_UNCHECKED=1` overrides.
7. `_convert_docx_to_pdf()` on both. **The PDF is the deliverable.**
8. Restore scrubbed/sanitized text, persist `.facts.json` — the record keeps
   what the page drops.

## How the rules get enforced

Rendering is pure code, so most of `rules.md` is enforced by passes, not by a
model reading it. Four mechanisms, in order: **preflight** (`_preflight_rules`,
first thing `_fill_template` does), **prompt** (Section B into every content
call, C only into External ones), **deterministic passes**, and **gates**
(`_verify_external_document_quality` blocks a bad save). Section A never reaches
an API call. Full map of all four: `guardrails.md`.

## Before calling a run done

- Correct builder: `company_charter.py::_fill_template` via
  `run_company_charter`. `charter_report.py` is a different document;
  `charter_document.py` builds nothing any more but is a live shared library.
- A real monitor-flag pass: flags re-checked against current facts, not stale.
- The review stage ran, or its failure was reported rather than passed over.
- Output only in `output/company_charters/`. Any script hitting
  `_fill_template` directly must call `_convert_docx_to_pdf` itself.

## Other entry points

`company_charter.py <REG_NO>` · `deep_research.py <REG_NO>` · `gst_intake.py
<PAN|GSTIN> <REG_NO>` · `charge_watch.py <CIN>` (borrowing repaid yet?) ·
`group_sweep.py <names...>` · `cts_resolve.py` and `ts_rera_client.py <name>`
(human-in-the-loop, CAPTCHA) · `nclt_search.py <party name> [bench]` and
`bombay_hc_search.py <party name> <year> [bench]` (human-in-the-loop, CAPTCHA —
direct court/tribunal portal queries) · `igr_maharashtra_search.py docno
<district> <SRO> <year> <doc_no>` (human-in-the-loop, CAPTCHA — registered-deed
search, Maharashtra only; also reachable inline via `run_igr_registered_deed_
check()`) · `charter_report.py` via
`run_charter_pipeline.py` / `build_report.py` · `executive_briefing.py` ·
`finalize_report.py` (no API).

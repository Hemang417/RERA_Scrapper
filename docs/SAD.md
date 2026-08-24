# Software Architecture Document (SAD)
## RERA Scrapper — Pan-India RERA Due-Diligence & Company Charter Pipeline

| Field | Value |
|---|---|
| **Document** | Software Architecture Document |
| **System** | RERA Scrapper (pan-India RERA project due-diligence and Company Charter generator) |
| **Repository root** | `RERA_Scrapper/` |
| **Version** | 2.0 |
| **Date** | 21 August 2026 |
| **Status** | Baseline — describes the system as implemented |
| **Owner** | Integrow Asset Management |
| **Companion documents** | `PRD.md` (product requirements), `CLAUDE.md` (flow), `rules.md` (content rules), `guardrails.md` (guards) |

---

## Table of contents

1. [Purpose, scope and audience](#1-purpose-scope-and-audience)
2. [Reference documents — the governance `.md` set](#2-reference-documents--the-governance-md-set)
3. [Architectural goals, principles and constraints](#3-architectural-goals-principles-and-constraints)
4. [System context](#4-system-context)
5. [Logical architecture — layered view](#5-logical-architecture--layered-view)
6. [Module inventory](#6-module-inventory)
7. [Runtime view — the end-to-end pipeline](#7-runtime-view--the-end-to-end-pipeline)
8. [Stage-by-stage specification](#8-stage-by-stage-specification)
9. [The Company Charter subsystem](#9-the-company-charter-subsystem)
10. [Data architecture](#10-data-architecture)
11. [The facts data model](#11-the-facts-data-model)
12. [Governance architecture — how `rules.md` is enforced](#12-governance-architecture--how-rulesmd-is-enforced)
13. [Guardrail architecture](#13-guardrail-architecture)
14. [Authentication and session architecture](#14-authentication-and-session-architecture)
15. [External integration architecture](#15-external-integration-architecture)
16. [LLM integration architecture and cost model](#16-llm-integration-architecture-and-cost-model)
17. [Scoring subsystem](#17-scoring-subsystem)
18. [Document rendering architecture](#18-document-rendering-architecture)
19. [Secondary and standalone builders](#19-secondary-and-standalone-builders)
20. [Concurrency and performance](#20-concurrency-and-performance)
21. [Failure, degradation and recovery model](#21-failure-degradation-and-recovery-model)
22. [Security, privacy and compliance](#22-security-privacy-and-compliance)
23. [Testing architecture](#23-testing-architecture)
24. [Deployment and operations](#24-deployment-and-operations)
25. [Known limitations, technical debt and risks](#25-known-limitations-technical-debt-and-risks)
26. [Appendices](#26-appendices)

---

## 1. Purpose, scope and audience

### 1.1 Purpose

This document describes the architecture of **RERA Scrapper**: a single-command pipeline that takes a MahaRERA project registration number and produces a defensible, source-cited due-diligence pack — a project summary PDF plus a paired **Company Charter** (Internal and External variants) covering the counterparty, its promoters and the collateral.

It is the architectural reference for anyone extending, operating or auditing the system. It records not only *what* the components are, but *why* several of them exist in the shape they do, because a large fraction of this codebase is hard-won accommodation of a live government portal that does not behave the way its API surface suggests.

### 1.2 Scope

In scope:

- The full ingestion → enrichment → assembly → rendering → verification pipeline.
- Both entry points: `main.py` (CLI) and `app.py` (Streamlit).
- The CAPTCHA-gated external portals (MahaRERA, the GST portal, Maha Bhulekh) and the redundancy chains built around them, plus the un-gated state registers reached without one.
- The rules/guardrail governance layer that decides whether a document may ship.
- The two document families (RERA summary PDF; Company Charter family) and three renderers.

Out of scope:

- The commercial underwriting decision the documents feed into.
- Any RERA authority's own system internals (each treated as an opaque, occasionally hostile third party).
- CI/CD infrastructure — none currently exists (see §25).

### 1.3 Audience

| Audience | Read |
|---|---|
| New engineer joining the codebase | §3–§9, then `CLAUDE.md` |
| Engineer changing a guard or gate | §12, §13, then `guardrails.md` |
| Engineer changing document content | §11, §12, §18, then `rules.md` |
| Operations / runbook author | §14, §21, §24 |
| Reviewer / auditor | §12, §13, §16, §22 |
| Product | `PRD.md` first, then §4, §7 |

---

## 2. Reference documents — the governance `.md` set

This repository is unusual in that **some of its Markdown is executable configuration, not prose**. Understanding which is which is a prerequisite to changing anything.

```
   +--------------------------------------------------------------------+
   |                  THE MARKDOWN GOVERNANCE SET                       |
   +--------------------------------------------------------------------+

   README.md                    CLAUDE.md                 rules.md
   +-----------------+          +-----------------+       +-----------------+
   | Human onboarding|          | THE FLOW        |       | THE CONTENT     |
   | Usage, install, |          | What runs, in   |       | RULES           |
   | ASCII pipeline  |--------->| what order,     |------>| 3 sections,     |
   | architecture    |  "how"   | what is opt-in, | "when"| A / B / C       |
   | Output layout   |          | what may not be |       |                 |
   |                 |          | skipped         |       | *** PARSED AT   |
   | Status: prose   |          |                 |       |     RUNTIME *** |
   +-----------------+          | Status: prose   |       | Status: LIVE    |
                                +-----------------+       |         CONFIG  |
                                        |                 +--------+--------+
                                        |                          |
                                        v                          v
                                guardrails.md            _read_rules_section()
                                +-----------------+      in company_charter.py
                                | THE GUARDS      |      injects B and C into
                                | Every gate,     |      Claude API calls
                                | fallback, bound |
                                | and never-fatal |      Section A is validated
                                | wrapper, BY     |      but NEVER transmitted
                                | SYMBOL          |
                                |                 |
                                | Status: prose,  |
                                | but test_guard- |
                                | rails_doc.py    |
                                | fails if a      |
                                | named symbol    |
                                | disappears      |
                                +-----------------+

   CHARTER_RESHAPE_SPEC.md      .claude/skills/maharera-report/SKILL.md
   +-----------------------+    +-----------------------------------+
   | HISTORICAL RECORD     |    | Project-local Claude Code skill    |
   | COMPLETED 2026-08-10  |    | for driving report generation      |
   | *** DO NOT FOLLOW *** |    | interactively                      |
   | Stale line refs,      |    +-----------------------------------+
   | stale dead-code claim |
   +-----------------------+    output/company_charters/
                                CHARTER_RESHAPE_CHANGE_LOG.md
                                +-----------------------------------+
                                | Per-change log of the 2026-08      |
                                | Charter reshape                    |
                                +-----------------------------------+
```

| File | Size | Role | Mutability |
|---|---|---|---|
| `CLAUDE.md` | ~5.5 KB | Pipeline flow map; the binding **flow** authority | Prose; edit when the flow changes |
| `rules.md` | ~18.4 KB | Content rules; the binding **content** authority. Sections B and C are injected verbatim into Claude API calls | **Live configuration.** Parsed by `_read_rules_section()`. Section markers must not be renamed or reordered |
| `guardrails.md` | ~5.6 KB | Map of every gate, fallback, bound and never-fatal wrapper, keyed **by symbol, never by line number** | Prose, but `test_guardrails_doc.py` fails the suite if a named symbol stops existing |
| `README.md` | ~15.8 KB | Onboarding, usage, the original ASCII pipeline architecture, output layout | Prose |
| `CHARTER_RESHAPE_SPEC.md` | ~13.1 KB | **Historical only.** All seven tasks shipped 2026-08-10. Explicitly stale in four named ways | Frozen |
| `.claude/skills/maharera-report/SKILL.md` | ~8.3 KB | Project-local agent skill for the report workflow | Living |
| `output/company_charters/CHARTER_RESHAPE_CHANGE_LOG.md` | ~8.9 KB | Change log of the reshape | Append-only |

> **Precedence rule, quoted from `rules.md`:** *"Where the two disagree, this file wins on content and CLAUDE.md wins on flow."*

### 2.1 The three sections of `rules.md`

```
   rules.md
   +----------------------------------------------------------------+
   |  --- Section A: CODING-TIME RULES ---                          |
   |      Audience: engineers.                                       |
   |      Delivery: read by humans; validated by _preflight_rules.   |
   |      *** NEVER SENT TO AN API. ***                              |
   |      test_claude_md_doc_review.py asserts its absence.          |
   +----------------------------------------------------------------+
   |  --- Section B: COMMON CONTENT RULES ---                       |
   |      Audience: the model.                                       |
   |      Delivery: _common_content_rules() -> a separate cacheable  |
   |      system-prompt block on EVERY content call, BOTH variants.  |
   |      Constraint: must contain no em dash and no " -- ".         |
   +----------------------------------------------------------------+
   |  --- Section C: STAGE-FIXED CONTENT RULE (external only) ---    |
   |      Audience: the model, external-facing calls only.           |
   |      Delivery: _external_citation_rule(), appended after B.     |
   |      Same punctuation constraint as B.                          |
   +----------------------------------------------------------------+
```

The punctuation constraint on B and C is not stylistic. `_verify_external_document_quality` hard-fails an External save containing an em dash or a hyphen-pair dash; prompt punctuation bleeds into model output; therefore the prompt itself must be clean or every External save fails. `_preflight_rules` enforces this before a paragraph is written.

---

## 3. Architectural goals, principles and constraints

### 3.1 Goals

| # | Goal | How the architecture serves it |
|---|---|---|
| G1 | **Every claim is traceable to a source** | `_FIELD_WITH_SOURCE` schema shape; `sources[]` with `topic` enumeration; per-claim citation resolution; source-trust registry |
| G2 | **A partial failure never costs the run** | Never-fatal wrappers around every optional stage; per-category thread isolation; manifest-row failure recording |
| G3 | **A bad document must not ship** | Three-tier gate chain: preflight → mechanical gate → semantic review, the last of which blocks the PDF |
| G4 | **A model can only match or improve, never degrade** | Every model-backed judgement keeps its deterministic predecessor behind it; cache-miss is indistinguishable from "no model ran" |
| G5 | **The record keeps what the page drops** | Scrub/restore contract before `.facts.json` is written |
| G6 | **Re-runs are cheap and diffable** | Run archiving, document reuse by `(document_id, filename)`, 24-hour research reuse window |
| G7 | **Nothing is silently guessed** | Absent data becomes a `gap`, never an approximation; disagreeing sources are both surfaced, never silently reconciled |

### 3.2 Principles

1. **Deterministic core, probabilistic edges.** Rendering is pure code. The model assembles facts and offers judgements; it never renders the External document.
2. **Fail-soft on ingestion, fail-hard on output.** Getting less data is acceptable; shipping a wrong document is not.
3. **Human-in-the-loop where the law puts a CAPTCHA.** No module reads or solves a CAPTCHA image. This is a hard constraint, stated in `session_auth.py`, `gst_portal.py` and `mahabhumi.py`.
4. **Symbols, not line numbers.** `guardrails.md` records guards by `module.symbol`; line references in this repo have gone stale twice.
5. **Two contradictory numbers beat one invented one.** Two known data contradictions are deliberately left unreconciled (`rules.md` §A).
6. **Absence is not a finding.** The single largest editorial rule: a clean check produces no sentence at all.

### 3.3 Constraints

| Constraint | Consequence |
|---|---|
| MahaRERA gates 7 of 9 categories behind a browser CAPTCHA | A human must be present for a full run, or supply a token |
| Maha Bhulekh regenerates its CAPTCHA on every partial postback | No reusable session; land-record lookup is opt-in and manual |
| GST portal needs one solve per PAN search **and** one per GSTIN | GST intake is opt-in and placed beside the other browser work |
| `docx2pdf` is Windows-only (Word COM automation) | PDF conversion — and therefore the deliverable — requires Windows |
| `output/` is gitignored, and holds the `.docx` template | Template must be backed up manually before structural change |
| No packaging manifest (`pyproject.toml` / `setup.py`) exists | Flat module namespace at repo root; `requirements.txt` is the only manifest |
| Sonnet 5 intro pricing expires **2026-08-31** | Cost figures under-report after that date unless updated |

---

## 4. System context

```
                        +==========================================+
                        |         HUMAN OPERATOR (Analyst)         |
                        |  Supplies: reg-no / project name / PAN   |
                        |  Performs: CAPTCHA solves (1..N)         |
                        |  Consumes: PDF + Charter Internal/External|
                        +====================+=====================+
                                             |
             CLI  python main.py <REG_NO> [--state MH|GJ|KA|TG|JH|WB|UP|TN|HR|DL]
                       [--group-sweep] [--group-gst] [--group-litigation]
                       [--gstin|--pan]
             UI   streamlit run app.py
                                             |
                                             v
   +=====================================================================+
   |                        RERA  SCRAPPER  (this system)                |
   |                     Python 3.11+, single process, local disk        |
   +=====================================================================+
        |            |            |            |             |
        |            |            |            |             |
        v            v            v            v             v
  +-----------+ +----------+ +----------+ +----------+ +-------------+
  | SIX RERA  | | MCA      | | Credit / | | GST      | | Anthropic   |
  | AUTHORITY | | mirrors  | | IBBI     | | Portal   | | Claude API  |
  | PORTALS   | |          | |          | |          | |             |
  | MH GJ KA  | |          | | CRISIL   | |          | |             |
  | TG JH WB  | |          | |          | |          | |             |
  |           | |          | |          | |          | |             |
  | maharera. | | Zauba-   | | ICRA     | | services.| | claude-     |
  |  maha...  | |  Corp    | | Infomer- | |  gst.gov | |  sonnet-5   |
  |  (Drupal, | | Tofler   | |  ics     | |  .in     | | + web_search|
  |   search) | | Insta-   | | IBBI     | |          | |   server    |
  |           | |  Finan-  | |          | | CAPTCHA  | |   tool      |
  | maharerait| |  cials   | | (public  | | per      | |             |
  |  .maha... | |          | |  search) | | lookup   | | ANTHROPIC_  |
  |  (API,    | | (3-way   | |          | |          | |  API_KEY    |
  |   CAPTCHA)| |  cross-  | +----------+ +----------+ +-------------+
  +-----------+ |  check)  |
                +----------+
        |            |            |             |            |
        v            v            v             v            v
  +-----------+ +----------+ +-----------+ +----------+ +-------------+
  | Indian    | | (order   | |           | |          | |             |
  | Kanoon    | |  regis-  | |           | |          | |             |
  | (case law | |  ters:   | |           | |          | |             |
  |  by name) | |  K-RERA  | |           | |          | |             |
  |           | |  MahaRERA| |           | |          | |             |
  |           | |  JHARERA | |           | |          | |             |
  |           | |  WBRERA) | |           | |          | |             |
  +-----------+ +----------+ +-----------+ +----------+ +-------------+

  +-----------+ +----------+ +-----------+ +----------+ +-------------+
  | Maha      | | Open     | | Google    | | xAI      | | Tesseract   |
  | Bhulekh   | | Street   | | Maps      | | Grok     | | OCR (local) |
  | (land     | | Map      | | (opt-in,  | | (opt-in  | | eng + mar   |
  |  records, | | Nominatim| |  ToS      | |  fallback| | traineddata |
  |  Marathi, | | (geocode | |  caveat)  | |  verifier| | bundled     |
  |  CAPTCHA  | |  1 req/s)| |           | |  XAI_API_| |             |
  |  per call)| |          | | COMPANY_  | |  KEY)    | | TESSERACT_  |
  +-----------+ +----------+ | CHARTER_  | +----------+ |  CMD        |
                             | USE_MAPS_ |              +-------------+
                             | SCRAPE=1  |
                             +-----------+

   LEGEND:  ==  system boundary      --  component boundary
            -->  synchronous call     v   data flow direction
```

**Trust tiering of the external world.** The system does not treat all sources equally. `_classify_source_tier` maps every source into one of 12 tiers with weights 20–100, feeding the Documentation Confidence score. The RERA authorities, MCA mirrors, rating agencies, IBBI, Indian Kanoon and Maps are *trusted by design* and excluded from the open-web trust registry; everything else (99acres, NoBroker, press, social, Wikipedia) accumulates hit counts in `source_trust_registry.json` across runs and is auto-promoted at the **5th distinct project**.

---

## 5. Logical architecture — layered view

```
+=========================================================================+
|  L6  PRESENTATION / DELIVERY                                            |
|  report.py (ReportLab PDF) . company_charter._fill_template (docx)       |
|  charter_report.py (docx) . executive_briefing.py (docx + matplotlib)   |
|  _convert_docx_to_pdf (docx2pdf / Word COM)  ->  THE DELIVERABLE IS PDF  |
+=========================================================================+
                                   ^
+=========================================================================+
|  L5  GOVERNANCE / VERIFICATION                                          |
|  _preflight_rules . _verify_external_document_quality (11 checks)       |
|  run_claude_md_document_review + _verified_violations  [BLOCKS THE PDF] |
|  charter_report.verify_charter_report_quality (6 checks)                |
|  rules.md sections A / B / C  .  guardrails.md symbol map               |
+=========================================================================+
                                   ^
+=========================================================================+
|  L4  EDITORIAL / NORMALISATION                                          |
|  run_editorial_passes (3 model passes, all with deterministic fallback) |
|  _scrub_clean_checks . _sanitize_process_gaps . _normalize_misfiled_facts|
|  _classify_flags . _flag_headline . run_finding_research                |
|  _restore_clean_checks  ->  the record keeps what the page drops        |
+=========================================================================+
                                   ^
+=========================================================================+
|  L3  ANALYSIS / SCORING                                                 |
|  _compute_developer_score (3 buckets, 9 sub-metrics, no renormalisation)|
|  _compute_documentation_confidence_score (8 criteria, renormalises)     |
|  gst_compliance (statutory due dates, friction-point model)             |
|  verify_cross_corroboration . _record_source_hits_and_promote           |
+=========================================================================+
                                   ^
+=========================================================================+
|  L2  ENRICHMENT                                                         |
|  deep_research.py (agentic web research)  .  promoter_portfolio.py      |
|  MCA-mirror chain (_run_mca_profile_chain)  .  credit rating  .  IBBI   |
|  gst_intake / gst_portal  .  mahabhumi (CTS land records)               |
|  search_maharera_judgments  .  run_review_authenticity_triage           |
+=========================================================================+
                                   ^
+=========================================================================+
|  L1  INGESTION                                                          |
|  resolver.py (Playwright search)  .  session_auth.py (CAPTCHA -> token) |
|  token_cache.py (90-min disk cache)  .  api_client.py (9 categories,    |
|  document + complaint-order download)  .  discover.py (--verify)        |
|  run_archive.py (snapshot previous run)                                 |
+=========================================================================+
                                   ^
+=========================================================================+
|  L0  FOUNDATION                                                         |
|  config.py (endpoints, selectors, timeouts, caps)                       |
|  Local filesystem: output/ tree  .  source_trust_registry.json          |
|  .guest_token_cache.json  .  tessdata/                                  |
+=========================================================================+
```

**Cross-cutting:** cost/usage accounting (`deep_research._record_usage`, per-label) spans L2–L5; the never-fatal wrapper policy spans L1–L5; the `facts` dict is the shared data structure from L2 upward.

---

## 6. Module inventory

### 6.1 Application modules (38 files at repository root, plus the `states/` package)

| Module | LOC/size | Layer | Role |
|---|---|---|---|
| `main.py` | 25.3 KB | Orchestration | CLI entry point; owns the 11-stage sequence and the never-fatal wrappers |
| `app.py` | 28.0 KB | Presentation | Streamlit UI over the same functions; 3 tabs; no duplicated logic |
| `config.py` | 5.8 KB | L0 | Endpoints, selectors, timeouts, `CATEGORY_ORDER`, `PROMOTER_PROJECT_LIMIT` |
| `resolver.py` | 8.2 KB | L1 | reg-no / name → internal numeric `project_id` via Playwright; also promoter search |
| `session_auth.py` | 3.8 KB | L1 | Visible browser, human CAPTCHA solve, reads `accessToken` from `sessionStorage` |
| `token_cache.py` | 2.7 KB | L1 | 90-minute disk token cache + standalone CLI |
| `api_client.py` | 26.0 KB | L1 | 9 category endpoints, envelope normalisation, document + complaint-order download |
| `discover.py` | 1.8 KB | L1 | `--verify` mode: probe every endpoint, report confirmed-vs-observed drift |
| `run_archive.py` | 5.8 KB | L1 | Snapshot the previous run and the previous Charter; supply reusable pieces |
| `promoter_portfolio.py` | 24.3 KB | L2 | Promoter's whole RERA portfolio, complaint/appeal totals, 5 km area, geocoding |
| `deep_research.py` | 38.5 KB | L2 | **The single transport for every Claude API call**; agentic research; usage/cost ledger |
| `gst_intake.py` | 9.9 KB | L2 | PAN → all GSTINs → filing tables → `gst_filing_input.json` |
| `gst_portal.py` | 18.5 KB | L2 | GST portal driver + filing-table parser; OCR-assisted |
| `gst_compliance.py` | 12.2 KB | L3 | Pure offline GSTIN/PAN validation, QRMP frequency, statutory due dates, delays |
| `mahabhumi.py` | 40.6 KB | L2 | Maha Bhulekh Property Card lookup; Marathi UI; district map. Requests the card in **English** and parses its HTML tables -- in Marathi the card is a single embedded JPEG -- and saves that image as the authoritative artefact |
| `cts_intake.py` | 3.0 KB | Entry | Standalone CTS lookup with no RERA number |
| `cts_resolve.py` | 6.7 KB | Entry | 4-step human-in-the-loop CTS office/village/number resolution |
| `promoter_intake.py` | 3.0 KB | Entry | Standalone CIN-only promoter checks |
| `attach_rera.py` | 2.3 KB | Entry | Link a pending CIN/CTS case to a RERA number |
| **`company_charter.py`** | **655.4 KB / 11,785 lines** | L2–L6 | **The core.** Facts assembly, registry chains, editorial passes, scoring, dual-variant rendering, all three gates |
| `charter_report.py` | 70.8 KB | L6 | Second builder — "Counterparty + Collateral" document; own quality gate |
| `charter_document.py` | 32.4 KB | Shared lib | Builder deleted 2026-08-10; **still a live shared library** (16 symbols) |
| `charter_research_prep.py` | 24.6 KB | Shared lib | Roster building + agent prompt construction for the `charter_report` pipeline |
| `run_charter_pipeline.py` | 5.4 KB | Entry | `prepare()` / `finish()` seam for the agent-driven charter report |
| `executive_briefing.py` | 21.0 KB | L6 | 8-page narrative companion; donut charts via matplotlib |
| `report.py` | 26.5 KB | L6 | The RERA project summary PDF (ReportLab) |
| `build_report.py` | 11.8 KB | One-off | Hand-curated research for one engagement; "delete or rewrite per engagement" |
| `finalize_report.py` | 4.6 KB | Entry | Rebuild the summary PDF from disk with **zero network calls** |

**Added in v2.0 -- the state seam and group-level diligence:**

| Module | LOC/size | Layer | Role |
|---|---|---|---|
| `states/base.py` | 20.2 KB | L1 | `StateProfile` (frozen dataclass, pure data) + `StateAdapter` (`typing.Protocol`, one `acquire()`), capability constants, `AcquisitionResult`, shared fetch/retry and filename helpers |
| `states/__init__.py` | 7.7 KB | L1 | Profile registry and state resolution: `--state` wins, else match every registered reg-no pattern; the MH/TG collision is probed, not guessed |
| `states/<state>.py` x6 | 2.8-4.9 KB | L0 | Per-state constants: endpoints, reg-no pattern, declared capabilities. MH, GJ, KA, TG, JH, WB |
| `states/adapter_<state>.py` x6 | 12.5-34.3 KB | L1 | Per-state acquisition behind one `acquire()`. Karnataka and West Bengal also expose their order registers |
| `ts_rera_client.py` | 11.5 KB | L1 | Telangana's pre-existing scraper, wrapped rather than rewritten |
| `promoter_identity.py` | 25.7 KB | L2 | Reads the promoter's PAN off the PAN card in the document library; emits an explicit `status`, never prose to be sniffed |
| `group_entities.py` | 25.0 KB | L2 | The entity graph: brand name PROPOSES, a hard link (shared director / registered office / filed relationship) CONFIRMS. Also state footprint from CIN codes and declared addresses |
| `group_sweep.py` | 19.5 KB | L2 | Group-wide RERA sweep with per-authority coverage and confirm/probable/refute outcomes |
| `charge_watch.py` | 10.3 KB | L2 | Secured borrowing movement between runs; a vanished charge is not a satisfied one |
| `gst_group.py` | 11.5 KB | L2 | GST across the entity graph. Coverage-first: GST is PAN-keyed and no MCA source publishes a PAN |
| `litigation_sweep.py` | 19.9 KB | L2 | Case law per entity and director, plus the authorities' own order registers; names the forums that are not indexed |
| `wb_orders.py` | 8.9 KB | L3 | Pure logic for joining WBRERA's party-less order register to a promoter via its cause lists; OCR keys resolved against a closed set |
| `run_cts_capture.py` | 5.3 KB | Entry | One live Property Card fetch, for a human at the keyboard |
| `build_data_coverage.py` | 21.2 KB | One-off | Regenerates `docs/RERA_Data_Coverage.xlsx` |

### 6.2 Test modules (50 files, `test_*.py` at root)

Grouped by what they protect:

| Group | Files |
|---|---|
| **Guard integrity** (stop a guard rotting) | `test_guardrails_doc.py`, `test_rules_preflight.py`, `test_claude_md_doc_review.py`, `test_claude_md_and_charter_fixes.py` |
| **Editorial policy** | `test_editorial_passes.py`, `test_clean_check_scrubber.py`, `test_process_text_sanitizer.py`, `test_flag_gap_pointers.py`, `test_charter_report_quality.py`, `test_charter_report_source_labels.py` |
| **Citations** | `test_citation_formatting.py`, `test_external_citations.py` |
| **Scoring** | `test_developer_score.py`, `test_documentation_confidence_score.py`, `test_executive_ready.py` |
| **Data correctness** | `test_facts_normalization.py`, `test_data_quality_fixes.py`, `test_finding_research.py` |
| **GST** | `test_gst_intake.py`, `test_gst_portal.py`, `test_gst_compliance.py`, `test_gst_compliance_check.py` |
| **Intake / carryover** | `test_cts_intake.py`, `test_promoter_intake.py`, `test_attach_rera_number.py`, `test_promoter_portfolio.py` |
| **Other** | `test_executive_briefing.py`, `test_usage_tracking.py` |

There is no `tests/` package, no `conftest.py`, no CI configuration.

---

## 7. Runtime view — the end-to-end pipeline

```
================================================================================
                 RERA SCRAPPER  --  END-TO-END PIPELINE
              entry: main.py::main()  |  stages 0-11, six authorities
================================================================================

  [ENTRY]
  +----------------------------------+     +---------------------------+
  |  main.py <REG_NO | project name> |     |  app.py  (Streamlit)      |
  |  --state MH|GJ|KA|TG|JH|WB|      |     |  Run Scraper tab calls    |
  |          UP|TN|HR|DL             |     |                           |
  |  --group-sweep --group-gst       |     |  states...acquire(), the  |
  |  --group-litigation              |     |  SAME method main.py does |
  |  --gstin X | --pan Y             |     |  (~160 duplicated lines   |
  |  --headed --token T              |     |   deleted; a test pins it)|
  |  --no-auto-auth --project-id N   |     +-------------+-------------+
  |  --output-dir D --verify         |                   |
  +----------------+-----------------+                   |
                   +------------------+------------------+
                                      |
                                      v
     pipeline_start_time = time.time()      <-- fed to the Charter version log
                                      |
================================================================================
  STAGE 0  --  WHICH AUTHORITY IS THIS?              states.candidate_profiles()
================================================================================
                                      v
                        +-------------------------+
                        |  --state given?         |--YES--> that profile wins,
                        +------------+------------+         no detection at all
                                     | NO
                                     v
                    match the query against EVERY registered
                    profile's reg_no_pattern
                                     |
        +----------------------------+----------------------------+
        | exactly one match          | TWO match                  | none
        v                            v                            v
   use that profile        MahaRERA and TG-RERA both        free-text name
                           accept  ^P + 11 digits           -> default MH,
                                     |                         announced
                                     v
                    *** PROBE BOTH. Whichever authority actually
                        HOLDS the project wins. The district-code
                        convention (P5.. vs P0..) only ORDERS the
                        attempts -- it is an observed convention,
                        not a published spec, and deciding on it
                        alone would answer confidently and wrong.
                        A firing heuristic is announced on stdout. ***
                                     |
                                     v
                              StateProfile
                    (name, acronym, regulator, reg-no
                     pattern, CAPABILITIES)  --> facts["state"]
                                              --> run_meta.json["state"]
                                      |
================================================================================
  STAGES 1-6  --  ACQUIRE            states.get_adapter(code).acquire(query, ctx)
                  ONE CALL: resolve, auth, scrape, documents,
                  complaint orders, promoter portfolio
================================================================================
                                      v
   +--------------------------------------------------------------------+
   |  Why one coarse call rather than six seams:                        |
   |    - Telangana cannot split resolve from auth; it CAPTCHA-gates    |
   |      the search itself                                             |
   |    - MahaRERA's 401/403 re-auth retry is MahaRERA's own            |
   |      orchestration, not a shared stage                             |
   |    - it is what let app.py stop duplicating the sequence           |
   +--------------------------------+-----------------------------------+
                                    |
        +---------------+-----------+-----------+---------------+
        v               v                       v               v
   +---------+    +---------+             +---------+     +---------+
   | MH      |    | GJ / KA |             | TG      |     | JH / WB |
   | ALL 7   |    | no auth |             | name    |     | no auth |
   | caps -- |    | no      |             | search  |     | no      |
   | the     |    | CAPTCHA |             | only,   |     | CAPTCHA |
   | ref.    |    |         |             | CAPTCHA |     |         |
   | adapter |    |         |             | gated   |     |         |
   +----+----+    +----+----+             +----+----+     +----+----+
        |              |                       |               |
        +--------------+-----------+-----------+---------------+
                                   v
   A CAPABILITY A STATE LACKS RETURNS THE EMPTY VALUE **PLUS A SENTENCE
   IN notes** -- never a stub, never a shape that reads as
   "checked, nothing found".  CAP_LOOKUP_BY_REG_NO / CAP_CATEGORY_API /
   CAP_DOCUMENTS / CAP_SEPARATE_AUTH / CAP_PROMOTER_PORTFOLIO /
   CAP_ORDERS_SEARCH / CAP_LAND_RECORDS
                                   |
                                   v
   AcquisitionResult { profile, reg_no, project_id, detail_url,
                       category_data, documents_manifest, documents_dir,
                       complaint_orders_manifest, promoter_name,
                       promoter_portfolio, raw_record, auth_source,
                       notes[] }
                                   |
        archiving stays with the CALLER via ctx.on_resolved, so the
        adapter never owns the output tree
                                   |
================================================================================
  STAGE 7  --  GST INTAKE                        [OPT-IN] [NEVER FATAL] [HUMAN]
================================================================================
                                   v
        needs --gstin or --pan.  PAN -> every GSTIN under it -> each
        filing table -> gst_filing_input.json
        ONE HUMAN CAPTCHA SOLVE PER LOOKUP, which is why it sits HERE,
        beside the other browser work, and not after stage 8: deep
        research runs unattended for minutes, so all human-attended
        work is kept contiguous.
                                   |
================================================================================
  STAGE 8  --  DEEP RESEARCH                        [NEVER FATAL] unattended
================================================================================
                                   v
        deep_research.py -- the SINGLE transport for every Claude API
        call.  Agentic web search, per-claim verification, bounded gap
        retry, shared _VerificationBudget, PIPELINE_COST_CAP_USD ceiling
        that REFUSES a call rather than starting it.
                                   |
================================================================================
  STAGE 9  --  COMPANY CHARTER                                  [NEVER FATAL]
================================================================================
                                   v
   9.1  Assemble facts (registry chains, insolvency, doc grounding).
        rules.md Section B is injected into every content call.
                                   |
   9.2  [OPT-IN] CTS land lookup     [OPT-IN] GST compliance check
                                   |
   9.2b CODE-COMPUTED GROUP PASSES -- never model-authored, each
        writing its own facts key AND rendering its own section
        (a test pins both halves: three capabilities were once built,
         tested, and consumed by nothing)
        +------------------------------------------------------------+
        | _safe_promoter_identity   PAN off the filed card            |
        | _safe_charge_movement     borrowing moved since last run    |
        | _safe_state_footprint     registered vs actually built      |
        | _safe_group_rera_sweep    [--group-sweep]                   |
        | _safe_group_gst           [--group-gst]  2 CAPTCHAs/entity  |
        | _safe_group_litigation    [--group-litigation]              |
        +------------------------------------------------------------+
        EVERY ONE REPORTS ITS OWN COVERAGE.  Absence of a finding
        never means absence of a check -- the defect this pipeline
        has met in every subsystem it has been built into.
                                   |
   9.3  Source-trust bookkeeping    9.4  Per-finding research
                                   |
   9.5  _fill_template  INTERNAL first (real facts, scores persisted),
        then EXTERNAL (from _externalized_facts_copy)
        each: preflight -> normalize -> scrub -> sanitize -> GATE
                                   |
   9.6  run_claude_md_document_review  -- STRICT: no PDF unless SHOWN
        to comply. A review that could not run is a FAILURE.
                                   |
   9.7  _convert_docx_to_pdf on both.  *** THE PDF IS THE DELIVERABLE ***
   9.8  restore scrubbed text, persist .facts.json
                                   |
================================================================================
  STAGE 10  --  RERA SUMMARY PDF                                     report.py
  STAGE 11  --  RUN SUMMARY + USAGE LOG                                main.py
================================================================================
                                   v
        report.build_pdf() -- the project report, NOT the Charter
        summarise_category_health() -- a declared absence is reported
        as an absence, never as a failed fetch
        write_usage_log() -> usage_summary.json (per-label cost ledger)
```

---

## 8. Stage-by-stage specification

> **Stages 1-6 now sit behind one call.** `states.get_adapter(code).acquire()`
> covers resolve, auth, scrape, documents, complaint orders and promoter
> portfolio, and `main.py` and `app.py` both call it -- which is what removed
> `app.py`'s ~160 duplicated lines. **Sections 8.1-8.5 below describe
> MahaRERA's adapter**, which is the reference implementation and still the
> most capable; the other five are structured the same way but declare fewer
> capabilities. See §8.0 for the seam itself.

### 8.0 The state seam

| Aspect | Detail |
|---|---|
| Two seams, not one | `StateProfile` is a frozen dataclass of pure data (name, acronym, regulator, reg-no pattern, capabilities) that travels into `facts["state"]` and `run_meta.json`. `StateAdapter` is a `typing.Protocol` with one method and never leaves acquisition. Separated so a Charter can be rendered from a hand-built facts dict with no adapter, no portal and no browser |
| Why a Protocol | The repo has no classes outside underscore-prefixed test fakes. Structural typing let `ts_rera_client.py` be wrapped without being rewritten or inheriting anything |
| Why `acquire()` is coarse | Telangana cannot split resolve from auth -- it CAPTCHA-gates the search itself -- and MahaRERA's 401/403 retry is MahaRERA-specific orchestration |
| Capabilities | `CAP_LOOKUP_BY_REG_NO`, `CAP_CATEGORY_API`, `CAP_DOCUMENTS`, `CAP_SEPARATE_AUTH`, `CAP_PROMOTER_PORTFOLIO`, `CAP_ORDERS_SEARCH`, `CAP_LAND_RECORDS`. A state omits what it lacks **and** returns the empty value plus a sentence in `notes` -- never a stub |
| Resolution | `--state` wins. Otherwise every registered pattern is matched. MahaRERA and TG-RERA share `P` + 11 digits, so **both are probed and whichever actually holds the project wins**; the district-code convention only orders the attempts, and a firing heuristic is announced on stdout |
| Ten states | MH (all seven capabilities), GJ, KA, TG, JH, WB, UP, TN, HR, DL. TG declares ZERO capabilities, which is a complete adapter rather than a stub: its public record does not display its own registration number and its search is CAPTCHA-gated by project name |
| Searchable by promoter | MH, KA, JH, TN, HR, DL. **GJ, WB, TG and UP are not**, each with a reader-facing reason in `group_sweep._CANNOT_SEARCH` -- a search that cannot run must never report a zero. UP-RERA is the sharpest case: its register is CAPTCHA-gated AND demands a district before a promoter, and an unsolved postback returns the form with an EMPTY results panel, indistinguishable from "this promoter has no projects" |
| Openable per project | All ten except TG, which has no identifier to be handed. Pinned by `test_project_summary.py`: a state either implements `fetch_project_summary` or has a written reason in `group_sweep._CANNOT_OPEN` |

### 8.1 Stage 1 — Resolve (MahaRERA: `resolver.py`)

| Aspect | Detail |
|---|---|
| Input | `P\d{11}` registration number, or free-text project name |
| Output | `(project_id, detail_url, reg_no)` |
| Transport | Playwright Chromium, viewport 1920×1080, `headless = not --headed` |
| Endpoint | `https://maharera.maharashtra.gov.in/` — Drupal, **no login, no CAPTCHA** |
| Timeout | `SEARCH_TIMEOUT_MS = 30000`; fixed 300 ms settle after a tab click |
| Retries | None |
| Failure | Any exception waiting for the first result link → `return []`, treated as "no results" |

**Key design decisions.**

1. **Read the `href`, never click it.** Clicking "View Details" fires a JS `confirm()` and lands on the CAPTCHA-gated page. The href already contains `/public/project/view/<id>`, so id resolution is 100 % unauthenticated.
2. **Bounded ancestor walk.** `_CARD_ANCESTOR_WALK_JS` climbs at most 8 parent levels looking for `innerText` matching `/P\d{11}/`, then falls back to `closest('div')`. Bounded so it cannot run away to `document.body`.
3. **Selector scoping.** MahaRERA reuses `id="edit-submit"` across four hidden per-tab forms; the Projects submit is therefore scoped as `#projects-form input[value='Search']` and the Promoters submit is the ordinal `#edit-submit--3`.
4. **`raw_text` is always retained** on `ProjectCandidate` so nothing is silently lost when a positional regex misses.
5. **Positional parsing is brittle by admission** — reg-no line, project name on the next line, promoter on the line after.
6. `search_promoters()` returns a promoter's entire registered portfolio in the same card format — this is what makes Stage 6 possible.

### 8.2 Stage 2 — Auth (MahaRERA: `session_auth.py`, `token_cache.py`)

The four `auth_source` values are `explicit`, `cached`, `fresh_browser`, `none`, and **the pipeline runs to completion in all four** — the last simply fetches only `projects` and `complaints`.

```
   TOKEN LIFECYCLE
   ---------------

   --token X ---------------------------------> [explicit]  no I/O
                                                            
   .guest_token_cache.json                                  
   {"token": "...", "saved_at": "<ISO>"}                    
        |                                                   
        | minutes_left() = max(0, 90 - age_minutes)         
        v                                                   
   > 0 ? ---YES---> [cached]                                
        |                                                   
        NO                                                  
        v                                                   
   acquire_token_via_browser()                              
        |  visible Chromium, project detail page            
        |  fast path: read token BEFORE prompting           
        |  poll every 2.0 s, budget 300 s                   
        v                                                   
   sessionStorage.getItem('tokens') -> JSON.parse -> .accessToken
        |                                                   
        +--success--> token_cache.save() --> [fresh_browser]
        |                                                   
        +--timeout/closed--> [WARN] --------> [none]        
```

**Notes.**
- `MAX_AGE_MINUTES = 90` against a real token lifetime of roughly 100 minutes — a deliberate 10-minute safety margin so a token cannot expire mid-run.
- Freshness is measured against local wall-clock `saved_at`, **not** a decoded JWT `exp`. Clock changes skew it.
- The token is stored **plaintext with default file permissions**. Mitigated by it being a short-lived guest token for public data (§22).
- `elapsed` in the poll loop is accumulated arithmetically, not wall-clock, so token-read latency is not charged against the 300 s budget; real wall time can exceed it.
- A corrupt cache file is silently treated as absent (`JSONDecodeError` → `None`). There is no file locking or atomic write.
- **Nothing solves the CAPTCHA.** Stated twice in the module docstring; an explicit design and ethics boundary.

### 8.3 Stage 3 — Archive (`run_archive.py`)

The call-order contract is load-bearing and is documented in the module docstring:

```
   load_prior_research()   -- BEFORE archiving (archiving moves the file away)
        |
   archive_previous_run()  -- shutil.MOVE, so no disk doubling and
        |                     output/<reg>/ is guaranteed empty afterwards
        v
   load_prior_manifest()   -- AFTER archiving (reads FROM the archive)
   prior_documents_dir()
   load_prior_complaint_orders_manifest()
   prior_complaint_orders_dir()
```

Charters live *outside* `output/<reg>/`, in `output/company_charters/`, so they were historically never archived — only overwritten. `archive_previous_charter()` closes that gap, and is what makes field-level change detection (`_diff_mortgage_lender`) possible. It matches by `_<reg_no>` **suffix**, not exact filename, because the project-name segment can change between runs when a RERA record is renamed.

Timestamp format `%Y%m%d_%H%M%S`, with `_2`, `_3`… suffixes on same-second collision. Not safe against two concurrent runs of the same `reg_no` — the collision check is not atomic.

### 8.4 Stage 4 — Scrape (MahaRERA: `api_client.py`)

The nine category endpoints, and their trust status as recorded in `config.CATEGORY_ENDPOINTS`:

| Category | Path | Status | Auth |
|---|---|---|---|
| `projects` | `…/projectregistartion/getProjectGeneralDetailsByProjectId` | **confirmed** | none |
| `complaints` | `…/projectregistartion/getComplaintDetailsByProjectId` | observed | none |
| `professionals` | `…/projectregistartion/getProjectProfessionalByType` | observed | token |
| `partners` | `…/projectregistartion/getProjectAndAssociatedPromoterDetails` | observed | token |
| `spocs` | `…/projectregistartion/getProjectSpocMapping` | observed | token |
| `sro_details` | `…/projectregistartion/getProjectSroDetails` | observed | token |
| `documents` | `…/projectregistartion/getUploadedDocuments` | observed | token |
| `past_experiences` | `/api/maha-rera-promoter-management-service/promoter/getPromoterPastExpProject` | observed | token |
| `appeals` | `/api/maha-rera-appeal-service/reatappeal/getAppealDetailsPublicView` | observed | token |

> The misspelling `projectregistartion` is **MahaRERA's own** and must be preserved verbatim.

**`confirmed` vs `observed` is a first-class, machine-readable concept.** `confirmed` means the exact payload/response shape was individually re-verified. `observed` means the endpoint name was taken from real successful traffic on the site's own page but was not re-verified. The distinction is surfaced in `fetch_all_categories`' warning lines, in the run summary, in `discover.verify_endpoints`' report table, and on the summary PDF cover as a **Data reliability note**.

**Five engineering accommodations worth citing:**

1. **Envelope normalisation** — MahaRERA returns HTTP 200 with pure bookkeeping (`{"message":"ERROR","status":"0","responseObject":null}`). `_ENVELOPE_ONLY_KEYS` subset detection collapses these to `{}` so they cannot render as a fake "message/status" data row.
2. **The `past_experiences` 400** — that endpoint keys off `userProfileId`, not `projectId`, and rejects the generic body with HTTP 400. `_past_experiences_body` reuses the id from the already-fetched `projects` response, or re-fetches it (cheap, no auth). This is the pipeline's one true ordering dependency.
3. **Documents are POST-downloaded** to the DMS service with a bearer token, not GET from a static URL. Recovered from observed working traffic.
4. **Content-Type is a lie** — raw PDF bytes have been observed labelled `application/json`. `_sniff_and_save` trusts JSON only if the body actually parses, follows one level of nested `url`/`downloadUrl`/`signedUrl`/`data`, and otherwise streams raw bytes in 8192-byte chunks. An unrecognised parsed JSON returns `False` and writes nothing rather than a bogus file.
5. **Filename collisions are real** — two genuinely different documents (different `document_id`) can share a filename. `_dedupe_filename` appends ` (2)`, ` (3)` before the extension.

### 8.5 Stage 5–7

Covered in the diagram in §7. Two asymmetries worth recording explicitly:

- **Document download is parallel (8 workers); complaint-order download is serial.** Deliberate: complaint counts are small, document counts can exceed 100.
- **GST intake sits between the portfolio build and deep research**, not after it, because deep research runs unattended for minutes while GST needs a human at the keyboard. All human-attended browser work is contiguous.

### 8.7 Stage 2b — the code-computed group passes

Six passes that are computed, never model-authored, each writing its own
facts key and rendering its own Charter section. A stage must do **both**:
`test_computed_facts_reach_the_page.py` exists because three capabilities
were once built, tested, and consumed by nothing.

| Pass | Opt-in | What it costs | What it must never do |
|---|---|---|---|
| `_safe_promoter_identity` | no | OCR over the filed PAN card | Treat an unverified candidate as a PAN |
| `_safe_charge_movement` | no | One mirror fetch | Read a vanished charge as a satisfied one |
| `_safe_state_footprint` | no | none (derived) | Imply a single-state group |
| `_safe_group_rera_sweep` | `--group-sweep` | Several portals in sequence | Report "nothing found" for an authority that was never asked |
| `_safe_group_gst` | `--group-gst` | **Two human CAPTCHA solves per entity** | Count an entity with no PAN as compliant |
| `_safe_group_litigation` | `--group-litigation` | HTTP per name, plus register fetches | Render a name match as this promoter's litigation |

Every one reports its own COVERAGE. The recurring failure this guards
against, in every subsystem it has appeared in, is a check that could not
run being read downstream as a check that found nothing.

### 8.6 Stage 10 — RERA summary PDF (`report.py`)

ReportLab only — deliberately no native dependencies, "deploys cleanly if this is ever hosted as a service". A4, 2 cm margins.

Section order:

```
  1  Cover ---------- title, reg no, internal project id, project name,
                      promoter, generated timestamp, "Data session:" label,
                      + Data reliability note (every non-"confirmed" category)
  2  Project Details ------------------- from `projects`
  3  Company Charter Highlights -------- Documentation Confidence Score
     (from charter_facts only,           Credit Rating (ICRA + Infomerics)
      nothing recomputed or refetched)   Insolvency Check (IBBI)
                                         Appeal-Level Judgments Found
                                         Mortgage Lender
  4  Promoter Profile ------------------ totals, limitations[], per-project
                                         table (FLAGGED when is_lapsed),
                                         Promoter External Profile research
  5  Market Research ------------------- Macro, then Micro (Locality)
  6  Remaining CATEGORY_ORDER ---------- SPOCs, Professionals, Partners,
                                         Past Experiences, SRO Details,
                                         Documents, Complaints, Appeals
```

Defensive details: `_esc()` XML-escapes every value (a bare `&` aborts a ReportLab build); `_LONG_VALUE_THRESHOLD = 300` pulls long values out of fixed-width cells to avoid `LayoutError`; `_MAX_TABLE_COLS = 6`. Footer on every page: `MahaRERA report -- <reg_no>` / `Page <n>`.

---

## 9. The Company Charter subsystem

`company_charter.py` is 10,182 lines, 226 module-level functions and one class. It is effectively **six layers in a single file**. This section decomposes it.

### 9.1 Internal architecture

```
+=============================================================================+
|                 company_charter.py  --  INTERNAL LAYERS                     |
+=============================================================================+

  (1) RULES / PROMPT ASSEMBLY                              lines 106-163
  +-----------------------------------------------------------------------+
  | _read_rules_section(marker)  <-- regex over rules.md, raises on        |
  |                                  missing or empty section             |
  | _coding_time_notes()      Section A  -- NEVER transmitted             |
  | _common_content_rules()   Section B  -- every content call            |
  | _external_citation_rule() Section C  -- external calls only           |
  | _charter_system_blocks(external=, extra=)                             |
  |     -> [B as cache_control ephemeral prefix] (+ C) (+ extra)          |
  +-----------------------------------------------------------------------+
                                    |
  (2) INGESTION + FACTS PASS                               lines 166-716
  +-----------------------------------------------------------------------+
  | _select_documents_for_extraction -> (extracted_text_by_label,         |
  |                                      all_labels_with_status)          |
  | _extract_document_text  -- PyMuPDF, falls back to Tesseract OCR;      |
  |                            a missing Tesseract degrades to a          |
  |                            "[OCR unavailable]" marker, never fatal    |
  | summarize_complaint_outcomes . summarize_professionals                |
  | _normalise_entity_name . _is_llp . _is_partnership . _role_word       |
  |                                                                       |
  | _CHARTER_FACTS_SCHEMA (JSON Schema) -> _CHARTER_JSON_SHAPE            |
  |                                     -> embedded in _SYSTEM_PROMPT     |
  | _run_charter_pass()  label="charter_pass"                             |
  +-----------------------------------------------------------------------+
                                    |
  (3) EXTERNAL ENRICHMENT  (5-way parallel)                lines 1131-3078
  +-----------------------------------------------------------------------+
  |  credit rating        ICRA + Infomerics, both, side by side           |
  |  insolvency           IBBI public search  -> _classify_ibbi_hit       |
  |  company profile      _run_mca_profile_chain  (see 9.3)               |
  |  group companies      ZaubaCorp ONLY, corroborated by                 |
  |                       InstaFinancials directorship counts             |
  |  judgments            MahaRERA /orders-judgements + retry             |
  |                       -> cross_reference_appeals                      |
  |                       -> _save_appeal_judgment_pdfs                   |
  |  CTS land record      run_cts_land_lookup  [opt-in, human]            |
  |  GST compliance       run_gst_compliance_check [opt-in]               |
  |  maps distances       _refine_distances_with_maps [env-gated]         |
  +-----------------------------------------------------------------------+
                                    |
  (4) EDITORIAL / NORMALISATION                            lines 4485-5310
  +-----------------------------------------------------------------------+
  | _normalize_misfiled_facts  -> _relocate_merge_orders                  |
  |                               _point_rera_landowner_at_identity_table |
  |                               (NOT reversed -- a relocation loses     |
  |                                nothing, the corrected record is       |
  |                                the better one)                        |
  | run_editorial_passes  -> 3 model passes, cached on `facts`            |
  | run_finding_research  -> per-finding deep research, capped at 8       |
  | _scrub_clean_checks  /  _restore_clean_checks     (REVERSIBLE)        |
  | _sanitize_process_gaps  /  _pre_sanitize_gaps     (REVERSIBLE)        |
  +-----------------------------------------------------------------------+
                                    |
  (5) SCORING                                              lines 7002-8263
  +-----------------------------------------------------------------------+
  | _classify_flags -> {imminent[], structural[], monitor[]}              |
  |                    stable 1-based gap_number per gaps[i]              |
  | _compute_developer_score        3 buckets, 9 sub-metrics, NO renorm   |
  | _compute_documentation_confidence_score  8 criteria, RENORMALISES     |
  | verify_cross_corroboration  (runs LAST so nothing looks single-       |
  |                              sourced mid-assembly)                    |
  | _record_source_hits_and_promote -> source_trust_registry.json         |
  +-----------------------------------------------------------------------+
                                    |
  (6) RENDERING  -- _fill_template, ~733 lines                lines 5311-6042
  +-----------------------------------------------------------------------+
  |   INTERNAL first (real facts, computes the persisted scores)          |
  |   EXTERNAL second (_externalized_facts_copy -- deep copy)             |
  |   see section 18                                                      |
  +-----------------------------------------------------------------------+
                                    |
  (7) GATES                                    lines 4191-4332, 4943-5155
  +-----------------------------------------------------------------------+
  |   _preflight_rules  ->  _verify_external_document_quality             |
  |                     ->  run_claude_md_document_review  [BLOCKS PDF]   |
  |   see section 13                                                      |
  +-----------------------------------------------------------------------+
```

### 9.2 `run_company_charter` — the 32-step orchestration

```python
def run_company_charter(
    reg_no, category_data, documents_manifest, documents_dir,
    research_data=None, output_dir=config.OUTPUT_ROOT,
    complaint_orders_manifest=None, complaint_orders_dir=None,
    reviews=None, review_source_label=None, promoter_portfolio=None,
    pre_built_facts=None, pipeline_start_time=None,
) -> tuple[str, dict]        # (internal PDF path or .docx fallback, facts)
```

```
  01  _charter_start_time = time.time(); assert TEMPLATE_PATH exists
  02  _select_documents_for_extraction() -> extracted_docs, doc_library_status
  03  FACTS: pre_built_facts, or _run_charter_pass(user_prompt)
         user_prompt = reg_no + projects JSON + partners JSON
                     + extracted doc text (truncated to _MAX_TOTAL_DOC_CHARS)
                     + full document-library name list
                     + research_context from deep_research.RESEARCH_KEYS
  04  _verify_material_claims(facts)         label="material_claim_verify"
  05  _check_document_grounding(facts, ...)  label="document_grounding_verify"
                                             (+ optional Grok fallback)
  06  facts["document_library"]   = doc_library_status   <- CODE, overwrites model
  07  facts["professional_team"]  = summarize_professionals(category_data)
  08  facts["promoter_portfolio"] = promoter_portfolio   <- CODE
  09  facts["complaint_outcomes_summary"] = summarize_complaint_outcomes(...)
  10  extract CIN / LLPIN; derive identifier_label; project name for judgments
  11  _load_promoter_carryover()  -- reuse a prior CIN-only intake if attached
  12  +------------------------- 5-WAY PARALLEL -------------------------+
      | ThreadPoolExecutor(max_workers=5)                                |
      |  _safe_credit_rating   _safe_ibbi_check   _safe_company_profile  |
      |  _safe_group_companies _safe_judgments_search                    |
      |  (first four SKIPPED entirely when a promoter carryover was used)|
      +------------------------------------------------------------------+
  13  fold results in sequentially; append one `sources` entry per hit
         roster_conflicts -> facts["gaps"]   (renders in BOTH variants)
  14  run_review_authenticity_triage(reviews, facts)   [if reviews.json present]
  15  run_cts_land_lookup(facts, reg_no, output_dir)        [opt-in]
  16  run_gst_compliance_check(facts, reg_no, output_dir)   [opt-in]
  17  _refine_distances_with_maps(facts, origin)  [COMPANY_CHARTER_USE_MAPS_SCRAPE=1]
  18  verify_cross_corroboration(facts)          <- deliberately LAST
  19  mkdir output/company_charters/
  20  archive_previous_charter -> load_prior_charter_facts
      -> _diff_mortgage_lender -> facts["mortgage_lender_history_note"]
  21  facts["source_promotion_notes"] = _record_source_hits_and_promote(...)
  22  _normalize_misfiled_facts(facts)
  23  run_editorial_passes(facts)      [try/except -> deterministic fallback]
  24  run_finding_research(facts)      [try/except -> original text kept]
  25  read deep_research.usage_summary()["total"]; compute elapsed
  26  _fill_template(..., doc_variant="internal", elapsed=, cost=, calls=)
  27  _fill_template(..., doc_variant="external")
  28  run_claude_md_document_review(...)   *** MAY RAISE CharterComplianceError
                                               BEFORE ANY PDF EXISTS ***
  29  _convert_docx_to_pdf(internal) ; _convert_docx_to_pdf(external)
  30  _restore_clean_checks(...) ; restore facts["gaps"]
  31  write Company_Charter_<Name>_<REG>.facts.json
  32  return (internal_pdf_path or out_path, facts)
```

> **Internal renders first, and this is not arbitrary.** Internal renders on the real `facts` dict and computes the scores that get persisted; External renders from a deep copy. Reversing the order would persist externalised values.

### 9.3 The MCA-mirror redundancy chain

This is the only place in the system where three independent sources are **cross-checked against each other** rather than merely falling back on failure.

```
   _run_mca_profile_chain(cin, promoter_name)
   +--------------------------------------------------------------------+
   |                                                                     |
   |   ZaubaCorp -----------+                                            |
   |   (CIN redirect trick) |                                            |
   |                        |                                            |
   |   Tofler --------------+---> _merge_director_rosters()              |
   |   (Playwright: type    |         |                                  |
   |    the name in a real  |         +-- rosters MATCH                  |
   |    browser, verify CIN)|         |     -> merge silently            |
   |                        |         |                                  |
   |   InstaFinancials -----+         +-- rosters DISAGREE               |
   |   (ASP.NET web service,|               -> roster_conflicts          |
   |    CIN search direct)  |               -> facts["gaps"]             |
   |                        |               -> renders in BOTH variants  |
   +--------------------------------------------------------------------+

   REAL CASE HIT: ZaubaCorp reported director "Cooper"; InstaFinancials
   reported "Sahaya", for the same CIN. BOTH were surfaced. Neither was
   silently picked. This is the architectural pattern the whole system
   is built around: two contradictory facts beat one invented one.

   Group companies come from ZaubaCorp ONLY -- Tofler and InstaFinancials
   do not expose that data freely -- and are corroborated against
   InstaFinancials directorship counts (_corroborate_group_companies).
```

### 9.4 Source-trust registry (cross-run, persistent)

```
   facts["sources"]  ->  _record_source_hits_and_promote(facts, reg_no)
        |
        |  _extract_open_web_domain()  -- excludes MahaRERA, MCA mirrors,
        |                                 rating agencies, IBBI, Maps
        |                                 (already trusted by design)
        v
   tally each open-web domain's hit count across ALL runs
   (99acres, NoBroker, press, social, Wikipedia, ...)
        |
        |  5th DISTINCT project  ->  AUTO-PROMOTE
        v
   source_trust_registry.json   (repository root, accumulates forever)
        |
        v
   "Source Trust Registry Updates (this run)"  ->  INTERNAL document only
```

This is the system's only piece of cross-engagement learned state. It is committed to the repository (1,565 bytes at time of writing), which makes promotions reviewable in version control.

---

## 10. Data architecture

### 10.1 Filesystem layout

```
RERA_Scrapper/
├── *.py                                    29 application modules (flat)
├── test_*.py                               28 test modules (flat)
├── CLAUDE.md  rules.md  guardrails.md  README.md  CHARTER_RESHAPE_SPEC.md
├── requirements.txt                        the ONLY dependency manifest
├── source_trust_registry.json              *** cross-run learned state ***
├── .guest_token_cache.json                 *** short-lived secret ***
├── RERA_Executive_Tracker.xlsx             operator-facing tracker
├── tessdata/
│   ├── eng.traineddata                     4.1 MB
│   └── mar.traineddata                     3.2 MB   (Marathi, for Maha Bhulekh)
├── .claude/
│   ├── launch.json  settings.local.json
│   └── skills/maharera-report/SKILL.md
├── docs/                                   11 .docx design artefacts, no source
│
└── output/                                 *** GITIGNORED ***
    ├── usage_log.jsonl                     one rollup line per run, forever
    ├── _history/<REG_NO>/<YYYYmmdd_HHMMSS>/    archived prior runs
    ├── _pending/<CIN | district_village_cts>/  pre-RERA intake cases
    │   ├── promoter_profile.json
    │   ├── land_record.json
    │   └── property_card_screenshot.png
    │
    ├── <REG_NO>/
    │   ├── raw/                            9 category JSONs, one per category
    │   │   ├── projects.json  partners.json  professionals.json
    │   │   ├── spocs.json  sro_details.json  past_experiences.json
    │   │   └── documents.json  complaints.json  appeals.json
    │   ├── documents/                      downloaded project documents (PDF)
    │   ├── complaint_orders/               complaint order PDFs
    │   ├── appeal_judgments/               appeal judgment PDFs
    │   ├── promoter/portfolio.json
    │   ├── research/deep_research.json
    │   ├── documents_manifest.json         complaint_orders_manifest.json
    │   ├── run_meta.json                   usage_summary.json
    │   ├── gst_portal_raw/                 per-lookup .png + .json
    │   ├── gst_filing_input.json           <- feeds run_gst_compliance_check
    │   ├── cts_office_candidates.json      cts_village_candidates.json
    │   ├── cts_number_candidates.json      cts_lookup_input.json
    │   ├── *_carryover.json                promoter_profile / land_record
    │   ├── reviews.json                    (human-supplied, optional)
    │   └── <REG_NO>_summary.pdf            <- Stage 10 deliverable
    │
    └── company_charters/                   *** THE PRIMARY DELIVERABLE DIR ***
        ├── Company_Charter_TEMPLATE_Integrow_Branded.docx   <- the .docx template
        ├── Company_Charter_<Name>_<REG>_Internal.docx / .pdf
        ├── Company_Charter_<Name>_<REG>_External.docx / .pdf
        ├── Company_Charter_<Name>_<REG>.facts.json    <- THE FULL RECORD
        ├── Company_Charter_<REG>_claude_md_review.json
        ├── _history/<REG_NO>/<timestamp>/
        ├── _verify/                        cheap no-API render loop output
        └── CHARTER_RESHAPE_CHANGE_LOG.md
```

> **`rules.md` §A: Charter files are only ever saved to `output/company_charters/`.** Never to a Desktop folder or any other location.

> **The template lives under gitignored `output/`.** It must be backed up with a timestamped copy before any change touching its structure.

### 10.2 Artefact lifecycle

```
   RUN N-1                          RUN N
   -------                          -----
   output/<reg>/  ------------------> output/_history/<reg>/<ts>/
        |                                    |
        | documents_manifest.json            | prior_manifest
        | + documents/                       | + prior_documents_dir
        |                                    v
        |                          reuse by (document_id, source_filename)
        |                          -> shutil.copy2 -> status "reused"
        |                          ("reused" is itself reusable -> chains)
        |
   research/deep_research.json ---> prior_research
        |                           within RESEARCH_REUSE_WINDOW_HOURS (24)?
        |                           -> carry confirmed sources forward,
        |                              re-attempt ONLY open gaps
        |
   company_charters/*.facts.json -> output/company_charters/_history/...
        |                           -> _diff_mortgage_lender vs current
        |                           -> facts["mortgage_lender_history_note"]
        v
   source_trust_registry.json  ----> accumulates ACROSS ALL PROJECTS, forever
   output/usage_log.jsonl      ----> append-only cost ledger
```

### 10.3 The two records that deliberately differ

```
   facts (in memory)
        |
        +--> _scrub_clean_checks()    stash _pre_scrub_narrative
        +--> _sanitize_process_gaps() stash _pre_sanitize_gaps
        |
        v
   RENDERED DOCUMENT  ....... the REDACTED view
        |                     (clean checks deleted, process failures
        |                      sanitised, file paths and module names gone)
        v
   _restore_clean_checks()  +  restore facts["gaps"]
        |
        v
   .facts.json  ............. the COMPLETE record
                             (what was checked stays in the record even
                              when it leaves the page)
```

**Consequence for consumers:** anything reading `.facts.json` — `charter_report.py`, `executive_briefing.py`, `finalize_report.py`, `cts_resolve.py` — gets the **unscrubbed** text. Without the restore step, every run would quietly hollow out the file the next run reads.

---

## 11. The facts data model

`_CHARTER_FACTS_SCHEMA` is a JSON Schema dict serialised into `_SYSTEM_PROMPT`. It has **28 required top-level keys** (everything except `developer_track_record`, optional by design).

### 11.1 Building blocks

```python
_FIELD_WITH_SOURCE = {"type": "object",
    "properties": {"value": {"type": "string"}, "source": {"type": "string"}},
    "required": ["value", "source"]}
_PLAIN_FIELD = {"type": "string"}
_INT_FIELD   = {"type": "integer"}
```

`_FIELD_WITH_SOURCE` is the schema-level expression of goal **G1**: a sourced field cannot exist without its source.

### 11.2 Model-authored keys

| Key | Shape |
|---|---|
| `methodology_note`, `executive_summary` | plain |
| `land_identification` | 7 × sourced: `survey_cts_plot_numbers`, `village_locality`, `mandal_taluka_district`, `pincode`, `total_gross_area`, `area_affected`, `net_area` (+ `land_assembly` added at runtime) |
| `corporate_identity` | 9 × sourced: `promoter_name`, `organization_type`, `cin_llpin`, `registered_office_main`, `registered_office_board_resolution`, `registered_office_planning_stage`, `authorized_signatory`, `partners_directors`, `landowner_investor` |
| `address_discrepancy_note`, `corporate_registry_cross_check` | plain |
| `litigation_status` | sourced (top-level, not nested) |
| `location_coordinates_note` | plain |
| `neighbourhood` | `east`, `west`, `north`, `south` |
| `distances` | array of `{landmark, distance_time, route_note}` |
| `connectivity` | `road`, `rail`, `metro`, `air` |
| `social_infrastructure`, `fsi_governing_framework`, `fsi_interpretation` | plain |
| `fsi_metrics` | `net_land_area`, `approved_bua`, `sanctioned_bua`, `mortgage_area`, `implied_fsi` + **`mortgage_lender` (sourced)** |
| `rules_statutory` | `governing_act`, `planning_approval_sequence`, `allotment_mechanics` |
| `rera_compliance` | `registration_summary`, `collection_account`, `escrow_subaccounts`, `litigations_complaints_appeals`, `statutory_declaration`, `construction_progress` |
| `local_planning` | `authority_of_record`, `project_type`, `professionals_of_record` |
| `micro_market_overview`, `area_intelligence_trend` | plain |
| `comparables` | array of `{project, configuration, pricing, source, distance_km}` — `distance_km` is a bare number |
| `rera_core_fields` | 13 plain + `total_complaints_count`, `total_appeals_count`, `units_total`, `units_sold` (int), `completion_date_current`, `completion_date_original` |
| `developer_track_record` | `years_in_industry` (int) + `years_in_industry_basis` — **omit entirely if unconfirmable** |
| `unit_summary_note` | plain |
| `blocks` | array of `{block_wing, floors, config, units_counted, note}` |
| `documents_reviewed_note`, `documents_absent_note` | plain |
| `gaps` | array of strings |
| `sources` | array of `{label, ref, topic, published_date, accessed_date}` |

**Enumerated `topic` values** — these drive both corroboration counting and automatic inline citation resolution:

`land_title` · `corporate_identity` · `litigation` · `pricing` · `market_trend` · `distance` · `project_registration` · `legal_documents` · `reputation` · `credit_rating` · `insolvency_status` · `company_profile` · `group_companies`

### 11.3 Code-computed keys (never model-authored)

These are attached verbatim after the LLM pass and **overwrite** anything the model produced for the same key:

`document_library` · `professional_team` · `promoter_portfolio` · `complaint_outcomes_summary` · `credit_rating_check` · `ibbi_insolvency_check` · `company_profile_check` · `group_companies_check` · `appeal_judgments_found` · `review_authenticity_triage` · `cts_land_record_check` · `cts_mismatch_note` · `gst_compliance_check` · `mortgage_lender_history_note` · `source_promotion_notes` · `developer_score` · `documentation_confidence_score` · `finding_research` · `claude_md_review` · `_verification_stats`

### 11.4 Ephemeral keys (stripped or restored before persist)

`_doc_variant` · `_citation_registry` · `_clean_check_verdicts` · `_claim_source_matches` · `_flag_headlines` · `_editorial_passes` · `_pre_scrub_narrative` · `_pre_sanitize_gaps`

> **Invariant, stated in three modules:** `facts["developer_score"]` is an **output** of a prior run, never a valid **input**. `charter_report.py` and `executive_briefing.py` both recompute `_classify_flags` and `_compute_developer_score` rather than trusting the persisted value.

---

## 12. Governance architecture — how `rules.md` is enforced

Rendering is pure code, so most of `rules.md` cannot be enforced by a model reading it. Four mechanisms, in execution order:

```
+=============================================================================+
|            FOUR ENFORCEMENT MECHANISMS, IN ORDER OF EXECUTION               |
+=============================================================================+

  (1) PREFLIGHT                       _preflight_rules(doc_variant)
      +-------------------------------------------------------------------+
      | The FIRST thing _fill_template does, before a paragraph is written.|
      |   a) every required section loads and is non-empty                 |
      |      (A + B always; C when external)                               |
      |   b) B -- and C when external -- contain NO em dash and NO " -- "  |
      |   c) A is loaded but NOT RETURNED, so preflight can never become   |
      |      a route by which coding-time guidance reaches an API call     |
      | Failure -> RuntimeError. Nothing is written.                       |
      +-------------------------------------------------------------------+
                                    |
  (2) PROMPT                        _charter_system_blocks()
      +-------------------------------------------------------------------+
      |  Section B -> a separate cache_control:{"type":"ephemeral"} block  |
      |               on EVERY content-generating or -checking call        |
      |  Section C -> appended after B, external-facing calls ONLY         |
      |  Section A -> NEVER                                                |
      |                                                                    |
      |  Note: the External document is generated ENTIRELY BY CODE.        |
      |  Section C therefore reaches only the advisory citation JUDGE      |
      |  (_llm_verify_citation_completeness), never a call that writes     |
      |  External content. Any External behaviour must be implemented      |
      |  in code -- editing the rules alone will not move it.              |
      +-------------------------------------------------------------------+
                                    |
  (3) DETERMINISTIC PASSES
      +-------------------------------------------------------------------+
      |  _normalize_misfiled_facts   -- corrections FIRST                  |
      |  _scrub_clean_checks         -- scrub SECOND (order matters)       |
      |  _sanitize_process_gaps                                            |
      |  _remove_empty_section_headings . _remove_gap_rows                 |
      |  _remove_fully_empty_rows . _consolidate_bullet_clauses            |
      |  _fix_bullet_capitalization . _expand_jargon_first_use             |
      |  _strip_inline_cin_din . _externalize_prose (external only)        |
      |  _center_all_table_cells (length-thresholded) . _apply_justify     |
      |  _apply_table_pagination (cantSplit + tblHeader)                   |
      +-------------------------------------------------------------------+
                                    |
  (4) GATES                                     see section 13
      +-------------------------------------------------------------------+
      |  _verify_external_document_quality  -> blocks the External save    |
      |  run_claude_md_document_review      -> blocks the PDF              |
      +-------------------------------------------------------------------+
```

### 12.1 The core editorial rules (Section B), and where each is enforced

| Rule | Prompt | Deterministic pass | Gate |
|---|---|---|---|
| A clean check produces no sentence | ✓ | `_scrub_clean_checks` / `_is_clean_check_clause` | `_is_cited_absence` |
| An empty section keeps its heading + "Nothing found." | ✓ | `_remove_empty_section_headings` (preserves the heading) | review |
| Findings first inside anything that survives | ✓ | — | review |
| Absence as evidence (3 carve-outs) | ✓ | `_is_carved_out` / `_REVIEW_CARVE_OUT_PATTERNS` | review |
| A gap is not an absence | ✓ | `_is_cited_absence` returns False for `Gap N.` | ✓ |
| Deep research on every finding | ✓ | `run_finding_research` | — |
| A live right is a finding | ✓ | `_merged_mortgage_value` | — |
| Ruled-out items survive, in the right section | ✓ | `_relocate_merge_orders` | — |
| Say it once | ✓ | `_point_rera_landowner_at_identity_table` | review |
| Flags summarise, Gaps explain | ✓ | `_flag_headline` + `(Gap N)` pointers | review |
| Internal keeps process failures, External does not | ✓ | `_sanitize_process_text`, `_externalize_prose` | ✓ |
| No jargon, plain language, keep key terms | ✓ | `_expand_jargon_first_use` | review |
| Gloss raw status strings | ✓ | — | review |
| Bullets and grammar | ✓ | `_consolidate_bullet_clauses`, `_fix_bullet_capitalization` | ✓ (numbering) |
| CIN/DIN placement | ✓ | `_strip_inline_cin_din` | carve-out in review |
| Empty table/row removal | ✓ | `_remove_gap_rows`, `_remove_fully_empty_rows` | — |
| Numbered citations `[N]` (Section C) | ✓ | `_register_citation`, `_insert_marker_at_clause_end` | ✓ (parens, numbering) |
| Marker placement at clause end | ✓ | `_insert_marker_at_clause_end` | advisory judge |
| Descriptive source labels | ✓ | `_external_source_label`, `_generic_one_label` | ✓ |

---

## 13. Guardrail architecture

### 13.1 The gate chain

```
   facts assembled
        |
        v
   +-------------------------------------------------------------------+
   |  GATE 1  --  _preflight_rules(doc_variant)                        |
   |  Stops: generation, BEFORE a paragraph is written                 |
   |  Checks: rules.md missing . section marker broken . section empty |
   |          . B or C carrying an em dash or double-hyphen dash       |
   |  Raises: RuntimeError                                             |
   +-------------------------------------------------------------------+
        |
        v  render Internal .docx   (no external gate applies)
        v  render External .docx
        |
        v
   +-------------------------------------------------------------------+
   |  GATE 2  --  _verify_external_document_quality(docx_path)         |
   |  Stops: the External .docx SAVE                                   |
   |  Re-opens the just-saved file. ELEVEN checks:                     |
   |                                                                    |
   |    per paragraph (body AND every table cell, recursively):        |
   |      1. " -- "  hyphen-pair dash                                  |
   |      2. em dash                                                    |
   |      3. leftover Internal Document Library status text            |
   |         (_EXTERNAL_DOC_LIBRARY_LEFTOVER_RE)                       |
   |      4. *** a citation marker attached to an absence ***          |
   |         (_is_cited_absence) -- the specific defect the whole      |
   |         clean-check rule exists to prevent                        |
   |    per run:                                                        |
   |      5. italic placeholder styling (carve-out: the Standing Gap   |
   |         paragraph is intentionally red C00000 + italic)           |
   |      6. run colour outside _EXTERNAL_ALLOWED_RUN_COLORS           |
   |    per Sources entry (body paragraphs matching ^\[(\d+)\]\s+\S):  |
   |      7. unbalanced citation parentheses                           |
   |      8. lost bullet numbering (pPr.numPr is None)                 |
   |    per table:                                                      |
   |      9. Weight column surviving in the Developer Score table      |
   |     10. Weight column surviving in Documentation Confidence       |
   |    numbering part:                                                 |
   |     11. abstractNum id=2 level 0 missing its hanging-indent fix   |
   |                                                                    |
   |  Raises: RuntimeError listing every violation                     |
   +-------------------------------------------------------------------+
        |
        v
   +-------------------------------------------------------------------+
   |  GATE 3  --  run_claude_md_document_review(...)   *** BLOCKS PDF **|
   |  Stops: THE PDF -- the actual deliverable                         |
   |  Per variant: _rendered_document_text(path, limit=60000)          |
   |    -> deep_research.review_document_against_rules(text, rules, v)  |
   |    rules = Section B (internal) | Section B + C (external)         |
   |    *** Section A is NEVER sent; test_claude_md_doc_review.py       |
   |        asserts this, and now asserts the marker is PRESENT before  |
   |        asserting it is ABSENT, after the test was found passing    |
   |        vacuously ***                                               |
   |                                                                    |
   |  TWO ways to fail:                                                 |
   |    (a) a VERIFIED violation                                        |
   |    (b) a review that could not run AT ALL                          |
   |        -- unverified is not the same as clean                      |
   |                                                                    |
   |  Writes: Company_Charter_<REG>_claude_md_review.json               |
   |  Raises: CharterComplianceError (the module's only class)          |
   |  Override: CHARTER_ALLOW_UNCHECKED=1|true|yes -> advisory          |
   |            "a decision to ship an unchecked document,              |
   |             not a convenience"                                     |
   +-------------------------------------------------------------------+
        |
        v
   _convert_docx_to_pdf(internal) ; _convert_docx_to_pdf(external)
        |
        v
   THE DELIVERABLE
```

### 13.2 Why blocking on a model's judgement is safe

```
   model returns violations[]
        |
        v
   _verified_violations(violations, document_text)
   +-------------------------------------------------------------------+
   |  normalise whitespace + case                                       |
   |                                                                    |
   |  quote length >= 12 normalised chars ?    --NO--> UNVERIFIABLE     |
   |        | YES                                      (cannot block)   |
   |        v                                                           |
   |  quote literally present in the document ? --NO--> UNVERIFIABLE    |
   |        | YES                                                       |
   |        v                                                           |
   |  _is_carved_out(raw) ?  -----------------YES----> UNVERIFIABLE     |
   |        | NO                             discarded_reason=          |
   |        v                                "explicitly permitted      |
   |     VERIFIED  ->  BLOCKS                 by rules.md"              |
   +-------------------------------------------------------------------+

   The model's role is reduced from "decide whether this complies" to
   "POINT AT the offending text", and the pointing is then checked
   mechanically. An invented or paraphrased quote is logged as
   unverifiable and CANNOT stop a good document. Quotes under 12
   characters are not trusted -- they match almost anything by
   coincidence.

   _REVIEW_CARVE_OUT_PATTERNS:
     ^\s*nothing found\.?\s*$
     ^\s*gap \d+\.
     \b\d+\s+complaints?\s*/\s*\d+\s+appeals?\b
     \b(?:no completion extension|0 complaints, 0 appeals)\b
     CIN/DIN identity-table id pattern
     ^\s*N/?A\b
```

### 13.3 Never-fatal wrappers

| Wrapped in | Stages |
|---|---|
| `main.py` | GST intake, complaint-order download, promoter portfolio, deep research, Charter generation |
| `company_charter.py` | the editorial passes, per-finding research, the document review (when non-strict) |

**The strongest case is `run_finding_research`:** a researcher that raises costs **only its own finding**, and no finding is ever deleted whatever the researcher returns — including on an empty or malformed reply. *Losing a finding to an expired token would be worse than never running the stage.*

`main._run_gst_intake_step` exists as its own named function **purely so that contract is testable** rather than asserted in a comment.

### 13.4 Fallbacks — a model can only match or improve

```
   MODEL PASS                         DETERMINISTIC PREDECESSOR
   ----------                         -------------------------
   classify_clean_checks       -->    risk nouns + field allow-list
   ("clean_check_judge")              _is_clean_check_clause

   match_claims_to_sources     -->    keyword topic table
   ("citation_match")                 _clause_topic_citation
                                      + _CLAUSE_TOPIC_PATTERNS

   write_flag_headlines        -->    first sentence
   ("flag_headline")                  _split_into_bullet_clauses[0]

   run_editorial_passes precomputes all three ONCE per document set and
   caches them on `facts`, KEYED BY CLAUSE TEXT.

   *** A lookup miss is indistinguishable from "no model ran". ***

   Therefore a failed, partial or malformed reply produces EXACTLY the
   deterministic output. Malformed replies are discarded rather than
   trusted: out-of-range ids, unrecognised verdict kinds and null
   matches are all dropped.
```

### 13.5 Bounds

| Bound | Value | Purpose |
|---|---|---|
| `deep_research.MAX_FINDING_RESEARCH_CALLS` | 8 | Findings beyond the cap keep their original text rather than being dropped |
| `deep_research.MAX_GAP_RETRY_ATTEMPTS` | 2 | One retry round per strategy, two strategies; every gap still open in a round is retried together, in one batched call |
| `deep_research.MAX_RESEARCH_VERIFICATION_CALLS` | 15 | Shared `_VerificationBudget` across all three research blocks, bounding the total number of batched verify/gap-retry calls in one `run_deep_research()` call |
| `deep_research.BATCH_VERIFY_CHUNK_SIZE` | 10 | Sources per `_verify_claims_batch` call; a block with 25 sources takes 3 calls, not 25 |
| `deep_research.BATCH_VERIFY_MAX_SEARCHES` | 10 | Shared web-search budget for one `_verify_claims_batch` call, across every claim in that chunk |
| `deep_research.BATCH_GAP_RETRY_MAX_SEARCHES` | 10 | Shared web-search budget for one `_retry_gaps_batch` call, across every gap still open in that round |
| `deep_research.PIPELINE_COST_CAP_USD` | $6.00 | Hard ceiling on total run spend (deep research + Charter generation combined), enforced in `_run_agentic_pass`; refuses a call that would start after the cap, never retried (`CostCapExceeded`) |
| `company_charter._MIN_FINDING_LENGTH` | 80 | A fragment is not a finding |
| review input cap | 60,000 chars | `_rendered_document_text(path, limit=60000)` |
| `_MAX_DOCUMENT_DOWNLOAD_WORKERS` | 8 | Download pool |
| `PROMOTER_PROJECT_LIMIT` | 25 | Portfolio fan-out; never silent (`truncated` field) |

`deep_research.MAX_RESEARCH_VERIFICATION_CALLS` used to bound a fan-out that scaled one API call per source and per gap; as of 2026-08-13 `_verify_claims_batch` and `_retry_gaps_batch` fixed the root cause by checking/retrying many at once per call, so this budget is now a backstop for a block or gap count large enough to need several chunks, rather than the primary defence.

`_field_fingerprint` and `_already_researched` stop per-finding research **recompounding**: an enriched finding is multi-sentence, so it re-splits into more clauses each pass and would be researched again. Any edit changes the fingerprint, so skipping is only ever an optimisation over identical input.

### 13.6 Reversibility

```
   _scrub_clean_checks    <---->  _restore_clean_checks
   _sanitize_process_gaps <---->  restore from _pre_sanitize_gaps
        |
        |  Both stash the original text and hand it back
        |  BEFORE .facts.json is written.
        |
        |  rules.md Section B requires this: what was checked stays
        |  in the record even when it leaves the page.
        v
   _normalize_misfiled_facts  --  the DELIBERATE EXCEPTION.
   A relocation moves text to the field it always belonged in, so
   nothing is lost and the corrected record is the better one.
   It is NOT reversed.
```

### 13.7 Guard integrity

`guardrails.md` names every guard by `module.symbol`. `test_guardrails_doc.py` fails the suite if any named symbol stops existing — which is what keeps the document honest. Several tests exist specifically to stop a guard rotting rather than to test a feature:

- malformed model replies are discarded;
- scrubbing never moves the Developer Score;
- a refusal leaves no half-written `.docx`;
- Section A never reaches an API request.

> **Adding a guardrail:** put it in code, add its symbol to the right table in `guardrails.md`, and let `test_guardrails_doc.py` confirm it resolves. *A guard nobody has watched fail is only a comment: write the test that breaks it deliberately.*

---

## 14. Authentication and session architecture

Three CAPTCHA gates, **three different session models**. This is the single most operationally significant fact about the system.

```
+============================================================================+
|  PORTAL          | SESSION MODEL              | HUMAN COST PER RUN         |
+============================================================================+
|  MahaRERA        | Reusable guest token       | 1 solve, then 90 min of    |
|  session_auth.py | cached to disk for 90 min  | free reuse across projects |
|                  | (real TTL ~100 min)        |                            |
|                  | CAPTCHA gates the SPA      | 0 if --token or cache hit  |
|                  | detail page, NOT the API   |                            |
+------------------+----------------------------+----------------------------+
|  GST Portal      | Per-lookup                 | 1 for the PAN search        |
|  gst_portal.py   | One solve covers ALL       | + 1 per GSTIN discovered    |
|                  | financial years for one    |                            |
|                  | GSTIN                      | 0 if --gstin/--pan omitted  |
+------------------+----------------------------+----------------------------+
|  Maha Bhulekh    | *** NONE ***               | 1 per Property Card fetch   |
|  mahabhumi.py    | The CAPTCHA image          |                            |
|                  | regenerates on EVERY       | This is precisely why the   |
|                  | partial postback, so       | land-record path is manual, |
|                  | token_cache's model does   | opt-in and NEVER auto-run   |
|                  | not apply at all           |                            |
+============================================================================+

   HARD CONSTRAINT, stated in all three modules:
   *** No module reads or solves a CAPTCHA image. ***
   A human must look at the real browser window and type it in.
```

### 14.1 The degradation ladder

```
   --token X
      |  miss
      v
   90-minute disk cache
      |  miss
      v
   visible-browser CAPTCHA solve (300 s budget, 2.0 s poll)
      |  timeout / window closed
      v
   auth_source = "none"  ->  fetch ONLY projects + complaints
                             (the 2 categories needing no token at all)
                             *** the run still completes ***

   ON 401/403 FOR GATED CATEGORIES DESPITE HAVING A SESSION:
      token_cache.invalidate() -> re-solve -> refetch ONLY the failed subset
      *** exactly one retry attempt. No exponential backoff anywhere. ***
```

---

## 15. External integration architecture

| System | Host / endpoint | Transport | Auth | Failure policy |
|---|---|---|---|---|
| MahaRERA search | `maharera.maharashtra.gov.in` | Playwright | none | `ProjectNotFoundError` → exit 1 |
| MahaRERA API | `maharerait.maharashtra.gov.in` | `requests` POST | guest bearer, 7 of 9 | per-category isolation; one auth retry |
| MahaRERA DMS | `/api/maha-rera-dms-service/batch-job/downloadDocumentForPublicView` | POST + bearer | yes | manifest row records the failure |
| MahaRERA judgments | `/orders-judgements` | Playwright + retry | none | `_safe_judgments_search` → never fatal |
| ICRA | public search + rating detail | `requests` | none | never fatal; one of two agencies |
| Infomerics | public search + rating detail | `requests` | none | never fatal |
| IBBI | public insolvency search | `requests` | none | never fatal |
| ZaubaCorp | CIN redirect trick | `requests` | none | `_looks_paywalled` detection; chain continues |
| Tofler | type name in a real browser, verify CIN | Playwright | none | chain continues |
| InstaFinancials | ASP.NET web service, CIN search | `requests` | none | chain continues |
| GST portal | `services.gst.gov.in` | Playwright + Tesseract | CAPTCHA/lookup | graded `note` strings; never raises |
| Maha Bhulekh | `bhulekh.mahabhumi.gov.in` | Playwright + Tesseract `mar+eng` | CAPTCHA/call | graded `note` strings; never raises |
| OSM Nominatim | `nominatim.openstreetmap.org/search` | `requests`, ≥1.1 s apart | none | failed geocode is **dropped**, never "0 km" |
| Google Maps | consumer UI | Playwright | none | **opt-in only**; ToS caveat; always falls back |
| Anthropic | Claude API | `anthropic` SDK, `tool_runner` | `ANTHROPIC_API_KEY` | every helper returns empty → deterministic fallback |
| xAI Grok | `https://api.x.ai/v1`, `grok-4.5` | `OpenAI` client | `XAI_API_KEY` | optional second opinion on document grounding |

### 15.1 Two integrations that carry explicit caveats

**Google Maps distance refinement.** `COMPANY_CHARTER_USE_MAPS_SCRAPE` must equal `"1"` exactly. It scrapes Google's *consumer UI* rather than the paid Distance Matrix/Routes API, so it may not comply with Google's Terms of Service and can break without warning. **Off by default.** Always falls back to the model's `web_search` estimate. The template's own Methodology Note states that distances are estimated.

**Group companies.** Sourced from **ZaubaCorp only** — Tofler and InstaFinancials do not expose that data freely — and corroborated against InstaFinancials directorship counts rather than treated as authoritative.

---

## 16. LLM integration architecture and cost model

### 16.1 Single transport

```
   EVERY Claude API call in the entire system funnels through:

        deep_research._run_agentic_pass(user_prompt, system, label)
              |
              v
        spent = usage_summary()["total"]["cost_usd"]   (read fresh, never cached)
        spent >= PIPELINE_COST_CAP_USD ($6.00) ?
              |                                    \
              |  NO                                  YES -> raise CostCapExceeded
              v                                              (never retried -- a
        client.beta.messages.tool_runner(                    call already in
            model = MODEL = "claude-sonnet-5",                flight is allowed
            max_tokens = 8000,                                to finish; only a
            system = <blocks>,                                call that would
            tools = [{"type": "web_search_20260209",           START after the
                      "name": "web_search"}],                  cap is refused)
            messages = [{"role":"user","content": user_prompt}],
            cache_control = {"type": "ephemeral"})
              |
              |  the runner is an ITERATOR -> one BetaMessage per turn
              v
        messages = list(runner)
              |
              +--> _record_usage(label, MODEL, messages)
              |      sums input+output tokens across ALL turns, because
              |      each web_search round-trip is separately billed --
              |      counting only the last message would UNDERCOUNT.
              |      cache_creation_input_tokens / cache_read_input_tokens
              |      are read off usage too -- reported separately from
              |      input_tokens by the API, priced separately here
              |      (_CACHE_WRITE_MULTIPLIER 1.25x, _CACHE_READ_MULTIPLIER
              |      0.10x of the base input rate) rather than folded in
              |
              +--> _parse_json_response(messages[-1])
                     strips ``` fences and a leading "json", json.loads
                     failure -> RuntimeError with the first 500 raw chars

   Structured output is enforced by PROMPT + PARSER, not by tool schema.
   Only server tools are given -- raw tool-schema dicts auto-execute
   server-side only for recognised server tools, so no custom handler
   is registered, deliberately.

   cache_control is passed UNCONDITIONALLY on every call (2026-08-13).
   Per the SDK it marks the last cacheable block in the request -- system
   plus tools, which precede the varying user message -- so a system
   prompt reused across calls sharing one label (every batched verify/
   gap-retry/finding-research call does) is billed at the cache-read rate
   on its second and later uses. A one-off call still writes the cache
   once at a small premium; there is no case where this costs more.
```

### 16.2 Call-site inventory

| Caller | Label | Rules injected |
|---|---|---|
| `company_charter._run_charter_pass` | `charter_pass` | **Section B** (cacheable prefix) + `_SYSTEM_PROMPT` |
| `company_charter._verify_one_field` → `deep_research._verify_claim` | `material_claim_verify` | none |
| `company_charter._verify_document_claim` | `document_grounding_verify` | none |
| `company_charter._verify_document_claim_via_grok` | *(not recorded — xAI, outside the ledger)* | none |
| `company_charter._llm_verify_citation_completeness` | `citation_completeness_judge` | **Section B + Section C** — the only call that exists specifically because a variant is External |
| `company_charter._attempt_second_source` | `second_source_verify` | none |
| `run_editorial_passes` pass 1 | `clean_check_judge` | none |
| `run_editorial_passes` pass 2 | `citation_match` | none |
| `run_editorial_passes` pass 3 | `flag_headline` | none |
| `run_finding_research` | `finding_research` | none |
| `run_claude_md_document_review` | `claude_md_doc_review` | **B** (internal) / **B + C** (external), passed as an argument |
| `deep_research.run_deep_research` | `research_generate` | none |
| `deep_research._verify_block` → `_verify_claims_batch` | `verify_claim_batch` | none |
| `deep_research._resolve_gaps` → `_retry_gaps_batch` / `_verify_claims_batch` | `gap_retry_batch`, `gap_retry_verify_batch` | none |

> **Section A never appears in this table.** That is the point.

### 16.3 Cost model

```python
MODEL = "claude-sonnet-5"
_PRICING_PER_1M_TOKENS = {"claude-sonnet-5": {"input": 2.00, "output": 10.00}}
_CACHE_WRITE_MULTIPLIER = 1.25   # x base input rate, for cache_creation_input_tokens
_CACHE_READ_MULTIPLIER = 0.10    # x base input rate, for cache_read_input_tokens
PIPELINE_COST_CAP_USD = 6.0      # hard ceiling, checked in _run_agentic_pass

cost_usd = (
    input_tokens * price["input"]
    + cache_write_tokens * price["input"] * _CACHE_WRITE_MULTIPLIER
    + cache_read_tokens * price["input"] * _CACHE_READ_MULTIPLIER
    + output_tokens * price["output"]
) / 1_000_000
```

- An **unknown model** yields `{"input": 0.0, "output": 0.0}` so tokens still count but cost reads `0.0` rather than "a silently wrong number".
- Per-label rollup: `usage_summary()` → `output/<reg>/usage_summary.json`; one rollup line appended to `output/usage_log.jsonl`. Each bucket now also carries `cache_write_tokens` and `cache_read_tokens`, summed separately from `input_tokens`/`output_tokens`.
- The Internal document's **version log** carries elapsed time, total cost and call count for the whole end-to-end run — hence `pipeline_start_time` being threaded from `main.py`.
- **`reset_usage_log()` is called at the start of every run** because `app.py`'s Streamlit process is long-lived and would otherwise carry token counts across unrelated projects.
- **`PIPELINE_COST_CAP_USD` (2026-08-13, $6.00)** is a hard ceiling on total run spend — deep research and Charter generation combined, not just one stage — checked at the top of every `_run_agentic_pass` call by reading `usage_summary()["total"]["cost_usd"]` fresh (never a stale snapshot). A call that would **start** at or over the cap raises `CostCapExceeded` and is **not retried** (unlike `BudgetExhausted`: a smaller search budget doesn't make an already-overspent run cheaper); a call already in flight is allowed to finish, so the real ceiling is the cap plus at most one more call's worst case. `CostCapExceeded` subclasses `RuntimeError`, so it degrades exactly like a missing `ANTHROPIC_API_KEY` — the stage it interrupts is `[never fatal]` in `main.py`, never the whole run.

> ⚠️ **Pricing time-bomb.** The comment records this as *"Sonnet 5's intro rate, active through 2026-08-31 — update to the standard $3.00/$15.00 after that date."* As of this document's date (13 Aug 2026) that leaves **18 days**. After expiry, every cost figure in `usage_summary.json`, `usage_log.jsonl`, the run summary and the Internal version log **under-reports by one third** until the constant is updated. This is unrelated to, and not fixed by, `PIPELINE_COST_CAP_USD` — the cap is denominated in the same (soon-to-be-stale) dollar figures, so it under-*enforces* by the same one third once the rate lapses.

### 16.4 Verification semantics

`deep_research._verify_claim` (still used directly by `company_charter._verify_one_field`, label `material_claim_verify`) and `deep_research._verify_claims_batch` (used by `_verify_block`, one call per chunk of up to `BATCH_VERIFY_CHUNK_SIZE` sources) both resolve every claim to one of four statuses, and the handling of the fourth is the interesting design decision:

| Status | Meaning | Handling |
|---|---|---|
| `confirmed` | Source re-checked and stands | keep |
| `unsupported` | Source does not support the claim | **drop** into gaps with the reason |
| `stale` | Source is out of date | **drop** into gaps with the reason |
| `verification_error` | The **check itself** could not run (missing key, network, rate limit, bad JSON, or a chunk skipped because `budget` ran out) | **keep the source**, and append an honest gap: *"(NOT independently re-verified this pass — reason)"* |

An unrecognised status, or a claim id the model's reply omits, is coerced to `unsupported` / treated as unverified. The `verification_error` branch is what prevents an API outage — or a budget-exhausted chunk — from silently deleting good sourcing. Batching (2026-08-13) changed *how many calls* this costs, never *what a status means or how it's handled* — the four-way semantics above are identical to the pre-batch, one-call-per-source implementation.

---

## 17. Scoring subsystem

Two scores coexist, with **deliberately opposite missing-data policies**.

### 17.1 Developer Score — never renormalises

```
   _compute_developer_score(facts, flags) -> {composite, grade, criteria}

   +--------------------------------------------------------------------+
   | BUCKET                  WEIGHT   SUB-METRICS                  EACH  |
   +--------------------------------------------------------------------+
   | Operational Strength     50.0    Team Strength                12.5  |
   |                                  Influence in Micromarket     12.5  |
   |                                  Past Experience - Area       12.5  |
   |                                  Track Record                 12.5  |
   +--------------------------------------------------------------------+
   | Financial Strength       20.0    Financial Strength           20.0  |
   +--------------------------------------------------------------------+
   | Governance Strength      30.0    RERA Compliance               7.5  |
   |                                  GST Compliance                7.5  |
   |                                  Cases (Past Defaults)         7.5  |
   |                                  Entity Rating                 7.5  |
   +--------------------------------------------------------------------+

   TIER SCORES   AAA 100.0 . AA 83.3 . A 66.7 . B 50.0 . C 33.3 . D 16.7
   THRESHOLDS    AAA >= 91.65 . AA >= 75.0 . A >= 58.35 . B >= 41.65
                 C >= 25.0 . D below           (midpoints between tiers)

   *** HARD CAP: if flags["imminent"] is non-empty and the grade would
       be AAA or AA, it is forced to "A". It never softens an already-
       lower grade. Nothing scored at all -> "D". ***

   *** NON-RENORMALISING BY DESIGN: an unscored sub-metric's weight is
       never redistributed and never removed from the denominator.
       A promoter with less publicly-verifiable data STRUCTURALLY
       scores lower. This is the intended behaviour, not a bug. ***
```

Three sub-metrics (`team_strength`, `financial_strength_debt`, and historically the compliance pair) have no wired-in data source.

**Worked bands** for two portfolio-driven sub-metrics:

| Sub-metric | AAA | AA | A | B | C | D |
|---|---|---|---|---|---|---|
| Past Experience – Area (lakh sq ft) | > 120 | ≥ 81 | ≥ 51 | ≥ 21 | ≥ 6 | else |
| Influence in Micromarket, 5 km (lakh sq ft) | > 50 | ≥ 21 | ≥ 6 | ≥ 2 | ≥ 1 | else |

### 17.2 GST Compliance — a friction-point model

```
   points = 0
   late_pct              > 40 -> +45 | > 15 -> +30 | > 0 -> +15 | else +0
   worst_delay_days      > 60 -> +30 | > 20 -> +15 |            else +0
   delays_last_12_months >  3 -> +45 | >  1 -> +25 |            else +0
                                        ^^^ weighted highest: what should
                                        actually drive an "ask the developer"
                                        conversation TODAY

   tier: 0 -> AAA | <=20 -> AA | <=40 -> A | <=60 -> B | <=80 -> C | else D

   The breakpoints are SHARED with _classify_flags via _FLAG_THRESHOLDS,
   so a flagged project cannot silently score AAA.

   TWO DISTINCT UNSCORED OUTCOMES:
     no gst_compliance_check / not found  -> a PENDING-INPUT gap
     on_time + late == 0                  -> "no period with both a
                                             resolvable due date and a
                                             recorded filing outcome"
```

Statutory due dates, computed offline in `gst_compliance.py`:

| Frequency | GSTR-1 | GSTR-3B |
|---|---|---|
| Monthly (span 28–31 days) | 11th | 20th |
| QRMP quarterly (span 89–92 days) | 13th | **22nd** (Category X states) / **24th** (Category Y) |

Category X state codes: `22,23,24,25,26,27,29,30,31,32,33,34,35,36,37` — Maharashtra is `27`. "Not in Category X" is the correct test for Y; Y is deliberately not enumerated.

**Frequency is detected per *period*, not per GSTIN**, because a taxpayer can switch in or out of QRMP at the start of any quarter.

> **Determinism warning.** `as_of` defaults to today. Callers generating scored, persisted Charter output must pass an explicit date, "otherwise the same `facts.json` re-rendered on a later date silently produces a different `delays_last_12_months` figure with no record of why." `run_gst_compliance_check` does pass `as_of=datetime.now().date()` — which makes the *run* deterministic but not the *re-render*.

### 17.3 Documentation Confidence — always renormalises

Scores **this document's sourcing**, not the promoter.

| Criterion | Weight |
|---|---|
| `verification_rate` | 0.25 |
| `source_tier_quality` | 0.20 |
| `primary_tier_density` | 0.15 |
| `cross_corroboration` | 0.15 |
| `financial_figures_confirmed` | 0.10 |
| `completeness_rate` | 0.05 |
| `recency_legal` | 0.05 |
| `recency_other` | 0.05 |

N/A criteria are excluded and remaining weights **renormalised to 100 %** — the opposite policy to the Developer Score — except zero verification attempts, which has its own override. `_TIER_WEIGHTS` maps 12 source tiers to 20–100. `_RECENCY_WINDOW_MONTHS = 18`; `_RECENCY_WINDOW_MONTHS_LEGAL = 3`. Bands: `High ≥ 70`, `Moderate ≥ 45`, `Limited` below.

Both score tables **stay fully intact however many N/A rows they have** — they are scoring methodology, not fact tables, and are exempt from empty-row removal.

### 17.4 Flags

`_classify_flags` is fully deterministic — no LLM. It produces `{imminent[], structural[], monitor[]}` with items `{text, field[, gap_number]}`, and assigns every `gaps[i]` a **stable 1-based `gap_number`** so a flag headline and its gap can be cross-referenced.

Checks: missing registration number · missing CTS/plot (imminent if `net_area` also missing, else structural) · **`cts_mismatch_note` — always imminent** · missing CIN/LLPIN · IBBI hit via `_classify_ibbi_hit` · complaint/appeal volume against `_FLAG_THRESHOLDS` · GST filing pattern · promoter portfolio total (always structural) · credit rating · `mortgage_lender_history_note` (monitor).

```
_FLAG_THRESHOLDS = {
    complaint_monitor: 15,   complaint_imminent: 40,
    appeal_monitor: 5,       appeal_imminent: 15,
    credit_rating_min_units: 500,
    gst_late_pct_monitor: 15,      gst_late_pct_imminent: 40,
    gst_recent_delays_monitor: 1,  gst_recent_delays_imminent: 3,
}
```

**Flags summarise, Gaps explain.** `_flag_headline` renders a one-sentence headline ending in `(Gap N)`; the full explanation lives once, under Gaps & Sources. A documented known limitation: on the 2026-08 Pranami data, 11 of 17 gaps were single sentences, so the headline was byte-identical to the gap — explicitly *"do not fix by truncating."*


---

## 18. Document rendering architecture

### 18.1 The Internal / External fork

```
================================================================================
      _fill_template()  --  ONE FUNCTION, TWO VARIANTS, ONE FACTS DICT
================================================================================

                         facts  (real, mutated in place)
                                       |
              +------------------------+------------------------+
              |                                                 |
              v                                                 v
      doc_variant = "internal"                        doc_variant = "external"
              |                                                 |
              |                                    _externalized_facts_copy()
              |                                    *** DEEP COPY -- never
              |                                        touches the real dict ***
              v                                                 v
      _preflight_rules("internal")                   _preflight_rules("external")
        validates A + B                                validates A + B + C
              |                                                 |
              v                                                 v
      _normalize_misfiled_facts  ->  _scrub_clean_checks  ->  _sanitize_process_gaps
        (corrections FIRST, scrub SECOND -- the order matters)
              |                                                 |
              v                                                 v
      shutil.copy2(TEMPLATE_PATH, out_path); python-docx opens the COPY
      *** the template is filled IN PLACE, preserving every style, colour
          and table already baked into it ***
              |                                                 |
              v                                                 v
      monkey-patch doc.add_paragraph -> _add_paragraph_sanitized
        BOTH: _strip_inline_cin_din + _expand_jargon_first_use
        EXTERNAL ONLY: + _externalize_prose
              |                                                 |
              v                                                 v
      "(label)" citations                             "[N]" numbered citations
      "(Code-Computed)" headings kept                 headings stripped
      Document Library table t[8] kept                t[8] DROPPED entirely
      Weight columns kept                             Weight columns DROPPED
      ALL gaps printed                                only gaps that also
                                                        earned an Imminent or
                                                        Structural flag,
                                                        colour-coded, "Gap N."
      full bibliographic Sources list                 Sources rebuilt from
        with [published:, accessed:]                    _citation_registry order
      "Source Trust Registry Updates"                 (suppressed)
      version log (elapsed / $ / calls)               (suppressed)
      8 internal-only paragraphs kept                 paragraphs 12, 13, 17, 18,
                                                        36, 38, 58, 63 DELETED
              |                                                 |
              v                                                 v
      *_Internal.docx                                 *_External.docx
              |                                                 |
              |                                    _verify_external_document_quality
              |                                      (11 checks -> RuntimeError)
              |                                    + advisory citation judge
              |                                                 |
              +------------------------+------------------------+
                                       |
                          run_claude_md_document_review
                                *** BLOCKS THE PDF ***
                                       |
                                       v
                      _convert_docx_to_pdf  x2  (docx2pdf / Word COM)
                                       |
                                       v
                *_Internal.pdf   +   *_External.pdf    <-- THE DELIVERABLE
                                       |
                                       v
                    _restore_clean_checks + restore gaps
                                       |
                                       v
                            *.facts.json  <-- THE FULL RECORD
```

### 18.2 The ten locked top-level sections

> **`rules.md` §A: "Section order is locked. Ten top-level sections, same sequence, always. Reshape content inside the flow; never reorder or rename a section."**

| # | Heading | Origin |
|---|---|---|
| 1 | *(title block)* — project/promoter line, `"Public Web-Sourced Edition -- Adapted for Maharashtra (MahaRERA)"`, date line | template paragraphs |
| 2 | **Overview & Flags** | `_append_overview_section`, relocated to the front |
| 3 | **Executive Summary** + 4 KPI cards | template + `_append_executive_summary_kpis` |
| 4 | **Counterparty** | renamed from "1. Legal Identifiers…"; `_append_counterparty_summary` relocated in |
| 5 | **The Asset** (H2 child: *FSI (Floor Space Index)*) | renamed from "2. Location Map"; Land Identification physically relocated in |
| 6 | **Compliance & Legal Detail** | renamed from "4. Rules"; gains *Complaint Order Outcomes* and *Appeal-Level Judgments* as H2 |
| 7 | **Market & Area Intelligence** | renamed from "5. Area Intelligence" |
| 8 | **RERA Core Data** | renamed from "6. RERA Scraping…" |
| 9 | **Diligence Appendix** | new H1; H2 children: Document Library Contents, Credit Rating Check, IBBI Insolvency Check, Company Registration Profile, Group / Affiliated Companies, Developer Score, Documentation Authenticity & Confidence Summary |
| 10 | **Gaps & Sources** (H2 child: *Sources*) | renamed from "Gaps & Limitations…" |

**Two conditional sections remain standalone Heading-1 appends at the document tail**, marked `# unmoved` in the code:

- `Land Record Check -- Maha Bhulekh Property Card (Code-Assisted, Human-Verified)`
- `Review Authenticity Triage (Code-Computed Heuristics)`

Both silently no-op unless the corresponding check ran, which is why they do not disturb the locked ten in the ordinary case. **This is a latent inconsistency with the locked-order rule and should be recorded as such** (see §25).

### 18.3 Tables

| Index | Table | Notes |
|---|---|---|
| `t[0]` | Land Identification | rows 1–7 + a grown `land_assembly` row |
| `t[1]` | Corporate Identity | rows 1–9; one of only three tables where a CIN/DIN may appear inline |
| `t[2]` | Neighbourhood | east / west / north / south |
| `t[3]` | Distances | variable |
| `t[4]` | FSI Metrics | rows 1–5 + the merged `"Mortgage / charge on the land"` row via `_merged_mortgage_value` |
| `t[5]` | Comparables | variable |
| `t[6]` | RERA Core Data | rows 1–13 |
| `t[7]` | Blocks | variable |
| `t[8]` | Document Library | **Internal only** |

### 18.4 Typographic rules enforced in code

| Rule | Implementation |
|---|---|
| Body text justified | `_justify_body_paragraphs` |
| Table cells centred **for short values only** | `_center_all_table_cells` with a length threshold — the original unconditional version produced unreadable Landowner/Investor, Board Resolution and Mortgage rows (amended 2026-08-10) |
| Table rows must not split across a page break; a continued table repeats its header | `_apply_table_pagination` sets `cantSplit` on each row and `tblHeader` on the header row |
| No URLs in narrow table columns | cells carry a short label or an `[N]` marker; the full URL lives once in Sources |
| Bullet hanging indent | `_fix_bullet_hanging_indent` + `_apply_bullet_hanging_indent`; gate check 11 verifies `abstractNum` id 2 |
| No manual page-break characters | headings use `page_break_before=True` — a manual break produced a real blank-page bug (enforced in `charter_report`'s gate) |

### 18.5 Two pieces of hidden state

`_fill_template` sets a module-global `_ACTIVE_EXTERNAL_FACTS` and monkey-patches `doc.add_paragraph`. Both are reset per call, but together they make **`_fill_template` non-reentrant and not thread-safe**. It must not be parallelised across variants.

### 18.6 The cheap verification loop

`_fill_template` runs straight off a saved `facts.json` and makes **no API calls**, so most rendering behaviour can be verified for free:

```python
import json, company_charter as cc
facts = json.load(open('output/company_charters/Company_Charter_<Name>_<REG>.facts.json',
                       encoding='utf-8'))
cc._fill_template('<REG>', facts, 'output/company_charters/_verify/int.docx',
                  doc_variant='internal')
cc._fill_template('<REG>', facts, 'output/company_charters/_verify/ext.docx',
                  doc_variant='external')
```

> Any script hitting `_fill_template` directly **must call `_convert_docx_to_pdf` itself afterwards**. `run_company_charter` is the only path that does it for you.

---

## 19. Secondary and standalone builders

```
================================================================================
   DOCUMENT FAMILIES, BUILDERS AND RENDERERS
================================================================================

   FAMILY 1 -- RERA PROJECT SUMMARY
   +---------------------------------------------------------------+
   | report.py :: build_pdf()            ReportLab                  |
   | LIVE. Stage 10 of main.py; also finalize_report.py, app.py.    |
   | -> output/<REG>/<REG>_summary.pdf                             |
   +---------------------------------------------------------------+

   FAMILY 2 -- COMPANY CHARTER
   +---------------------------------------------------------------+
   | (a) company_charter.py :: _fill_template()   python-docx       |
   |     *** THE LIVE AUTOMATIC PATH ***                            |
   |     via run_company_charter, called from main.py               |
   |     -> Internal + External .docx -> .pdf                       |
   |     Gate: _verify_external_document_quality (11 checks)        |
   |                                                                |
   | (b) charter_report.py :: build_charter_report()  python-docx   |
   |     MANUAL / STANDALONE ONLY. Never called from main.py.       |
   |     "Counterparty + Collateral" restructured document.         |
   |     Own already-implemented findings-not-absence policy.       |
   |     Gate: verify_charter_report_quality (6 checks, incl. the   |
   |           bare-domain check the other gate does NOT have)      |
   |     Does NOT convert to PDF -- conversion is a separate step.  |
   |                                                                |
   | (c) executive_briefing.py :: build_executive_briefing()        |
   |     python-docx + matplotlib (Agg) donut charts.               |
   |     8-page narrative COMPANION, not a replacement.             |
   |     Narrates only what the Charter already established;        |
   |     performs no new interpretation of raw data.                |
   |     No __main__ and no in-repo caller -- a host must import it.|
   |                                                                |
   | (d) charter_document.py                                        |
   |     *** NOT A BUILDER. A LIVE SHARED LIBRARY. ***              |
   |     build_charter_document() and the 24 definitions only it    |
   |     reached (the _Builder class + all 15 _section_N_* render-  |
   |     ers) were DELETED 2026-08-10 after an audit confirmed it   |
   |     had never been called from a non-test file since 5b8c6ee.  |
   |     ~710 lines remain. charter_report.py imports 16 symbols;   |
   |     executive_briefing.py uses _green_flags.                   |
   |     *** DO NOT DELETE. DO NOT TREAT AS DEAD CODE. ***          |
   |     (CHARTER_RESHAPE_SPEC.md calls it dead code. That file is  |
   |      stale and must not be followed.)                          |
   +---------------------------------------------------------------+

   PDF CONVERSION
   +---------------------------------------------------------------+
   | company_charter._convert_docx_to_pdf  --  docx2pdf / Word COM  |
   | The ONLY converter in the system. Windows-only.                |
   | run_company_charter returns the INTERNAL PDF path as out_path, |
   | falling back to the .docx only if conversion failed.           |
   | *** THE PDF IS THE DELIVERABLE, NOT THE .DOCX ***              |
   +---------------------------------------------------------------+
```

### 19.1 `charter_report.py` — the second builder

Section structure ("Counterparty + Collateral"):

```
   Cover page  ---- COMPANY CHARTER (28 pt navy) / "Company, Promoters and
                    Collateral Due-Diligence Note" / Company / Collateral /
                    Location / Date of review / classification line
                    ("Internal -- Integrow Asset Management" vs
                     "Strictly Private and Confidential")
                    Composite Developer Score: N/100 (grade G)

   1. About the Company ................ prose only, no tables
   2. The Company ...................... Identity and Registration
                                         Verification Summary
                                           (Overall Rating, Documentation
                                            Confidence, Key Items to Verify)
                                         Portfolio and Track Record
                                         Litigation and Regulatory Screening
                                         >>> Needs Attention, Company
   3. The Promoters .................... one subsection per current director
                                         + one material past director
                                         >>> Needs Attention, Promoters
   4. The Collateral: <project> ........ Asset Identity and Land Record
                                         Approvals, FSI and Title
                                         RERA Compliance and Escrow
                                         Project Professionals
                                         Location, Connectivity, Market Read
                                         Document and Diligence Trail
                                         Litigation and Regulatory Screening
                                         >>> Needs Attention, Collateral
   5. Closing Read ..................... signals/finding table + verdict
                                         Recommended Verification Steps
   6. Scoring Detail (Appendix) ........ by sub-metric, by criterion
   Annexure: Related Entity Mapping .... one combined table for all promoters
   References .......................... EXTERNAL ONLY (Internal uses
                                         self-descriptive "(per X)" inline)

   *** There is no "What Checks Out" heading anywhere in this document. ***
   Every attention item is consolidated into that section's own
   "Needs Attention, <label>" subsection.
```

Its gate, `verify_charter_report_quality(docx_path)`, walks every body paragraph **and every table cell recursively** on the re-opened saved file:

1. Dash artefacts — `" -- "`, en dash, em dash surviving `_clean_text`
2. Redundant inline attribution — a paragraph matching both a citation marker and a "(per X)" / "confirmed identically across…" pattern
3. **Bare-domain mentions** never converted into a citation — *the check the other gate does not have*
4. No manual page-break character (`w:br w:type="page"`) — headings must use `page_break_before=True`
5. Footer page-number fields — both `PAGE` and `NUMPAGES` `w:instrText` must be present
6. Citation integrity — reports `missing_refs` and `orphan_refs` separately

### 19.2 The orchestrator seam

```
   run_charter_pipeline.py
   +-------------------------------------------------------------------+
   |  prepare()  -> charter_research_prep.build_roster(facts)          |
   |                build_director_prompt() / build_group_prompt()     |
   |                RETURNS PROMPTS                                     |
   |                                                                    |
   |         >>> the HOST (an LLM orchestrator) runs the research      |
   |             agents in parallel -- spawning a research agent is a  |
   |             tool call only an orchestrator can make <<<           |
   |                                                                    |
   |  finish()   -> parse_agent_json() -> assemble_research()          |
   |                -> charter_report.build_charter_report()           |
   |                -> (PDF conversion is a separate post-finish step) |
   +-------------------------------------------------------------------+

   build_report.py is the hand-transcribed equivalent of that middle
   step for one engagement. It is explicitly "not a reusable pipeline
   component -- delete or rewrite per engagement."
```

`DEFAULT_PAST_DIRECTOR_LINK_THRESHOLD = 5`: a past director needs at least 5 of their own related-entity links to earn a subsection; the rest become `additional_next_steps`. `is_partnership` swaps director↔partner throughout, after confirmed live bugs for an LLP promoter and a bare partnership firm with no DIN — the no-DIN branch changes the whole search strategy.

### 19.3 Pre-RERA intake paths

```
   A CIN or a CTS number often reaches the pipeline BEFORE a RERA
   registration number exists. Two standalone intakes handle that:

   promoter_intake.py <CIN> [company_name]
        -> MCA profile + IBBI + group companies + credit rating
        -> output/_pending/<CIN>/promoter_profile.json
           *** written with the EXACT facts.json keys ***

   cts_intake.py <district> <office> <village> <cts> <mobile>
        -> output/_pending/<district_village_cts>/land_record.json
           key: cts_land_record_check  -- again the exact facts.json key
           (a bare CTS number is not globally unique; district+village+
            cts_number together form the key)

                              |
                              |  later, when a RERA number is known:
                              v
   attach_rera.py <case_id> <reg_no>
        -> COPIES (never moves) into output/<reg_no>/ as *_carryover.json
        -> a later `python main.py <reg_no>`:
             reuses the promoter profile INSTEAD of re-running live CIN
               checks (the 5-way fan-out skips four of its five branches)
             reuses the land record INSTEAD of opening a new CAPTCHA
               browser, and cross-checks the CTS against RERA's own
               survey_cts_plot_numbers
             *** a genuine mismatch -> facts["cts_mismatch_note"]
                 -> _classify_flags promotes it to IMMINENT ***

   "an explicit action, never auto-matched" -- the pipeline's standing
   policy that a human confirms every identifier link.
```

---

## 20. Concurrency and performance

### 20.1 Concurrency map

```
   COMPONENT                          MODEL                       WORKERS
   -----------------------------------------------------------------------
   fetch_all_categories               ThreadPoolExecutor          8
     (projects fetched serially first -- the one ordering dependency)
   download_documents                 ThreadPoolExecutor          8
   download_complaint_orders          SERIAL                      1
   company_charter phase-2 checks     ThreadPoolExecutor          5
   promoter_portfolio                 SERIAL                      1
   deep_research                      SERIAL                      1
   gst_intake / gst_portal            SERIAL (visible browser)    1
   mahabhumi                          SERIAL (visible browser)    1
   _fill_template                     SERIAL, NON-REENTRANT       1
   discover.verify_endpoints          SERIAL (deliberate)         1
```

**Thread-safety notes.**
- Every category and document worker creates **its own `requests.Session()`** — the library does not guarantee `Session` thread-safety, and the connection-reuse benefit of sharing one is marginal next to the risk.
- One `threading.Lock` guards the only genuinely shared state in the download pool: `seen_filenames` and `seen_urls`. The check-then-add is the sole critical section.
- `fetch_all_categories` consumes futures in submission order on the main thread, so the results dict is only ever mutated single-threaded.
- `promoter_portfolio` uses a **module-global** `_last_geocode_at` to honour Nominatim's ~1 req/s policy — not thread-safe, by design, because the module is single-threaded.
- `deep_research._USAGE_LOG` and its lazily-created singleton client are process-global.

### 20.2 Measured bottlenecks

`fetch_all_categories`' own docstring names its concurrency as *"the other half of a real, measured bottleneck (alongside the Phase 2 checks and MahaRERA judgments search) in a full pipeline run."*

The three measured hot spots, therefore:

1. Category fetch (parallelised, 8 workers)
2. Charter phase-2 external checks (parallelised, 5 workers)
3. MahaRERA judgments search (Playwright, with its own retry)

**LLM cost profile.** The docstring records that *"a handful of small calls (one per cited source needing re-verification) dominates far more than the one big Charter-assembly pass."* Verification, not generation, is the cost centre.

**Human wall-clock** is the dominant end-to-end factor on any run using GST or land records: one CAPTCHA solve for MahaRERA, one for the GST PAN search, one per GSTIN, and one per Property Card fetch.

### 20.3 Timeout and bound constants (single reference)

| Constant | Value | Where |
|---|---|---|
| `REQUEST_TIMEOUT` | 30 s | all HTTP |
| `SEARCH_TIMEOUT_MS` | 30000 | Playwright navigation |
| `CAPTCHA_TIMEOUT_SECONDS` | 300 | all three portals |
| `CAPTCHA_POLL_INTERVAL_SECONDS` | 2.0 | all three portals |
| `token_cache.MAX_AGE_MINUTES` | 90 | vs ~100 min real TTL |
| `_MAX_DOCUMENT_DOWNLOAD_WORKERS` | 8 | `api_client` |
| `PROMOTER_PROJECT_LIMIT` | 25 | `config` |
| `_GEOCODE_MIN_INTERVAL_S` | 1.1 | Nominatim politeness |
| `_FIVE_KM_RADIUS` | 5.0 km | portfolio filter |
| Earth radius | 6371.0 km | haversine |
| `MAX_GAP_RETRY_ATTEMPTS` | 2 | `deep_research` |
| `MAX_FINDING_RESEARCH_CALLS` | 8 | `deep_research` |
| `MAX_RESEARCH_VERIFICATION_CALLS` | 15 | `deep_research` |
| `BATCH_VERIFY_CHUNK_SIZE` | 10 | `deep_research` |
| `BATCH_VERIFY_MAX_SEARCHES` | 10 | `deep_research` |
| `BATCH_GAP_RETRY_MAX_SEARCHES` | 10 | `deep_research` |
| `PIPELINE_COST_CAP_USD` | $6.00 | `deep_research` |
| `_CACHE_WRITE_MULTIPLIER` / `_CACHE_READ_MULTIPLIER` | 1.25x / 0.10x base input rate | `deep_research` |
| `RESEARCH_REUSE_WINDOW_HOURS` | 24 | `deep_research` |
| `max_tokens` | 8000 | every agentic pass |
| review input cap | 60000 chars | `_rendered_document_text` |
| `_MIN_FINDING_LENGTH` | 80 | `company_charter` |
| `_SOURCE_PROMOTION_HIT_THRESHOLD` | 5 distinct projects | trust registry |
| `DEFAULT_PAST_DIRECTOR_LINK_THRESHOLD` | 5 | `charter_research_prep` |
| `_LONG_VALUE_THRESHOLD` | 300 chars | `report.py` |
| `_MAX_TABLE_COLS` | 6 | `report.py` |

There is **no exponential backoff anywhere in the system**. The only retry is `main.py`'s single auth-refresh, plus bounded per-attempt retries in `mahabhumi` (3) and the judgments search.

---

## 21. Failure, degradation and recovery model

### 21.1 The degradation matrix

```
+===========================================================================+
| FAILURE                       | COST                    | RUN OUTCOME     |
+===========================================================================+
| CAPTCHA not solved / timeout  | 7 of 9 categories       | COMPLETES       |
| Token expired mid-run (401)   | one auth retry, then    | COMPLETES       |
|                               | those categories only   |                 |
| One category endpoint fails   | that category           | COMPLETES       |
| One document download fails   | a manifest row          | COMPLETES       |
| Complaint-order download dies | the whole sub-feature   | COMPLETES       |
| Geocode fails for a project   | that entry dropped from | COMPLETES       |
|                               | the 5 km sum (never     |                 |
|                               | counted as "0 km away") |                 |
| Promoter portfolio build dies | one Charter input       | COMPLETES       |
| GST portal outage / no solve  | ONE unscored sub-metric | COMPLETES       |
| ANTHROPIC_API_KEY missing     | deep research + Charter | COMPLETES       |
|                               | (retry standalone)      |                 |
| Deep research pass fails      | market/promoter context | COMPLETES       |
| Editorial pass fails          | deterministic fallback  | COMPLETES       |
| Per-finding research fails    | ONLY that finding, and  | COMPLETES       |
|                               | its ORIGINAL TEXT KEEPS |                 |
| Tesseract not installed       | "[OCR unavailable]"     | COMPLETES       |
|                               | marker per document     |                 |
| Google Maps scrape fails      | falls back to estimate  | COMPLETES       |
| PIPELINE_COST_CAP_USD reached | whichever deep research | COMPLETES       |
|                               | / Charter call was next |                 |
+---------------------------------------------------------------------------+
| rules.md missing / malformed  | ***  BLOCKS  ***        | RuntimeError    |
| B or C carries an em dash     | ***  BLOCKS  ***        | RuntimeError    |
| External gate violation       | ***  BLOCKS THE SAVE ***| RuntimeError    |
| Verified rules violation      | ***  BLOCKS THE PDF  ***| CharterCompl... |
| Review could not run at all   | ***  BLOCKS THE PDF  ***| CharterCompl... |
| Template file missing         | ***  BLOCKS  ***        | FileNotFound    |
+---------------------------------------------------------------------------+
| Project not found in search   | run cannot start        | exit(1)         |
| Multiple matches, no TTY      | run cannot start        | exit(2)         |
+===========================================================================+
```

### 21.2 Recovery paths

| Situation | Recovery |
|---|---|
| Deep research failed | `python deep_research.py <REG_NO>` — reads `raw/`, rebuilds the PDF |
| Charter failed | `python company_charter.py <REG_NO>` |
| Only the PDF needs rebuilding | `python finalize_report.py <REG_NO>` — **zero network calls, no Playwright, no CAPTCHA** |
| Search index cannot find a live project | `--project-id N` — the direct API can still return full data for a project the search box cannot find; a site-side index gap, not a sign the project is gone |
| Endpoint drift suspected | `python main.py <REG_NO> --verify` — probes every endpoint, prints `confirmed`/`observed` next to the empirical outcome |
| A run needs to be diffed against the previous one | it already is: `output/_history/<reg>/<ts>/` |
| Rendering change needs checking | the cheap no-API `_fill_template` loop (§18.6) |
| Cached session is stale | `python token_cache.py get \| set <token> \| minutes-left` |

### 21.3 Known verify-mode blind spot

`discover.verify_endpoints` does not pass a `body` override, so `past_experiences` legitimately reports `FAILED: HTTP 400` even though the real pipeline handles it correctly via `_past_experiences_body`. Operators must not read that as a regression.

---

## 22. Security, privacy and compliance

### 22.1 Secrets

| Secret | Storage | Risk | Mitigation |
|---|---|---|---|
| MahaRERA guest token | `.guest_token_cache.json`, **plaintext, default permissions**, beside the source | Low | Short-lived (~100 min), guest-scope, public data only |
| `ANTHROPIC_API_KEY` | environment variable | Medium | Never written to disk by this system; never logged |
| `XAI_API_KEY` | environment variable | Medium | Optional; absent → that fallback is simply skipped |
| Promoter PAN / GSTIN | passed on the CLI; echoed into `output/<reg>/gst_portal_raw/` filenames and JSON | **Medium — PII** | See §22.3 |

### 22.2 Ethical and legal boundaries — explicitly recorded in code

1. **No CAPTCHA is ever read or solved by the system.** Stated in `session_auth.py`, `gst_portal.py` and `mahabhumi.py`. A human solves every one.
2. **Google Maps scraping is opt-in and carries a ToS caveat.** `COMPANY_CHARTER_USE_MAPS_SCRAPE` defaults off; the code comment states plainly that it scrapes the consumer UI rather than the paid API and "may not comply with Google's Terms of Service."
3. **Nominatim is rate-limited to ~1 req/s** with a descriptive `User-Agent` identifying the tool as a low-volume personal research tool.
4. **DMS requests set `Origin` and `Referer`** mimicking the real detail page. This is anti-bot accommodation, not authentication bypass — the endpoints are public and require the guest token the site itself mints.
5. **Group-company data is taken only from the source that publishes it freely** (ZaubaCorp); paywalled mirrors are detected (`_looks_paywalled`) rather than circumvented.

### 22.3 Data protection observations

- **`output/` is gitignored**, which is what keeps promoter PII, downloaded documents and GST filing data out of version control. This is load-bearing.
- **`source_trust_registry.json` IS committed.** It contains open-web domains and hit counts, not project data — acceptable, and desirable, because it makes trust promotions reviewable in version control.
- **`.facts.json` is the unscrubbed record** — it retains process failures, file paths and clean-check text that the External document deliberately strips. It must be treated as an **Internal-classification artefact** and never forwarded to a counterparty.
- **`gst_portal_raw/` contains full-page screenshots** of GST portal responses keyed by PAN and GSTIN. These are PII-bearing and are retained indefinitely.
- **`output/_history/` grows without bound.** No retention policy exists.

### 22.4 Document classification

| Artefact | Classification | Distribution |
|---|---|---|
| `*_External.pdf` | Client-shareable | Counterparty / investor |
| `*_Internal.pdf` | Internal — Integrow Asset Management | Internal only |
| `*.facts.json` | **Internal, unscrubbed** | Never leaves the system |
| `<REG>_summary.pdf` | Internal working document | Internal |
| `output/<reg>/raw/*` | Internal | Internal |
| `gst_portal_raw/*` | **Internal, PII-bearing** | Internal |

The External document's own classification line reads **"Strictly Private and Confidential"**; the Internal one reads **"Internal -- Integrow Asset Management"**.

---

## 23. Testing architecture

### 23.1 Shape

28 `test_*.py` files at repository root. No `tests/` package, no `conftest.py`, no CI configuration, no `pyproject.toml`. Run with `python -m pytest -q`.

### 23.2 Three kinds of test

```
   (1) FEATURE TESTS
       Ordinary behaviour: scoring bands, parsing, portfolio maths.
       e.g. test_developer_score.py, test_gst_compliance.py,
            test_promoter_portfolio.py

   (2) POLICY TESTS
       Assert an EDITORIAL RULE holds in rendered output.
       e.g. test_clean_check_scrubber.py -- the Pranami litigation bullet
            must NOT survive, while the Lis Pendens finding MUST, and the
            Developer Score RERA-Compliance note must be UNTOUCHED.
            test_external_citations.py, test_flag_gap_pointers.py

   (3) GUARD-INTEGRITY TESTS   <-- the unusual category
       Exist specifically to stop a guard rotting, not to test a feature.
         . test_guardrails_doc.py -- fails if any symbol NAMED IN
           guardrails.md stops existing
         . malformed model replies are discarded
         . scrubbing never moves the Developer Score
         . a refusal leaves no half-written .docx
         . Section A never reaches an API request -- and this test now
           asserts the marker is PRESENT before asserting it is ABSENT,
           after it was found PASSING VACUOUSLY
```

That last detail is the most instructive thing in the test suite: a guard test that passes for the wrong reason is worse than no test, and the fix was to assert the precondition first.

### 23.3 The cheap verification loop

Because `_fill_template` makes no API calls when run off a saved `facts.json`, the majority of rendering and editorial behaviour is testable **for free**. Only per-finding research needs live API calls.

### 23.4 Known fixture fragility

`CHARTER_RESHAPE_SPEC.md` Task 0 documents a fixture-naming drift that once broke 16 tests, 13 of them on Windows too, with six further failures cascading because one test builds a scratch file later tests consume. The lesson recorded there stands: `docx2pdf` is Windows-only and those tests need `pytest.importorskip("docx2pdf")` to keep the suite runnable on Linux/CI.

---

## 24. Deployment and operations

### 24.1 Runtime requirements

| Requirement | Detail |
|---|---|
| Python | 3.11+ (uses `str \| None` syntax throughout) |
| OS | **Windows required for the PDF deliverable** (`docx2pdf` → Word COM). Everything else is cross-platform |
| Browser | Chromium via `playwright install chromium` |
| OCR | Tesseract with `eng` + `mar`; `tessdata/` is bundled in-repo. `TESSERACT_CMD` or the two standard Windows installer paths |
| Word | Microsoft Word installed, for COM automation |
| Network | Outbound HTTPS to the systems in §15 |
| Human | Present at the keyboard for CAPTCHA solves |

`requirements.txt`:

```
playwright>=1.44      requests>=2.31       reportlab>=4.0
streamlit>=1.32       anthropic>=0.40      python-docx>=1.1
pymupdf>=1.24         pytesseract>=0.3     Pillow>=10.0
beautifulsoup4>=4.12  docx2pdf>=0.1.8
```

`matplotlib` is used by `executive_briefing.py` but is **not listed** — an undeclared dependency (see §25).

### 24.2 Environment variables

| Variable | Consumer | Effect if unset |
|---|---|---|
| `ANTHROPIC_API_KEY` | `deep_research` (implicitly, via `Anthropic()`) | Deep research and Charter generation fail — never fatally |
| `XAI_API_KEY` | `_verify_document_claim_via_grok` | The Grok second-opinion path is skipped |
| `TESSERACT_CMD` | `company_charter`, `gst_portal`, `mahabhumi` | Falls back to PATH, then two Windows installer paths, then `"[OCR unavailable]"` |
| `TESSDATA_PREFIX` | set by the code to the project-local `tessdata/` | `mahabhumi` **overwrites**; `gst_portal` uses `setdefault` — because `TESSDATA_PREFIX` *replaces* the search path, `eng.traineddata` must be copied in alongside `mar.traineddata` |
| `COMPANY_CHARTER_USE_MAPS_SCRAPE` | `_refine_distances_with_maps` | Must equal `"1"` exactly; otherwise the whole Maps upgrade is skipped |
| `CHARTER_ALLOW_UNCHECKED` | `run_company_charter` | `1`/`true`/`yes` downgrades the blocking review to advisory. **A decision to ship an unchecked document, not a convenience** |

### 24.3 Entry points

```
   PRIMARY
   python main.py <REG_NO|project name> [--gstin X | --pan Y] [--headed]
                  [--token T] [--no-auto-auth] [--captcha-timeout N]
                  [--project-id N] [--output-dir D] [--verify]
   streamlit run app.py

   COMPONENT RETRY
   python deep_research.py <REG_NO> [--output-dir] [--no-rebuild] [--force-refresh]
   python company_charter.py <REG_NO>
   python finalize_report.py <REG_NO> [--output-dir D]

   PRE-RERA INTAKE
   python promoter_intake.py <CIN> [company_name] [--output-dir D]
   python cts_intake.py <district> <office> <village> <cts> <mobile>
   python attach_rera.py <case_id> <reg_no> [--output-dir D]

   HUMAN-IN-THE-LOOP LAND RECORDS
   python mahabhumi.py offices <district>
   python mahabhumi.py villages <district> <office_label>
   python cts_resolve.py offices <reg_no>
   python cts_resolve.py villages <reg_no> "<office>"
   python cts_resolve.py candidates <reg_no> "<office>" "<village>" <cts_query>
   python cts_resolve.py finalize <reg_no> "<office>" "<village>" <cts> <mobile>

   GST
   python gst_intake.py <PAN|GSTIN> <reg_no> [--output-dir]
   python gst_portal.py pan <PAN>
   python gst_portal.py filing <GSTIN>

   SESSION
   python token_cache.py get | set <token> | minutes-left
     exit 0 on success, 1 if stale/absent, 2 on bad usage -- designed for
     shell and Makefile use

   SECOND BUILDER
   python run_charter_pipeline.py    (prepare/finish seam)
   python build_report.py            (one-off, per engagement)
```

### 24.4 Operational checklist — before calling a run done

Straight from `CLAUDE.md`:

- ✅ Correct builder used: `company_charter.py::_fill_template` via `run_company_charter`. `charter_report.py` is a different document for a different request; `charter_document.py` builds nothing at all any more.
- ✅ A **real** monitor-flag resolution pass — flags re-checked against current facts, not carried over stale.
- ✅ The review stage ran, or its failure was **reported rather than passed over**.
- ✅ Output only in `output/company_charters/`.
- ✅ Any script hitting `_fill_template` directly called `_convert_docx_to_pdf` itself.

---

## 25. Known limitations, technical debt and risks

### 25.1 Deliberate, documented, do-not-fix

| Item | Why it stays |
|---|---|
| **Group/Affiliated Companies count contradiction** — 65 linked entities vs "of 299" in the Director Relationship Map | A real disagreement in the source data. Silently choosing one side would present a guess as a fact |
| **Internal document library's 61 rows** — 10 labelled only `Other – Legal`, 8 filenames appearing twice | Same reason |
| **Developer Score never renormalises** | A promoter with less publicly-verifiable data *should* structurally score lower |
| **Flag headlines byte-identical to their gap** on single-sentence gaps (11 of 17 on Pranami) | Explicitly *"do not fix by truncating"* |
| **`charter_document.py` looks like dead code** | It is a live shared library. 16 symbols. Do not delete |
| **`_verify_external_document_quality` treats "Document Library" as a violation** | Vestigial from when the section was Internal-only. Must be **narrowed** to the leftover template placeholder text before an External document library ships — otherwise every External save hard-fails |

### 25.2 Technical debt

| # | Item | Impact | Suggested action |
|---|---|---|---|
| D1 | **`company_charter.py` is 10,182 lines / 591 KB** with six distinct responsibilities | Severe cognitive load; merge-conflict magnet; the file's own analysis needs tooling to read | Extract, in order of independence: registry lookups → `registries.py`; editorial passes → `editorial.py`; scoring → `scoring.py`; rendering primitives → `docx_helpers.py`. Keep `_fill_template` and `run_company_charter` together |
| D2 | **No packaging manifest and no CI** | The suite is run by hand; drift is invisible until someone runs it | Add `pyproject.toml` + a GitHub Actions job running `pytest -q` with `docx2pdf` skipped |
| D3 | **`matplotlib` is an undeclared dependency** | `executive_briefing.py` fails on a clean install | Add to `requirements.txt` |
| D4 | **Sonnet 5 intro pricing expires 2026-08-31** | Every cost figure under-reports by a third afterwards | Update `_PRICING_PER_1M_TOKENS` to `$3.00`/`$15.00`; better, add a dated pricing table |
| D5 | **`docx2pdf` makes the deliverable Windows-only** | No Linux/container deployment path | Evaluate LibreOffice `--headless --convert-to pdf` as a fallback converter |
| D6 | **Two conditional sections sit outside the "locked ten"** | Latent inconsistency with a Section A rule | Either fold them into Diligence Appendix as H2, or amend the rule to say "ten *unconditional* sections plus two conditional appends" |
| D7 | **`fetch_gstin_filing_table`'s docstring is stale** — it claims it does not parse into the record shape; `parse_filing_table` does exactly that, confirmed against 76 periods | Misleads a reader | Update the docstring |
| D8 | **`gst_compliance.summarize_filing_pattern` `as_of` defaults to today** | Re-rendering the same `facts.json` later silently produces a different `delays_last_12_months` | Persist the `as_of` used into `facts["gst_compliance_check"]` and re-use it on re-render |
| D9 | **`output/_history/` and `gst_portal_raw/` grow without bound** | Disk; PII retention | Define and implement a retention policy |
| D10 | **`token_cache` has no atomic write or file lock** | A concurrent save/load can observe a truncated file (degrades to "no token", so low severity) | Write-to-temp-then-rename |
| D11 | **`run_archive` is not safe for two concurrent runs of the same reg-no** | Timestamp collision check is not atomic | Advisory lock file per reg-no |
| D12 | **`_fill_template` is non-reentrant** (module-global `_ACTIVE_EXTERNAL_FACTS` + monkey-patched `add_paragraph`) | Cannot be parallelised across variants | Document it (done here); consider a context object instead of a global |
| D13 | **`executive_briefing.py` has no caller and no `__main__`** | Unreachable except by import | Add a CLI, or record it as library-only in `CLAUDE.md` |
| D14 | **`build_report.py` hardcodes one engagement's paths** | Will fail for any other project | It says so itself — "delete or rewrite per engagement" |
| D15 | **Grok verification calls are not recorded in the usage ledger** | Cost blind spot | Route through `_record_usage` with its own label and pricing entry |

### 25.3 Operational risks

| Risk | Likelihood | Impact | Mitigation in place | Gap |
|---|---|---|---|---|
| MahaRERA changes an endpoint or selector | High | Run fails or silently under-collects | `confirmed`/`observed` tiering; `--verify` mode; envelope normalisation | No scheduled canary run |
| MahaRERA changes the CAPTCHA mechanism | Medium | Full auth path breaks | `--token` manual escape hatch | — |
| A registry mirror goes paywalled | Medium | One of three chain members lost | `_looks_paywalled`; 3-way chain; roster conflicts surfaced | — |
| GST portal layout changes | Medium | GST sub-metric unscored | Graded `note` strings; never fatal | — |
| Maha Bhulekh Marathi labels change | Low | District map misses | `_DISTRICT_NAME_MAP` with pre-rename aliases; never fuzzy-matches | Map is manual |
| Anthropic API outage | Low | Research + Charter degrade | Never fatal; standalone retry commands | — |
| Word/COM unavailable on the run host | Medium | **No PDF — the deliverable** | `.docx` remains on disk as the fallback output | D5 |
| Template `.docx` corrupted or lost | Low | **Charter generation impossible** | Section A mandates a timestamped backup before structural change | Backup is manual; template is gitignored |

---

## 26. Appendices

### Appendix A — Complete file inventory

**Application modules (29):** `main.py` · `app.py` · `config.py` · `resolver.py` · `session_auth.py` · `token_cache.py` · `api_client.py` · `discover.py` · `run_archive.py` · `promoter_portfolio.py` · `deep_research.py` · `gst_intake.py` · `gst_portal.py` · `gst_compliance.py` · `mahabhumi.py` · `cts_intake.py` · `cts_resolve.py` · `promoter_intake.py` · `attach_rera.py` · `company_charter.py` · `charter_report.py` · `charter_document.py` · `charter_research_prep.py` · `run_charter_pipeline.py` · `executive_briefing.py` · `report.py` · `build_report.py` · `finalize_report.py`

**Markdown (7):** `CLAUDE.md` · `rules.md` · `guardrails.md` · `README.md` · `CHARTER_RESHAPE_SPEC.md` · `.claude/skills/maharera-report/SKILL.md` · `output/company_charters/CHARTER_RESHAPE_CHANGE_LOG.md`

**Config / state (5):** `requirements.txt` · `.gitignore` · `source_trust_registry.json` · `.guest_token_cache.json` · `.claude/settings.local.json`

**Binary assets:** `tessdata/eng.traineddata` (4.1 MB) · `tessdata/mar.traineddata` (3.2 MB) · `RERA_Executive_Tracker.xlsx` · `docs/*.docx` (11 design artefacts)

**Tests (28):** listed in §6.2.

### Appendix B — Usage labels (the cost ledger's key space)

`research_generate` · `verify_claim_batch` · `gap_retry_batch` · `gap_retry_verify_batch` · `charter_pass` · `material_claim_verify` · `document_grounding_verify` · `citation_completeness_judge` · `second_source_verify` · `clean_check_judge` · `citation_match` · `flag_headline` · `finding_research` · `claude_md_doc_review` · `agentic_pass` *(default)*

Each label is a separable cost line in `usage_summary.json` and in the run summary. `claude_md_doc_review` in particular is separable **by design**, so the cost of the compliance gate is visible and arguable.

> As of 2026-08-13, `verify_claim_batch` / `gap_retry_batch` / `gap_retry_verify_batch` replaced the bare `verify_claim` / `gap_retry` / `gap_retry_verify` labels — one call now covers a whole chunk of sources or a whole retry round, not one source or gap. `deep_research._verify_claim` (the single-claim function) still exists and is still called directly, but only with an explicit label override (`material_claim_verify`), so the bare `verify_claim` label itself no longer appears in the ledger.

### Appendix C — Exception inventory

| Exception | Module | Meaning |
|---|---|---|
| `ProjectNotFoundError` | `resolver` | Search returned zero candidates |
| `CaptchaTimeoutError` | `session_auth`, `gst_portal`, `mahabhumi` | Nobody solved it in time |
| `BrowserClosedError` | `session_auth`, `gst_portal`, `mahabhumi` | Window closed / page unreachable |
| `AmbiguousSelectionError` | `mahabhumi` | Carries `hint` and `options` so the caller can re-present the real list |
| `CategoryFetchError` | `api_client` | Carries `.category` and `.status_code` so callers can distinguish 401/403 |
| `GstIntakeError` | `gst_intake` | Intake produced no scoreable filing data |
| `BudgetExhausted` | `deep_research` | A search/token budget ran dry mid-call; the "answer was malformed" and "ran out of room" cases are kept distinguishable |
| `CostCapExceeded` | `deep_research` | Refused a call because this run's total spend (§16.3) is already at or over `PIPELINE_COST_CAP_USD`; unlike `BudgetExhausted`, **never retried** |
| `CharterComplianceError` | `company_charter` | **The only class in the module.** Carries `.results`. Blocks the PDF |
| `RuntimeError` | `company_charter` | Preflight failure, External gate failure |
| `FileNotFoundError` | `company_charter`, `finalize_report` | Missing template / missing `raw/` |

### Appendix D — Glossary

| Term | Meaning |
|---|---|
| **Charter** | The Company Charter document pair (Internal + External). The primary deliverable |
| **Clean check** | A check that ran and came back clear. Under Section B it produces **no sentence at all** |
| **Gap** | An open unknown the reader can act on. *"We looked and could not establish this."* Never compressed or deleted |
| **Finding** | Something established. Earns a dedicated follow-up research pass |
| **Absence** | *"We checked and it is clear."* Deleted, with three carve-outs |
| **Flag** | A one-sentence headline in Overview & Flags ending in `(Gap N)`. Severity: imminent / structural / monitor |
| **`confirmed` / `observed`** | Endpoint trust levels. `confirmed` = payload individually re-verified; `observed` = taken from real traffic, not re-verified |
| **Variant** | `internal` or `external` — the fork at render time |
| **Carryover** | A pre-RERA intake result (`promoter_profile` / `land_record`) attached to a reg-no by an explicit human action |
| **QRMP** | Quarterly Return Monthly Payment — the GST scheme whose due dates are state-dependent |
| **CTS** | City Survey number — the Maharashtra land parcel identifier |
| **Property Card** | मालमत्ता पत्रक, the Maha Bhulekh land record |
| **Never fatal** | A stage wrapped so that its failure logs a warning and the run continues |
| **Hard gate** | A guard that stops the run or blocks a save/deliverable |

### Appendix E — Architecture decision record (retrospective)

| ADR | Decision | Rationale | Status |
|---|---|---|---|
| ADR-01 | Read the `View Details` href instead of clicking it | A click fires a JS `confirm()` and lands on the CAPTCHA page; the href is unauthenticated | Accepted |
| ADR-02 | Never solve a CAPTCHA programmatically | Ethics and ToS; stated in three modules | Accepted, load-bearing |
| ADR-03 | Cache the guest token for 90 min against a ~100 min TTL | A token must not expire mid-run | Accepted |
| ADR-04 | Per-category thread isolation with per-worker `Session` | `requests.Session` thread-safety is not guaranteed; the reuse benefit is marginal | Accepted |
| ADR-05 | Tier endpoints `confirmed` vs `observed` in config | Makes the gap between "we think this works" and "it works" machine-readable and visible on the deliverable | Accepted, unusual and valuable |
| ADR-06 | Put content rules in `rules.md` and parse them at runtime | One source of truth for both humans and prompts | Accepted |
| ADR-07 | Never send Section A to an API | Coding-time guidance is not content guidance; asserted by test | Accepted |
| ADR-08 | Generate the External document entirely in code | A model cannot be trusted to apply citation rules deterministically | Accepted; means rule edits alone cannot change External behaviour |
| ADR-09 | Block the PDF on a semantic review, but verify every quoted violation mechanically | Reduces the model's role to "point at the text"; an invented quote cannot block | Accepted; the key safety argument |
| ADR-10 | Keep a deterministic predecessor behind every model pass, keyed so a miss is indistinguishable from "no model ran" | A model can only match or improve, never degrade | Accepted |
| ADR-11 | Scrub the page, restore the record | The record must keep what the page drops | Accepted |
| ADR-12 | Never renormalise the Developer Score | Less disclosure *should* score lower | Accepted |
| ADR-13 | Always renormalise Documentation Confidence | It scores this document's sourcing, where N/A is genuinely not applicable | Accepted |
| ADR-14 | Surface disagreeing director rosters rather than picking one | Two contradictory facts beat one invented one | Accepted; caught a real 3-way disagreement |
| ADR-15 | Retire `charter_document.py`'s builder but keep the module | 16 symbols are still imported | Accepted 2026-08-10 |
| ADR-16 | Archive the previous run with `move`, not `copy` | No disk doubling; guarantees a clean target directory | Accepted |
| ADR-17 | Make GST and land-record lookups opt-in | Each costs a human CAPTCHA solve | Accepted |
| ADR-18 | Route every LLM call through one transport | Single place for usage accounting, JSON parsing and failure policy | Accepted |

---

*End of Software Architecture Document.*

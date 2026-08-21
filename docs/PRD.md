# Product Requirements Document (PRD)
## RERA Scrapper — Pan-India RERA Due-Diligence & Company Charter Pipeline

| Field | Value |
|---|---|
| **Document** | Product Requirements Document |
| **Product** | RERA Scrapper |
| **Version** | 2.0 |
| **Date** | 21 August 2026 |
| **Status** | Baseline — documents the product as built, plus forward roadmap |
| **Owner** | Integrow Asset Management |
| **Companion documents** | `SAD.md` (architecture), `CLAUDE.md` (flow), `rules.md` (content rules), `guardrails.md` (guards) |

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Problem statement](#2-problem-statement)
3. [Background and context](#3-background-and-context)
4. [Goals and non-goals](#4-goals-and-non-goals)
5. [Success metrics](#5-success-metrics)
6. [Users and personas](#6-users-and-personas)
7. [User journeys](#7-user-journeys)
8. [Product scope — the feature map](#8-product-scope--the-feature-map)
9. [Functional requirements](#9-functional-requirements)
10. [Content and editorial requirements](#10-content-and-editorial-requirements)
11. [Deliverable specifications](#11-deliverable-specifications)
12. [Non-functional requirements](#12-non-functional-requirements)
13. [Governance, quality and compliance requirements](#13-governance-quality-and-compliance-requirements)
14. [Data requirements](#14-data-requirements)
15. [Constraints, assumptions and dependencies](#15-constraints-assumptions-and-dependencies)
16. [Out of scope](#16-out-of-scope)
17. [Acceptance criteria — definition of done](#17-acceptance-criteria--definition-of-done)
18. [Release history and roadmap](#18-release-history-and-roadmap)
19. [Risks and mitigations](#19-risks-and-mitigations)
20. [Open questions](#20-open-questions)
21. [Appendices](#21-appendices)

---

## 1. Executive summary

**RERA Scrapper turns a single RERA registration number -- from any of six state authorities -- into a defensible, source-cited due-diligence pack in one command.**

Real-estate underwriting requires assembling a picture of three things — the **counterparty** (the promoter company), its **promoters** (the individuals behind it), and the **collateral** (the project and its land). That picture is scattered across six state regulators' portals (each with its own vendor, format and gaps), three corporate-registry mirrors, three credit-rating agencies, an insolvency board, a GST portal, a Marathi-language land-records system, an open case-law index, and the open web. It is also rarely confined to one state: a promoter's real track record is usually held under OTHER companies, on OTHER states' registers. Assembling it by hand takes an analyst days, produces inconsistent output, and — most importantly — leaves no audit trail of what was checked versus what was found.

This product automates that assembly and, critically, **governs what the resulting document is allowed to say**. It produces a paired **Company Charter** — an Internal variant that keeps every process failure and full bibliographic sourcing, and an External variant that is client-shareable with numbered citations — plus a RERA project summary PDF, a Developer Score, a Documentation Confidence Score, and a numbered, actionable gap list.

The product's distinguishing characteristic is not that it scrapes. It is that **it refuses to ship a document it cannot show to be compliant**, and that it never invents a fact to fill a hole.

```
   ONE COMMAND                                    THE PACK
   -----------                                    --------
                                       +---> Company_Charter_..._Internal.pdf
   python main.py P51800012345         |     Company_Charter_..._External.pdf
     [--state MH|GJ|KA|TG|JH|WB]       |     Company_Charter_....facts.json
     [--group-sweep] [--group-gst]  -->+     <REG_NO>_summary.pdf
     [--group-litigation]              |     documents/ + complaint_orders/
     [--gstin X | --pan Y]             |     usage_summary.json (cost ledger)
                                       +---> a numbered, actionable gap list
   1 human CAPTCHA solve                     ...each section stating its own
   (+1 per GST lookup)                       COVERAGE, not just its findings
```

---

## 2. Problem statement

### 2.1 The user's problem

An underwriter evaluating a Maharashtra real-estate opportunity must answer, defensibly:

| Question | Where the answer lives today |
|---|---|
| Is this developer real, or a single-project vehicle? | MahaRERA promoter search + past-experience records, manually cross-read |
| What is their track record — delivered area, on-time rate, complaints? | Every one of their other registered projects, one page at a time |
| Who actually controls the company? | ZaubaCorp, Tofler, InstaFinancials — **which frequently disagree** |
| Are they insolvent, or in proceedings? | IBBI public search |
| Are they rated? By whom? | ICRA and Infomerics, separately |
| Are they filing GST on time? | GST portal, one CAPTCHA per lookup |
| Is the land what they say it is? | Maha Bhulekh Property Card — Marathi-only, CAPTCHA per call |
| Is the project litigated? | MahaRERA complaints, appeals, orders and judgments |
| What is the micro-market actually doing? | The open web, of highly uneven quality |
| **What did we NOT manage to establish?** | **Nowhere. This is the question nobody records.** |

That last row is the real problem. A manual diligence pack tells you what was found. It rarely tells you, in a form an investment committee can act on, **what was looked for and not found** — and it almost never distinguishes *"we checked and it is clear"* from *"we looked and could not establish this."* Those are completely different risks.

### 2.2 The failure modes this product exists to prevent

```
   FAILURE MODE                        PRODUCT RESPONSE
   ------------                        ----------------

   "No litigation found against the    Section B: A CLEAN CHECK PRODUCES NO
    promoter, the project or the       SENTENCE. Three citations spent
    underlying land in any source      establishing there is nothing to report
    reviewed; MahaRERA's records       is three citations wasted. The line is
    are empty[18]; the Title           DELETED. The scope of what was checked
    Report's 30-year search            stays in the record and does not reach
    returned nothing[1]"               the page.

   Two registries name different       BOTH are surfaced as a roster conflict
   directors; the tool silently        and become a gap that renders in BOTH
   picks one                           document variants. Neither is picked.

   A source is cited that does not     "A missing marker is a lesser failure
   actually support the claim          than a wrong one: leave a clause uncited
                                       rather than borrow an adjacent source."

   A number is quietly guessed to      Anything unconfirmable goes in `gaps`,
   fill a table cell                   verbatim. Two known data contradictions
                                       are DELIBERATELY left unreconciled.

   A model hallucinates and the        Every violation the reviewer reports is
   document is blocked (or shipped)    verified against the document text
   on that basis                       first. An invented quote cannot block a
                                       good document.

   An API outage silently deletes      run_finding_research: a researcher that
   a real finding                      raises costs ONLY its own finding, and
                                       no finding is ever deleted whatever the
                                       researcher returns.

   The document gets cleaner while     Scrub/restore contract: the page drops,
   the record gets thinner every run   the record keeps. .facts.json always
                                       holds the complete text.
```

---

## 3. Background and context

### 3.1 Regulatory context

The Real Estate (Regulation and Development) Act, 2016 requires every qualifying project in India to register with a state authority and publish a defined public record. **MahaRERA** is Maharashtra's authority. The public record includes project details, promoters and associated partners, professionals of record, uploaded documents, complaints, appeals and SRO details.

That record is *public*, but it is not *accessible*: two of nine data categories are open, and the other seven sit behind a short-lived guest session that the portal mints only after a human solves a text CAPTCHA on the project detail page.

Two further public systems matter and are similarly gated:

- **GST portal** (`services.gst.gov.in`) — a taxpayer's return-filing history, one CAPTCHA per lookup.
- **Maha Bhulekh** (`bhulekh.mahabhumi.gov.in`) — the Property Card (मालमत्ता पत्रक) land record, Marathi-only, with a CAPTCHA that regenerates on every partial postback and therefore admits **no reusable session at all**.

### 3.2 Organisational context

Integrow Asset Management underwrites Indian real-estate exposures. The Company Charter is the artefact its investment process consumes. Two audiences exist for the same underlying analysis:

- **Internal** — the underwriting team, who need the process failures, the file names, the unverified items and the full bibliography.
- **External** — the counterparty or co-investor, who need a clean, numbered-citation document with no internal tooling detail in it.

Producing two documents by hand from one analysis is where inconsistency creeps in. **Producing both from one facts dictionary, forking only at render time, is the product's core structural bet.**

### 3.3 Product history

| Date | Milestone |
|---|---|
| — | Initial scraper: resolve, auth, 9 categories, summary PDF |
| — | Agentic deep research added (`deep_research.py`), with per-claim verification and bounded gap retry |
| — | First Company Charter (Pranami Bliss) produced by hand-written Node/docx-js scripts |
| — | `company_charter.py` replaces those scripts: template-filled in place, dual variant |
| — | Promoter portfolio, MCA-mirror chain, credit rating, IBBI, judgments search |
| — | GST intake and compliance scoring; CTS land-record path |
| 2026-07-31 | GST intake confirmed live end-to-end: PAN → GSTIN → **76 scoreable filing periods** spanning 2022-08 to 2026-06 |
| **2026-08-10** | **Charter reshape shipped** (all 7 tasks of `CHARTER_RESHAPE_SPEC.md`): flags-vs-gaps split, clean-check deletion, per-clause External citations, facts-schema corrections, per-finding deep research. `charter_document.py`'s builder retired. Rules migrated from `CLAUDE.md` into `rules.md` |
| 2026-08-11 | Guardrails documentation and the blocking compliance review hardened; this PRD baselined |
| 2026-08-12 | Deep research's own verify/gap-retry fan-out capped with a shared `_VerificationBudget`, after P51800077150's first-ever research pass (no prior research to reuse) ran past $10 before being killed by hand |
| **2026-08-13** | **Hard pipeline-wide cost ceiling added** (`PIPELINE_COST_CAP_USD`, $6.00, refuses a call rather than starting it once a run's total spend across deep research **and** Charter generation reaches the cap). The verify and gap-retry fan-out itself was also **batched** — many sources, or every gap open in a retry round, are now checked/retried in one shared-budget call instead of one call each — and every Claude API call now marks its request cacheable (`cache_control`), with cache-write/cache-read tokens priced and reported separately from plain input tokens |
| **2026-08-17** | **State seam landed.** `states/` package: `StateProfile` (data) + `StateAdapter` (one `acquire()` call), capability declaration, and state resolution from the registration number. `app.py`'s ~160 duplicated lines deleted |
| 2026-08-18 | Gujarat, Karnataka and Telangana adapters |
| 2026-08-19 | Jharkhand and West Bengal adapters, built subject-first rather than by register size. Karnataka run completed end-to-end |
| **2026-08-20** | **Group-level diligence**: entity graph (propose by name, confirm by hard link), group-wide RERA sweep with per-authority coverage, promoter PAN read off the filed card, charge movement, state footprint, CRISIL added |
| **2026-08-21** | **Land records fixed** -- `fields` had been `{}` on every CTS lookup ever made; now 15 fields plus mutation entries. **Group GST** (`gst_group.py`), **group case law** (`litigation_sweep.py`), and promoter-keyed **order registers from four authorities** (K-RERA's five registers including penalties, MahaRERA, JHARERA, WBRERA via its cause lists). MahaRERA's own orders search was found to have been returning nothing for every query, and repaired |

---

## 4. Goals and non-goals

### 4.1 Product goals

| # | Goal | Measurable expression |
|---|---|---|
| **PG1** | **Compress diligence time from days to one command** | One operator, one command, one CAPTCHA solve produces the full pack |
| **PG2** | **Every claim traceable** | 100 % of External factual claims carry a `[N]` marker resolving to a checkable Sources entry |
| **PG3** | **Never present a guess as a fact** | Zero fabricated values; unconfirmable items appear as numbered gaps |
| **PG4** | **Distinguish "clear" from "unknown"** | Clean checks produce no sentence; gaps keep their full explanation |
| **PG5** | **A partial failure never costs the run** | Every optional stage degrades to a warning; the run completes |
| **PG6** | **A non-compliant document does not ship** | The PDF is blocked unless the document can be *shown* to comply |
| **PG7** | **Two audiences, one analysis** | Internal and External render from the same facts dict, forking only at render time |
| **PG8** | **Costs are visible and arguable, and bounded** | Per-label token and USD cost in every run summary and `usage_summary.json`; total run spend cannot exceed `PIPELINE_COST_CAP_USD` |
| **PG9** | **Re-runs are cheap and diffable** | Documents reused, research reused within 24 h, prior runs archived not clobbered |
| **PG10** | **The record outlives the page** | `.facts.json` retains everything the rendered document drops |

### 4.2 Non-goals

| # | Non-goal | Rationale |
|---|---|---|
| NG1 | **Making the investment decision** | The product produces evidence and scores; a human underwrites |
| NG2 | **Solving CAPTCHAs** | Hard ethical and ToS boundary, stated in three modules. A human solves every one |
| NG3 | **Covering all 36 states and UTs** | Six authorities are built (MH, GJ, KA, TG, JH, WB); roughly 24 live portals remain. Each is a separate vendor, schema, language and land system, and is scoped as its own plan -- a state is added when a subject operates there, not to complete a set |
| NG4 | **Real-time monitoring** | Batch, on-demand, human-triggered. Change detection is run-to-run, not continuous |
| NG5 | **Replacing legal title diligence** | The land-record check is corroborative. A Title Report is a lawyer's product |
| NG6 | **A hosted multi-tenant service** | Single-operator, local filesystem, Windows-bound PDF conversion |
| NG7 | **Reconciling genuinely contradictory sources** | Two known contradictions are deliberately preserved. Picking a side would present a guess as a fact |
| NG8 | **Automating the CTS→village resolution** | Marathi office and village labels do not map 1:1 to RERA's English text. Picking the wrong village would misattribute an entire legal land record |

---

## 5. Success metrics

### 5.1 Primary metrics

| Metric | Definition | Target |
|---|---|---|
| **Time to pack** | Command issued → Internal PDF on disk | < 30 min excluding human CAPTCHA wait |
| **Human touches per run** | CAPTCHA solves + interactive picks | 1 (no GST) / 1 + 1 + N (with GST, N = GSTINs) |
| **Citation coverage, External** | Factual claims carrying a supporting `[N]` marker | 100 % (was ~30 % pre-reshape, ~83 % mid-reshape) |
| **Gate pass rate** | Runs reaching PDF without a compliance block | > 95 %, with every block genuinely justified |
| **Category completeness** | Categories fetched / 9 | 9/9 with a session; 2/9 is a valid degraded outcome |
| **Cost per pack** | `usage_summary.json` total `cost_usd` | Tracked per run; per-label attribution is mandatory; total spend across deep research and Charter generation combined is hard-capped at `PIPELINE_COST_CAP_USD` ($6.00) |

### 5.2 Quality metrics

| Metric | Definition | Target |
|---|---|---|
| **Clean-check leakage** | Absence statements surviving into a rendered document | 0 |
| **Cited absences** | `[N]` markers attached to a nothing | 0 (hard-gated) |
| **Orphan sources** | Sources listed but never cited | 0 |
| **Mis-citations** | A marker that does not support its clause | 0 tolerated; a missing marker is preferred to a wrong one |
| **Duplicate reporting** | The same fact or absence appearing in two places | 0 |
| **Findings lost to tooling failure** | Findings deleted by a failed research call | **0 — architecturally guaranteed** |
| **Guard-symbol drift** | Symbols named in `guardrails.md` that no longer exist | 0 (test-enforced) |

### 5.3 Leading indicators of trouble

| Indicator | What it means |
|---|---|
| `--verify` reports a `confirmed` endpoint failing | MahaRERA changed something load-bearing |
| Rising `verification_error` gap count | Anthropic API instability, or an expiring key |
| `roster_conflicts` appearing frequently | Registry mirrors drifting apart — a *feature* signal, not a bug |
| `truncated: true` on portfolios | `PROMOTER_PROJECT_LIMIT` (25) is biting; the score may understate |
| Compliance blocks clustering on one rule | That rule's deterministic pass is under-implemented |

---

## 6. Users and personas

```
+==========================================================================+
|  PERSONA 1 -- THE ANALYST  (primary user, ~90% of runs)                  |
+==========================================================================+
|  Goal        Produce a diligence pack for a named project, today.        |
|  Context     Windows laptop, Word installed, at the keyboard.            |
|  Inputs      A registration number, sometimes only a project name,       |
|              sometimes a PAN from the promoter's own document set.       |
|  Behaviour   Runs `python main.py <REG>`, solves one CAPTCHA, walks      |
|              away, comes back to a PDF pair.                             |
|  Needs       Speed. A clear run summary. To know what FAILED and how to  |
|              retry just that piece.                                      |
|  Pain if     A partial failure kills the whole run and wastes the        |
|  unserved    CAPTCHA solve.                                              |
+==========================================================================+
|  PERSONA 2 -- THE UNDERWRITER / IC MEMBER  (document consumer)           |
+==========================================================================+
|  Goal        Decide. Quickly, and defensibly.                            |
|  Context     Reads the Internal PDF; may forward the External one.       |
|  Needs       Overview & Flags on page one. A Developer Score with its    |
|              workings shown. A numbered gap list they can hand back      |
|              as questions for the developer.                             |
|  Pain if     Two pages of "nothing was found" prose before the first     |
|  unserved    real finding. A score they cannot interrogate.              |
+==========================================================================+
|  PERSONA 3 -- THE COUNTERPARTY / CO-INVESTOR  (External doc reader)      |
+==========================================================================+
|  Goal        Understand what was assessed and on what basis.             |
|  Needs       Numbered citations they can actually check. Descriptive     |
|              source labels, not internal filenames.                      |
|  Must NEVER  A file path, module name, JSON key, raw exception string,   |
|  see         an internal tooling failure, or a bare CIN in prose.        |
+==========================================================================+
|  PERSONA 4 -- THE ENGINEER  (maintainer)                                 |
+==========================================================================+
|  Goal        Change one thing without breaking three.                    |
|  Needs       CLAUDE.md for flow, rules.md for content, guardrails.md     |
|              for guards. Symbols not line numbers. A free, no-API        |
|              render loop for verification.                               |
|  Pain if     A guard that looks redundant and is not. Stale docs that    |
|  unserved    describe a builder that was deleted.                        |
+==========================================================================+
|  PERSONA 5 -- THE ORCHESTRATOR  (LLM agent, for charter_report.py)       |
+==========================================================================+
|  Goal        Run the "Counterparty + Collateral" document, which needs   |
|              per-director web research an agent must spawn.              |
|  Needs       A clean prepare()/finish() seam that returns prompts and    |
|              accepts parsed replies.                                     |
+==========================================================================+
```

---

## 7. User journeys

### 7.1 Journey A — the standard run (Analyst, happy path)

```
   ANALYST                    SYSTEM                          ARTEFACTS
   -------                    ------                          ---------

   python main.py
   P51800012345  ------------>
                              resolve via public search
                              (no login, no CAPTCHA)
                          <-- "[OK] Resolved to internal
                               project ID: 23600"

                              no cached session
                          <-- opens a VISIBLE browser
   solves the CAPTCHA -------> token minted, cached 90 min --> .guest_token_cache
                          <-- "[OK] Using session"

   [walks away]               archive previous run       ----> output/_history/...
                              fetch 9 categories (8 in
                                parallel)                ----> raw/*.json
                              download documents (8
                                workers, reuse prior)    ----> documents/*.pdf
                              download complaint orders  ----> complaint_orders/
                              build promoter portfolio   ----> promoter/portfolio.json
                              agentic deep research      ----> research/deep_research.json
                                (reuses confirmed sources
                                 if < 24 h old)
                              generate Company Charter   ----> company_charters/*
                                5-way parallel checks
                                editorial passes
                                render Internal, External
                                *** COMPLIANCE REVIEW ***
                                convert both to PDF
                              build summary PDF          ----> <REG>_summary.pdf
                              write usage log            ----> usage_summary.json

                          <-- Run summary:
                              auth source, per-category counts,
                              documents downloaded, promoter_profile,
                              market_research, gst_filing_intake,
                              company_charter, claude_api_usage by label

   [returns]                                             ====> THE PACK
```

### 7.2 Journey B — the run with GST (one extra decision, several extra solves)

```
   ANALYST                          SYSTEM
   -------                          ------
   Finds the promoter's PAN in
   MahaRERA's own document set
   (RERA always includes a PAN
    card and never a GSTIN --
    which is why --pan is the
    flag you usually have)
          |
          v
   python main.py P518... --pan AANCP0234D
          |
          v
                                    ... stages 1-6 as above ...
                                    |
                                    v
                          STAGE 7 -- GST INTAKE
                          format-validate the PAN FIRST
                          (a typo must never cost a human
                           a CAPTCHA solve)
                                    |
   solve CAPTCHA #2 <--------------- search_gstins_by_pan
                                    -> every GSTIN under the PAN,
                                       across every state
                                    |
   solve CAPTCHA #3..N <------------ fetch_gstin_filing_table per GSTIN
                                    ONE solve then walks ALL financial
                                    years in the same session
                                    |
                                    v
                          parse GSTR-1 / GSTR-3B periods only
                          (CMP08, GSTR9/9C, GSTR1A deliberately NOT
                           parsed: no statutory due-date rule is
                           implemented, so scoring them would be a
                           guess, not a computation)
                                    |
                                    v
                          primary GSTIN = the one with the MOST periods
                          -> gst_filing_input.json
                                    |
                                    v
                          STAGE 9: run_gst_compliance_check reads it
                          -> statutory due dates -> delays
                          -> GST Compliance sub-metric (7.5% weight)
                          -> flags if late_pct or recent delays breach
                             _FLAG_THRESHOLDS

   IF ANYTHING FAILS: one unscored sub-metric. NEVER the run.
```

### 7.3 Journey C — the land-record path (human-in-the-loop, four steps)

```
   WHY THIS IS MANUAL:
   Maha Bhulekh's office and village names are Marathi-only and do NOT
   map 1:1 to RERA's English district/taluka/village text. Confirmed live:
   one real project's OFFICE was "...,Andheri" while its own VILLAGE was
   "Aambivali" -- one office's jurisdiction covers several villages.
   Picking the wrong village would misattribute an entire legal land
   record to the wrong place. So the system NEVER guesses a label.

   STEP 0   python company_charter.py <REG>   (at least once, to have facts)
              |  run_cts_land_lookup auto-fetches office candidates once
              |  per reg_no, and re-emits the reminder gap on EVERY run
              |  until cts_lookup_input.json exists -- so this cannot go
              |  quiet and get forgotten after the first mention
              v
   STEP 1   python cts_resolve.py offices <REG>        [headless, no CAPTCHA]
              -> cts_office_candidates.json
              -> ANALYST PICKS the office
              v
   STEP 2   python cts_resolve.py villages <REG> "<office>"   [headless]
              -> cts_village_candidates.json
              -> ANALYST PICKS the village
              v
   STEP 3   python cts_resolve.py candidates <REG> "<office>" "<village>" <cts>
              -> cts_number_candidates.json                   [headless]
              -> ANALYST PICKS the exact CTS sub-division
              v
   STEP 4   python cts_resolve.py finalize <REG> "<office>" "<village>" <cts> <mobile>
              -> cts_lookup_input.json  {district, office, village,
                                         cts_number, mobile}
              v
   STEP 5   python main.py <REG>   (or company_charter.py <REG>)
              -> run_cts_land_lookup finds the input file
              -> opens a VISIBLE browser
              -> ANALYST SOLVES THE CAPTCHA
              -> Property Card scraped (+ OCR mar+eng, + screenshot)
              -> facts["cts_land_record_check"]
              -> *** CTS cross-checked against RERA's own
                     survey_cts_plot_numbers; a genuine mismatch becomes
                     facts["cts_mismatch_note"] and is promoted to an
                     IMMINENT flag ***
```

### 7.4 Journey D — pre-RERA intake and later attachment

```
   A CIN or a CTS number often reaches the desk BEFORE a RERA number exists.

   TODAY                                        LATER
   -----                                        -----
   python promoter_intake.py <CIN> "<name>"
     -> MCA profile + IBBI + group companies
        + credit rating (needs the name)
     -> output/_pending/<CIN>/promoter_profile.json
        *** written with the EXACT facts.json keys ***
                    |
   python cts_intake.py <district> <office>     |
                       <village> <cts> <mobile> |
     -> output/_pending/<slug>/land_record.json |
        key: cts_land_record_check              |
                    |                            |
                    +----------------------------+
                                 |
                                 v
                    python attach_rera.py <case_id> <REG>
                      COPIES (never moves) into output/<REG>/
                      as *_carryover.json
                      "an explicit action, never auto-matched"
                                 |
                                 v
                    python main.py <REG>
                      reuses the promoter profile INSTEAD of re-running
                        live CIN checks -> the 5-way fan-out skips four
                        of its five branches
                      reuses the land record INSTEAD of opening a new
                        CAPTCHA browser
                      cross-checks CTS -> IMMINENT flag on mismatch
```

### 7.5 Journey E — recovery

```
   SOMETHING FAILED             DO THIS                        COST
   ----------------             -------                        ----
   deep research failed         python deep_research.py <REG>  API only
   charter failed               python company_charter.py <REG> API only
   only the PDF is wrong        python finalize_report.py <REG> *** ZERO
                                                                 network,
                                                                 no browser,
                                                                 no CAPTCHA ***
   search cannot find a         python main.py <REG> --project-id N
   project that still exists      (the direct API can still return full
                                   data for a project the search box
                                   cannot find -- a site-side index gap)
   endpoint drift suspected     python main.py <REG> --verify
   session expired              python token_cache.py minutes-left
   rendering change to check    the free no-API _fill_template loop
   need to diff two runs        already done: output/_history/<REG>/<ts>/
```

---

## 8. Product scope — the feature map

```
+=========================================================================+
|                        RERA SCRAPPER -- FEATURE MAP                     |
+=========================================================================+

  F1  ACQUISITION                          F2  CORPORATE INTELLIGENCE
  +--------------------------------+       +--------------------------------+
  | F1.1 Project resolution        |       | F2.1 MCA profile, 3-way        |
  |      (reg-no or free text)     |       |      cross-checked chain       |
  | F1.2 Guest session management  |       | F2.2 Director roster merge     |
  |      (90-min cache)            |       |      + CONFLICT SURFACING      |
  | F1.3 9-category scrape         |       | F2.3 Group / affiliated cos    |
  | F1.4 Document download + reuse |       | F2.4 Credit rating (ICRA +     |
  | F1.5 Complaint-order download  |       |      Infomerics, side by side) |
  | F1.6 Run archiving + diffing   |       | F2.5 IBBI insolvency check     |
  | F1.7 Endpoint verification     |       | F2.6 CIN-only pre-RERA intake  |
  +--------------------------------+       +--------------------------------+

  F3  TRACK RECORD                         F4  COLLATERAL
  +--------------------------------+       +--------------------------------+
  | F3.1 Promoter portfolio        |       | F4.1 Land identification       |
  |      (all registered projects) |       | F4.2 CTS -> Property Card      |
  | F3.2 Cross-project complaint / |       |      (human-in-the-loop)       |
  |      appeal totals             |       | F4.3 CTS cross-check ->        |
  | F3.3 Delivered area + on-time  |       |      IMMINENT flag on mismatch |
  |      rate                      |       | F4.4 FSI metrics + mortgage    |
  | F3.4 5 km micro-market         |       |      / charge (a LIVE RIGHT    |
  |      influence (geocoded)      |       |      IS A FINDING)             |
  | F3.5 Lapsed / revoked flagging |       | F4.5 Document library + OCR    |
  +--------------------------------+       +--------------------------------+

  F5  COMPLIANCE                           F6  MARKET
  +--------------------------------+       +--------------------------------+
  | F5.1 GST intake (PAN -> all    |       | F6.1 Agentic macro-market      |
  |      GSTINs -> filing tables)  |       |      research                  |
  | F5.2 Statutory due dates       |       | F6.2 Micro-market / locality   |
  |      (monthly + QRMP, state-   |       | F6.3 Promoter external profile |
  |      specific)                 |       | F6.4 Per-claim verification    |
  | F5.3 Delay measurement +       |       | F6.5 Bounded gap retry with    |
  |      scoring                   |       |      DIFFERENT strategies      |
  | F5.4 MahaRERA judgments search |       | F6.6 Comparables + distances   |
  | F5.5 Appeal cross-referencing  |       | F6.7 Per-finding deep research |
  | F5.6 Review authenticity triage|       | F6.8 Source-trust registry     |
  +--------------------------------+       +--------------------------------+

  F7  SCORING                              F8  DOCUMENTS
  +--------------------------------+       +--------------------------------+
  | F7.1 Developer Score           |       | F8.1 Company Charter Internal  |
  |      3 buckets, 9 sub-metrics  |       | F8.2 Company Charter External  |
  |      NEVER renormalises        |       | F8.3 facts.json (full record)  |
  | F7.2 Imminent-flag hard cap    |       | F8.4 RERA summary PDF          |
  | F7.3 Documentation Confidence  |       | F8.5 Counterparty+Collateral   |
  |      8 criteria, RENORMALISES  |       |      report (standalone)       |
  | F7.4 Flag classification       |       | F8.6 Executive Briefing        |
  |      imminent/structural/      |       |      (8-page companion)        |
  |      monitor                   |       | F8.7 PDF conversion            |
  | F7.5 KPI cards                 |       +--------------------------------+
  +--------------------------------+

  F9  GOVERNANCE                           F10  OPERATIONS
  +--------------------------------+       +--------------------------------+
  | F9.1 rules.md as live config   |       | F10.1 CLI entry point          |
  | F9.2 Preflight gate            |       | F10.2 Streamlit UI (3 tabs)    |
  | F9.3 External quality gate     |       | F10.3 Per-label cost ledger    |
  |      (11 checks)               |       | F10.4 Run summary              |
  | F9.4 Blocking compliance       |       | F10.5 Offline PDF rebuild      |
  |      review + quote            |       | F10.6 Component retry commands |
  |      verification              |       | F10.7 Token cache CLI          |
  | F9.5 Deterministic fallbacks   |       +--------------------------------+
  | F9.6 Scrub / restore contract  |
  | F9.7 Never-fatal wrappers      |
  | F9.8 Guard-symbol test         |
  +--------------------------------+
```

### 8.1 Feature priority

| Priority | Features | Rationale |
|---|---|---|
| **P0 — the product does not exist without these** | F1.1–F1.4, F8.1–F8.3, F8.7, F9.1–F9.4, F9.7 | Acquisition, the Charter pair, and the gates that make it defensible |
| **P1 — core value** | F2.*, F3.*, F6.1–F6.5, F7.*, F9.5–F9.6, F10.1, F10.3–F10.4 | The intelligence and the scores |
| **P2 — high value, opt-in** | F4.2–F4.3, F5.1–F5.3, F6.7–F6.8, F1.6, F10.5–F10.7 | Each costs a human solve or is a refinement |
| **P3 — adjacent deliverables** | F8.4–F8.6, F10.2, F5.6 | Separate documents and the UI |

---

## 9. Functional requirements

Requirements are numbered `FR-<area>-<n>`. **MUST / SHOULD / MAY** carry RFC-2119 force.

### 9.1 Acquisition (FR-ACQ)

| ID | Requirement | Priority |
|---|---|---|
| FR-ACQ-01 | The system **MUST** accept a registration number from any registered state profile, or a free-text project name, and **MUST** resolve which authority it belongs to from the number itself | P0 |
| FR-ACQ-02 | On multiple name matches the system **MUST** present the candidates and let the operator pick; with no interactive terminal it **MUST** exit code 2 with instructions rather than guess | P0 |
| FR-ACQ-03 | Project-id resolution **MUST NOT** require a login or a CAPTCHA, and **MUST NOT** click the "View Details" link (which fires a JS confirm and lands on the gated page) | P0 |
| FR-ACQ-04 | The system **MUST** accept `--project-id N` to bypass search entirely, for projects the portal's own search index cannot find but whose API record still exists | P1 |
| FR-ACQ-05 | The system **MUST** obtain a guest session by opening a **visible** browser for a human to solve the CAPTCHA, and **MUST NOT** attempt to read or solve the CAPTCHA image | P0 |
| FR-ACQ-06 | The system **MUST** cache a valid session on disk and reuse it for **90 minutes**, against a real token lifetime of roughly 100 minutes | P0 |
| FR-ACQ-07 | The system **MUST** accept `--token` (manually captured) and `--no-auto-auth` (skip the browser), and **MUST** complete the run in all four auth states: `explicit`, `cached`, `fresh_browser`, `none` | P0 |
| FR-ACQ-08 | The system **MUST** fetch all nine categories, `projects` first and alone (it carries the `userProfileId` that `past_experiences` requires), the remaining eight in parallel | P0 |
| FR-ACQ-09 | The system **MUST** normalise MahaRERA's response envelopes and **MUST** treat a 200 response containing only bookkeeping keys as empty, not as data | P0 |
| FR-ACQ-10 | On 401/403 for gated categories despite holding a session, the system **MUST** invalidate the cache, re-acquire a session, and re-fetch **only the failed subset** — exactly once | P0 |
| FR-ACQ-11 | The system **MUST** write one raw JSON file per category, including a `{"_error":…}` sentinel on failure, so `raw/` always has nine files | P0 |
| FR-ACQ-12 | The system **MUST** download project documents by POST to the DMS service with the bearer token, falling back to direct-URL scanning for shapes that do not match | P0 |
| FR-ACQ-13 | The system **MUST NOT** trust the DMS `Content-Type` header, and **MUST** verify that a purported JSON body actually parses before treating it as JSON | P0 |
| FR-ACQ-14 | The system **MUST** de-duplicate filenames by appending ` (2)`, ` (3)` before the extension, because two genuinely different documents can share a filename | P0 |
| FR-ACQ-15 | The system **MUST** reuse documents from the previous run, matched by `(document_id, source_filename)` for DMS downloads and by `original_url` for direct URLs, and record `status: "reused"` | P1 |
| FR-ACQ-16 | The system **MUST** download complaint-order PDFs, and this **MUST NOT** be fatal | P1 |
| FR-ACQ-17 | The system **MUST** archive the previous run by **moving** it to `output/_history/<reg>/<timestamp>/`, resolving same-second collisions with a numeric suffix | P1 |
| FR-ACQ-18 | The system **MUST** read prior research *before* archiving and prior manifests *after* archiving | P1 |
| FR-ACQ-19 | The system **MUST** provide `--verify` mode that probes every endpoint and prints its configured trust level (`confirmed`/`observed`) alongside the empirical outcome | P2 |
| FR-ACQ-20 | `--state` **MUST** always override detection. Where two authorities share a number format (MahaRERA and TG-RERA are both `P` + 11 digits) the system **MUST** probe both and let the one that actually holds the project win, **MUST NOT** decide on the district-code convention alone, and **MUST** say on stdout when a heuristic fired | P0 |
| FR-ACQ-21 | Each state **MUST** declare what it HAS (`profile.capabilities`). A capability a state lacks **MUST** return the empty value plus an honest sentence in `notes` -- never a stub, and never a shape that reads as "checked, nothing found" | P0 |
| FR-ACQ-22 | Acquisition **MUST** sit behind a single `acquire()` call covering resolve, auth, scrape, documents, orders and promoter portfolio, and `app.py` **MUST** call the same method as `main.py` (enforced by a test) | P0 |
| FR-ACQ-23 | A state-specific code path **MUST NOT** run for another state. MahaRERA's orders search, the Maha Bhulekh land path and the MahaRERA-only document flow are each gated on a declared capability | P0 |
| FR-ACQ-24 | A response that arrives TRUNCATED **MUST** be refused rather than parsed. Several registers are multi-megabyte single-request pages, and a short read yields fewer rows that are indistinguishable from a smaller register | P0 |

### 9.2 Corporate intelligence (FR-CORP)

| ID | Requirement | Priority |
|---|---|---|
| FR-CORP-01 | The system **MUST** extract a CIN or LLPIN from the RERA record and label it correctly | P1 |
| FR-CORP-02 | The system **MUST** query three independent MCA mirrors (ZaubaCorp, Tofler, InstaFinancials) for the company profile | P1 |
| FR-CORP-03 | When director rosters **agree**, the system **MUST** merge them silently | P1 |
| FR-CORP-04 | When director rosters **disagree**, the system **MUST** surface both as `roster_conflicts`, promote them into `facts["gaps"]`, and render them in **both** document variants. It **MUST NOT** silently pick one | P0 |
| FR-CORP-05 | The system **MUST** detect a paywalled mirror response rather than treating it as data | P1 |
| FR-CORP-06 | The system **MUST** query both ICRA and Infomerics for credit ratings and present them side by side | P1 |
| FR-CORP-07 | The system **MUST** check IBBI for insolvency proceedings and classify the hit | P1 |
| FR-CORP-08 | The system **MUST** source group/affiliated companies from ZaubaCorp only, and **MUST** corroborate them against InstaFinancials directorship counts rather than treating them as authoritative | P1 |
| FR-CORP-09 | The system **MUST** run these five checks in parallel (credit rating, IBBI, company profile, group companies, judgments search) | P1 |
| FR-CORP-10 | Each of these checks **MUST** be individually never-fatal | P0 |
| FR-CORP-11 | The system **MUST** support a CIN-only pre-RERA intake writing `output/_pending/<CIN>/promoter_profile.json` **with the exact `facts.json` keys**, so a later merge is a plain dict update | P2 |
| FR-CORP-12 | When a promoter carryover is attached, the system **MUST** skip the four live corporate checks it replaces | P2 |

### 9.3 Track record (FR-TRK)

| ID | Requirement | Priority |
|---|---|---|
| FR-TRK-01 | The system **MUST** discover every project registered under the promoter's name via MahaRERA's Promoters search tab | P1 |
| FR-TRK-02 | Fan-out **MUST** be capped at `PROMOTER_PROJECT_LIMIT = 25`, and truncation **MUST NEVER** be silent — a `truncated` field and an extra limitation entry are required | P1 |
| FR-TRK-03 | The system **MUST** aggregate complaints, appeals, delivered area and on-time completion rate across the portfolio | P1 |
| FR-TRK-04 | On-time classification **MUST** compare actual against *original proposed* completion date, and **MUST** count an unparseable pair into neither bucket | P1 |
| FR-TRK-05 | The system **MUST** geocode project locations via OSM Nominatim at no more than ~1 request/second, with a per-run cache | P1 |
| FR-TRK-06 | A project whose geocode fails **MUST** be **dropped** from the 5 km sum and **MUST NOT** be treated as "0 km away" | P0 |
| FR-TRK-07 | The subject project **MUST** be excluded from area totals by the **entry's own** registration number or exact name — never by the fetch registration number, which destroyed real data for single-project SPVs | P1 |
| FR-TRK-08 | The system **MUST** flag lapsed, revoked or cancelled projects | P1 |
| FR-TRK-09 | The portfolio **MUST** always emit its standing list of seven limitations | P1 |
| FR-TRK-10 | Portfolio build failure **MUST** be never-fatal | P0 |

### 9.4 Collateral and land (FR-LAND)

| ID | Requirement | Priority |
|---|---|---|
| FR-LAND-01 | The system **MUST** extract land identification (survey/CTS, village, taluka/district, pincode, gross/affected/net area), each with its source | P0 |
| FR-LAND-02 | The system **MUST** support a Maha Bhulekh Property Card lookup, opt-in and never auto-run, and **MUST** extract the card's own labelled fields -- holder, area, tenure, encumbrance and the mutation table | P2 |
| FR-LAND-03 | The system **MUST NOT** fuzzy-match Marathi office or village labels against RERA's English text; the operator **MUST** pick from the site's own option list | P0 |
| FR-LAND-04 | The system **MUST** provide a four-step human-in-the-loop resolution chain (`offices` → `villages` → `candidates` → `finalize`), of which only the final fetch requires a CAPTCHA | P2 |
| FR-LAND-05 | The system **MUST** re-emit the land-record reminder gap on **every** run until `cts_lookup_input.json` exists, so it cannot be forgotten after the first mention | P2 |
| FR-LAND-06 | The system **MUST** cross-check a retrieved CTS number against RERA's own `survey_cts_plot_numbers`, and **MUST** raise an **IMMINENT** flag on a genuine mismatch | P1 |
| FR-LAND-07 | A composite key of district + village + CTS number **MUST** be used for pending land records, because a bare CTS number is not globally unique | P2 |
| FR-LAND-08 | The system **MUST** merge `mortgage_area` and `mortgage_lender` into a single "Mortgage / charge on the land" field, preserving the finding that development agreements permit a mortgage even where none has been taken — **a live right is a finding** | P1 |
| FR-LAND-09 | The system **MUST** extract text from downloaded documents, falling back to OCR, and **MUST** degrade to an `[OCR unavailable]` marker rather than failing when Tesseract is absent | P1 |
| FR-LAND-10 | The Property Card **MUST** be requested in English and parsed from its HTML tables. In Marathi the portal serves the card as a single embedded JPEG, which no parser can read without a Marathi OCR pack; the language **MUST** be set by code, never left to whoever is at the CAPTCHA | P2 |
| FR-LAND-11 | The Marathi card image **MUST** be saved alongside every capture as the authoritative artefact, because the portal's own disclaimer states the transliterated text is "prone to occasional inconsistencies" and that the Marathi content is sacrosanct | P2 |
| FR-LAND-12 | A card that is present but unreadable **MUST** report NO READING TAKEN. The PU-ID is matched by regex over the whole page and so survives a card whose every labelled row failed to parse; a result carrying only a PU-ID **MUST NOT** be reported as a plot with no owner, no encumbrance and no mutation entries | P0 |

### 9.5 Compliance and litigation (FR-COMP)

| ID | Requirement | Priority |
|---|---|---|
| FR-COMP-01 | GST intake **MUST** be opt-in via mutually exclusive `--gstin` / `--pan` | P2 |
| FR-COMP-02 | The system **MUST** format-validate the identifier **before** opening a browser, because a typo must never cost a human a CAPTCHA solve | P2 |
| FR-COMP-03 | The system **MUST** enumerate every GSTIN registered under the PAN, across every state | P2 |
| FR-COMP-04 | One CAPTCHA solve per GSTIN **MUST** cover every financial year for that GSTIN | P2 |
| FR-COMP-05 | The system **MUST** parse only GSTR-1 and GSTR-3B into scoreable records. CMP08, GSTR9/9C and GSTR1A **MUST NOT** be scored, because no statutory due-date rule is implemented for them and scoring them would be a guess | P2 |
| FR-COMP-06 | The primary GSTIN **MUST** be the one with the most filing periods; the others' raw data **MUST** be retained, not discarded | P2 |
| FR-COMP-07 | The system **MUST NOT** write an empty `records: []` file, because the file's existence signals "GST data was supplied" | P2 |
| FR-COMP-08 | Due dates **MUST** be computed per **period** (not per GSTIN), detecting monthly vs QRMP frequency from the period span, because a taxpayer can switch schemes at any quarter boundary | P2 |
| FR-COMP-09 | QRMP GSTR-3B due day **MUST** be derived from the GSTIN's own state code (22nd for Category X, 24th for Category Y) | P2 |
| FR-COMP-10 | A period whose frequency cannot be resolved **MUST** be counted as `unresolvable_frequency` and excluded from every other count, never assigned a guessed due date | P2 |
| FR-COMP-11 | A period not yet due **MUST** be excluded from all counts — that is not a gap, it is simply not due yet | P2 |
| FR-COMP-12 | GST failure **MUST** cost exactly one unscored sub-metric, never the run | P0 |
| FR-COMP-13 | The system **MUST** search the authority's orders/judgments and cross-reference the results against the project's appeals, saving the judgment PDFs. An orders search that could not run **MUST** be distinguishable from one that found nothing | P1 |
| FR-COMP-14 | The system **SHOULD** triage the authenticity of any operator-supplied `reviews.json` (rating polarisation, bursts, near-duplicates, one-hit-wonder reviewers, claim cross-referencing) | P3 |
| FR-COMP-15 | GST **MAY** be checked across the group (`--group-gst`). GST is keyed on PAN and no public MCA source publishes one, so the section **MUST** lead with how many entities were checked out of how many exist, and **MUST** name the ones that were not | P2 |
| FR-COMP-16 | A PAN **MUST NOT** be guessed. Each **MUST** carry a provenance -- read off a filed PAN card, named in an authority's filing, extracted arithmetically from a known GSTIN, or supplied by hand -- and an unverified OCR candidate **MUST** be refused | P0 |
| FR-COMP-17 | Case law **MAY** be searched across the group (`--group-litigation`), per entity and per director. Every hit is a NAME match on a full-text index and **MUST** be rendered as a candidate to confirm, never as this promoter's litigation | P2 |
| FR-COMP-18 | The forums open case-law search does NOT reliably index -- consumer fora, most of NCLT/NCLAT, district courts, arbitration, and the RERA authorities' own orders -- **MUST** be named on the page, and a nil result **MUST NOT** be described as a clean record | P0 |
| FR-COMP-19 | The system **SHOULD** search the RERA authorities' own order registers by promoter name where one is published, and **MUST** name every authority whose register was not searched, with the reason | P1 |
| FR-COMP-20 | Where an order register names no party (WBRERA publishes 4,881 orders keyed only by complaint number), a join **MAY** be made through another published document. A join key recovered from OCR **MUST** be resolved against a closed set of real values and **MUST** be refused when it does not land on exactly one | P0 |

### 9.6 Market research (FR-RES)

| ID | Requirement | Priority |
|---|---|---|
| FR-RES-01 | The system **MUST** produce three research blocks: `macro_market`, `micro_market`, `promoter_external` | P1 |
| FR-RES-02 | Every claim **MUST** carry an inline source marker resolving to a `sources` entry with a URL | P1 |
| FR-RES-03 | Every cited source **MUST** be independently re-verified by a separate pass | P1 |
| FR-RES-04 | A source whose verification **could not run** (missing key, network, rate limit) **MUST** be **kept**, with an honest gap appended stating it was not re-verified this pass | P1 |
| FR-RES-05 | A source that is unsupported or stale **MUST** be dropped into gaps with the reason | P1 |
| FR-RES-06 | Unresolved gaps **MUST** get bounded retries (max 2), each with a **different strategy**, and retry results **MUST** themselves be re-verified before merging | P1 |
| FR-RES-07 | A gap that survives retry **MUST** be reported as such, and **MUST NOT** be fabricated away to shorten the list | P0 |
| FR-RES-08 | Research **MUST** be reusable within a 24-hour window: confirmed sources carried forward untouched, only open gaps re-attempted | P1 |
| FR-RES-09 | Every finding that survives **MUST** earn a dedicated follow-up research pass establishing what it is, who is involved, when it arose, whether it is still live, and what it means for this project — capped at 8 calls | P1 |
| FR-RES-10 | A failed per-finding research call **MUST** keep the original finding text. **No finding may ever be deleted** by a research failure | P0 |
| FR-RES-11 | The system **MUST** maintain a cross-run source-trust registry, tallying open-web domains and auto-promoting at the 5th distinct project, with promotion notes appearing in the **Internal document only** | P2 |
| FR-RES-12 | Deep research failure **MUST** be never-fatal | P0 |
| FR-RES-13 | Source re-verification and gap retry **MUST NOT** cost one API call per source or per gap; a block or retry round **MUST** be checked in as few shared-budget calls as the chunking bound allows, so cost stops scaling with how many claims a project's research turns up | P1 |

### 9.7 Scoring (FR-SCORE)

| ID | Requirement | Priority |
|---|---|---|
| FR-SCORE-01 | The system **MUST** compute a Developer Score across three buckets — Operational Strength (50), Financial Strength (20), Governance Strength (30) — and nine sub-metrics | P1 |
| FR-SCORE-02 | An unscored sub-metric's weight **MUST NOT** be redistributed or removed from the denominator. A promoter with less publicly-verifiable data **must** structurally score lower | P0 |
| FR-SCORE-03 | If any **imminent** flag exists, a grade of AAA or AA **MUST** be capped to A. It **MUST NOT** soften an already-lower grade | P1 |
| FR-SCORE-04 | The system **MUST** compute a Documentation Confidence Score over eight criteria, **renormalising** when a criterion is N/A — the opposite policy to the Developer Score, and deliberate | P1 |
| FR-SCORE-05 | Both score tables **MUST** render fully intact however many N/A rows they contain; they are scoring methodology, not fact tables, and are exempt from empty-row removal | P1 |
| FR-SCORE-06 | The External variant **MUST NOT** render the Weight column in either score table (hard-gated) | P1 |
| FR-SCORE-07 | GST scoring **MUST** use breakpoints shared with flag classification, so a flagged project cannot silently score AAA | P1 |
| FR-SCORE-08 | Two distinct unscored GST outcomes **MUST** be distinguished: "no input supplied" (a pending-input gap) versus "input supplied but no scoreable period" | P2 |
| FR-SCORE-09 | The system **MUST** classify flags as imminent, structural or monitor, assigning each gap a **stable 1-based number** | P1 |
| FR-SCORE-10 | `facts["developer_score"]` **MUST** be treated as an output of a prior run, never a valid input. Every consumer **MUST** recompute | P1 |
| FR-SCORE-11 | The system **MUST** render four KPI cards: Completion Slippage, Units Sold, Litigation Load, Land Title — each coloured by its own real severity | P1 |

### 9.8 Documents (FR-DOC)

| ID | Requirement | Priority |
|---|---|---|
| FR-DOC-01 | The system **MUST** produce a Company Charter in exactly two variants, Internal and External, from **one** facts dictionary, forking only at render time | P0 |
| FR-DOC-02 | Internal **MUST** render first, on the real facts dict, and **MUST** compute the scores that get persisted. External **MUST** render from a deep copy | P0 |
| FR-DOC-03 | The document **MUST** have exactly ten locked top-level sections in a fixed sequence. Content may be reshaped inside the flow; sections **MUST NOT** be reordered or renamed | P0 |
| FR-DOC-04 | The template `.docx` **MUST** be filled **in place**, preserving every style, colour and table already baked into it | P0 |
| FR-DOC-05 | The final deliverable **MUST** be PDF. The `.docx` remains on disk as the conversion input, and as the only output if conversion fails | P0 |
| FR-DOC-06 | Any script invoking `_fill_template` directly **MUST** call the PDF conversion itself; only `run_company_charter` does it automatically | P0 |
| FR-DOC-07 | Charter files **MUST** only ever be saved to `output/company_charters/` | P0 |
| FR-DOC-08 | The system **MUST** persist a `.facts.json` containing the **complete, unscrubbed** record | P0 |
| FR-DOC-09 | The system **MUST** produce a RERA project summary PDF, which is a **different document** from the Charter | P1 |
| FR-DOC-10 | The summary PDF **MUST** carry a data-reliability note listing every category using a non-`confirmed` endpoint | P1 |
| FR-DOC-11 | Body text **MUST** be justified; table cells **MUST** be centred only for short values, with narrative prose left-aligned | P1 |
| FR-DOC-12 | Table rows **MUST NOT** split across a page break, and a continued table **MUST** repeat its header row | P1 |
| FR-DOC-13 | Long URLs **MUST NOT** appear in narrow table columns; a cell carries a short label or an `[N]` marker, and the full URL lives once in Sources | P1 |
| FR-DOC-14 | The Internal document **MUST** carry a version log with elapsed end-to-end run time, total API cost and call count | P2 |
| FR-DOC-15 | The system **SHOULD** support a standalone "Counterparty + Collateral" report with its own independent quality gate | P3 |
| FR-DOC-16 | The system **MAY** produce an 8-page Executive Briefing that narrates only what the Charter already established, performing no new interpretation | P3 |

### 9.9 Operations (FR-OPS)

| ID | Requirement | Priority |
|---|---|---|
| FR-OPS-01 | The system **MUST** print a run summary covering auth source, per-category counts, documents, promoter profile, market research, GST intake, Charter status and API usage | P0 |
| FR-OPS-02 | The system **MUST** record token and USD cost **per label**, so each stage's cost is separately visible and arguable | P1 |
| FR-OPS-03 | Usage tracking **MUST** be reset at the start of each run, because the Streamlit process is long-lived | P1 |
| FR-OPS-04 | The system **MUST** support rebuilding the summary PDF from disk with **zero** network calls, no browser and no CAPTCHA | P2 |
| FR-OPS-05 | Every optional stage **MUST** be individually retryable from the command line | P1 |
| FR-OPS-06 | The system **MUST** offer a Streamlit UI wrapping the same functions, with **no duplicated logic** | P3 |
| FR-OPS-07 | The token cache **MUST** expose a CLI with shell-friendly exit codes (0 success, 1 stale/absent, 2 bad usage) | P2 |
| FR-OPS-08 | Cost figures for an unknown model **MUST** read `0.0` rather than a silently wrong number | P1 |
| FR-OPS-09 | The system **MUST** enforce a hard ceiling (`PIPELINE_COST_CAP_USD`) on total spend for one project run, deep research and Charter generation combined. A call that would **start** after the run's spend is at or over the ceiling **MUST** be refused (`CostCapExceeded`, never retried); a call already in flight when the ceiling is crossed **MUST** be allowed to finish | P1 |
| FR-OPS-10 | Prompt-cache write and read tokens **MUST** be priced and reported separately from plain input tokens, never folded into or ignored against the plain input rate | P2 |

---

## 10. Content and editorial requirements

These are product requirements, not style preferences. They are the reason the documents are usable, and they are enforced in code. The authoritative statement is `rules.md`; this section states them as requirements with their enforcement points.

### 10.1 The governing principle

```
   +====================================================================+
   |                                                                    |
   |     REPORT WHAT WAS FOUND.  NEVER PAD WHAT WAS NOT.                |
   |                                                                    |
   |     "We checked and it is clear"     ->  DELETED                   |
   |     "We looked and could not         ->  KEPT IN FULL, as a        |
   |      establish this"                     numbered gap              |
   |                                                                    |
   |     These are different risks. The document must not blur them.    |
   |                                                                    |
   +====================================================================+
```

### 10.2 Editorial requirements (FR-ED)

| ID | Requirement | Enforcement |
|---|---|---|
| FR-ED-01 | **A clean check produces no sentence.** No sentence, no citation marker, no list of what was searched, no named sources. Delete the line | Prompt + `_scrub_clean_checks` + `_is_cited_absence` gate |
| FR-ED-02 | **Never attach a citation marker to an absence.** Three carve-outs only: an entire empty section, an absence that is the stated basis of a score, and a gap | Hard gate check #4 |
| FR-ED-03 | **An empty section keeps its heading and one bare line** — "Nothing found." No sources, no scope, no explanation, no marker | `_remove_empty_section_headings` preserves the heading |
| FR-ED-04 | **Findings first.** Never open on a negative when the passage goes on to give real content. Applies to table cells exactly as to bullets | Prompt + review |
| FR-ED-05 | **Absence as evidence** survives only where it is the stated basis of a number the reader would otherwise take on trust | `_is_carved_out` patterns |
| FR-ED-06 | **A gap is not an absence.** Gaps & Sources keeps every item with its full explanation, including permanent ones. Never compressed under the clean-check rule | `_is_cited_absence` returns False for `Gap N.` |
| FR-ED-07 | **Deep research on every finding.** Anything surviving as a finding earns a dedicated follow-up pass, not a one-line mention | `run_finding_research` |
| FR-ED-08 | **A live right is a finding.** A contractual permission, option, charge or entitlement counts as found even if never exercised, because it remains exercisable | `_merged_mortgage_value` |
| FR-ED-09 | **Ruled-out items survive, in the right section.** Something discovered and dismissed is a finding, not an absence. Age alone never rules an item out | `_relocate_merge_orders` |
| FR-ED-10 | **Say it once.** A fact, and an absence, appears in exactly one place. A Summary line is a verdict plus a pointer, never a restatement | `_point_rera_landowner_at_identity_table` + review |
| FR-ED-11 | **Flags summarise, Gaps explain.** A flag is a one-sentence headline ending in `(Gap N)`; the full explanation lives once, under Gaps & Sources, numbered to match | `_flag_headline` + `_classify_flags` gap numbering |
| FR-ED-12 | **Internal keeps process failures, External does not.** Never put a file path, module name, function or parameter name, JSON key, or raw exception string into either document; least of all External | `_sanitize_process_text` + `_externalize_prose` |
| FR-ED-13 | **Deep-dive vs nothing-found.** A section with real findings gets expanded properly; a genuinely empty one collapses to the heading plus "Nothing found." Never padded, never supported with sources or search scope | Prompt + review |
| FR-ED-14 | **No jargon.** Every initialism (CIRP, NCLT, IBC, DSRA, IGR, KMP, ROC, MCA, IOD, IOA, CHS, NCD, SRO, SPOC, IBBI, FSI, DCPR, MHADA, MRTP, CC/OC…) **MUST** be expanded or plain-languaged once on first use per section, **keeping the term itself**. A plain-language pass, not a dumbing-down pass — precision on legal and registry facts must not be lost | `_expand_jargon_first_use` |
| FR-ED-15 | **Gloss raw status strings.** A verbatim registry value (e.g. IBBI's "ASSIGNMENT NOT APPROVED YET") is quoted as-is and translated in the same sentence. Never present an opaque database string as a finding | Prompt + review |
| FR-ED-16 | **Bullets stay the default format.** Consolidate fragmented bullets into one well-formed bullet. Sentence casing, proper entity capitalisation, complete sentences | `_consolidate_bullet_clauses`, `_fix_bullet_capitalization` |
| FR-ED-17 | **CIN/DIN placement.** Never inline in prose, anywhere, **except** the three identity tables (Corporate/Promoter Identity, Current Directors & KMP, Company Registration Profile) | `_strip_inline_cin_din` |
| FR-ED-18 | **Empty table/row removal.** Drop a whole table if every row is empty; drop a row only if **every** column is empty. Except the two score tables | `_remove_gap_rows`, `_remove_fully_empty_rows` |

### 10.3 External-only requirements (FR-EXT)

| ID | Requirement | Enforcement |
|---|---|---|
| FR-EXT-01 | **Numbered citations.** Every inline factual claim resolves to a `[N]` marker: one marker per distinct claim, not one glued to the end of a multi-sentence passage | `_register_citation`, `_insert_marker_at_clause_end` |
| FR-EXT-02 | **Marker placement.** A marker sits at the end of the clause it supports, not the end of the paragraph. `[1][2][4]` parked after the final full stop is the failure this rule exists to prevent. A marker never lands mid-word | `_insert_marker_at_clause_end` |
| FR-EXT-03 | **Scope is total.** Executive Summary bullets, flag headlines, table cells, appendix prose and score-table notes are all in scope. A section shipping with no markers at all is a defect, not a stylistic choice | Advisory judge + review |
| FR-EXT-04 | **The marker must actually support the claim.** Citing the MahaRERA complaints record for a sentence beginning "independent web research found…" is a mis-citation even though both are real sources. **A missing marker is a lesser failure than a wrong one** | `_clause_topic_citation` returns `None` rather than borrowing an adjacent source |
| FR-EXT-05 | **Descriptive source labels.** Issuer, what the document is, and its date. Never a raw internal filename, never a bare category label a reader cannot check | `_external_source_label`, `_generic_one_label` |
| FR-EXT-06 | **No em dash, no hyphen-pair dash** anywhere in an External paragraph | Hard gate checks #1 and #2 |
| FR-EXT-07 | Only gaps that also earned an Imminent or Structural flag appear in External, colour-coded by severity and numbered to match | `_external_gaps` |
| FR-EXT-08 | The External Sources list is built **only from tokens actually cited**, in first-use order, so an uncited source can never appear | `_citation_registry` order |
| FR-EXT-09 | Numbered Sources entries **MUST** retain balanced parentheses and their bullet numbering | Hard gate checks #7 and #8 |
| FR-EXT-10 | The Document Library table and the Weight columns **MUST NOT** appear in External | Hard gate checks #9 and #10; `t[8]` dropped |

### 10.4 The consequence rule (a constraint on the rules themselves)

```
   Sections B and C of rules.md are injected VERBATIM into
   External-facing API calls.
                    |
                    v
   Prompt punctuation BLEEDS INTO model output.
                    |
                    v
   _verify_external_document_quality HARD-FAILS an External save
   containing an em dash or a hyphen-pair dash.
                    |
                    v
   *** THEREFORE Sections B and C MUST THEMSELVES contain no em dash
       and no " -- ". Use commas, colons or a single spaced hyphen. ***
                    |
                    v
   _preflight_rules checks exactly this, before a paragraph is written.

   Section A is never sent, and may use them freely.

   This is checked whenever B or C is edited. It is the clearest example
   in the product of a rule whose FORM is constrained by its DELIVERY
   MECHANISM.
```

---

## 11. Deliverable specifications

### 11.1 Company Charter — Internal

| Attribute | Specification |
|---|---|
| **Filename** | `Company_Charter_<ProjectName>_<REG_NO>_Internal.pdf` (`.docx` retained) |
| **Location** | `output/company_charters/` — and nowhere else |
| **Classification** | *Internal — Integrow Asset Management* |
| **Audience** | Underwriting team |
| **Citation style** | Self-descriptive inline `(label)` |
| **Sources list** | Full bibliographic, with `[published: …, accessed: …]` and internal filenames |
| **Contains** | Process failures · Document Library table · Weight columns in both score tables · Source Trust Registry Updates · version log (elapsed / cost / call count) · **all** gaps · "(Code-Computed)" heading suffixes |
| **Section order** | The ten locked sections (§11.3) |
| **Gate** | Preflight + blocking compliance review |

### 11.2 Company Charter — External

| Attribute | Specification |
|---|---|
| **Filename** | `Company_Charter_<ProjectName>_<REG_NO>_External.pdf` |
| **Classification** | *Strictly Private and Confidential* |
| **Audience** | Counterparty / co-investor |
| **Citation style** | Numbered `[N]`, one per distinct claim, at clause end |
| **Sources list** | Descriptive citations only, built from tokens actually cited, in first-use order |
| **Excludes** | Every process failure · file paths, module names, JSON keys, exception strings · the Document Library table · Weight columns · Source Trust Registry Updates · the version log · gaps that did not earn an Imminent or Structural flag · "(Code-Computed)" suffixes · 8 named internal-only paragraphs |
| **Forbidden characters** | Em dash, hyphen-pair dash — hard-gated |
| **Gate** | Preflight + **11-check mechanical gate** + blocking compliance review |

### 11.3 The ten locked sections

```
    1.  (title block)
    2.  Overview & Flags                  <-- the reader's first page:
                                              deal snapshot, headline
                                              scorecard, imminent /
                                              structural / monitor lists
    3.  Executive Summary                 <-- + 4 KPI cards
    4.  Counterparty
    5.  The Asset                         <-- H2: FSI (Floor Space Index)
    6.  Compliance & Legal Detail         <-- H2: Complaint Order Outcomes,
                                              Appeal-Level Judgments
    7.  Market & Area Intelligence
    8.  RERA Core Data
    9.  Diligence Appendix                <-- H2: Document Library Contents,
                                              Credit Rating Check, IBBI
                                              Insolvency Check, Company
                                              Registration Profile, Group /
                                              Affiliated Companies, Developer
                                              Score, Documentation Authenticity
                                              & Confidence Summary
   10.  Gaps & Sources                    <-- H2: Sources

   PLUS two CONDITIONAL appends that render only when their check ran:
        Land Record Check -- Maha Bhulekh Property Card
        Review Authenticity Triage (Code-Computed Heuristics)

   *** SECTION ORDER IS LOCKED. Reshape content inside the flow;
       never reorder or rename a section. ***
```

### 11.4 `.facts.json` — the record

| Attribute | Specification |
|---|---|
| **Filename** | `Company_Charter_<ProjectName>_<REG_NO>.facts.json` |
| **Classification** | **Internal, unscrubbed. Never leaves the system.** |
| **Contents** | 28 required model-authored keys + ~20 code-computed keys, **with clean-check text and process-failure text restored** |
| **Purpose** | The complete record; the input to `charter_report.py`, `executive_briefing.py`, `finalize_report.py`, `cts_resolve.py`; the basis of the free no-API render loop |
| **Invariant** | `developer_score` in this file is an **output**, never a valid input |

### 11.5 RERA project summary PDF

| Attribute | Specification |
|---|---|
| **Filename** | `output/<REG_NO>/<REG_NO>_summary.pdf` |
| **Renderer** | ReportLab (no native dependencies) |
| **Sections** | Cover (+ data-reliability note) → Project Details → Company Charter Highlights → Promoter Profile → Market Research → remaining categories |
| **Rebuildable** | Yes, offline, zero network calls |
| **Note** | **A different document from the Charter. Do not confuse them.** |

### 11.6 Supporting artefacts

| Artefact | Purpose |
|---|---|
| `Company_Charter_<REG>_claude_md_review.json` | The compliance review's verdict, violations and unverifiable items |
| `usage_summary.json` | Per-label calls, turns, tokens and USD |
| `output/usage_log.jsonl` | Append-only, one rollup line per run, forever |
| `documents_manifest.json` | Every document with `status`, `method`, `document_id`, `source_filename` |
| `complaint_orders_manifest.json` | As above, plus `complaint_id` and `complaint_registration_no` so an outcome can be attributed back to a specific complaint |
| `run_meta.json` | reg-no, project id, auth source, promoter name, charter path, timestamp |
| `promoter/portfolio.json` | Portfolio totals, per-project rows, and the standing limitations list |
| `research/deep_research.json` | Three blocks, stamped `_generated_at` and `_reused_prior` |
| `source_trust_registry.json` | **Committed to the repository** — cross-run open-web trust tallies |

---

## 12. Non-functional requirements

### 12.1 Reliability (FR-NFR-REL)

| ID | Requirement |
|---|---|
| NFR-REL-01 | A failure in any optional stage **MUST NOT** prevent the run from completing |
| NFR-REL-02 | A failure in one category, document, or promoter-portfolio project **MUST NOT** affect any other |
| NFR-REL-03 | A refusal or gate failure **MUST NOT** leave a half-written `.docx` on disk |
| NFR-REL-04 | Corrupt cached state (token cache) **MUST** degrade to "absent", never to an exception |
| NFR-REL-05 | Editorial passes **MUST** be idempotent — safe to run across two renders |
| NFR-REL-06 | Re-running the same project **MUST NOT** silently clobber the previous run |

### 12.2 Performance (NFR-PERF)

| ID | Requirement |
|---|---|
| NFR-PERF-01 | Category fetch **MUST** be parallelised (8 workers) |
| NFR-PERF-02 | Document download **MUST** be parallelised (8 workers) |
| NFR-PERF-03 | The five external corporate checks **MUST** run in parallel (5 workers) |
| NFR-PERF-04 | Documents already downloaded **MUST** be reused rather than re-fetched from MahaRERA's slow DMS |
| NFR-PERF-05 | Confirmed research sources **MUST** be reused within 24 hours; only open gaps re-attempted |
| NFR-PERF-06 | Section B **MUST** be delivered as a cacheable system-prompt prefix block |
| NFR-PERF-07 | All human-attended browser work **MUST** be contiguous in the pipeline, so the operator is not called back after a long unattended stage |
| NFR-PERF-08 | Rendering **MUST** be verifiable with zero API calls from a saved `facts.json` |
| NFR-PERF-09 | Every Claude API call **MUST** mark its request cacheable (`cache_control`), so a system prompt reused across calls under the same label is billed at the cache-read rate on its second and later uses |
| NFR-PERF-10 | Verification and gap-retry fan-out **MUST** be batched — many claims or gaps checked per call, sharing one search budget — rather than issuing one independent call per claim or gap |

### 12.3 Usability (NFR-USE)

| ID | Requirement |
|---|---|
| NFR-USE-01 | Every console line **MUST** be prefixed `[INFO]`, `[OK]`, `[WARN]` or `[ERROR]` |
| NFR-USE-02 | A failed stage **MUST** print the exact command to retry just that stage |
| NFR-USE-03 | The CAPTCHA wait **MUST** print a progress line at least every 30 seconds |
| NFR-USE-04 | The run summary **MUST** show cost broken down by label, sorted by cost |
| NFR-USE-05 | Endpoint trust levels **MUST** be surfaced to the operator, and on the summary PDF cover |
| NFR-USE-06 | Truncation, capping or sampling **MUST NEVER** be silent |

### 12.4 Maintainability (NFR-MAINT)

| ID | Requirement |
|---|---|
| NFR-MAINT-01 | Guards **MUST** be documented by `module.symbol`, never by line number |
| NFR-MAINT-02 | A named guard symbol disappearing **MUST** fail the test suite |
| NFR-MAINT-03 | Content rules **MUST** live in one file that is both human-readable and machine-parsed |
| NFR-MAINT-04 | A completed spec **MUST** be marked historical, with its specific staleness enumerated |
| NFR-MAINT-05 | A module retained only as a shared library **MUST** say so, and say who imports what |
| NFR-MAINT-06 | Every guard that "looks redundant" **MUST** record why it exists |

### 12.5 Portability (NFR-PORT)

| ID | Requirement |
|---|---|
| NFR-PORT-01 | Everything except PDF conversion **MUST** run on Linux, macOS and Windows |
| NFR-PORT-02 | PDF conversion currently requires Windows + Word (`docx2pdf`); the `.docx` **MUST** remain as the fallback output |
| NFR-PORT-03 | Windows-only tests **MUST** be skippable so the suite runs on Linux/CI |
| NFR-PORT-04 | OCR language data **MUST** be bundled in-repo, not assumed installed |
| NFR-PORT-05 | The summary PDF renderer **MUST** avoid native dependencies, to keep a hosted-service path open |

### 12.6 Security and privacy (NFR-SEC)

| ID | Requirement |
|---|---|
| NFR-SEC-01 | API keys **MUST** be read from the environment and **MUST NOT** be written to disk or logged |
| NFR-SEC-02 | All run output **MUST** be gitignored |
| NFR-SEC-03 | The External document **MUST NOT** contain any internal tooling detail |
| NFR-SEC-04 | `.facts.json` **MUST** be treated as Internal-classification and never forwarded to a counterparty |
| NFR-SEC-05 | The system **MUST NOT** solve or read a CAPTCHA image |
| NFR-SEC-06 | Third-party rate limits and politeness policies **MUST** be honoured (Nominatim ~1 req/s with an identifying User-Agent) |
| NFR-SEC-07 | Any integration with a Terms-of-Service question **MUST** be opt-in, off by default, and carry an in-code caveat |

---

## 13. Governance, quality and compliance requirements

### 13.1 The gate chain (FR-GATE)

```
                          facts assembled
                                |
   +----------------------------v----------------------------+
   |  GATE 1  PREFLIGHT                                       |
   |  Runs BEFORE a paragraph is written.                     |
   |  rules.md present . markers intact . sections non-empty  |
   |  . B (and C when external) free of em dash and " -- "    |
   |  . Section A validated but NOT RETURNED                  |
   +----------------------------+----------------------------+
                                | pass
                     render Internal, then External
                                |
   +----------------------------v----------------------------+
   |  GATE 2  EXTERNAL QUALITY  (11 mechanical checks)        |
   |  Re-opens the SAVED file. Blocks the SAVE.               |
   |  The headline check: A CITATION MARKER ATTACHED TO AN    |
   |  ABSENCE -- the specific defect the whole editorial      |
   |  policy exists to prevent.                               |
   +----------------------------+----------------------------+
                                | pass
   +----------------------------v----------------------------+
   |  GATE 3  COMPLIANCE REVIEW   *** BLOCKS THE PDF ***      |
   |  Re-reads BOTH saved documents and audits them against   |
   |  rules.md via the model.                                 |
   |                                                          |
   |  STRICT BY DEFAULT. Two ways to fail:                    |
   |    (a) a VERIFIED violation                              |
   |    (b) a review that COULD NOT RUN AT ALL                |
   |        -- unverified is not the same as clean            |
   |                                                          |
   |  Safe to block on a model's judgement ONLY because every |
   |  violation is verified against the document text first.  |
   |  An invented quote is logged unverifiable and CANNOT     |
   |  stop a good document. Quotes under 12 chars are not     |
   |  trusted -- they match almost anything by coincidence.   |
   |                                                          |
   |  The model's role is reduced from "decide whether this   |
   |  complies" to "POINT AT the offending text", and the     |
   |  pointing is then checked mechanically.                  |
   +----------------------------+----------------------------+
                                | pass
                        convert both to PDF
                                |
                        *** THE DELIVERABLE ***
```

| ID | Requirement |
|---|---|
| FR-GATE-01 | Preflight **MUST** run before any content is written and **MUST** fail the run on a missing, renamed or empty rules section |
| FR-GATE-02 | Preflight **MUST** validate Section A without returning it, so it cannot become a route by which coding-time guidance reaches an API call |
| FR-GATE-03 | The External mechanical gate **MUST** re-open the saved file and run all eleven checks, walking body paragraphs **and every table cell recursively** |
| FR-GATE-04 | The compliance review **MUST** block the PDF by default |
| FR-GATE-05 | A review that could not run **MUST** count as a failure |
| FR-GATE-06 | Every reported violation **MUST** be verified against the document text before it may block; quotes under 12 normalised characters **MUST NOT** be trusted |
| FR-GATE-07 | Explicitly permitted patterns ("Nothing found.", `Gap N.`, score-note absences, identity-table identifiers, "N/A") **MUST** be carved out |
| FR-GATE-08 | Section A **MUST NEVER** be transmitted to any API, and a test **MUST** assert this — asserting the marker is present *before* asserting it is absent |
| FR-GATE-09 | An override (`CHARTER_ALLOW_UNCHECKED`) **MAY** exist, and **MUST** be documented as *a decision to ship an unchecked document, not a convenience* |
| FR-GATE-10 | The cost of the review **MUST** be separately labelled so it is visible and arguable |

### 13.2 Degradation requirements (FR-DEG)

| ID | Requirement |
|---|---|
| FR-DEG-01 | Every model-backed judgement **MUST** keep its deterministic predecessor behind it |
| FR-DEG-02 | Fallback caches **MUST** be keyed such that a lookup miss is **indistinguishable from "no model ran"** |
| FR-DEG-03 | Malformed model replies **MUST** be discarded, not trusted: out-of-range ids, unrecognised verdict kinds and null matches are all dropped |
| FR-DEG-04 | A per-finding research failure **MUST** cost only its own finding |
| FR-DEG-05 | **No finding may ever be deleted** by any research outcome, including an empty or malformed reply |
| FR-DEG-06 | Fan-out **MUST** be bounded, and the bound **MUST** be documented |
| FR-DEG-07 | Per-finding research **MUST NOT** recompound — an already-enriched finding must be recognised by fingerprint and skipped |

### 13.3 Reversibility requirements (FR-REV)

| ID | Requirement |
|---|---|
| FR-REV-01 | Any pass that removes text from the page **MUST** stash the original and restore it before `.facts.json` is written |
| FR-REV-02 | Without this, every run would quietly hollow out the file the next run reads. This **MUST NOT** be allowed to happen |
| FR-REV-03 | A **relocation** is the deliberate exception and **MUST NOT** be reversed — nothing is lost and the corrected record is the better one |

---

## 14. Data requirements

### 14.1 The facts contract

| ID | Requirement |
|---|---|
| FR-DATA-01 | The facts schema **MUST** require, for every sourced field, both a `value` and a `source` — a sourced field cannot exist without its source |
| FR-DATA-02 | Sources **MUST** carry an enumerated `topic` from a fixed list of 13, driving both corroboration counting and automatic citation resolution |
| FR-DATA-03 | Code-computed fields (document library, portfolio, complaint outcomes, all check results, both scores) **MUST** be attached verbatim and **MUST** overwrite any model output for the same key |
| FR-DATA-04 | An unconfirmable optional structure (`developer_track_record`) **MUST** be omitted entirely rather than filled with nulls |
| FR-DATA-05 | Pre-RERA intake files **MUST** use the exact `facts.json` keys, so a later merge is a plain dict update and never a reshape |
| FR-DATA-06 | Ephemeral render-time keys **MUST** be stripped before persist |

### 14.2 Retention and lifecycle

| ID | Requirement |
|---|---|
| FR-DATA-07 | Prior runs **MUST** be archived, not overwritten |
| FR-DATA-08 | Prior Charters **MUST** be archived too, matched by registration-number suffix (the project-name segment can change when a RERA record is renamed) |
| FR-DATA-09 | Field-level change between runs (e.g. mortgage lender) **MUST** be detected and surfaced |
| FR-DATA-10 | The cost ledger **MUST** be append-only |
| FR-DATA-11 | The source-trust registry **MUST** accumulate across all projects and **SHOULD** be version-controlled so promotions are reviewable |
| FR-DATA-12 | **[GAP]** A retention policy for `output/_history/` and PII-bearing `gst_portal_raw/` **MUST** be defined. *Not yet implemented — see §19* |

### 14.3 Data quality invariants

```
   INVARIANT                                    ENFORCED BY
   ---------                                    -----------
   A failed geocode is DROPPED,                 promoter_portfolio
   never counted as "0 km away"

   An unresolvable GST frequency is             gst_compliance
   EXCLUDED, never given a guessed due date

   A period not yet due is EXCLUDED             gst_compliance
   from every count -- not a gap, just
   not due yet

   A verification that COULD NOT RUN keeps      deep_research._verify_block
   the source and adds an honest gap

   Two disagreeing registries BOTH surface      _merge_director_rosters

   Two known data contradictions are            rules.md Section A
   DELIBERATELY left unreconciled

   The subject project is excluded from its     promoter_portfolio
   own portfolio totals by the ENTRY's own
   reg-no, never by the fetch reg-no

   An empty GST records file is NEVER written   gst_intake
   -- the file's existence means "data supplied"
```

---

## 15. Constraints, assumptions and dependencies

### 15.1 Hard constraints

| ID | Constraint | Consequence |
|---|---|---|
| C1 | 7 of 9 MahaRERA categories are CAPTCHA-gated | A human is required, or a token supplied |
| C2 | Maha Bhulekh admits **no** reusable session | Land records are opt-in and manual, four steps plus a solve |
| C3 | The GST portal needs one solve per PAN search and one per GSTIN | GST is opt-in, and sits beside the other browser work |
| C4 | `docx2pdf` is Windows-only | **The deliverable requires Windows + Word** |
| C5 | Marathi land-record labels do not map to RERA's English text | Office/village must be human-picked; never fuzzy-matched |
| C6 | `output/` is gitignored and holds the `.docx` template | The template must be backed up manually before structural change |
| C7 | Sections B and C are injected verbatim into prompts | They must themselves avoid the characters the gate forbids |
| C8 | The External document is generated **entirely by code** | Editing the rules alone cannot change External behaviour; it must be implemented in code |
| C9 | Sonnet 5 intro pricing expires **2026-08-31** | Cost figures under-report by a third after that date unless updated |
| C10 | No packaging manifest, no CI | The suite is run by hand; drift is invisible until someone runs it |

### 15.2 Assumptions

| ID | Assumption | Risk if wrong |
|---|---|---|
| A1 | MahaRERA's public record is materially complete and current | The whole product's evidence base weakens |
| A2 | An operator is available to solve CAPTCHAs during a run | Full runs become impossible; only 2 of 9 categories fetchable |
| A3 | The MCA mirrors continue to publish freely | The 3-way cross-check degrades toward single-source |
| A4 | RERA document sets always include a PAN and never a GSTIN | `--pan` remains the usual flag; `--gstin` the exception |
| A5 | Word is installed on the run host | No PDF — the `.docx` fallback becomes the deliverable |
| A6 | Positional parsing of MahaRERA search cards remains valid | Candidate metadata degrades (but `raw_text` is always retained) |

### 15.3 Dependencies

| Dependency | Version | Used for | Criticality |
|---|---|---|---|
| `playwright` | ≥1.44 | All browser work | **Critical** |
| `requests` | ≥2.31 | All HTTP | **Critical** |
| `anthropic` | ≥0.40 | Every LLM call, via `tool_runner` | **Critical** |
| `python-docx` | ≥1.1 | Both Charter builders + Executive Briefing | **Critical** |
| `docx2pdf` | ≥0.1.8 | The deliverable | **Critical (Windows)** |
| `reportlab` | ≥4.0 | Summary PDF | High |
| `pymupdf` | ≥1.24 | Document text extraction | High |
| `pytesseract` + `Pillow` | ≥0.3 / ≥10.0 | OCR fallback | Medium |
| `beautifulsoup4` | ≥4.12 | Registry and land-record scraping | High |
| `streamlit` | ≥1.32 | UI | Low |
| `matplotlib` | **undeclared** | Executive Briefing donut charts | **Gap — see §19** |
| Tesseract binary + `eng`/`mar` data | — | OCR | Medium (bundled data) |
| Microsoft Word | — | COM automation for PDF | **Critical (Windows)** |

---

## 16. Out of scope

| Item | Rationale |
|---|---|
| States other than Maharashtra | Different portals, schemas, land systems, languages |
| Continuous monitoring / alerting | Batch, on-demand, human-triggered by design |
| Automated CAPTCHA solving | Hard ethical and ToS boundary |
| Legal title opinion | The land-record check is corroborative; a Title Report is a lawyer's product |
| Financial modelling, valuation, IRR | Downstream of this product |
| Multi-tenant hosting, RBAC, audit login | Single-operator local tool |
| A REST/JSON API | No consumer requires one today |
| Automated identifier linking (CIN/CTS → reg-no) | Deliberately an explicit human action, never auto-matched |
| Reconciling the two known data contradictions | Deliberately preserved |
| Translating the Charter | English only |
| A database | The filesystem *is* the data store; `.facts.json` is the record |

---

## 17. Acceptance criteria — definition of done

### 17.1 For a single run

```
   [ ] The correct builder was used:
       company_charter.py::_fill_template via run_company_charter
       (NOT charter_report.py, which is a different document for a
        different request; charter_document.py builds nothing at all)

   [ ] A REAL monitor-flag resolution pass was performed -- flags in
       Overview & Flags were actually re-checked against current facts,
       not carried over stale

   [ ] The compliance review stage RAN, or its failure was REPORTED
       rather than passed over silently

   [ ] Output exists only in output/company_charters/

   [ ] Both PDFs exist (or the conversion failure was reported)

   [ ] .facts.json was written AFTER restoration, so it holds the
       complete unscrubbed record

   [ ] The run summary shows per-label API cost

   [ ] Any script that hit _fill_template directly called
       _convert_docx_to_pdf itself
```

### 17.2 For the External document specifically

```
   [ ] Zero em dashes, zero " -- "
   [ ] Zero citation markers attached to an absence
   [ ] Zero uncited factual bullets (allowing the explicit exemption
       list: pipeline-computed figures, qualifier sentences, statements
       of statute)
   [ ] Zero markers glued mid-word
   [ ] Zero orphan sources (listed but never cited)
   [ ] Zero leftover Internal Document Library status text
   [ ] Zero italic placeholder styling (except the intentional
       red Standing Gap paragraph)
   [ ] Zero run colours outside the allowed set
   [ ] Balanced parentheses in every numbered Sources entry
   [ ] Bullet numbering intact on every Sources entry
   [ ] No Weight column in either score table
   [ ] Bullet hanging-indent fix present in the numbering part
```

### 17.3 For a code change

```
   [ ] python -m pytest -q passes, and any remaining failure is one you
       can explain -- record the count in the commit message so drift
       is visible next time

   [ ] If a guard was added: its symbol is in the right table in
       guardrails.md, test_guardrails_doc.py resolves it, and there is
       a test that BREAKS IT DELIBERATELY
       ("a guard nobody has watched fail is only a comment")

   [ ] If rules.md Section B or C was edited: no em dash, no " -- ",
       and _preflight_rules still passes

   [ ] If a rendering change was made: verified via the free no-API
       _fill_template loop against a saved facts.json

   [ ] If the template's structure was touched: a timestamped backup
       was taken first

   [ ] CLAUDE.md updated if the FLOW changed;
       rules.md updated if the CONTENT RULES changed
```

---

## 18. Release history and roadmap

### 18.1 Shipped (as of 2026-08-13)

```
  v0.x  ACQUISITION
        [x] Project resolution, guest-session management, 9-category scrape
        [x] Document + complaint-order download with reuse
        [x] Summary PDF, run archiving, endpoint verification mode

  v0.x  INTELLIGENCE
        [x] Agentic deep research with per-claim verification
        [x] Bounded gap retry with differentiated strategies
        [x] Promoter portfolio with 5 km micro-market influence
        [x] 3-way MCA-mirror chain with roster-conflict surfacing
        [x] Credit rating (ICRA + Infomerics), IBBI, group companies
        [x] MahaRERA judgments search + appeal cross-referencing

  v0.x  COMPLIANCE
        [x] GST intake, confirmed live 2026-07-31 (76 scoreable periods)
        [x] Statutory due dates incl. state-specific QRMP
        [x] CTS land-record path with human-in-the-loop resolution
        [x] Pre-RERA intake (CIN-only, CTS-only) + explicit attachment

  v1.0  THE RESHAPE  (2026-08-10, all 7 tasks)
        [x] Task 0  Test fixtures repaired
        [x] Task 1  Flags become headlines, Gaps carries the detail
        [x] Task 2  Clean checks deleted (prompt + scrubber + gate)
        [x] Task 3  Per-clause External citations (shipped as topic
                    matching, later moved to a model pass -- per-claim
                    tokens in the schema were REJECTED as a breaking
                    change needing regeneration)
        [x] Task 4  Facts-schema corrections (merge orders relocated,
                    mortgage fields merged, landowner row made a pointer)
        [x] Task 5  Per-finding deep research as a pipeline stage
        [x]         Rules migrated from CLAUDE.md into rules.md
        [x]         charter_document.py builder retired; module kept

  v1.0  GOVERNANCE  (2026-08-11)
        [x] guardrails.md + test_guardrails_doc.py symbol enforcement
        [x] Blocking compliance review with mechanical quote verification
        [x] CHARTER_ALLOW_UNCHECKED documented as a deliberate decision

  v1.1  COST CONTROL  (2026-08-12 -- 2026-08-13)
        [x] Shared _VerificationBudget caps the verify/gap-retry fan-out,
            after P51800077150's first-ever research pass ran past $10
        [x] _verify_claims_batch / _retry_gaps_batch: many sources or every
            open gap in a round checked/retried in ONE call, not one each
        [x] PIPELINE_COST_CAP_USD ($6.00) -- hard ceiling on total run
            spend, deep research and Charter generation combined
        [x] cache_control on every agentic pass; cache write/read tokens
            priced and reported separately from plain input tokens

  v2.0  PAN-INDIA + GROUP-LEVEL DILIGENCE  (2026-08-17 -- 2026-08-21)
        [x] states/ seam: StateProfile (data) + StateAdapter (one
            acquire()); capability declaration; state resolved from the
            registration number, --state always overriding
        [x] Six authorities: MH, GJ, KA, TG, JH, WB. app.py deleted its
            ~160 duplicated lines and calls the same acquire()
        [x] Group entity graph -- brand name PROPOSES, a hard link
            CONFIRMS -- then group-wide RERA sweep, GST, case law
        [x] Promoter PAN off the filed card; charge movement; state
            footprint; CRISIL added to the rating chain
        [x] CTS land records fixed: fields had been {} on EVERY lookup
            this repo had ever made; now 15 fields plus mutation entries
        [x] Promoter-keyed order registers from four authorities, and
            MahaRERA's own orders search repaired after it was found to
            have been returning nothing for every query
        [x] Every group pass reports its own COVERAGE, because the
            recurring defect in all of the above was a check that could
            not run being read as a check that found nothing
```

### 18.2 Roadmap

```
  NEXT  --  HEALTH OF THE CODEBASE            effort   value   risk if skipped
  +---------------------------------------------------------------------+
  | R1  Add matplotlib to requirements.txt      XS      M      Executive |
  |                                                            Briefing  |
  |                                                            fails on  |
  |                                                            clean     |
  |                                                            install   |
  | R2  Update Sonnet pricing before 08-31      XS      H      All cost  |
  |     (or add a DATED pricing table)                         figures   |
  |                                                            wrong by  |
  |                                                            one third |
  | R3  pyproject.toml + CI running pytest      S       H      Drift     |
  |     with docx2pdf skipped                                  invisible |
  | R4  Narrow the "Document Library" External  S       H      Blocks a  |
  |     gate check to the real placeholder                     planned   |
  |     text                                                   feature   |
  | R5  Persist the GST `as_of` date into       S       M      Re-render |
  |     facts so a re-render is deterministic                  drift     |
  | R6  Fix the stale fetch_gstin_filing_table  XS      L      Misleads  |
  |     docstring                                              a reader  |
  +---------------------------------------------------------------------+

  THEN  --  STRUCTURE
  +---------------------------------------------------------------------+
  | R7  Decompose company_charter.py (10,182 lines) in order of         |
  |     independence:  registries.py -> editorial.py -> scoring.py      |
  |     -> docx_helpers.py.  Keep _fill_template and run_company_charter|
  |     together.                                       effort L, value H|
  | R8  Resolve the "locked ten" vs two conditional appends             |
  |     inconsistency -- fold them in, or amend the rule                |
  | R9  Route Grok verification through the usage ledger                |
  | R10 Retention policy for output/_history/ and gst_portal_raw/       |
  +---------------------------------------------------------------------+

  THEN  --  REACH
  +---------------------------------------------------------------------+
  | R11 LibreOffice headless as a PDF fallback -> unblocks Linux and    |
  |     containerised deployment                        effort M, value H|
  | R12 Wire the three unsourced Developer Score sub-metrics            |
  |     (team_strength, financial_strength_debt) or document them as    |
  |     permanently manual                                              |
  | R13 A scheduled canary run against a known-good project, alerting   |
  |     when a `confirmed` endpoint starts failing                      |
  | R14 Batch mode: a list of registration numbers in, a pack each out  |
  | R15 Cross-project comparison view (the Executive Tracker, automated)|
  +---------------------------------------------------------------------+

  MAYBE  --  requires a decision, not just effort
  +---------------------------------------------------------------------+
  | R16 A second state authority (each is a new portal, schema and      |
  |     land system -- effectively a new product)                       |
  | R17 Change-monitoring: re-run on a schedule and alert on material   |
  |     field diffs (the archive + _diff_mortgage_lender machinery      |
  |     already exists; this is the productisation of it)               |
  | R18 A hosted service (blocked by C4 until R11 lands)                |
  +---------------------------------------------------------------------+
```

---

## 19. Risks and mitigations

| # | Risk | L | I | Mitigation in place | Residual action |
|---|---|---|---|---|---|
| RK1 | MahaRERA changes an endpoint or selector | High | High | `confirmed`/`observed` tiering; `--verify`; envelope normalisation; `raw_text` always retained | **R13** — scheduled canary |
| RK2 | MahaRERA changes the CAPTCHA mechanism | Med | High | `--token` manual escape hatch | Monitor |
| RK3 | Sonnet 5 intro pricing expires 2026-08-31 | **Certain** | Med | — | **R2 — 20 days** |
| RK4 | A registry mirror goes paywalled | Med | Med | `_looks_paywalled`; 3-way chain; conflicts surfaced | Accept |
| RK5 | Word/COM unavailable on the run host | Med | **High** | `.docx` retained as fallback output | **R11** |
| RK6 | Template `.docx` lost or corrupted | Low | **Critical** | Section A mandates a timestamped backup | Automate the backup; template is gitignored |
| RK7 | `company_charter.py` becomes unmaintainable | Med | High | `CLAUDE.md` + `guardrails.md` + `rules.md` navigation | **R7** |
| RK8 | The suite rots silently without CI | **High** | High | 28 test files exist and are meaningful | **R3** |
| RK9 | A guard test passes vacuously | Med | High | Precedent already caught and fixed (Section-A test) | Review assertion preconditions on every guard test |
| RK10 | PII accumulates indefinitely in `gst_portal_raw/` and `_history/` | **Certain** | Med | `output/` is gitignored | **R10** |
| RK11 | GST scoring drifts on re-render (`as_of` defaults to today) | Med | Med | `run_gst_compliance_check` passes an explicit date at run time | **R5** |
| RK12 | Anthropic API outage mid-run | Low | Med | Never fatal; standalone retry; `verification_error` keeps sources | Accept |
| RK13 | An operator ships an External document with `CHARTER_ALLOW_UNCHECKED=1` | Low | **High** | Documented as *a decision, not a convenience*; the review JSON records it | Consider making the override stamp the document |
| RK14 | `PROMOTER_PROJECT_LIMIT = 25` silently understates a large developer | Med | Med | Never silent — `truncated` field plus an extra limitation | Consider raising, or paginating |
| RK15 | Google Maps scrape is a ToS exposure | Low | Med | Off by default; in-code caveat; always falls back | Keep off; consider the paid Routes API |
| RK16 | The two conditional sections violate the locked-order rule | **Certain** | Low | Documented here and in `SAD.md` §25 | **R8** |

---

## 20. Open questions

| # | Question | Owner | Blocking |
|---|---|---|---|
| Q1 | Should the three unsourced Developer Score sub-metrics be wired to a data source, or formally declared manual-input? Today they silently drag the composite down | Product | R12 |
| Q2 | What is the retention period for PII-bearing `gst_portal_raw/` screenshots? | Compliance | R10 |
| Q3 | Should `CHARTER_ALLOW_UNCHECKED` stamp a visible marker into the document it ships? | Product | RK13 |
| Q4 | Is LibreOffice-rendered PDF fidelity acceptable for a client-facing External document, or is Word required? | Product + Design | R11 |
| Q5 | Should the two conditional sections be folded into Diligence Appendix, or should the locked-ten rule be amended to "ten unconditional plus two conditional"? | Engineering | R8 |
| Q6 | Should `charter_report.py`'s "Counterparty + Collateral" layout eventually replace the ten-section Charter, or remain a parallel deliverable? `rules.md` says its layout is locked and to *ask first* | Product | R7 scope |
| Q7 | Is a 24-hour research reuse window right? Too long for a fast-moving litigation picture, too short for stable macro-market data | Product | — |
| Q8 | Should `executive_briefing.py` get a CLI, or be formally declared library-only? | Engineering | D13 |

---

## 21. Appendices

### Appendix A — Requirement traceability

| Product goal | Functional requirements | Enforcement | Verified by |
|---|---|---|---|
| PG1 Compress diligence time | FR-ACQ-08, FR-CORP-09, FR-ACQ-15, FR-RES-08 | Parallelism + reuse | Wall-clock per run |
| PG2 Every claim traceable | FR-DATA-01/02, FR-EXT-01..05 | Schema + citation registry | Gate 2 checks 7–8; `test_external_citations.py` |
| PG3 Never guess | FR-RES-07, FR-TRK-06, FR-COMP-10, FR-LAND-03 | Drop-not-guess everywhere | `test_data_quality_fixes.py` |
| PG4 Clear vs unknown | FR-ED-01/02/06 | Scrubber + gate | `test_clean_check_scrubber.py` |
| PG5 Partial failure never costs the run | FR-CORP-10, FR-TRK-10, FR-COMP-12, FR-RES-12, NFR-REL-01/02 | Never-fatal wrappers | `test_gst_intake.py`, `test_promoter_portfolio.py` |
| PG6 Non-compliant does not ship | FR-GATE-01..10 | Three-tier gate chain | `test_rules_preflight.py`, `test_claude_md_doc_review.py` |
| PG7 Two audiences, one analysis | FR-DOC-01/02, FR-EXT-* | Render-time fork | `test_charter_report_source_labels.py` |
| PG8 Costs visible and bounded | FR-OPS-02/03/08/09/10, FR-RES-13 | Per-label ledger + `PIPELINE_COST_CAP_USD` + batched fan-out | `test_usage_tracking.py`, `test_search_budget.py` |
| PG9 Cheap re-runs | FR-ACQ-15/17/18, FR-RES-08 | Archive + reuse | `test_attach_rera_number.py` |
| PG10 Record outlives page | FR-REV-01..03, FR-DOC-08 | Scrub/restore | `test_process_text_sanitizer.py` |

### Appendix B — Command reference

```
   PRIMARY
   python main.py <REG_NO | project name>
       [--gstin X | --pan Y]   opt-in GST intake (mutually exclusive)
       [--headed]              show the search browser
       [--token T]             supply a session you captured yourself
       [--no-auto-auth]        skip the CAPTCHA browser (2 categories only)
       [--captcha-timeout N]   default 300
       [--project-id N]        bypass search resolution entirely
       [--output-dir D]        default "output"
       [--verify]              probe endpoints instead of building

   streamlit run app.py        same pipeline, 3 tabs

   COMPONENT RETRY
   python deep_research.py <REG_NO> [--output-dir] [--no-rebuild] [--force-refresh]
   python company_charter.py <REG_NO>
   python finalize_report.py <REG_NO>          zero network calls

   PRE-RERA INTAKE
   python promoter_intake.py <CIN> [company_name]
   python cts_intake.py <district> <office> <village> <cts> <mobile>
   python attach_rera.py <case_id> <reg_no>

   LAND RECORDS  (in order)
   python mahabhumi.py offices <district>
   python mahabhumi.py villages <district> <office_label>
   python cts_resolve.py offices <REG>
   python cts_resolve.py villages <REG> "<office>"
   python cts_resolve.py candidates <REG> "<office>" "<village>" <cts_query>
   python cts_resolve.py finalize <REG> "<office>" "<village>" <cts> <mobile>

   GST
   python gst_intake.py <PAN|GSTIN> <reg_no>
   python gst_portal.py pan <PAN>
   python gst_portal.py filing <GSTIN>

   SESSION
   python token_cache.py get           exit 0 ok, 1 stale/absent
   python token_cache.py set <token>
   python token_cache.py minutes-left

   SECOND BUILDER
   python run_charter_pipeline.py      prepare/finish seam
   python build_report.py              one-off, per engagement
```

### Appendix C — Environment variables

| Variable | Required | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | For research + Charter | Absent → both degrade, never fatally |
| `XAI_API_KEY` | Optional | Enables the Grok second-opinion document-grounding path |
| `TESSERACT_CMD` | Optional | OCR binary location; falls back to PATH then two Windows paths |
| `COMPANY_CHARTER_USE_MAPS_SCRAPE` | Optional | Must be exactly `"1"`. Opt-in Maps distance refinement; ToS caveat |
| `CHARTER_ALLOW_UNCHECKED` | Optional | `1`/`true`/`yes` downgrades the blocking review to advisory. **A decision to ship an unchecked document, not a convenience** |

### Appendix D — Where to look when changing something

```
   CHANGING...                       READ FIRST              THEN CHECK
   -----------                       ----------              ----------
   the pipeline flow                 CLAUDE.md               update CLAUDE.md
   what the document may say         rules.md                _preflight_rules,
                                                             then the gates
   a gate, fallback or bound         guardrails.md           add the symbol,
                                                             write the test
                                                             that breaks it
   the ten sections                  rules.md Section A      *** ASK FIRST.
                                                             The order is
                                                             locked. ***
   the .docx template structure      rules.md Section A      BACK IT UP first
                                                             (timestamped)
   Section B or C text               rules.md Section A      no em dash,
                                                             no " -- "
   External behaviour                CHARTER_RESHAPE_SPEC.md It is generated
                                     (historical note)       BY CODE. Editing
                                                             rules alone will
                                                             NOT move it.
   scoring                           SAD.md section 17       Developer Score
                                                             never renormalises;
                                                             Doc Confidence
                                                             always does
   anything in company_charter.py    SAD.md section 9        10,182 lines --
                                                             use the function
                                                             group map
```

### Appendix E — Glossary

| Term | Meaning |
|---|---|
| **Charter** | The Company Charter document pair. The primary deliverable |
| **Clean check** | A check that ran and came back clear. Produces **no sentence** |
| **Gap** | An open unknown the reader can act on. Kept in full, numbered |
| **Finding** | Something established. Earns a dedicated research pass |
| **Flag** | A one-sentence headline ending in `(Gap N)`. Imminent / structural / monitor |
| **Variant** | `internal` or `external` — the fork at render time |
| **Carryover** | A pre-RERA intake result attached to a reg-no by explicit human action |
| **`confirmed` / `observed`** | Endpoint trust levels — individually re-verified vs taken from real traffic |
| **Never fatal** | A stage whose failure logs a warning and lets the run continue |
| **Hard gate** | A guard that stops the run or blocks a save or the deliverable |
| **QRMP** | Quarterly Return Monthly Payment — the GST scheme with state-specific due dates |
| **CTS** | City Survey number — the Maharashtra land parcel identifier |
| **Property Card** | मालमत्ता पत्रक — the Maha Bhulekh land record |
| **SPV** | Single-purpose vehicle — a company formed for one project. Detecting one is a core product job |
| **Roster conflict** | Two registries naming different directors for the same company. Both are surfaced |

---

*End of Product Requirements Document.*

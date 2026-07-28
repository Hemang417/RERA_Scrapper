# MahaRERA Scraper

Fetches a MahaRERA-registered project's complete public record (project
details, promoters, professionals, documents, complaints, appeals), cross-
checks the promoter's corporate identity against multiple independent
registries, runs an agentic deep-research pass for market/promoter context,
and produces a PDF summary report plus a pair of Company Charter documents
(Internal and External) -- all from one command.

No MahaRERA login is required for the search step or the `projects`/
`complaints` categories. The other 7 categories need a short-lived guest
session that MahaRERA gates behind a CAPTCHA on the project detail page; by
default a real browser opens for you to solve it once per session (cached
afterward).

## Usage

```bash
python main.py P51800012345
python main.py "Kalpataru Height"          # free-text project name also works
python main.py P51800012345 --verify        # sanity-check category endpoints instead of building a PDF
python main.py P51800012345 --no-auto-auth  # skip the CAPTCHA browser; only the 2 no-auth categories
python main.py P51800012345 --token <...>   # supply a session token you captured yourself
```

A Streamlit UI wrapping the same pipeline is available via `streamlit run app.py`.

Requires `ANTHROPIC_API_KEY` for the deep-research and Company Charter
generation steps -- either failing is never fatal to the run; retry them
standalone with `python deep_research.py <REG_NO>` / `python
company_charter.py <REG_NO>`.

See `requirements.txt` for dependencies (Playwright, Anthropic SDK,
python-docx, PyMuPDF, BeautifulSoup, etc.).

## Architecture

```
================================================================================
                     MahaRERA SCRAPER -- PIPELINE ARCHITECTURE
================================================================================

   ENTRY POINTS
   +----------------------+        +----------------------+
   |  main.py <REG_NO>    |        |  app.py (Streamlit)  |
   |  (CLI)               |        |  wraps the same calls|
   +-----------+----------+        +-----------+----------+
               +--------------------+-----------+
                          |
                          v
================================================================================
 STAGE 1 -- RESOLVE + AUTH
================================================================================
   resolver.py                          session_auth.py / token_cache.py
   +-----------------------+            +---------------------------------+
   | resolve_project_id()  |            | cached token? --yes--> reuse    |
   | REG_NO -> internal ID |----------->|      |no                       |
   | (public search page)  |            |      v                         |
   +-----------------------+            | acquire_token_via_browser()    |
                                         | (Playwright: solve CAPTCHA,    |
                                         |  mint guest accessToken)       |
                                         +---------------------------------+
                                                       |
                                                       v
================================================================================
 STAGE 2 -- FETCH RAW DATA                    (all PARALLEL, ThreadPoolExecutor)
================================================================================
   api_client.py
   +------------------------------------------------------------------------+
   | fetch_all_categories()                                                 |
   |   projects, spocs, professionals, partners, past_experiences,          |
   |   sro_details, documents, complaints, appeals                         |
   |        (each = POST to a MahaRERA microservice endpoint)               |
   +------------------------------------------------------------------------+
                    |                              |
                    v                              v
     download_documents()              download_complaint_orders()
     (per-doc, parallel workers)        (per-complaint PDF, parallel)
                    |                              |
                    +--------------+---------------+
                                   v
================================================================================
 STAGE 3 -- PROMOTER PORTFOLIO
================================================================================
   promoter_portfolio.py
   +------------------------------------------------------------------------+
   | build_promoter_portfolio()                                             |
   |   for each of promoter's OTHER registered projects (capped at          |
   |   PROMOTER_PROJECT_LIMIT): fetch complaints/appeals, geocode, filter    |
   |   to a 5km radius                                                      |
   |   -> portfolio.json (cross-project complaint/appeal/area totals)       |
   +------------------------------------------------------------------------+
                                   |
                                   v
================================================================================
 STAGE 4 -- DEEP RESEARCH  (LLM agent -- best-effort, degrades gracefully)
================================================================================
   deep_research.py
   +------------------------------------------------------------------------+
   | run_deep_research()                                                    |
   |   Claude + web_search/web_fetch tools -> macro_market / micro_market /  |
   |   promoter_external research, each claim tagged with a source          |
   |   [requires ANTHROPIC_API_KEY -- on failure: WARN, continue anyway]    |
   +------------------------------------------------------------------------+
                                   |
                                   v
================================================================================
 STAGE 5 -- COMPANY CHARTER GENERATION            company_charter.py
================================================================================
   run_company_charter()
   +------------------------------------------------------------------------+
   | (a) _run_charter_pass()        Claude -> facts JSON (_CHARTER_FACTS_    |
   |        SCHEMA) from RERA data + extracted document text + research     |
   | (b) _verify_material_claims()  Claude re-checks land/litigation/FSI    |
   | (c) _check_document_grounding()                                        |
   | (d) attach code-computed fields verbatim (never LLM-paraphrased):       |
   |        document_library, promoter_portfolio, complaint_outcomes         |
   +------------------------------------------------------------------------+
                                   |
                                   v
   +------------------------------------------------------------------------+
   | (e) PHASE-2 CHECKS -- 5-way ThreadPoolExecutor, independent sites       |
   |                                                                         |
   |  +-------------+ +-----------+ +---------------------------------+     |
   |  |credit rating| |IBBI check | |  COMPANY PROFILE  (zoom below)  |     |
   |  |ICRA/Infomer.| |insolvency | |                                 |     |
   |  +-------------+ +-----------+ +---------------------------------+     |
   |  +---------------------+  +------------------------------------+       |
   |  | group companies     |  | MahaRERA judgments search           |       |
   |  | (ZaubaCorp only --  |  | (/orders-judgements)                |       |
   |  | Tofler/InstaFin.    |  |                                     |       |
   |  | don't expose this   |  +------------------------------------+       |
   |  | data freely)        |                                                |
   |  +---------------------+                                               |
   +------------------------------------------------------------------------+
                                   |
                                   v
   +------------------------------------------------------------------------+
   | ZOOM: COMPANY PROFILE -- MCA-mirror redundancy chain                    |
   | _run_mca_profile_chain(cin, promoter_name)                              |
   |                                                                         |
   |    ZaubaCorp ------+                                                    |
   |    (CIN redirect    |                                                    |
   |     trick)          |                                                    |
   |                     |                                                    |
   |    Tofler ----------+--> _merge_director_rosters()                      |
   |    (Playwright:      |      - matching directors  -> merge silently      |
   |     type name in a  |      - DISAGREEING rosters  -> roster_conflicts   |
   |     real browser,   |        (real case hit: ZaubaCorp says Cooper,     |
   |     verify CIN)     |         InstaFinancials says Sahaya -- BOTH       |
   |                     |         surfaced, neither silently picked)        |
   |    InstaFinancials --+                                                   |
   |    (ASP.NET web                                                          |
   |     service, CIN                    roster_conflicts                    |
   |     search direct)                        |                             |
   |                                            v                             |
   |                              facts["gaps"] (renders in BOTH doc variants)|
   +------------------------------------------------------------------------+
                                   |
                                   v
   +------------------------------------------------------------------------+
   | (f) SOURCE-TRUST REGISTRY  (cross-run, persistent)                      |
   |                                                                         |
   |  facts["sources"] -> _record_source_hits_and_promote()                 |
   |       |                        |                                        |
   |       |              tally each open-web domain's hit count             |
   |       |              (99acres, NoBroker, press, social, Wikipedia --    |
   |       |               MahaRERA/MCA-mirror/rating/IBBI/Maps excluded,    |
   |       |               already trusted by design)                       |
   |       |                        |                                        |
   |       |              5th distinct project -> AUTO-PROMOTE               |
   |       v                        v                                        |
   |  source_trust_registry.json (repo root, accumulates across ALL runs)   |
   |                                |                                        |
   |                    promotion review note -> Internal doc ONLY           |
   +------------------------------------------------------------------------+
                                   |
                                   v
   +------------------------------------------------------------------------+
   | (g) _diff_mortgage_lender() vs prior run   (run_archive.py snapshot)   |
   | (h) run_review_authenticity_triage()                                   |
   +------------------------------------------------------------------------+
                                   |
                                   v
   +------------------------------------------------------------------------+
   | (i) DUAL DOCUMENT RENDER  -- _fill_template() x2, same facts dict       |
   |                                                                         |
   |        facts (real, mutated in place)                                  |
   |              |                                                          |
   |      +-------+--------+                                                |
   |      v                v                                                |
   |  doc_variant=      doc_variant=                                        |
   |  "internal"        "external"                                          |
   |      |           (deep-copies facts first -- never touches real dict)  |
   |      v                v                                                |
   |  "(label)" cites   "[N]" numbered cites -> generic Sources list         |
   |  "(Code-Computed)" headings dropped, internal-process prose cleaned,   |
   |  labels kept       "(see gaps[N])"-style leaks stripped                |
   |      v                v                                                |
   |  *_Internal.docx   *_External.docx                                     |
   +------------------------------------------------------------------------+
                                   |
                                   v
              *.facts.json persisted (the REAL, Internal-mutated facts)
                                   |
                                   v
================================================================================
 STAGE 6 -- PDF REPORT                              report.py
================================================================================
   build_pdf() -- combines category_data + charter facts + document
                  manifest into <REG_NO>_summary.pdf
                                   |
                                   v
================================================================================
 STAGE 7 -- RUN SUMMARY                              main.py
================================================================================
   prints: auth source, per-category counts, documents downloaded,
           promoter_profile built, market_research status,
           company_charter (Internal + External paths) status

--------------------------------------------------------------------------------
 SIDE PATH (manual, opt-in, never auto-run):
   mahabhumi.py -- CTS -> Maha Bhulekh Property Card lookup. Requires a
   human to drop output/<reg_no>/cts_lookup_input.json with the office/
   village pre-resolved (via `python mahabhumi.py offices/villages ...`),
   because the site's CAPTCHA has no reusable session -- solving it blocks
   on a human every single call, so it's never triggered automatically.
================================================================================
```

A few things worth noting from this shape:

- Everything through Stage 4 is code-only/deterministic except the deep-
  research LLM pass, which is allowed to fail without breaking the run.
- The Company Profile chain is the only place three independent web
  sources get cross-checked against each other rather than just falling
  back on failure -- confirmed catching a real 3-way director-roster
  disagreement for one promoter (ZaubaCorp vs. InstaFinancials).
- The Internal/External document fork happens once, right at render time,
  from the same in-memory `facts` dict -- everything upstream of it is
  variant-agnostic.

## Output layout

```
output/<REG_NO>/
  raw/                    per-category raw JSON
  documents/              downloaded project documents
  complaint_orders/       downloaded complaint order PDFs
  promoter/portfolio.json cross-project promoter track record
  <REG_NO>_summary.pdf    final PDF report

output/company_charters/
  Company_Charter_<Name>_<REG_NO>_Internal.docx
  Company_Charter_<Name>_<REG_NO>_External.docx
  Company_Charter_<Name>_<REG_NO>.facts.json

source_trust_registry.json   cross-run open-web source trust tallies (repo root)
```

# Company Charter reshape — implementation spec

> **COMPLETED 2026-08-10. HISTORICAL RECORD ONLY — do not follow this file.**
>
> All seven tasks below shipped. Kept for the record of what was asked and why,
> not as current guidance. It is stale in ways that will mislead:
>
> * it calls `charter_document.py` dead code that must never be edited. Its
>   builder was retired, but the module is a LIVE shared library that
>   `charter_report.py` imports 16 symbols from. Do not delete it.
> * its line references (`~line 514`, `~4581`, `~7185`) have moved by thousands.
> * "Already done (batch 1)" describes work now committed.
> * Task 3 was NOT built as described. Per-claim tokens in the facts schema were
>   rejected as a breaking change needing regeneration; topic matching shipped
>   instead, and later moved to a model pass (`run_editorial_passes`).
> * the rules moved out of `CLAUDE.md` into `rules.md`, so every reference here
>   to "CLAUDE.md Section B/C" now means `rules.md`.
>
> **Current guidance: `CLAUDE.md` for the pipeline flow, `rules.md` for the
> rules.** Two standing constraints unique to this file (locked section order,
> and the two deliberately unreconciled data contradictions) were migrated into
> `rules.md` Section A in commit `042f5d2`.

Work through these in order. Each task is independently shippable and names the
function to change and the check that proves it landed.

`CLAUDE.md` (repo root) is the binding policy. This file is the *build order*
for making the code match it. Where the two disagree, CLAUDE.md wins — fix this
file rather than the rules.

**Read before starting:** Sections B and C of CLAUDE.md are injected at runtime
into only two call sites — `_run_charter_pass` (line ~708, Section B only) and
`_llm_verify_citation_completeness` (line ~4248, B + C). Section C therefore
reaches only the advisory citation *judge*, never a call that writes External
content, because **the External document is generated entirely by code**
(`_fill_template(doc_variant="external")`), not by a model. Any External
behaviour in this spec must be implemented in code. Editing the rules alone will
not move it.

**Already done (batch 1, do not redo):** `_center_all_table_cells` length
threshold, `_apply_table_pagination`, `_ensure_documentation_confidence`,
`_period_is_abbreviation` / `_NON_TERMINAL_ABBREVIATIONS`,
`_EXTERNAL_DOC_LIBRARY_LEFTOVER_RE`.

---

## Task 0 — Repair the test fixtures (do this first)

The External quality gate currently has **no working test coverage**, so nothing
in this spec can be verified until this is fixed. 16 tests fail before any of
your changes; 13 of those fail on Windows too.

Root causes, confirmed by running the suite:

| Test file | Wants | Repo actually has |
|---|---|---|
| `test_executive_ready.py` `_FIXTURES` | `Company_Charter_PranamiBliss_P51800077150.facts.json` | `Company_Charter_Pranami_Bliss_P51800077150.facts.json` |
| `test_executive_ready.py` `_FIXTURES` | `Company_Charter_IRAInsignia_P51700031409.facts.json` | `Company_Charter_IRA_Insignia_P51700031409.facts.json` |
| `test_developer_score.py`, `test_documentation_confidence_score.py` | `Company_Charter_GodrejParkGreens_P52100019639.facts.json` | not in the repo at all |

The naming convention gained underscores and the fixtures were never updated.
Six further failures cascade from this: `test_executive_ready.py` builds a
scratch file that later tests consume, so when the first test dies the rest die
with it.

**Do:**
1. Repoint the two Pranami/IRA paths to the real filenames.
2. For Godrej: either locate the facts file (check
   `output/company_charters/_history/` and `reference_backup_2026-07-29/`) and
   restore it, or repoint those tests to a fixture that exists
   (`Company_Charter_Stellar_P51700048590.facts.json` is a good stand-in) and
   re-derive the expected values from an actual run. **Do not delete the
   assertions** — they encode real scoring behaviour.
3. `docx2pdf` is Windows-only; guard those two tests with
   `pytest.importorskip("docx2pdf")` so the suite is runnable on Linux/CI too.

**Verify:** `python -m pytest -q` — the only remaining failures should be ones
you can explain. Record the count in the commit message so drift is visible next
time.

---

## Task 1 — Flags become headlines, Gaps carries the detail

**Policy:** CLAUDE.md Section B, "Flags summarise, Gaps explain".

Today `_append_flag_list` (nested inside `_append_overview_section`, ~line 7185)
prints each flag's full text, and the same text is printed again in full under
Gaps & Sources. In the Pranami Bliss run that duplicated ~1.5 pages verbatim.

**Do:**
1. Give each gap a stable 1-based number at classification time in
   `_classify_flags`, so a flag and its gap can be cross-referenced.
2. In `_append_flag_list`, render a **one-sentence headline** ending in
   `(Gap N)`. Derive the headline from the first sentence of the flag text —
   reuse `_split_into_bullet_clauses` and take `[0]` rather than writing a new
   splitter.
3. Gaps & Sources keeps the full text, numbered to match.
4. Keep the existing `not items` branch exactly as it is — it already implements
   "an empty section keeps its heading and one bare line".

**Verify:** in a regenerated Internal document, no flag body text appears twice;
every `(Gap N)` on page 1 resolves to a numbered entry under Gaps & Sources.

---

## Task 2 — Delete clean checks (the core editorial rule)

**Policy:** CLAUDE.md Section B, "A clean check produces no sentence".

Prompt alone will not do this reliably — Section B reaches only
`_run_charter_pass`, and model compliance is probabilistic. Implement all three
layers:

1. **Prompt** — already live in Section B, no work needed.
2. **Deterministic scrubber** — a new pass over the assembled facts, before
   `_fill_template`, that drops any narrative field whose entire content is a
   clean-check statement. Detect on the *shape* of the claim, not a keyword
   blocklist: a sentence asserting non-existence ("no X found", "none on
   record", "X records are empty", "no X is disclosed") with no accompanying
   positive finding.
3. **Gate** — extend `_verify_external_document_quality` to fail when a
   paragraph is a pure absence statement carrying a `[N]` marker. That exact
   combination — a citation attached to a nothing — is the specific defect this
   rule exists to prevent, and it is cheap to detect.

**Respect the three carve-outs.** Do not touch: an entire empty section (Task 1
already handles it), an absence that is the stated basis of a score
(`_compute_developer_score` sub-metric notes, the `Litigation Load` KPI card in
`_append_executive_summary_kpis`), or anything under Gaps & Sources.

**Verify:** add a test asserting the Pranami Bliss litigation bullet
("No litigation found … records are empty[18] … returned nothing[1] … Form B
declares[2]") does not survive, while the Lis Pendens finding does, and while
the Developer Score RERA-Compliance note ("no completion extension, 0
complaints, 0 appeals") is untouched.

---

## Task 3 — Per-clause citations for External

**Policy:** CLAUDE.md Section C. Currently ~30% of External factual bullets
carry a marker; the rule demands all of them.

This cannot be prompted — External is built by code. The working approach,
proven in the hand-built draft:

1. Carry a source token **on each claim** in the facts structure rather than one
   `source` string per field. Fields already carrying `_FIELD_WITH_SOURCE`
   (see ~line 514) are the migration path.
2. At render time, resolve tokens to `[N]` for External and to the existing
   trailing parenthetical for Internal. One resolver, two output modes.
3. Number the Sources list from **tokens actually cited**, in first-use order,
   so an uncited source can never appear in the list.
4. Rewrite External source labels as descriptive citations (issuer + what it is
   + date). Never a raw internal filename — `Form B of SPs compressed.pdf` and
   the misspelled `NON Encubrance Finanace and legal.pdf` must not reach a
   client. Section C has the required shape.

**Two rules that are easy to miss:** a marker sits at the end of the clause it
supports, never at the end of a multi-claim paragraph; and the marker must
actually support the claim. The shipped charter cited the MahaRERA
complaints/appeals record for a sentence beginning "independent web research
found…" — both real sources, wrong pairing.

**Verify:** a test asserting zero uncited factual bullets in External (allowing
an explicit exemption list: pipeline-computed figures, qualifier sentences and
statements of statute), zero markers glued mid-word, and zero orphan sources.

---

## Task 4 — Facts-schema corrections

Three specific data-placement bugs, each small:

1. **Merge orders are land assembly, not litigation.** The two 6 March 2024
   Deputy Registrar orders currently sit inside `litigation_status`, where they
   exist only to be denied. Move them to the land/title chain field so they read
   as how the two plots became one.
2. **Merge the two mortgage fields.** `fsi_metrics.mortgage_area` and
   `fsi_metrics.mortgage_lender` (~lines 514–524, rendered ~4581–4589) both
   report the same absence in adjacent rows. Collapse to a single
   "Mortgage / charge on the land" field. Keep the real finding — that
   development agreements permit the developer to mortgage its free-sale area,
   and that no mortgage has been taken. Per CLAUDE.md, "a live right is a
   finding".
3. **RERA Core Data `Promoter (Land Owner / Investor)`** duplicates the
   Counterparty landowner row almost verbatim. Make it a pointer.

**Verify:** regenerate and confirm each fact appears exactly once.

---

## Task 5 — Per-finding deep research (largest task, do last)

**Policy:** CLAUDE.md Section B, "Deep research on every finding".

A new pipeline stage, not a formatting pass. After facts are assembled and flags
classified, every **confirmed finding** (not gaps, not clean checks) gets its own
follow-up research call establishing: what it is, who is involved, when it
arose, whether it is still live, and what it means for this project.

Worked example — the Lis Pendens is currently one line ("a 2017 notice against
an adjoining society"). It should resolve: which society, under what suit,
whether still pending, whether it touches this plot's boundary.

**Design notes:**
- Model this on the existing `_run_agentic_pass` call sites; do not invent a new
  transport.
- Budget it. `deep_research.py` already tracks per-label cost via `_record_usage`
  — give this stage its own label so its cost is visible in `usage_summary.json`.
- Cap the fan-out. `MAX_GAP_RETRY_ATTEMPTS` (deep_research.py:46) is the
  precedent for a bounded retry constant.
- It must degrade safely: if the research call fails, keep the original
  one-line finding rather than dropping it. An auth failure must never silently
  delete a finding.

**Verify:** with the stage stubbed to return a fixed payload, findings are
enriched and clean checks and gaps are untouched. Then one live run, checking
`usage_summary.json` for the new label's cost.

---

## Standing constraints

- **Section order is locked.** Ten top-level sections, same sequence, always.
  Reshape content inside the flow; never reorder or rename.
- **`charter_document.py` is dead code.** Never edit it to change live output.
  `test_charter_document.py` tests that dead builder and gives false confidence
  about the live pipeline.
- **The live path is** `company_charter.py::_fill_template`, via
  `run_company_charter`, called from `main.py`.
- **Back up the template** (`output/company_charters/Company_Charter_TEMPLATE_WebSourced.docx`,
  gitignored) with a timestamped copy before any change touching its structure.
- **PDF is the deliverable.** Any script hitting `_fill_template` directly must
  call `_convert_docx_to_pdf` itself afterward.
- **Sections B and C of CLAUDE.md must contain no em dash and no " -- "** — they
  are injected into External-facing prompts, and
  `_verify_external_document_quality` hard-fails the save on either character in
  an External paragraph.
- **Two known data contradictions, left deliberately unreconciled.** Do not
  "fix" them by picking a number: the Group/Affiliated Companies count (65
  linked entities vs. "of 299" in the Director Relationship Map), and the
  Internal document library's 61 rows (10 labelled only `Other – Legal`, 8
  filenames appearing twice).

## Cheap verification loop

`_fill_template` runs straight off a saved `facts.json` and makes **no API
calls**, so most of this spec can be verified for free:

```python
import json, company_charter as cc
facts = json.load(open('output/company_charters/Company_Charter_Pranami_Bliss_P51800077150.facts.json', encoding='utf-8'))
cc._fill_template('P51800077150', facts, 'output/company_charters/_verify/int.docx', doc_variant='internal')
cc._fill_template('P51800077150', facts, 'output/company_charters/_verify/ext.docx', doc_variant='external')
```

Only Task 5 needs live API calls.

# Company Charter pipeline — binding rules

This file governs `company_charter.py`'s Company Charter generator (invoked from
`main.py` at the end of the scrape pipeline, and directly via
`python company_charter.py <REG_NO>`). It is split into three sections with
different audiences and different delivery mechanisms — see the note at the
end of each section.

--- Section A: CODING-TIME RULES ---

These apply to Claude Code / human engineering sessions working on this repo.
They are NEVER sent to any runtime Claude API call this pipeline makes —
`_coding_time_notes()` in `company_charter.py` reads this section for
documentation purposes only and must not be passed into any system prompt.

- **Output location.** Company Charter files (template and generated) are only
  ever saved to `output/company_charters/` in this repo. Never to a Desktop
  "Promoter Profiles" folder or any other location.
- **Two/three-builders gotcha.** `charter_document.py` is a DEAD/legacy
  builder — not wired into `main.py` or any live entry point. The live
  automatic-pipeline path is `company_charter.py`'s `_fill_template()`
  (invoked via `run_company_charter`, called from `main.py`). There is also a
  THIRD module, `charter_report.py` (the "Counterparty + Collateral"
  restructured document, ~1457 lines, its own already-implemented
  findings-not-absence editorial policy) — it is invoked manually/standalone
  for specific requests, not from `main.py`. Rules in this file target
  `company_charter.py` specifically unless a task explicitly says otherwise.
  Never edit `charter_document.py` to change live charter output.
  `test_charter_document.py` tests `charter_document.py` (the dead builder) —
  confirmed by direct audit: it imports both `charter_document as cd` and
  `company_charter as cc`, but uses `cd.` 34 times vs `cc.` only 4 times (and
  never touches `_fill_template`, `_split_into_bullet_clauses`, or
  `_set_paragraph_as_bullets`). This gives false confidence about the live
  pipeline. Flag this back to the user rather than silently deleting or
  "fixing" it — do not treat its passing tests as evidence the live pipeline
  is correct.
- **Generation checklist.** Before declaring a charter run done, confirm
  (a) the correct builder was used (`company_charter.py`'s `_fill_template`,
  not `charter_document.py`), and (b) a real monitor-flag resolution pass was
  performed — flags in Overview & Flags were actually re-checked against
  current facts, not carried over stale.
- **Alignment.** Body text stays justified, table cells stay centered — don't
  regress `_justify_body_paragraphs` / `_center_all_table_cells` while
  touching anything else.
- **Template safety.** The `.docx` template
  (`output/company_charters/Company_Charter_TEMPLATE_WebSourced.docx`) lives
  under the gitignored `output/` directory. Back it up (timestamped copy)
  before any change touching template structure.
- **Restructure decisions.** If a generation run uses the newer Counterparty +
  Collateral section structure (`charter_report.py`), its layout is locked —
  don't re-shuffle it casually, ask first.
- **Final output format is PDF.** `run_company_charter` converts each rendered
  `.docx` to a same-named `.pdf` via `_convert_docx_to_pdf` (docx2pdf / Word
  COM automation) and returns the Internal PDF's path as `out_path` — the PDF
  is the actual deliverable, not the `.docx` (which stays on disk as the
  conversion input, and as the only output if conversion fails). Any script
  that regenerates a charter directly against `_fill_template` (bypassing
  `run_company_charter`) must call `_convert_docx_to_pdf` itself afterward.

--- Section B: COMMON CONTENT RULES ---

These are injected into every Claude API call `company_charter.py` makes that
generates or checks narrative/table content, regardless of section or doc
variant (both Internal and External). Delivery: `_common_content_rules()`
reads this section and is prepended as a separate, cacheable system-prompt
text block (Anthropic prompt caching, `cache_control: ephemeral`) ahead of the
call-specific system prompt — the content is identical across every call and
every project, so it is a stable cache prefix.

- **Deep-dive vs. nothing-found.** Read the heading, not just the bullet. If a
  section has real, confirmed findings, expand on them properly — don't let a
  material finding sit next to a generic "nothing found" placeholder that
  undersells it. A "nothing found" statement stays if it's itself materially
  informative (a clean record, a missing registration, a real red flag); cut
  it if it's pure filler with no bearing either way (e.g. an empty scoring
  sub-row with no informative content). When genuinely nothing was found for
  an entire section, collapse it to a single plain "Nothing found" — don't pad
  the absence into multiple bullets. Does NOT apply to Developer Score or
  Documentation Confidence Summary — those stay fully intact even when mostly
  N/A (intentional scoring methodology, not a gap to clean up).
- **No jargon, plain language, keep key terms.** Terms like CIRP, NCLT, RP,
  IBC Section 30, DSRA, ECLGS, IGR/e-ASR, KMP, ROC should not appear
  unexplained. On first use per section, expand or plain-language it once
  (e.g. "under active insolvency proceedings (CIRP) before the insolvency
  tribunal (NCLT)") — keep the term itself (still searchable/
  cross-referenceable) but don't require the reader to already know it. This
  is a plain-language pass, not a dumbing-down pass — don't lose precision,
  especially on legal/registry facts.
- **Bullets and grammar.** Bullets stay the default format — don't convert
  narrative sections to paragraph prose. Consolidate split/fragmented bullets
  into one well-formed bullet rather than a fragment trail. Correct grammar
  and capitalization throughout: sentence casing, proper capitalization of
  entities, complete sentences (not fragments strung together with
  semicolons).
- **CIN/DIN placement.** Never state CIN or DIN inline in prose, anywhere —
  EXCEPT the stakeholder/director identity table(s) (Corporate/Promoter
  Identity, Current Directors & KMP, Company Registration Profile), where
  CIN/DIN is required, not forbidden. One rule, one table exception — not two
  separate rules, and no "first mention keeps it" carve-out.
- **Empty table/row removal.** Drop a whole table if every row has no real
  information; drop a row only if every column in it is empty — a row
  survives if even one column has a real, confirmed value. EXCEPT: never
  touch Developer Score or Documentation Confidence Summary under this rule —
  those are scoring methodology, not fact tables, and stay fully intact
  however many N/A rows they have.

--- Section C: STAGE-FIXED CONTENT RULE (external doc_variant only) ---

Injected ONLY into calls that build or check `doc_variant == "external"`
content — never into internal-only calls. Delivery: `_external_citation_rule()`
reads this section and is appended after Section B, only when the caller is
operating on the External document.

- **Numbered citations.** Every inline factual claim in the External document
  resolves to a numbered `[N]` marker tied to the Sources list — one marker
  per distinct claim, not one marker glued to the end of a multi-sentence
  passage covering several claims. Does not apply to the Internal document at
  all — never inject this rule into any internal-only call.

# Company Charter pipeline — binding rules

These rules govern what the Company Charter documents may and may not say.
`CLAUDE.md` describes the pipeline that applies them; this file is the rules
themselves. Where the two disagree, this file wins on content and CLAUDE.md
wins on flow.

Three sections, three different audiences and delivery mechanisms. The section
markers are parsed at runtime by `_read_rules_section()` in
`company_charter.py`, so do not rename or reorder them.

--- Section A: CODING-TIME RULES ---


- **Output location.** Company Charter files (template and generated) are only
  ever saved to `output/company_charters/` in this repo. Never to a Desktop
  "Promoter Profiles" folder or any other location.
- **Two builders, and one shared library.** The live automatic-pipeline path is
  `company_charter.py`'s `_fill_template()` (invoked via `run_company_charter`,
  called from `main.py`). The second builder is `charter_report.py` (the
  "Counterparty + Collateral" restructured document, ~1494 lines, its own
  already-implemented findings-not-absence editorial policy) — invoked
  manually/standalone for specific requests, never from `main.py`. Rules in
  this file target `company_charter.py` unless a task says otherwise.
  `charter_document.py` is NO LONGER a builder: its `build_charter_document()`
  entry point and the 24 definitions only it reached (the `_Builder` class and
  all 15 `_section_N_*` renderers) were deleted on 2026-08-10 after an audit
  confirmed it had never been called from a non-test file since it was added
  in `5b8c6ee`. What remains, ~710 lines, is a live shared library:
  `charter_report.py` imports 16 symbols from it (`build_claim_rows`,
  `assess_counterparty`, `rollup_developer_score_buckets`,
  `rollup_doc_confidence_buckets`, `_headline_claim_rows`,
  `_recommended_steps`, `_related_entity_implication`,
  `_related_entity_linked_via`, `_field_display_name`, and the six `STATUS_*`
  constants) and `executive_briefing.py` uses `_green_flags`. So do not delete
  the module, and do not treat it as dead code — but nothing in it renders a
  document any more. `test_charter_document.py` was removed with the builder.
- **Generation checklist.** Before declaring a charter run done, confirm
  (a) the correct builder was used (`company_charter.py`'s `_fill_template`,
  via `run_company_charter` — not `charter_report.py`, which is a different
  document for a different request), (b) a real monitor-flag resolution pass
  was performed — flags in Overview & Flags were actually re-checked against
  current facts, not carried over stale, and (c) the final CLAUDE.md review
  stage ran, or its failure was reported rather than passed over silently.
- **Alignment.** Body text stays justified. Table cells stay centered *for
  short values* — a figure, a date, a name, a status, anything that fits on
  one or two lines. A cell carrying a paragraph of narrative prose is
  left-aligned instead, because centered prose is ragged on both edges and
  unreadable. `_center_all_table_cells` must therefore skip cells over a
  length threshold rather than centering unconditionally; don't regress
  `_justify_body_paragraphs` or the short-value centering while touching
  anything else. (Amended 2026-08-10: the original rule centered every cell,
  which produced unreadable Landowner/Investor, Board Resolution and Mortgage
  rows.)
- **Table pagination.** Table rows must not split across a page break, and a
  table continuing onto a new page repeats its header row. Set
  `cantSplit` on each row and `tblHeader` on the header row.
- **No URLs in narrow table columns.** A long URL in a narrow column wraps
  mid-token and can split across a page break. Table cells carry a short
  label or an `[N]` marker; the full URL lives once in the Sources list.
- **The External quality gate constrains prompt text too.**
  `_verify_external_document_quality` raises and blocks the save when an
  External paragraph contains an em dash or a hyphen-pair dash. Sections B and
  C are injected verbatim into External-facing API calls, and prompt
  punctuation bleeds into model output — so **Sections B and C must themselves
  stay free of em dashes and of " -- "**. Use commas, colons or a single spaced
  hyphen there instead. Section A is never sent to the API and may use them
  freely. Check this whenever you edit B or C.
- **Gate check before adding a Document Library to External.**
  `_verify_external_document_quality` currently treats the literal string
  "Document Library" surviving in the External document as a violation, from
  the era when that section was Internal-only. The agreed design now gives
  External its own opened-documents-only table, so that check must be narrowed
  (to the leftover template placeholder text it was really aimed at) before the
  table ships — otherwise every External save hard-fails.
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
- **The final stage is a CLAUDE.md review, and it is advisory.** After both
  variants render and before the PDFs are produced, `run_company_charter`
  calls `run_claude_md_document_review`, which re-reads each saved `.docx` and
  audits it against these rules via the Claude API. Section B goes to both
  variants and Section C only to External, exactly as at generation time.
  **Section A is never sent** — it is coding-time guidance, and
  `test_claude_md_doc_review.py` asserts that. The review REPORTS and writes
  `Company_Charter_<REG_NO>_claude_md_review.json`; it does not block the PDF,
  because `_verify_external_document_quality` already hard-fails a genuinely
  bad save and a model's opinion should not be able to stop a finished
  document being delivered. It never raises: a missing `ANTHROPIC_API_KEY`, a
  rate limit or a malformed reply all come back as `reviewed: false` with the
  reason, and the run continues. Cost is separable under its own
  `claude_md_doc_review` usage label. If you want it to block on violations
  instead, that is a deliberate change, not a bug fix.
- **GST intake is opt-in and never fatal.** `main.py` takes mutually-exclusive
  `--gstin` / `--pan`; either runs the full intake (enumerate every GSTIN under
  the PAN, fetch each filing table, write `gst_filing_input.json` for
  `run_gst_compliance_check`). It runs between the promoter-portfolio build and
  deep research, because it needs a human to solve a CAPTCHA per lookup and
  that belongs beside the other browser work. A failure costs one unscored
  sub-metric, never the run. Supplying neither flag skips GST entirely.

--- Section B: COMMON CONTENT RULES ---

Injected into every Claude API call that generates or checks narrative or table
content, both doc variants. Delivery: `_common_content_rules()` reads this
section and prepends it as a separate cacheable system-prompt block.

Keep this section free of em dashes, and free of a double hyphen used as a
dash. It is injected into External-facing calls, prompt punctuation bleeds into
model output, and `_verify_external_document_quality` hard-fails the save on
either. Use commas, colons or a single spaced hyphen. Note that this paragraph
deliberately describes those characters rather than showing them, for the same
reason.

- **A clean check produces no sentence.** When a check ran and came back clear,
  the document says nothing at all: no sentence, no citation marker, no list of
  what was searched, no named sources. Delete the line. "No litigation found
  against the promoter, the project or the underlying land in any source
  reviewed; MahaRERA's complaint, appeal and warrant records are empty[18]; the
  Title Report's 30-year search returned nothing[1]" is three citations spent
  establishing there is nothing to report: it all goes. The scope of what was
  checked stays in the facts file and does not reach the page. **Never attach a
  citation marker to an absence.** Three carve-outs only: an entire empty
  section, an absence that is the stated basis of a score, and a gap.
- **An empty section keeps its heading and one bare line.** A section with no
  findings keeps its heading exactly where it is, with the single line "Nothing
  found." underneath. No sources, no scope, no explanation, no marker.
- **Findings first inside anything that survives.** State what was established
  before what is missing. Never open on a negative ("Not directly confirmed...",
  "No discrepancy found.") when the passage goes on to give real content: lead
  with the content, close with the qualifier. This applies to table cells
  exactly as it applies to bullets.
- **Absence as evidence.** An absence survives only where it is the stated basis
  of a number the reader would otherwise take on trust: a Developer Score
  sub-metric note ("no completion extension, 0 complaints, 0 appeals" is why
  RERA Compliance bands AAA), or a metric card ("Litigation Load: 0 complaints /
  0 appeals"). The bare figure is a finding; a sentence explaining that figure
  elsewhere in narrative prose is not, and is deleted.
- **A gap is not an absence.** "We checked and it is clear" is deleted; "we
  looked and could not establish this" is kept in full. A gap is an open unknown
  the reader can act on, so Gaps & Sources keeps every item with its full
  explanation, including permanent ones. Never compress or delete a gap under
  the clean-check rule.
- **Deep research on every finding.** Anything that survives as a finding earns
  a dedicated follow-up research pass, not a one-line mention: what it is, who
  is involved, when it arose, whether it is still live, and what it means for
  this project. A Notice of Lis Pendens is not "a 2017 notice against an
  adjoining society": resolve which society, under what suit, whether it is
  still pending, and whether it touches this plot's boundary.
- **A live right is a finding.** A contractual permission, option, charge or
  entitlement in a reviewed document counts as found even if never exercised,
  because it remains exercisable. Report the right and note it has not been
  acted on. Establish whether it is still live or has been extinguished.
- **Ruled-out items survive, in the right section.** Something discovered and
  then dismissed is a finding, not an absence. Found but unrelated to this
  project stays where it is, compressed to one line with the reason it was
  cleared. Found, related, but miscategorised moves to the section where it is a
  real fact rather than sitting elsewhere as a denial: Deputy Registrar
  society-amalgamation orders are land assembly, not litigation, and belong
  under Land Identification. Age alone never rules an item out.
- **Say it once.** A fact, and an absence, appears in exactly one place. A
  Summary line is a verdict plus a pointer ("Insolvency: clean"), never a
  restatement of the detail it points at, and never a pointer to a section that
  collapsed to "Nothing found." A missing document is named once, in Gaps. Two
  table rows must not both exist to report the same absence.
- **Flags summarise, Gaps explain.** An item in Overview & Flags is a
  one-sentence headline ending in a `(Gap N)` pointer. The full explanation
  lives once, under Gaps & Sources, numbered to match. Never print the same item
  in full in both places.
- **Internal keeps process failures, External does not.** An item recording a
  tooling failure rather than a fact about the project, a re-verification that
  could not run, an authentication error, a pending manual step, stays in the
  Internal document and is cut from External. Where such an item also contains a
  genuine finding, for example that a topic rests on a single source, preserve
  the finding in one consolidated line and drop the error text. Never put a file
  path, module name, function or parameter name, JSON key, or raw exception
  string into either document; least of all External.
- **Deep-dive vs. nothing-found.** Read the heading, not just the bullet. A
  section with real confirmed findings gets expanded properly. When genuinely
  nothing was found for an entire section, it collapses to the heading plus a
  single "Nothing found.", never padded into multiple bullets and never
  supported with sources or search scope. Does not apply to Developer Score or
  Documentation Confidence Summary: those stay fully intact even when mostly
  N/A, and their N/A notes are covered by "Absence as evidence".
- **No jargon, plain language, keep key terms.** CIRP, NCLT, RP, IBC Section 30,
  DSRA, ECLGS, IGR/e-ASR, KMP, ROC, MCA, IOD, IOA, CHS, NCD, ICAI, SRO, SPOC,
  IBBI, FSI, DCPR, MHADA, MRTP, CC/OC and any other initialism the document
  introduces must not appear unexplained. On first use per section, expand or
  plain-language it once ("under active insolvency proceedings (CIRP) before the
  insolvency tribunal (NCLT)"), keeping the term itself. A plain-language pass,
  not a dumbing-down pass: do not lose precision on legal or registry facts.
- **Gloss raw status strings.** A verbatim status value copied out of a registry
  or database, for example IBBI's "ASSIGNMENT NOT APPROVED YET", is quoted as-is
  and immediately translated into what it means for this promoter, in the same
  sentence. Never present an opaque database string as a finding the reader can
  interpret.
- **Bullets and grammar.** Bullets stay the default format: do not convert
  narrative sections to paragraph prose. Consolidate split or fragmented bullets
  into one well-formed bullet rather than a fragment trail. Correct grammar and
  capitalization throughout: sentence casing, proper capitalization of entities,
  complete sentences rather than fragments strung together with semicolons.
- **CIN/DIN placement.** Never state a CIN or DIN inline in prose, anywhere,
  EXCEPT the stakeholder and director identity tables (Corporate/Promoter
  Identity, Current Directors & KMP, Company Registration Profile), where it is
  required. One rule, one table exception, no first-mention carve-out.
- **Empty table/row removal.** Drop a whole table if every row has no real
  information; drop a row only if every column in it is empty, so a row survives
  if even one column has a real confirmed value. EXCEPT Developer Score and
  Documentation Confidence Summary, which are scoring methodology rather than
  fact tables and stay fully intact however many N/A rows they have.

--- Section C: STAGE-FIXED CONTENT RULE (external doc_variant only) ---

Injected ONLY into calls that build or check `doc_variant == "external"`
content, never into internal-only calls. Delivery: `_external_citation_rule()`
reads this section and appends it after Section B. Same punctuation constraint
as Section B: no em dashes, and no double hyphen used as a dash.

- **Numbered citations.** Every inline factual claim in the External document
  resolves to a numbered `[N]` marker tied to the Sources list: one marker per
  distinct claim, not one glued to the end of a multi-sentence passage covering
  several claims. Does not apply to the Internal document at all.
- **Marker placement.** A marker sits at the end of the clause it supports, not
  at the end of the paragraph. A sentence carrying three claims from three
  sources gets three markers, each after its own clause; `[1][2][4]` parked
  after the final full stop is the failure this rule exists to prevent. A marker
  never lands mid-word or mid-token ("MahaRERA[10]-registered").
- **Scope of the rule.** Every inline factual claim means every one: Executive
  Summary bullets, flag headlines, table cells, appendix prose and score-table
  notes are all in scope. A section shipping with no markers at all is a defect,
  not a stylistic choice. Treat full coverage as the target and check it.
- **The marker must actually support the claim.** Cite the source that
  establishes the specific statement. Citing the MahaRERA complaints record for
  a sentence beginning "independent web research found..." is a mis-citation
  even though both are real sources. Where a claim rests on the absence of a
  result from a named search, cite that search. A missing marker is a lesser
  failure than a wrong one: leave a clause uncited rather than borrow an
  adjacent source.
- **Source labels.** External Sources entries are descriptive citations: issuer,
  what the document is, and its date ("Affidavit-cum-Declaration (Form B) under
  Rule 3(6), notarized 12-13 March 2024, filed with MahaRERA"). Never a raw
  internal filename, never a bare category label like "Project record" or
  "appeals data" that a reader cannot check. Internal keeps its own full
  bibliographic list with filenames; that list is not reused for External.

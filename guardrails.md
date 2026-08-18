# Guardrails

What stops this pipeline shipping a bad document, and what stops one failure
taking down a run.

**These are code, not configuration.** Unlike `rules.md`, which is parsed at
runtime and injected into API calls, nothing reads this file. It is a map of
guards that live in Python. Deleting it changes no behaviour; letting it go
stale only misleads a reader. `test_guardrails_doc.py` fails the suite if any
symbol named here stops existing, which is what keeps it honest.

Symbols are given as `module.name`, never as line numbers. Line references in
this repo have gone stale twice already.

## 1. Hard gates — these stop the run

| Guard | Stops | Checks |
|---|---|---|
| `company_charter._preflight_rules` | Generation, before a paragraph is written | `rules.md` missing, a `--- Section X ---` marker broken, a section empty, or Sections B/C carrying an em dash or double-hyphen dash |
| `company_charter._verify_external_document_quality` | The External `.docx` save | 11 checks, below |
| `company_charter.run_claude_md_document_review` | **The PDF** | Semantic compliance with `rules.md`, judged by the model. Raises `CharterComplianceError` |
| `charter_report.verify_charter_report_quality` | The `charter_report.py` save | Its own independent gate, including a bare-domain check |
| `company_charter._read_rules_section` | Any rules read | Raises on a missing or empty section |

The External gate's 11 checks: em dash; double-hyphen dash; leftover Internal
Document Library status text; **a citation marker attached to an absence**
(`company_charter._is_cited_absence`); italic placeholder styling; unexpected
run colours; unbalanced citation parentheses; lost bullet numbering; a Weight
column surviving in either score table; and a missing hanging-indent fix.

Section C is required only for External, matching where it is injected.
Section A is validated but never returned, so the preflight cannot become a
route by which coding-time guidance reaches an API call.

## 2. Never fatal — a failure costs one feature, never the run

Wrapped in `main.py`: GST intake, complaint-order download, promoter portfolio,
deep research, Charter generation. Wrapped in `company_charter`: the editorial
passes, per-finding research, and the document review.

The strongest case is `company_charter.run_finding_research`: a researcher that
raises costs **only its own finding**, and no finding is ever deleted whatever
the researcher returns, including on an empty or malformed reply. Losing a
finding to an expired token would be worse than never running the stage.

`main._run_gst_intake_step` exists as its own function purely so that contract
is testable rather than asserted in a comment.

## 3. Fallbacks — a model can only match or improve, never degrade

Each model-backed judgement keeps its deterministic predecessor behind it:

| Model pass | Falls back to |
|---|---|
| clean-check classification | risk nouns + field allow-list, in `company_charter._is_clean_check_clause` |
| citation matching | keyword topic table, in `company_charter._clause_topic_citation` |
| flag headlines | first sentence, in `company_charter._flag_headline` |

`deep_research._extract_json_object` is the same idea one level lower. Every
system prompt here demands a bare JSON object and nothing else; a live
verification call answered with a sentence of commentary, a blank line, and then
a perfectly correct object, which `json.loads` rejected outright. The right
answer scored as a failed check, which demotes a confirmed claim to a gap. The
instruction stays, but it is no longer load-bearing. Truncated JSON is still a
failure: recovery must not become a way for a cut-off reply to look complete.

`company_charter.run_editorial_passes` precomputes all three once per document
set and caches them on `facts` keyed by clause text. A lookup miss is
indistinguishable from "no model ran", so a failed, partial or malformed reply
produces exactly the deterministic output. Malformed replies are discarded
rather than trusted: out-of-range ids, unrecognised verdict kinds and null
matches are dropped.

## 4. Reversibility — the page drops, the record keeps

`company_charter._scrub_clean_checks` and `company_charter._sanitize_process_gaps`
both stash the original text and hand it back through
`company_charter._restore_clean_checks` before `.facts.json` is written.
`rules.md` Section B requires this: what was checked stays in the record even
when it leaves the page. Without it every run would quietly hollow out the file
the next run reads.

`company_charter._normalize_misfiled_facts` is the deliberate exception. A
relocation moves text to the field it always belonged in, so nothing is lost
and the corrected record is the better one.

## 5. Bounds

`deep_research.MAX_FINDING_RESEARCH_CALLS`, `deep_research.MAX_GAP_RETRY_ATTEMPTS`,
`deep_research.MAX_RESEARCH_VERIFICATION_CALLS`, `deep_research.PIPELINE_COST_CAP_USD`,
`company_charter._MIN_FINDING_LENGTH`, and the review's own input cap.

### The total, not just each piece

Every bound above caps its own stage. None of them stops the pieces from
adding up past what a run should ever cost -- deep research staying under its
own ceiling and Charter generation staying under its own ceiling can still sum
to more than either alone. `deep_research.PIPELINE_COST_CAP_USD` (2026-08-13,
$6.00) is a hard ceiling on the WHOLE run, checked in `_run_agentic_pass`
itself -- the one function every Claude API call in this pipeline funnels
through, deep research's own calls and `company_charter.py`'s alike -- so it
is the one place a check actually covers the total. A call already in flight
when spend crosses the line is allowed to finish; only a call that would
START after the cap is reached is refused (`deep_research.CostCapExceeded`,
not retried), so the real ceiling is the cap plus at most one more call's
worst case, not a precise stop at exactly $6.00. Hitting it fails whichever
stage was mid-call exactly the way a missing `ANTHROPIC_API_KEY` already
does -- gracefully, since both stages are `[never fatal]` in `main.py`.

`deep_research.MAX_RESEARCH_VERIFICATION_CALLS` bounds a fan-out the other two
don't: `_verify_block` used to call `_verify_claim` once per source with no
limit on how many sources a block has, and `_resolve_gaps` retried every gap
it was handed with no limit on how many gaps a block has (`MAX_GAP_RETRY_
ATTEMPTS` only bounds attempts *per* gap, not the number of gaps). This is not
hypothetical: P51800077150's first-ever research pass (2026-08-12, no
`prior_research` to reuse) ran past $10 before being killed by hand.

`deep_research._verify_claims_batch` and `deep_research._retry_gaps_batch`
(2026-08-13) fixed the root cause rather than just capping it: many sources,
or every gap still open in a retry round, are checked in ONE call sharing one
search budget, instead of one independent call each. `BATCH_VERIFY_CHUNK_SIZE`
keeps any single call's load bounded regardless of block size. A
`_VerificationBudget` shared across all three research blocks (and across
`_resolve_gaps`'s own use of `_verify_claims_batch` on retry results) in one
`run_deep_research()` call still caps the total batched-call count -- now a
backstop for an unusually large block or gap count, since batching already
cut the call volume that caused the $10 run. A source or gap reached after
the budget is spent is kept, never dropped -- annotated the same way a
`verification_error` already is.

### The search budget, which is also the reply budget

A web-search result is billed against the same `max_tokens` as the reply, so a
call can spend its whole allowance searching and stop with no text at all. The
Charter pass did exactly that three times: 27 searches, zero characters written.
Raising `max_tokens` cannot fix it, because `deep_research.MAX_NONSTREAMING_TOKENS`
(21,333) is a hard SDK ceiling. Capping the searches is the only side of the
trade that moves.

Three guards, in order:

1. **Opt in.** `deep_research._run_agentic_pass` takes `search=False`. It used
   to attach the tool unconditionally, giving this failure mode to six calls
   that only judge text they were already handed and have nothing to look up.
2. **Bound it.** `deep_research._web_search_tool` sets `max_uses`, enforced by
   the API. Budgets: `DEFAULT_MAX_SEARCHES`, `RESEARCH_MAX_SEARCHES`,
   `CHARTER_PASS_MAX_SEARCHES`, `RETRY_MAX_SEARCHES`. Each prompt states the
   same number the API enforces, so the model is not budgeting for searches it
   will not get.
3. **Retry rather than lose the run.** `deep_research.BudgetExhausted` separates
   "ran out of room to answer" from "the answer was malformed". A searching call
   that hits it gets one retry at `RETRY_MAX_SEARCHES`, told to answer from what
   it has, billed under its own `_retry` label. A non-searching call is not
   retried: its budget went on the reply itself, so an unchanged retry would
   only spend the money twice.

`test_search_budget.py` drives every caller through a fake client and asserts on
the `tools` that actually reached the API, not on the source text.

`company_charter._field_fingerprint` and `company_charter._already_researched`
stop per-finding research recompounding: an enriched finding is multi-sentence,
so it re-splits into more clauses each pass and would be researched again. Any
edit changes the fingerprint, so skipping is only ever an optimisation over
identical input.

## 6. Compliance gate — the semantic check, and it blocks

The mechanical gate catches formatting and leaks. It cannot tell whether a
sentence is a clean check, so `company_charter.run_claude_md_document_review`
re-reads both saved documents and judges them against `rules.md`. It is
**strict by default**: no PDF unless the document can be SHOWN to comply.

Two ways to fail: a verified violation, or a review that could not run at all.
The second matters as much as the first, because unverified is not the same as
clean, and treating a missing key as a pass would make the whole chain optional
exactly when it is load-bearing.

Blocking on a model's judgement is only safe because of
`company_charter._verified_violations`: a violation counts only if the text it
quotes really appears in the document. That reduces the model's role from
"decide whether this complies" to "point at the offending text", and the
pointing is then checked mechanically. An invented or paraphrased quote is
logged as unverifiable and cannot block. Quotes under 12 characters are not
trusted, since they match almost anything by coincidence.

`CHARTER_ALLOW_UNCHECKED=1` restores advisory behaviour. It is a decision to
ship an unchecked document, not a convenience.

`company_charter._check_citation_completeness` remains genuinely advisory.

## 7. Tests as guardrails

Several exist specifically to stop a guard rotting rather than to test a
feature: that malformed model replies are discarded; that scrubbing never moves
the Developer Score; that a refusal leaves no half-written `.docx`; and that
Section A never reaches an API request, which now asserts its marker is present
before asserting it is absent, after that test was found passing vacuously.

## State guards — these stop one state's work running for another

Added with the pan-India `StateAdapter` seam. A state declares what its portal
offers; code checks before doing anything shaped like it. Absence is declared,
never stubbed — see `states/base.py`.

| Guard | Stops | Why it exists |
|---|---|---|
| `states.candidate_profiles` | A registration number resolving to the wrong authority | MahaRERA and TG-RERA both issue `P` + 11 digits, so the number is tried against each in turn and the one that ACTUALLY HAS the project wins. Detection is empirical, not a guess |
| `states.CAP_LOOKUP_BY_REG_NO` | Probing an authority that cannot answer | TG-RERA's public record does not display a registration number at all; the ladder skips it and tells the operator to supply a project name instead |
| `states.StateProfile.can` | A capability typo reading as "this state lacks it" | Raises on an unknown capability, so a typo cannot silently skip work |
| `company_charter._state_profile` | A render defaulting to `None` | Falls back to Maharashtra, so every pre-existing caller renders identically |
| `company_charter._state_dash_rewrites` | The External gate blocking a save | Generates the Internal/External subtitle pair together, per state, so they cannot drift out of lockstep |
| `states.get_adapter` | A blank failure for a state with no adapter | Raises naming the `pre_built_facts` route that does work |

Three capability gates fix live bugs rather than prevent future ones: the
MahaRERA Orders/Judgments scrape used to fire for Telangana projects it could
never match; `company_charter._extract_district_hint` used to query
Maharashtra's district map for every state; and
`charter_document.classify_claim_evidence` used to downgrade any non-MahaRERA
RERA record to "stated only".

`test_state_leak_guard.py` walks the rendering modules' ASTs and fails on a new
hardcoded state literal. `test_state_labels.py` renders the same facts under two
profiles and asserts the difference is exactly the state-bearing paragraphs —
proving parameterisation is both complete and contained.

## Adding a guardrail

Put it in code, add its symbol to the right table above, and let
`test_guardrails_doc.py` confirm it resolves. A guard nobody has watched fail
is only a comment: write the test that breaks it deliberately.

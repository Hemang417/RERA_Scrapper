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

`company_charter._safe_promoter_identity` wraps the promoter-PAN read. The
underlying pass does not signal failure by raising -- it returns an explicit
not-found or unverified result carrying its own reason -- so this wrapper only
covers a hard failure of the OCR stack itself.

## 2c. Coverage — "not found" and "never asked" are different findings

`group_sweep.sweep` reports a STATUS per authority, and that table is the
product, not the project list. GujRERA, WBRERA and TG-RERA publish no promoter
search at all, and ~24 states have no adapter: in every one of those cases an
empty result means nobody looked. Three bugs here each manufactured a clean
record from a search that never ran, and each now has a guard:

- an injected test searcher could stand in for a state with no promoter search,
  reporting GujRERA and WBRERA as "searched". Capability comes from the adapter;
  injection replaces only the implementation.
- the search budget was global with the STATE as the outer loop, so the first
  authority consumed it and the rest were reported "searched, 0 projects"
  having run zero queries. Entity is now the outer loop, and a state the budget
  never reached says so (`STATUS_BUDGET_EXHAUSTED`).
- an unreachable portal is `STATUS_UNREACHABLE`, never a zero.

`group_sweep.enrich_projects` opens each match, and that is what CONFIRMS or
REFUTES it: seven of ten live matches were false brand hits. Three outcomes,
because a project SPV is neither identity nor a stranger: **confirmed** (the
project's promoter is the entity), **probable** (a separate entity sharing the
group's distinctive name, the one-vehicle-per-project pattern), **refuted**.
Demanding identity refuted the one genuine group project.

`gst_group.sweep` reports GST standing per group entity, and here the coverage
line is nearly the whole product. **GST is keyed on PAN; the entity graph is
keyed on CIN; no public MCA source publishes a company's PAN** -- so most of a
group is structurally unreachable, and a section listing only findings would
read as a clean bill of health for the whole group when two of sixty-five were
looked at. Guards:

- every entity appears with a STATUS, including `STATUS_NO_PAN`. An omitted
  entity reads as one with nothing to report.
- `gst_group.entities_with_findings` is the only way into the findings table,
  so an unchecked entity cannot be rendered with a blank filing record.
- `gst_group.coverage_sentence` leads with the denominator and states outright
  that the rest are not being called compliant or non-compliant.
- a PAN is never guessed. Each carries a provenance
  (`PAN_SOURCE_FILED_CARD`, `..._RERA_FILING`, `..._FROM_GSTIN`, `..._SUPPLIED`),
  strongest wins, and an `unverified_candidate` from `promoter_identity` is
  refused outright -- it would spend a human's CAPTCHA solve looking up possibly
  the wrong company and then attribute the answer to this one.
- the entity limit is a HUMAN budget (two CAPTCHA solves each, minimum), and
  anything past it is `STATUS_BUDGET_EXHAUSTED`, never a silent truncation.

`litigation_sweep.sweep` searches case law by NAME, and returns CANDIDATES.
The first live query proved why: searching "Pranami Builders", a Ranchi company,
returned "Pranami Builders , **Ahmedabad** vs Department Of Income Tax" plus two
judgments that merely shared tokens on a full-text index. Guards:

- every hit records whether the searched name is in the CASE TITLE or only in
  the body (`MATCH_TITLE` / `MATCH_BODY`); a body mention on a full-text index
  is very often an unrelated judgment.
- the place in the title is checked against the group's known footprint. It is a
  CAUTION, never an exclusion -- a company litigates where the cause of action
  arose -- so the wording claims only what was established.
- a director query carries a STANDING caution: Indian personal names repeat
  enormously, and it is the highest false-positive search this pipeline makes.
- entities are searched before directors, so a small budget is not spent on the
  noisiest queries first; unsearched names are reported, "neither clear nor
  implicated".

**The absence side is the more dangerous half.** `NOT_RELIABLY_INDEXED` names
what open case-law search does not carry -- consumer fora, most of NCLT/NCLAT,
district courts, arbitration, and the RERA authorities' own orders -- and the
document says so. A clean paragraph drawn from a source that would not have
carried the bad news anyway is the worst false clean record this pipeline could
produce. `coverage_sentence` may not use the words "clean" or "no litigation".

`litigation_sweep.state_order_sweep` searches the regulators' OWN order
registers, which case-law indexes do not carry. Only Karnataka is wired, and the
limitation names every register that was NOT searched. K-RERA's own POST
(`/viewJudgementDetails`) does **not** filter server-side: a real firm name and a
nonsense string return byte-identical pages apart from the visitor counter, so
wiring it up as a search would have reported "no orders" for every promoter ever
queried. The whole register ships to the browser instead and is filtered there;
validated with a control -- 111 entries for a known developer, 0 for a nonsense
name. `parse_search_index` refuses to zip the four parallel arrays when their
lengths disagree, since a mismatch attaches the wrong promoter to an order.

**The order registers, probed rather than assumed (2026-08-21).** K-RERA
publishes five promoter-keyed registers, not one: the order-search index plus
authority orders, Adjudicating Officer orders, interim orders and complaints
under process -- 15,600+ rows, including a **penalty table naming the violation,
the section and the amount**. Two guards:

- `adapter_karnataka._looks_complete` rejects a body with no closing `</html>`.
  These pages truncate silently: the 10.4 MB authority-orders page arrived once
  with 2 of its 3 tables, **dropping the penalty register entirely**, and the
  AO-orders page truncated mid-attribute on another fetch. A partial register
  reads as a smaller one, so it is refused, not trimmed.
- `adapter_karnataka.order_register_coverage` names any register that did not
  load. Missing is not empty.

The others were probed, and the reasons are recorded in
`litigation_sweep.ORDERS_NOT_SEARCHABLE` so they are not re-derived: **MahaRERA
does accept a respondent (promoter) name** -- an earlier note in this repo said
it did not -- but its portal answered every attempt with an empty BigPipe shell;
**WBRERA** publishes 4,881 authority orders keyed only by complaint number, with
no party named in any column; **GujRERA and JHARERA** are single-page apps whose
order pages need JavaScript; **TG-RERA** publishes no such register.

**WBRERA's orders are joined through its cause lists** (`wb_orders.py`). Its
register publishes 4,881 authority orders and names NO party in any column --
only a complaint number. The promoter is inside each order PDF, and at ~900 KB
each, 4,881 of them is over 4 GB. The cause lists DO name the parties against
each complaint number, so they supply the join. Three guards, all necessary:

- **`resolve_complaint_no` resolves against a CLOSED SET.** The cause-list PDFs
  carry a poor OCR text layer that mangles exactly the join field --
  `WBRERA/COMOO2117` for 002117, `WBRERAJCOMOOOTS4` for 000754. Correcting those
  characters freely would be inventing a complaint number; resolving them against
  the 1,157 real numbers the order register publishes picks an existing one
  instead. **Two candidates means none** -- attaching an order to the wrong
  promoter is worse than reporting one fewer. Measured live: six of seven
  resolved uniquely, the seventh had no order at all (a pending hearing).
- **No name is labelled "respondent".** The OCR does not preserve columns: names
  wrap mid-title and hearing labels interleave, so "the last name in the run"
  read PAPPU SINGH as the respondent of a complaint answered by SK BUILDERS AND
  DEVELOPERS PVT. LIMITED. The block is kept whole and the promoter is matched by
  PROXIMITY inside it, which makes every row a candidate -- a promoter appearing
  as COMPLAINANT would match too.
- **`coverage_note` says how few cause lists were read.** Reading all of them is
  565 PDFs; the default reads the most recent slice. An unread cause list is a
  silently missing order, and a short answer here looks exactly like a promoter
  with a clean record.

The cause-list PDFs are fetched with a PLAIN `requests` session, never the
legacy-TLS pool -- the same reason `_download_documents` uses one. They sit on a
different host over plain HTTP, and urllib3 rejects `assert_hostname` on a
non-TLS connection, so through the pool every PDF failed and the join reported
"0 of 565 cause lists read". The coverage line is what surfaced it.

**JHARERA's order register** is `/Home/judgement_order` -- the whole state in one
request, 228 entries. It names BOTH parties in a single column and writes the
separator four ways ("Vs", "-Vs-", "V/s", "versus"); handling only the first
parsed 38 of 228, all four parse 225. `" & "` and `" And "` are deliberately NOT
separators -- "& Others" and "& Ors." are part of a party name, and splitting on
them would file a complainant's name as the promoter, attributing a homeowner's
own complaint to the developer. Only the RESPONDENT side is matched, since a
complaint is filed against the promoter. Rows whose separator is unrecognised
have no respondent, are invisible to a promoter search, and their count is
reported rather than left to vanish.

**GujRERA publishes no order register.** Probed 2026-08-21: its e-court
judgement data comes from `complain/SECURE/complaint-judgments-Details`, which
answers `{"Error":"Invalid Request"}` without a login. The only public e-court
endpoint is `complain/ecourt/public/find-all-complaint-count/`, which returns
COUNTS. The endpoint names are recorded so the next session does not re-derive
them by guessing paths.

**MahaRERA's orders search, fixed 2026-08-21.** It had been returning nothing
for every query. Four faults, and all four had to go:

1. **`big_pipe_nojs=1` must be set for that host.** Without it Drupal serves the
   results region as a BigPipe placeholder only a browser resolves, so the
   response carries the form and no results at all.
2. **The POST must carry the WHOLE form.** The old request sent five fields; the
   form posts eighteen. `orders_judgements_type` is a required radio and was
   never sent, and `ruling_judgement_from`/`_to` are a date window the page
   pre-fills.
3. **The date window defaults to the last three years**, which would hide
   everything older. `_MAHARERA_ORDERS_SINCE` widens it to 01-05-2017, when RERA
   commenced.
4. **`judgements_by_adjudicating_officers` was wrong** -- the form posts
   `..._officer`, SINGULAR -- so half of every search had been matching nothing.

`_maharera_orders_form_defaults` uses `has_attr("selected")`, not
`.get("selected")`: a bare `selected` reads back as `""`, which would silently
drop `order_state` and post no state at all. Three outcomes stay distinguishable
-- result cards, the portal's own "No Record" (a real nil), or neither, which
means the filter never applied and must NOT be read as an absence.

**MahaRERA is now promoter-searchable**, correcting an earlier note here: the
form accepts a RESPONDENT name, and a complaint is filed against the promoter,
so `search_maharera_orders_by_promoter` gives the group sweep its second
authority. Results paginate ten at a time and the page states its own total, so
`_MAHARERA_ORDERS_MAX_PAGES` truncation is visible rather than inferred.

`company_charter.search_maharera_judgments` returned `[]` both for "no order
published" and for "every attempt hit the shell". Its own docstring warned
callers not to read an empty result as an absence, while giving them no way to
tell -- and on 2026-08-21 a large, certainly-litigated promoter hit the shell
every time, so the pipeline would have reported no orders for it.
`_maharera_orders_search_once` now returns `None` for the shell and `[]` only
for a real empty result, and `search_maharera_judgments_status` exposes
`{"searched": bool}`. **A caller that does not check `searched` is asserting an
absence it has not established.**

`company_charter._safe_charge_movement` + `charge_watch.compare` answer whether
secured borrowing was repaid. A failed fetch is `checked=False`, never "no
change"; a charge that VANISHED from the register is not a satisfied one (a
satisfied charge stays listed with a closure date); a CHG-1 modification is not
a release. `charge_watch` never claims the mirror proves the money is still
owed, only that no satisfaction has reached it.

## 2b. Identity — a wrong PAN is worse than no PAN

`promoter_identity.verify_pan` is the gate on every PAN read off a filed card.
A PAN is a national join key: it pulls MCA charges, GST filings and litigation
into the Charter, so a misread one returns another company's records, all
internally consistent and all about the wrong entity. Two independent checks
must pass -- the 4th character against the closed set of PAN holder-type codes,
and the 5th against the initial of the promoter name **the portal itself
published** (`company_charter._portal_promoter_name`, deliberately not the
model-authored `corporate_identity.promoter_name`, so both sides of the check
are non-model sources). A candidate that fails either is returned in
`unverified_candidates` with its reason, never used and never silently dropped.

`promoter_identity.tesseract_available` guards the failure this cost real
debugging time for. pytesseract shells out to `tesseract` by NAME; when the
binary is absent it does not raise, every scanned card OCRs to the empty
string, and the run reports "no PAN found" -- which reads in the finished
Charter as *the promoter filed no PAN card*. That is a different finding from
*we could not read the card they filed*, and only the second one is true. The
guard reports the tooling gap instead. `company_charter.py` carries the same
warning about the same binary.

`promoter_identity._content_crop` refuses to crop a page whose content already
fills it. Cropping exists for a small card on a blank sheet; applied to a dense
page it would shave margins off a document that was already OCRing correctly.

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

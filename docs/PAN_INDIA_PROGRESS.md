# Pan-India RERA — progress and resumption notes

**Updated 26 August 2026.** Working tree is green: `python -m pytest -q` -> **708 passed**.
MahaRERA output is byte-identical to the pre-refactor baseline.

Full plan: `~/.claude/plans/yes-make-the-plan-starry-neumann.md`
Data coverage: `docs/RERA_Data_Coverage.xlsx` (regenerate with `python build_data_coverage.py`)

---

## 2026-08-26 (later) — UP-RERA's CAPTCHA gate is now passable, human-in-the-loop

Two of UP's coverage cells were "Partial" and "No" specifically because its
search is CAPTCHA-gated -- confirmed to EXIST, never confirmed to actually
WORK once solved. `up_captcha_search.py` closes that gap the same way
`gst_portal.py` and `mahabhumi.py` already do for GST and land records: a
real, VISIBLE Playwright browser opens, a human reads the CAPTCHA and types
it in themselves, and the script only reads the result after they submit.
Nothing in it reads or solves a CAPTCHA image.

**Both searches are now confirmed live, not just confirmed present.**
`search_projects_by_promoter("BALAJIMAHIMA INFRATECH PRIVATE LIMITED",
"Barabanki")` correctly returned UPRERAPRJ14636 (BALAJI GREENS) -- the exact
project already known from the adapter's own live-verified examples.
`search_appeals("BALAJIMAHIMA INFRATECH PRIVATE LIMITED")` against UP-REAT
returned a clean "No Data Found" in a real results table. `RERA_FINDINGS_LATER`
in `build_data_coverage.py` records both: "Promoter's other projects (track
record)" UP-RERA moved from No to Partial, "Appeals register" UP-RERA's note
was strengthened from "CAPTCHA-gated, unconfirmed" to "confirmed passable."
Both stay Partial rather than Yes -- every search still costs one human
CAPTCHA solve, and the projects search additionally demands a district before
a promoter, so an unattended sweep is still refused
(`group_sweep._CANNOT_SEARCH["UP"]` is unchanged on purpose).

**Three bugs fell out of getting the automation itself right, each found by
actually running it against a real human, not by reasoning about it:**
1. **"Did the page change" is not "did the human submit."** The first
   attempt polled for a jump in rendered body text (the same heuristic
   `gst_portal.py` uses) -- and closed the browser before the human had
   touched the CAPTCHA, because the promoter dropdown alone renders ~2,300
   option strings into that text, trivially tripping a length threshold on
   its own.
2. **"Did a navigation happen" is not "did the human submit," either.**
   Waiting for any page navigation was still too broad: clicking "refresh
   CAPTCHA" is ALSO a full ASP.NET postback (a fresh guid needs a server
   round trip), so a human who read an unclear CAPTCHA and refreshed it once
   closed the browser on themselves before ever reaching Search. Fixed by
   waiting for the SPECIFIC POST that names the Search control -- confirmed
   empirically (a headless diagnostic, no CAPTCHA needed) that
   `btnSearch` appears only in the real Search submit's POST body and never
   in the refresh button's.
3. **A "successful" read can still be a blank, mid-navigation page.**
   Even after catching the right request, reading `page.content()` a moment
   too early either raised Playwright's own "page is navigating" error or
   worse -- silently succeeded against a blank interim document. Fixed with
   a retry-on-short-read wrapper and a wait for a marker element that exists
   on the resulting page regardless of outcome.

None of this changes `states/adapter_uttarpradesh.py` or `group_sweep.py` --
`up_captcha_search.py` is a separate, opt-in, human-in-the-loop tool, the
same category as `gst_intake.py` and `cts_resolve.py`, not a silent
capability upgrade to the unattended pipeline.

---

## 2026-08-26 (later still) — Delhi-RERA's Appeals register: Partial to Yes, no CAPTCHA needed

The other "Partial" this session went after was Delhi-RERA's REAT appeal
register: real, live, 505 rows -- but it names no party in its own columns,
so it was browsable, never promoter-searchable. Unlike UP-RERA's gate, this
one needed no CAPTCHA and no human at all: every row links a scanned
judgement PDF, and REAT's own case captions state the Appellant and
Respondent on the PDF's first page (e.g. "M/s Hiptage Infrastructure Pvt.
Ltd. ..... Appellant" / "V/s" / "RERA for NCT of Delhi & Anr Respondent").

`states/adapter_delhi.py` gained `build_appeal_party_index()` and
`search_appeals_by_party()`: download each of the 481 distinct order PDFs
behind the 505 rows, OCR just the FIRST page (PyMuPDF native text first --
confirmed zero characters of native text on every sample checked, these are
scans, not text PDFs -- Tesseract fallback otherwise; a 20-25-page order
does not need OCRing in full for a caption that only ever sits on page 1),
and extract both party names from the caption's own structure. Cost:
~5 minutes for the whole register (304s live), one download+OCR per PDF,
cached to `output/delhi_reat_appeal_cache/` (text only, not the PDFs
themselves -- 481 of those at several MB each was not worth keeping) so a
re-run only fetches what changed.

**Result, confirmed live: 437/505 rows (87%) now carry a real party name.**
`search_appeals_by_party("Parsvnath", rows)` returns 27 genuine hits --
"Bimal Kumar & Ors. vs M/s Parsvnath Landmark Developers Pvt. Ltd." among
them. Of the 68 that don't: 63 are the AUTHORITY'S OWN register linking a
PDF that 404s (confirmed directly, not a scraping artifact -- their own
links are broken), and only 5 PDFs (12 rows, all 2021-2022) use an older
caption shape the parser doesn't yet recognise. `build_data_coverage.py`
moved Delhi-RERA's "Appeals register" Partial to Yes.

**Getting the caption parser right took three real bugs, each an OCR
artifact reading as a wrong PARSE rather than a wrong CHARACTER:**
1. A first cut required a dotted leader ("..... Appellant") immediately
   before the role word on the SAME line. One real caption's Respondent
   line OCR'd with no dots at all ("RERA forNCT of Delhi & Ant
   Respondents") -- a document that genuinely has no dots there, not a
   scraping failure, so the fix loosened dots to optional.
2. Loosening that surfaced a worse failure: some captions split the party
   name onto its OWN line, with a blank line, then a THIRD line carrying
   only leader noise before the role word. A same-line-only regex read
   that noise as the party's NAME -- confirmed live, the "name" came back
   as a single garbled character. Fixed with a line-walker that falls back
   to the nearest preceding non-blank, non-noise line.
3. The noise itself was under-specified: Tesseract renders a run of leader
   dots as ONE ellipsis character (U+2026), not literal dots, and a
   different sample produced a replacement character (U+FFFD) instead --
   two different renderings of the same "there were dots here" fact,
   requiring both in the noise class, not just the one first observed.
   A fourth, smaller fix: a compound label like "Applicant/Respondent"
   (an application decided within an appeal) matched the bare
   "Respondent" pattern midword, so "Applicant/" leaked into the captured
   name until the role regex was widened to swallow any `word/` prefix
   immediately before the target role word too.

Same lesson as UP-RERA's CAPTCHA automation two sections up: the actual
CAPTCHA/OCR step was never the hard part. Telling a real answer apart from
something that only LOOKS like one -- an empty string, a garbled character,
a stray "Applicant/" -- was.

---

## 2026-08-26 (final) — "Projects under investigation": one real ceiling, one closed gap

Attempted both remaining Partials. Different outcomes.

**UP-RERA: a dead end, confirmed rather than assumed.** The three registers
named in the earlier audit (Abeyance Projects, NCLT Projects, Withdrawn
Registration) are real pages, but their data loads via a
`WebService1.asmx` POST (`loadAbeyanceprojectWithParameter`,
`loadNCLTprojectWithParameter`, `loadwithdrawnproject`) that returned HTTP
500 on every parameter tried -- empty string, a real registration number,
junk text -- via both `requests` and a REAL HEADLESS BROWSER driving the
page's own default load call. That rules out a request-shaping problem on
this end: the authority's own backend is failing right now, for its own
page, loaded the way a human would load it. Nothing was built against a
source that cannot currently be read at all; `build_data_coverage.py`'s
note now says so with the confirming detail instead of leaving the three
registers as an unexplained "none wired in yet."

**TNRERA: closed the fixable half.** The two enforcement PDFs (Show Cause
Notices for non-registration, and the Personal-Use/Not-For-Sale caution
list) turned out to be NATIVE-TEXT, not scans -- confirmed live, no OCR
needed, unlike every other PDF-parsing gotcha this session ran into.
`adapter_tamilnadu.py` gained `parse_enforcement_pdf()` (pdfplumber's own
table extraction, not a raw text dump -- a row's three columns don't
reliably stay apart in a text stream) and
`search_enforcement_lists_by_name()`. Confirmed live: 35 rows off the
5-page SCN list, 1,502 off the 118-page caution list, and searching
"Sivakaminathan" returns the real row. The verdict stays Partial on
purpose: neither list was ever going to cover a REGISTERED project under
suo-moto scrutiny, only the unregistered-building enforcement pipeline --
that is a fact about what TNRERA publishes, not something more code fixes.

Both sessions' worth of "Partial to Full" attempts land on the same
distinction: some ceilings are a missing parser (fixable, and now fixed
three times over -- Delhi's REAT parties, UP's project sweep, TNRERA's
enforcement PDFs), and some are the authority genuinely not publishing the
thing at all, or its own systems being down. Telling the two apart, live,
is the actual work.

---

## 2026-08-26 — the 32 "Unaudited" cells got audited

The four states added 2026-08-24 (UP-RERA, TNRERA, HARERA, Delhi-RERA) left 32
coverage-workbook cells marked "Unaudited" -- appeals registers on all four,
financial disclosure on UP/TN/HR, defaulters and projects-under-investigation
on all four, delay reasons and NOC expiry on most. Each was resolved by an
actual live portal check (four parallel sessions, one per state, all HTTP
against the real authority), not by inference from a menu label. Full detail
lives in `build_data_coverage.py`'s `RERA_NOTES_LATER`; the shape of it:

**Every one of the four states has a separate Real Estate Appellate
Tribunal**, distinct from the RERA authority itself -- none of this pipeline
had looked for one before. Coverage varies wildly: HARERA's (HREAT) is a
plain GET, no CAPTCHA, 2,779 rows, genuinely promoter-searchable
(`haryanarera.gov.in/admincontrol/judgements/3`). Delhi's (REAT, shared with
the UT of Chandigarh) is a live 505-row register **not linked from any
Delhi-RERA page** -- found only by web search -- but names no party, so it's
browsable, not searchable. UP's (UP-REAT) has a real judgement search with a
party-name field, CAPTCHA-gated. Tamil Nadu's (TNREAT) exists as a body but
its site publishes nothing searchable at all -- a sign-in page and a
virtual-meeting report, nothing else.

**All four turned out to publish some real defaulter/enforcement register the
2026-08-24 audit never found**, each reached by a different mechanism: UP-RERA's
`DefaulterList` (72 rows) is reachable ONLY via an ASP.NET `__doPostBack`, not
a plain link -- a GET on the URL itself serves the same 48.7 KB empty shell as
an unissued project id, which is presumably why it was missed the first time.
HARERA's `cancelled_projects` register (23 Panchkula + 5 Gurugram rows) is
confirmed **distinct** from the already-imported-but-unused `lapsed_projects`
register (320 + 235 rows) -- lapsed is validity expiry, cancelled is an
authority action against the promoter, and they are not the same finding.
TNRERA's penalty register (147 rows across Building + Layout) names the
promoter and the amount levied. Delhi's execution register (7,493 rows) names
the non-complying promoter as "Judgement Debtor," and its suo-moto register
(1,797 rows) is the single clearest "projects under investigation" signal
found on any of the four -- ironic, given Delhi otherwise has no reachable
per-project record at all.

**Each of those seven new registers got a small, pure, live-verified parser
function**, added to the relevant `states/adapter_*.py`, following the
precedent `adapter_westbengal.fetch_defaulters()` set: written and correct,
**not wired into `acquire()`**, because turning a coverage finding into a
Charter input is a separate decision from confirming the finding is real.
They are catalogued on Sheet D of the coverage workbook. `acquire()` was not
touched on any of the four adapters, and all 98 of their tests plus the full
708-test suite pass unchanged.

**What stayed a real "No" got a reason instead of staying "Unaudited":** none
of the four publishes a profit & loss statement or an income-tax return
anywhere (checked against real document grids/field blocks, not assumed);
UP-RERA and Delhi-RERA publish no balance sheet either (HARERA's "Yes" is
worth a second look -- it rests on a compliance-declaration checkbox and a CA
certificate that *references* a balance sheet, not a document literally
labelled one); and none of the four states an NOC *expiry* date -- HARERA's
"Statutory Approvals Status" table gives the date a clearance was obtained,
never when it lapses.

---

## Status

| Phase | Scope | State |
|---|---|---|
| Stage 0 | Template renamed to `Company_Charter_TEMPLATE_Integrow_Branded.docx` | **Done** |
| Stage 1 | `states/` package; state a first-class field; ~20 label sites parameterised | **Done** |
| Stage 2 | Capability gates; state-independent bug fixes; `rules.md` generalised | **Done** |
| Stage 3 | Acquisition extracted behind `StateAdapter`; `main.py` + `app.py` on one call | **Done** |
| Phase 2a | Gujarat (GujRERA) adapter | **Done**, verified live end-to-end |
| Phase 2b | Karnataka (K-RERA) adapter | **Done**, end-to-end run completed 2026-08-19 (12 complaints, 122 docs / 88 retrieved) |
| Phase 2c | Wrap `ts_rera_client` as the Telangana adapter | **Done**, mapper tested against the real CONSTELLA capture |
| Phase 2d | Jharkhand (JHARERA) + West Bengal (WBRERA) adapters | **Done**, both live-verified |
| Phase 3 | Fix Maharashtra CTS land-record extraction | **Done**, live-verified 2026-08-21. 15 fields + 3 mutation entries off the real card (was `{}` on every prior lookup) |
| Phase 4a | Group entity graph (propose by name, confirm by hard link) | **Done** |
| Phase 4b | Group-wide RERA sweep | **Done** — `group_sweep.py`, with per-authority coverage and confirm/refute |
| Phase 4c | Litigation across the group | **Done** 2026-08-21 — case law per entity/director, plus promoter-keyed orders from FOUR authorities (K-RERA x5 registers incl. penalties, MahaRERA, JHARERA, WBRERA via cause lists). GujRERA and TG-RERA probed; the reason each cannot be searched is recorded and rendered |
| Phase 4d | Finances (charges, ratings) | **Done** — MCA charges, `charge_watch.py`, CRISIL added, ratings across group entities |
| Phase 4e | Statutory (GST) across the group | **Done** 2026-08-21 — `gst_group.py`, opt-in `--group-gst`. Coverage-first: GST is PAN-keyed, so most of a group is unreachable |
| Phase 2e | Uttar Pradesh (UP-RERA) + Tamil Nadu (TNRERA) adapters | **Done** 2026-08-24, both live-verified end-to-end |
| Phase 2f | `fetch_project_summary` for Gujarat and West Bengal; coverage workbook and PRD/SAD brought to ten states | **Done** 2026-08-24 |
| Phase 2 (rest) | ~20 remaining state portals | Not started — always a separate plan |

### Phase 4c (group litigation): names propose, nothing here confirms

`litigation_sweep.py`, opt-in via `--group-litigation` /
`CHARTER_GROUP_LITIGATION=1`, rendering a "Group Case-Law Search" section.
Two sources, deliberately kept apart.

**Case law (Indian Kanoon), per entity and per director.** Every hit is a
CANDIDATE. The first live query proved why: searching *Pranami Builders* -- a
Ranchi company, `U51909JH1995PTC013805` -- returned *"Pranami Builders,
**Ahmedabad** vs Department Of Income Tax"*, plus an Indore income-tax matter
and a thermal-spray case that merely shared tokens on a full-text index.
Rendering any of them as this promoter's litigation would invent a finding out
of a name collision. So each row carries whether the name is in the CASE TITLE
or only in the body, and the place named in the title is checked against the
group's known footprint -- a CAUTION, never an exclusion, since a company
litigates where the cause of action arose. Director queries carry a standing
caution: Indian personal names repeat enormously.

**The absence side is the more dangerous half.** Open case-law search does not
reliably carry consumer fora, most of NCLT/NCLAT, district courts, arbitration,
or the RERA authorities' own orders. `coverage_sentence` may not use the words
"clean" or "no litigation", and the forums NOT indexed are named on the page. A
clean paragraph drawn from a source that would not have carried the bad news
anyway is the worst false clean record this pipeline could produce.

**K-RERA's own order register**, searched by promoter name. 11,732 entries
across 1,821 promoters, one request, no CAPTCHA -- the same
whole-state-index-in-one-request pattern as the project index, so it reuses
`parse_search_index` including its refusal to zip mismatched arrays.

> **The trap here, and it was nearly walked into.** K-RERA's own
> `/viewJudgementDetails` POST does NOT filter server-side. A real firm name and
> a nonsense string return byte-identical pages apart from the visitor counter,
> because the whole register ships to the browser and is filtered there. Wiring
> that POST up as a search would have reported "no orders" for every promoter
> ever queried -- the Maha Bhulekh bug again. The control lookup caught it:
> 111 entries for a known developer, 0 for a nonsense name.

**What remains:** other states' order registers. MahaRERA's own orders search is
per-project rather than per-promoter; GujRERA's judgements sit behind a flow
this pipeline does not query; TG/JH/WB publish no promoter-keyed register. The
section names all of them, so an empty table is never read as a clean record.
K-RERA also publishes five further order endpoints not yet read
(`/viewAllInterimOrders`, `/viewAllProjectOrders`, `/viewAllAOorders`,
`/viewAllComplaints`, `/viewAllComplaintDetails`).

**The remaining registers, probed 2026-08-21 rather than assumed.**

| Authority | Promoter-keyed order register? |
|---|---|
| **Karnataka** | **Yes — five of them.** Order-search index (11,732), authority orders, AO orders, interim orders, complaints under process. 15,600+ rows, including a **penalty table with violation, section and amount** (440 rows) |
| **MahaRERA** | **Yes — fixed 2026-08-21.** `order_respondent_name` is promoter-keyed. Needed `big_pipe_nojs=1`, the whole 18-field form, a widened date window, and the singular complaint-type value. Validated: 40 rows for a real respondent, 0 for a control |
| **WBRERA** | No. 4,881 authority orders published, keyed only by complaint number; no party named in any column. Joinable via complaint numbers the adapter already reads per project |
| **JHARERA** | **Yes — added 2026-08-21.** `/Home/judgement_order`, 228 entries in one request. Both parties in one column, four separator spellings; 225 parse, 3 do not and are counted |
| **GujRERA** | No. Judgement data is `complain/SECURE/complaint-judgments-Details` (login required); only complaint COUNTS are public |
| **TG-RERA** | No such register |

> **Two silent-failure bugs found, both of the same species.**
> 
> **1. K-RERA pages truncate.** The 10.4 MB authority-orders page arrived once
> with 2 of its 3 tables, dropping the penalty register entirely — a penalised
> promoter would have shown none. The AO-orders page truncated mid-attribute on
> another fetch. `_looks_complete` now refuses any body without a closing
> `</html>`, and `order_register_coverage()` names registers that did not load.
> 
> **2. MahaRERA's orders search has been returning nothing for everything.**
> `search_maharera_judgments` returned `[]` both for "no order published" and
> for "every attempt hit the shell". Confirmed live: a search for a large,
> certainly-litigated promoter hit the shell every time, so the pipeline was
> reporting no orders for it. `search_maharera_judgments_status` now exposes
> `{"searched": bool}`. **The BigPipe follow-up is now fixed** — see the row above.

### Phase 4e (group GST): the join key is the whole problem

`gst_group.py`, opt-in via `--group-gst` / `CHARTER_GROUP_GST=1`, rendering a
"Group GST Filing Standing" section.

**GST is keyed on PAN. The entity graph is keyed on CIN. No public MCA source
publishes a company PAN.** So most of a group cannot be reached at all, and the
section leads with that: on the real Pranami graph it reads *"GST filing history
was obtained for 2 of 65 group entities. The remaining 63 could not be checked
and are not reported as compliant or non-compliant."*

A PAN is never guessed. Each carries a provenance -- read off a filed PAN card
(`promoter_identity`), named in another authority's filing (this is how
JHARERA gave up Pranami Builders' `AAECP0371L`), extracted arithmetically from
a known GSTIN, or supplied by hand. An `unverified_candidate` from the OCR pass
is refused: it would spend a human CAPTCHA solve on possibly the wrong company
and then attribute the answer to this one.

The bound is a HUMAN budget, not a rate limit -- two CAPTCHA solves per entity
minimum (one PAN search, one per GSTIN found). Anything past the limit is
`STATUS_BUDGET_EXHAUSTED`, never a silent truncation.

**What would widen coverage** (not built): PANs are printed on RERA filings in
several states, so extending `promoter_identity`-style extraction across the
group sweep's document libraries would convert unreachable entities into
checkable ones. That is the natural next step and it needs no new source.

Registered states: **MH**, **GJ**, **KA**, **TG**, **JH**, **WB**, **UP**, **TN**, **HR**,
**DL** — all ten have adapters. TG declares zero capabilities, which is a complete
adapter, not a stub.

### Phase 2e (UP + TN): what the audit found that the profiles did not

Both profiles were written from a portal audit before the adapters existed, and both
were wrong about something that changed the design.

**UP-RERA's search is CAPTCHA-gated, and nothing recorded that.** The profile said the
promoter dropdown was merely paired with a mandatory district filter. `View_projects.aspx`
in fact ships `function validate() { ... alert("Please Enter Captcha") }` against a
`txtcap` field and a `CaptchaImage.axd` image, and a postback without a solved CAPTCHA
returns the form with its results panel **empty** — indistinguishable from "this promoter
has no projects". So the adapter never posts that form; `group_sweep._CANNOT_SEARCH["UP"]`
now carries the reason, and `states/adapter_uttarpradesh.py` deliberately does NOT define
`search_promoter_projects` (a test asserts the absence).

> **A MISSING PROJECT ON UP-RERA IS HTTP 200.** `?id=378870`, `?id=30000` and every other
> unissued id return a 48.7 KB page shell with a 200 — site chrome, no record, no error.
> Parsed for fields it yields no promoter, no land, no bank account and no documents,
> which on a Charter reads as a promoter who filed nothing. Resolution therefore checks
> that the page **served the number that was asked for**, not that the fetch succeeded.
> `ViewDocument` does the same thing for a file it does not hold, so downloads are judged
> on the body rather than the status code.

Resolving a LEGACY UP number still costs no search — the detail page's `?id=` is the
number's own numeric suffix. The post-2024 scheme (`UPRERAPRJ378870/03/2025`) does not
follow that and is **refused with the reason stated** rather than guessed at; a guessed id
lands on another promoter's project. Documents need no VIEWSTATE round-trip: the download
LinkButton answers with `window.open('ViewDocument?Param=<file name>')` and that file name
is already in the grid's own column. Live: 24/24 documents on UPRERAPRJ2499, plus 7 slots
carrying the promoter's own 'NA' — **not filed** is a third state, and among those seven
are the CA, ARCHITECT and ENGINEERS certificates, which is a finding rather than a gap.

**TNRERA's register is two applications whose volumes do not meet.** Counted live:

```
Building 2024, static per-year register:   96 rows, serials   1..96
Building 2024, current application:       313 rows, serials 301..613
overlap: ZERO.  On neither register: 204 numbers, serials 97..300.
```

Every other year 2017-2026 is whole. 2024 is the year the authority switched
applications. An adapter reading one register reports "not registered" for projects the
state has registered; one reading both still meets 204 numbers that are on neither, and
`coverage_note()` distinguishes a serial **inside that hole** (a limit of what TNRERA
publishes) from one **past the end** of the year's numbering (genuinely unissued). The
registration number names its own page — type and year are in it — so a lookup costs two
GETs and no search.

TNRERA has **three** order registers, not the one the profile knew about:
`tnrera_judgements` (Authority, 2018-), `smb_judgements` (single-member bench, 2022-) and
`adjudicating_judgements` (Adjudicating Officer, 2018-). All three name complainant AND
respondent in their own columns, so TNRERA is promoter-searchable for orders — 23
register-years read per sweep, with the unread ones named. Reading one and calling it the
litigation record is the K-RERA five-registers mistake.

> **The PAN leak, refused on purpose.** TNRERA masks the PAN (`XXXXXX230D`) and the same
> promoter view was observed carrying a full unmasked PAN in the adjacent `Company
> Registration No` field, where somebody typed it into the wrong box. `parse_public_view`
> refuses to return a PAN-shaped value from that field: harvesting it would key a group
> entity on another portal's data-entry error. Watch the label test — `"pan" in label`
> matches **Com-pan-y**, and the loose version blanked the withholding note itself.

**Two bugs the live runs found that no unit test would have.** The carpet-area statement
is served as `.xlsx` and was written to disk as `...View_File.pdf` — a ZIP archive named
PDF, which anything opening it by type reads as empty; and the Form-C anchor carries no
link text, so its document saved as `document.pdf` and a second one would have collided.
Both now have guards. The pattern held again: **every real defect came from running it.**

### The Haryana document downloader was dead on arrival

Found while auditing the new adapters, fixed 2026-08-24, live-verified on
RERA-GRG-741-2020 (102 EDEN ESTATE): **60/60 documents, 60 files on disk, all `%PDF`.**

`_download_documents` called `safe_document_filename(label, used)` against a helper whose
signature is `(documents_dir, label, used_names)`. TypeError on the FIRST document — and
the call sat OUTSIDE the try/except wrapping the fetch, so it did not degrade into a
per-document "failed" row: it propagated out of `acquire()` as a bare TypeError, which is
not a `StateAcquisitionError`, so main.py did not catch it either. A real Gurugram record
links sixty documents, so every Haryana run with a document died. Nothing caught it
because Delhi and Haryana shipped without test files.

The directory argument is not a formality: the length budget is measured against the
caller's own output directory, and HARERA's labels are long ("AMSTORIA 26-2-2015.DWG
STREET LIGHTING-MODEL"). The live run's longest path came to 251 characters — nine short
of the Windows limit that reports itself as FileNotFoundError.

> **And the same 200-OK trap, for the third portal in a row.** A document id HARERA does
> not hold answers **HTTP 200 with 25 KB of HTML**. Proven against the pre-fix code: it
> wrote that error page to disk as `JAMABANDI.pdf` and reported it "downloaded". A
> Charter's document library would list a licence, a jamabandi and a demarcation plan
> that are all the same web page. HARERA's document URLs are opaque hashes with no file
> name, so the extension now comes from the served Content-Type.

`test_haryana_adapter.py` guards both, and both guards were checked against the pre-fix
code rather than assumed to fire.

### Every state can now be OPENED, or says why not

`fetch_project_summary` existed on seven of ten adapters. Gujarat and West
Bengal now have one; Telangana deliberately does not, and that is now written
down rather than left as a silent absence.

**Why this is not the same as being searchable.** GJ, WB, TG and UP cannot be
searched by promoter, so `group_sweep` never produces hits for them — a
`fetch_project_summary` is not there to serve the sweep. It is there for the
other half: a registration arriving from ANY other source (a promoter's
declared past project, a past-experience address, a human) can now be opened
and read instead of listed and left unconfirmed.

Each earns it for a specific reason:

- **West Bengal** — WBRERA's index does not name the promoter. The project page
  does. Opening the project is the ONLY way to attach a WB registration to a
  name, which is exactly what a sweep candidate needs to be confirmed or
  refuted. Live: `WBRERA/P/NOR/2025/002592` → AARIKA CONSTRUCTION LLP.
- **Gujarat** — `getprev-project-list` is where a Gujarat promoter declares the
  projects they built BEFORE this one. In a state that cannot be searched by
  promoter, that is the only route to those registrations at all. Live on
  `PR/GJ/SURAT/.../PAA12907/120224/311228`: AALEKH ENTERPRISE, plus 2 declared
  earlier projects.
- **Telangana** — cannot. Its public record does not display its own
  registration number and its search is CAPTCHA-gated by project name, so
  there is nothing to hand a fetch. A stub that always failed is precisely what
  `states/base.py` forbids, so the reason lives in
  `group_sweep._CANNOT_OPEN` and reaches the page: an unopened TG project now
  carries that sentence instead of a generic "no per-project fetch".

`test_project_summary.py` pins the contract — every state either implements the
call or has a written reason — so a new adapter arriving with neither fails at
the moment it is added.

> **A test that had quietly stopped testing anything.**
> `test_group_sweep.py::test_a_state_with_no_detail_fetch_says_so` used Gujarat
> as its example of an unopenable state. The moment Gujarat gained a fetch, that
> test stopped exercising the no-fetch path AND started making a live request to
> GujRERA from the offline suite. It uses Telangana now, which imports and
> requests nothing.

### Docs and the coverage workbook, brought to ten states

`docs/PRD.md` and `docs/SAD.md` had six-state `--state` lists in three ASCII
diagrams and a "Six states" row; all now read ten, and the SAD gains rows for
which states are searchable and which are openable. `build_data_coverage.py`
listed six authorities, so `docs/RERA_Data_Coverage.xlsx` had no columns for
UP, TN, HR or DL at all — it now has ten, 21 items each.

**32 of those new cells say "Unaudited", and that is the point.** Every "Yes"
was seen on a real record — UPRERAPRJ14636 and 2499, TNRERA/29/BLG/0001/2026,
TN/16/Building/0001/2024, RERA-GRG-741-2020, Delhi's whole register — and
nothing was inferred from a portal's menu. The sheet already had that
vocabulary for exactly this.

### Delhi and Haryana: covered by tests at last, and three more bugs fell out

Both adapters shipped with **no test file at all**, which is how a hard crash on every
Haryana document run went unnoticed. `test_delhi_adapter.py` (19 guards) and
`test_haryana_adapter.py` (24) now cover them. Writing them found three further defects,
every one by counting the live register rather than by reading the code.

**1. One Delhi project was invisible.** The register appends a footnote marker INTO the
cell -- `<label style="color:red" title="view disclaimer">*</label>` -- so the text reads
`DLRERA2025P0003 *`. That string matches nothing: not the DL profile's own reg-no pattern,
not a reader pasting the number as issued. Good Earth Capital Crest could not be resolved
by its own registration number, could not appear in a portfolio, and could not be
confirmed as a sweep hit. The register states the number twice and the second one is
clean -- `data-diary-no="DLRERA2025P0003"` -- so the attribute is preferred now and the
strip is the fallback. The marker survives as `has_disclaimer`, with a note saying the
authority does not publish what it means (the only disclaimer text on the page is a
site-wide data-migration notice) rather than inventing an interpretation.

**2. Delhi's complaint count was counting orders.** The register publishes one row per
ORDER: complaint 30/2020 -- one complainant, one respondent -- occupies **34 rows**
differing only by decision date. Live on the Delhi Development Authority the run now
reports *"2 matching complaint(s) against 20 published order(s)"* where it would have said
20 complaints. The complaint NUMBER alone is not the key either: 624 rows carry 68
distinct (number + parties) but only 51 distinct numbers, so numbers are reused between
unrelated cases and collapsing on the number would merge different people's complaints and
UNDER-report. The parties separate them. Each row's judgement PDF link was also being
discarded and is now kept.

**3. Fifty-two Haryana projects would have shared one output directory.** 52 of the 2,161
rows across both benches print the literal string `NA` in the Project ID column -- BPTP
NEST 83-A/B/C, NINEX RESIDENCY, several 2017 affordable-housing schemes, almost all older
or lapsed. The Project ID is the primary key of `output/<reg_no>/`, so all 52 would have
landed in `output/NA/` and overwritten each other: the Gujarat nested-path bug the other
way round. All 52 carry a certificate number and all 52 of those are distinct, so
`project_key()` falls back to it and the run says so. Verified live: HRERA-PKL-FBD-155-2019
(BPTP NEST 83-A) now resolves and files under its certificate.

> **The trap that was NOT fallen into, and the test that exists to answer it.** Every Delhi
> register row carries a hidden `hdnPromoterID`, and it looks exactly like the promoter
> identifier the adapter's notes say Delhi does not publish. It is issued per
> REGISTRATION, not per promoter: the Delhi Development Authority appears under **22**
> different ones, NBCC under eleven across two spellings of its name. Joining a portfolio
> on it would split one promoter into 22 and report a developer with 24 Delhi projects as
> having one. The name match is the weaker-looking option and the correct one; its own
> limitation is stated in the portfolio notes instead.
> `test_the_hidden_promoter_id_must_not_be_used_for_the_portfolio` is there for whoever
> notices that field next.

Every guard in both files was checked against the pre-fix code -- the Delhi parse really
does yield `DLRERA2025P0003 *` and a 3-order complaint really is counted as 3 -- rather
than assumed to fire.

---

## RESUME HERE

### Step 6 (CTS land records): DONE, live-verified 2026-08-21

`fields` was `{}` on every lookup this repo had ever made. It is now 15
fields plus 3 mutation entries, cross-checked value by value against the
card image. CTS 183, village आंबिवली, Mumbai Suburban:

```
owner/holder  'h sheti' (year 1964)      area      '25562.70'
tenure        '[- - 25562.70] sheti'     encumbrance ''  <- none recorded
mutations     19/11/1979, 16/12/2015, 02/06/2025  (nos. 599, 922)
PU-ID 88331721167 | CTS 183 | ambivli / nagar bhumapan adhikari,andheri
```

**The finding that cost five CAPTCHA solves: the Marathi card is a JPEG.**
This module used to assert the opposite -- "real structured Devanagari text
... NOT a scanned image" -- and the test file called the work "a parser fix
rather than an OCR project". Both were false. In Marathi the card is a
single base64 JPEG in an `<img>` src, so `PU-ID` appears in no frame's HTML
while a screenshot shows it plainly. The **English** rendering is real HTML
tables and is the only machine-readable form without a Marathi OCR pack
(not installed). The values diligence turns on -- CTS number, area, dates,
mutation numbers -- are digits and identical in both; only Marathi words
transliterate, and the authoritative Marathi JPEG is saved beside every
capture by `save_embedded_card_image`.

**Structure notes, each of which silently emptied or corrupted fields:**

- The village/taluka/district table is nested INSIDE the table carrying the
  CTS/Area/Tennure header. Skipping wrapper *rows* is required; skipping
  wrapper *tables* destroys every header field. Both halves matter -- the
  same lesson the JHARERA adapter taught.
- Those three are written `Label : value` inside ONE cell.
- The label block opens each row with an empty spacer `<td>`, so the label
  is in `cells[1]` and the value in `cells[2]`.
- The mutation table's third column is खंड क्रमांक (Vol.No.), NOT the
  mutation number -- that is prose inside the attestation cell
  ("ferafar kran. 599 pramane"), extracted by regex.

**Every failure was the same species: something unreadable presenting
itself as something absent.** Wrong language, a "Processing, Please wait"
overlay scraped instead of the card, a main-frame-only text check that
could never see a child frame, a DOM/screenshot race, the spacer `<td>`,
and a success test that passed on a PU-ID alone (PU-ID is regex-matched
over the whole page, so it survives a card whose every row failed). On a
Charter each renders as "no owner, no encumbrance, no mutation entries" --
a clean title on a plot nobody read. Guards now: a card yielding only a
PU-ID reports "NO READING TAKEN"; `run_cts_capture.py` judges on labelled
rows, not `len(fields)`; no field may carry another field's text.

`card_page.html` is saved on every capture, so the next failure can be
diagnosed offline without spending a human CAPTCHA solve.

**Left for a human eye:** `original_holder` reads `'h sheti'`. That is what
the card shows (the holder column reads "H" over "शेती"), not a parse
error, but it should be reviewed before it reaches a Charter.


### Blocked, not broken: the Karnataka end-to-end run

`rera.karnataka.gov.in` has been **unreachable all of 19 August** — three consecutive
connect timeouts, having worked fine on the 18th. The adapter is unaffected; it is the
portal. Retry when it is back:

```bash
python -u main.py "PRM/KA/RERA/1251/309/PR/201001/003607" --output-dir output
```

Expect ~9,900 projects indexed, 122 documents listed / ~89 retrieved, **12 complaints**,
a 2-project promoter portfolio, and `run_meta.json` carrying `"state": "KA"`.

The outage did produce something useful: the run now **degrades cleanly** instead of
crashing, which is what it did this morning.

```
[WARN] K-RERA project index failed (ConnectionError), attempt 1/3 -- retrying.
[WARN] K-RERA project index failed (ReadTimeout), attempt 2/3 -- retrying.
[ERROR] '...' could not be found on any authority whose registration format it matches.
          - K-RERA: ... The portal appears to be unreachable right now; this is not a
            problem with the project or the registration number.
```

### Next up

**Phase 3 (CTS extraction)** — its first step is re-running one Maharashtra land-record
lookup, which NEEDS A HUMAN at the keyboard for a CAPTCHA solve. That single test decides
whether it is a half-hour parser fix or a real project.

**Phase 4b/4c/4e** — the group-wide sweeps. 4a built the entity graph they iterate, so
they are unblocked: run `promoter_portfolio` per confirmed entity across the state
adapters, then litigation and GST the same way. Use
`group_entities.entity_names_for_sweep(graph)`, which defaults to director-or-filed links
and deliberately excludes address-only ones.

## Step 3 — Jharkhand and West Bengal adapters

Built **subject-first, not size-first**. JHARERA holds ~1,200 projects against
MahaRERA's ~55,000, so by volume it would rank near the bottom of the thirty.
It is also where the promoter that drove this whole effort actually built.

**The payoff, first request:** searching JHARERA for "PRANAMI" returns
**PRANAMI CREST, JHARERA/PROJECT/35/2023**, whose page names
**Bijay Kumar Agarwal, `projects@pranamigroup.com`** — the same director as the
MahaRERA entity — lists **PRANAMI BUILDERS PVT.LTD** as contractor with PAN
`AAECP0371L`, and declares an earlier registration, **PRANAMI BLUE SAPPHIRE,
JRERA/PROJECT/08/2018**.

### What each portal publishes

**JHARERA** — ASP.NET MVC, no CAPTCHA, no token, plain GET search. The least
hostile portal audited. Publishes what MahaRERA does not: **PANs as text
fields** (contractor, architect, structural engineer), a **declared
past-projects table**, a **per-project litigation table**, audited balance
sheets and three years of income-tax returns, and separate state-wide
**rejected**, **surrendered** and **disposed-complaint** registers.

**Two live registration spellings**, and they are not interchangeable:
`JHARERA/PROJECT/35/2023` (current) and `JRERA/PROJECT/08/2018` (pre-2019).
A pattern accepting only the longer one would fail to resolve every older
project — including the declared past project of the very project this
adapter was built for. The profile accepts both.

**WBRERA** — PHP, no CAPTCHA. **Requires legacy TLS renegotiation, exactly
like GujRERA**; plain `requests` fails the handshake outright. No search box,
but `district_project.php?dcode=0` returns the **whole state in one request**
(4,721 projects, verified: 4,721 raw rows in, 4,721 parsed, none dropped).
Publishes a promoter row, a litigation field, agents and consultants, and
documents including PAN card, balance sheet and ITRs.

WBRERA does **not** get `CAP_PROMOTER_PORTFOLIO`: its index does not name the
promoter and there is no promoter search, so a portfolio would mean opening
all 4,721 detail pages. It returns None and says so.

**West Bengal's register is young for a structural reason.** The state ran its
own Housing Industry Regulation Act until the Supreme Court struck it down in
May 2021, and WBRERA was constituted afterwards — so a genuine pre-2021 West
Bengal project may have no record here at all. That is in the authority notes,
because otherwise an absence reads as a finding.

### Bugs found by running them live

**1. Two documents out of sixty-nine (JHARERA).** Every document link on a
JHARERA project page is labelled "View" and served from
`/FirstLevel/ViewDocument/<id>` with no file extension. Matching on a `.pdf`
suffix found **2 of 69** — missing the promoter's PAN card, audited balance
sheet and three years of income-tax returns. Worse, naming files from the link
text would have saved all 69 over one filename and `promoter_identity` would
never have found the PAN card. Labels are now derived from the surrounding
table: the column header where the row is a set of slots, the sibling cell
where the row is one document.

**2. 267 of 275 documents unretrievable (WBRERA).** The project's filings live
on a different host, `doc.repository.semtwb.in`, over **plain HTTP** — and
urllib3 rejects `assert_hostname` on a non-TLS connection with a TypeError, so
every `http://` document failed inside the legacy-TLS pool. Downloads now use a
plain requests session; only `rera.wb.gov.in` needs the TLS workaround.

**3. The portal's own paperwork counted as project filings (WBRERA).** The
West Bengal Real Estate Rules, four user manuals and authority orders under
`/scrol/` are linked from every project page. Documents are now classified
explicitly: on the document host AND under a project-scoped path segment
(`/nproj/`, `/aproj/`, `/upcer/`). 261 real filings, boilerplate excluded.

**4. A portfolio search on the wrong name (JHARERA).** The promoter of record
is the SPV `PBPL PRANAMI CREST RERA PRIVATE LIMITED`, which matches nothing
else. The portfolio search now falls back to the brand token.

Both adapters degrade cleanly: JHARERA went unreachable mid-run during
testing and produced a `StateFetchError` with an honest message after three
retries, not a traceback.

## Phase 4f — promoter PAN, the cross-state join key

**`promoter_identity.py` + `test_promoter_identity.py` (15 tests).** Verified
live on the real filing:

```
PAN AANCP0234D | Company | incorporated 2022-06-27 | source: "PAN Card"
checks: holder-type 'C' = Company; 5th character 'P' matches promoter name initial
```

**Why this was worth doing.** No RERA portal publishes a CIN or DIN, and only
TG-RERA and K-RERA publish a PAN as a field. MahaRERA and GujRERA publish
neither — but both require the PAN **card** to be uploaded, and both serve it
from the document library this pipeline already downloads. So the one
identifier that joins RERA to MCA, GST and income-tax records was already on
disk after every run, as a picture, unread.

**Why whole-page OCR returned nothing.** A PAN card scanned onto A4 fills about
a sixth of the sheet, so Tesseract returns the empty string — not an error, not
garbage, nothing. `company_charter._extract_document_text` hits exactly this.
Cropping to the scan's content box and upscaling recovers it under psm 6, 4 and
11 alike. `test_whole_page_ocr_really_does_fail_on_this_file` is the
anti-vacuous-pass guard: if plain OCR ever starts working on that artifact, the
end-to-end test stops proving the crop path.

**The verification, which is the point.** A PAN pulls another company's charges
and litigation if it is wrong, so it is checked twice — the 4th character
against the closed set of holder-type codes, the 5th against the initial of the
promoter name **the portal itself published** (not the model-authored one, so
both sides are non-model sources). Failures land in `unverified_candidates`
with a reason.

Wired into `facts["promoter_identity_check"]` via
`company_charter._safe_promoter_identity`. **Not yet rendered into the Charter**
— that needs the compliance review, which needs an API call, which is blocked
until 2026-09-01.

## Phase 4a/4d — what landed

**Group entity graph** (`group_entities.py`). Propose by brand name, confirm by a hard
link. On the real Pranami subject: 65 confirmed entities, and 6 name-matched candidates
correctly held back as unconfirmed — including a Delhi hydro-power company and a Gujarat
non-profit that merely share the word "PRANAMI".

Link strength is tiered, because the three signals are not equal: **filed** relationship >
**shared director** > **shared registered office**. 28 of those 65 were address-only, and a
registered-office service provider in Mumbai hosts dozens of unrelated companies —
`entity_names_for_sweep()` therefore excludes them by default, so a co-tenant's litigation
never lands in this promoter's track record.

**MCA charge filings** (`company_charter.summarise_charges`). Parsed from the ZaubaCorp page
the pipeline was ALREADY fetching — no new request, no cost. Live on the real promoter:

```
100857390  2023-10-31  OPEN        3,491,110.00  HDFC BANK LIMITED
100878097  2024-01-29  OPEN      300,000,000.00  CATALYST TRUSTEESHIP LIMITED
100939871  2024-03-26  OPEN      300,000,000.00  CATALYST TRUSTEESHIP LIMITED
100940922  2024-03-26  OPEN      300,000,000.00  CATALYST TRUSTEESHIP LIMITED
=> Rs 90.35 crore of OPEN secured borrowing, 2 lenders
```

No closure date means the charge is live. This is the only independent check the pipeline
has on a promoter's declared mortgage — the RERA record states an area and never a lender.

**Still to wire:** neither is rendered into the Charter yet. The data is in
`company_profile_check.charges`; a Charter section and the Developer Score's
financial-strength sub-metric are the obvious consumers.

---

## Bugs found by running things live

Each of these was invisible until a real portal was hit, and each now has a guard.

**0. app.py did not compile — and was committed and pushed that way.**
The Stage 3 refactor moved main.py's archiving callback into app.py verbatim, but app.py
runs at MODULE level (Streamlit executes the script top-to-bottom), so its
`nonlocal prior_research` had no enclosing function to bind to. A hard SyntaxError on
import. Every guard passed and the suite stayed green because
`test_app_no_duplicate_orchestration.py` used `ast.parse()`, which does NOT run the
symbol-table pass that resolves scope — and nothing ever imported app.py. That test now
uses `compile()` across every entry point; verified that `ast.parse` accepts the broken
code and `compile()` rejects it.

**0b. A portal outage escaped as a traceback.**
`requests.exceptions.ConnectionError` propagated straight past main.py, which only caught
`StateResolutionError`. There is now a `StateAcquisitionError` base (with
`StateResolutionError`, `StateAuthError` and a new `StateFetchError` under it), callers
catch the base, and `states.fetch_with_retry` retries connection-level failures with
backoff — deliberately not parse or HTTP errors, which fail identically however often
they are repeated.

**1. False clean record (K-RERA) — the worst one.**
K-RERA's per-project complaint page does not reliably carry complaints. For
ADARSH GREENS PHASE 1 it returns only a land-owner table, while the state-wide
register lists **12 complaints**. The first adapter parsed that page and would have
reported a clean project. Complaint counts now come from `/projectComplaintReport`.
Guarded by `test_karnataka_adapter.py::test_live_complaint_count_comes_from_the_state_register`.

**2. A capability that meant two different things (K-RERA).**
`CAP_ORDERS_SEARCH` gates MahaRERA's *own* Orders search. Karnataka was declared with
it true because K-RERA publishes judgements — so a Karnataka project fired a MahaRERA
search, with retries, at a portal that could never match it, and the run hung. The
capability docstrings for `CAP_ORDERS_SEARCH` and `CAP_LAND_RECORDS` now state
explicitly that they mean *"this pipeline can query THAT authority"*, and
`test_state_adapter_contract.py::test_only_maharashtra_may_declare_the_maharera_only_capabilities`
fails if any non-MH profile declares either.

**3. Declared-but-undelivered capability (GujRERA).**
Gujarat declared `CAP_PROMOTER_PORTFOLIO` while returning `promoter_portfolio=None`.
Confirmed live that GujRERA publishes no promoter→projects link at all: searching a
known promoter returns zero projects and `projectAllApplications/<id>` returns `[]`.
Capability dropped; `test_gujarat_adapter.py::test_no_adapter_claims_a_capability_it_does_not_deliver`
now walks every adapter.

**4. Registration numbers are not filesystem-safe.**
Gujarat's `PR/GJ/SURAT/SURAT CITY/SUDA/...` produced a six-level nested directory tree.
`states.base.storage_key()` now flattens the key; `AcquisitionResult.registration_number`
carries the real number for citation. MahaRERA's `P51800077150` is unchanged.

**5. Document filename collisions (both new states).**
Promoters file different document slots under one filename (three separate
`NOT AVAILABLE.pdf` placeholders in one Gujarat project). 15 of 42 documents silently
overwrote each other. Both adapters now de-duplicate; manifest count and disk count agree.

**6. "FAILED" used for declared absences.**
The run summary reported categories an authority simply does not publish as failures.
`AcquisitionResult.categories_not_published` now distinguishes them, and the summary
prints "not published by GujRERA".

---

## Portal notes worth keeping

**GujRERA** — Angular SPA, no CAPTCHA, no token. The search request schema was recovered
from the server's own error envelope, which echoes expected fields (`query`, `startWith`,
`dataSize`, `sortBy`) back as nulls. **The host requires legacy TLS renegotiation**
(`ssl.OP_LEGACY_SERVER_CONNECT`), which works through urllib3 but *not* through
requests 2.32.2 — hence that adapter uses urllib3 directly. Documents download from
`/vdms/download/<uid>`, not `/vdms/getDoc/`.

**K-RERA** — server-rendered JSP, no CAPTCHA, plain TLS. The search page embeds the
entire state index client-side as four parallel JS arrays (ack no / reg no / project /
promoter), giving a promoter→projects map for the whole state in one request. Detail
tables are matched **by header content, never by index** — block tables repeat per tower,
so positions shift between projects. 33 of 122 documents are listed but the portal
serves 0 bytes for them; reported as "not held by the portal".

**TG-RERA** — CAPTCHA-gates the search itself and does not display its own registration
number, so it can never be found by reg no. Profile registered (zero capabilities) purely
so the MH/TG registration-format collision is real and testable.

---

## Biggest unused findings (see Sheet D of the coverage workbook)

- **GujRERA publishes audited balance sheets, P&L and income-tax returns** per project.
  Already downloaded by the adapter; no Charter field consumes them.
- **K-RERA publishes cost incurred vs estimated, delay reasons, NOC expiry.** Same.
- **MCA charge filings** are on the ZaubaCorp page the pipeline already fetches, and are
  the only independent check on a promoter's declared mortgage. Not parsed.
- **Maha Bhulekh** land-record card already carries owner, area, tenure, encumbrance and
  mutation — none reaches the document, because the parser looks for the wrong HTML shape.

---

## Known gaps, deliberately left

- **Charter rendering for any non-Maharashtra project is unverified** — it needs an
  Anthropic API call and the account is over its limit until 2026-09-01. The render path
  is state-parameterised and covered by the differential-render test, but nobody has
  watched it produce a non-Maharashtra Charter, for any of the nine non-MH states.
- ~~`docs/PRD.md` and `docs/SAD.md` still describe the pre-refactor architecture.~~
  **Fixed 2026-08-24** — both now describe the ten-state `StateAdapter` architecture; see
  "Docs and the coverage workbook, brought to ten states" below.
- ~~`output/_history/`~~ **cleared 2026-08-24.** It held three archived runs of
  P51800077150 (257 MB, 226 files), all but a handful of them byte-for-byte
  duplicates of the live `output/P51800077150/`. Two files were NOT duplicates and
  were preserved into `output/P51800077150/research/` before the delete:
  `pranami_bliss_charter_facts.py` (49 KB) and `run_pranami_charter.py` — the
  hand-authored `pre_built_facts` recipe, which existed in exactly one place on disk
  and had never been committed. They now sit where every other one-off script for a
  project lives, beside `output/CONSTELLA_TS/research/patch_state_labels.py`.
- ~20 of ~30 state portals remain unaudited — the ten built are the ones actually looked
  at; the rest are still an unopened list, always a separate plan (see "Phase 2 (rest)"
  in the status table above).
- **Four states cannot be swept by promoter UNATTENDED — GJ, TG, UP, WB — each with a
  written reason in `group_sweep._CANNOT_SEARCH` rather than a silent zero.** GujRERA and
  WBRERA publish no promoter-to-projects link at all; TG-RERA's search is CAPTCHA-gated by
  project name and its record does not even show its own registration number. **UP-RERA was
  the one worth revisiting, and it has now been** — `up_captcha_search.py` (2026-08-26)
  proved live that a human-solved CAPTCHA really does unlock `View_projects.aspx`'s
  promoter+district search (correctly returned UPRERAPRJ14636 for a known promoter in
  Barabanki). That is a real, working, human-in-the-loop lookup for ONE promoter in ONE
  district at a time -- it does not change `group_sweep._CANNOT_SEARCH["UP"]`, because a
  full statewide sweep would still cost one CAPTCHA solve per district across 75 of them,
  which is not something to run unattended. See the "UP-RERA's CAPTCHA gate is now passable"
  section above for what was actually proven and the three bugs fixed getting there.

---

## Join keys — what each portal actually publishes

Verified live 19 August 2026. **No portal publishes CIN or DIN.**

| Authority | Promoter key | PAN | Contact |
|---|---|---|---|
| MahaRERA | `userProfileId` — **proven stable across a promoter's projects** (AGARWAL REALTORS: P99000002207 + P99000002405 both → 112633) | filed **document** only (`documentTypeId 26`) | `emailId`/`mobileNo`/`addressLine` are **AES-encrypted** |
| GujRERA | `promoterId` | filed document only (`projectdoc.panCardDocUId`); the JSON's only PAN fields are the **structural engineer's** and architect's | plaintext |
| K-RERA | — | partner PANs on the project page | — |
| TG-RERA | — | promoter `pan` + `gstin` as fields | — |

Two corrections to earlier notes: **`P99000002207` is a live MahaRERA number**,
so `states._likelihood_order_maharashtra_telangana`'s "observed numbers begin
P5" is incomplete (harmless — it only orders the probe). And GujRERA's
`entityType: PROMOTER` search rows are **not distinct promoters**: their
`entityId` is a project/application id.

**Deriving the states to sweep.** Never a fixed list — three signals already in
the data: `past_experiences[].address` (Pranami's declares "Mall of Ranchi,
Ratu Road Ranchi Jharkhand 835222"; GujRERA's equivalent is
`getprev-project-list/<id>`), the **CIN state code** (Pranami's 65-entity graph:
MH 19, WB 12, JH 7, AS 3, BR/DL/KA/PN 1 each — though **20 LLPs carry no state
code at all** in an LLPIN), and MCA registered addresses.

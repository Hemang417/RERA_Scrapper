# Pan-India RERA — progress and resumption notes

**Updated 19 August 2026.** Working tree is green: `python -m pytest -q` → **543 passed**.
MahaRERA output is byte-identical to the pre-refactor baseline.

Full plan: `~/.claude/plans/yes-make-the-plan-starry-neumann.md`
Data coverage: `docs/RERA_Data_Coverage.xlsx` (regenerate with `python build_data_coverage.py`)

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
| Phase 4c | Litigation across the group | **Partial.** Per-project litigation tables read for JH/WB; no Indian Kanoon sweep, no cross-state orders search, no NCLT/consumer fora |
| Phase 4d | Finances (charges, ratings) | **Done** — MCA charges, `charge_watch.py`, CRISIL added, ratings across group entities |
| Phase 4e | Statutory (GST) across the group | **Not started.** GST intake is still single-subject and opt-in |
| Phase 2 (rest) | ~24 remaining state portals | Not started — always a separate plan |

Registered states: **MH**, **GJ**, **KA**, **TG**, **JH**, **WB** — all six have adapters.
TG declares zero capabilities, which is a complete adapter, not a stub.

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

- **Charter rendering for GJ/KA projects is unverified** — it needs an Anthropic API call
  and the account is over its limit until 2026-09-01. The render path is state-parameterised
  and covered by the differential-render test, but nobody has watched it produce a
  non-Maharashtra Charter.
- `docs/PRD.md` and `docs/SAD.md` still describe the pre-refactor architecture.
- `output/_history/P51800077150/20260818_185831/` is leftover from a test run.
- 26 of ~30 state portals remain unaudited.
- **Jharkhand and West Bengal have no adapter**, and for a Ranchi-origin
  promoter like Pranami that is where the real track record is. See the
  join-key notes below.

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

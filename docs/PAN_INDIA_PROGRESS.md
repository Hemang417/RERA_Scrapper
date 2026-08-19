# Pan-India RERA — progress and resumption notes

**Updated 19 August 2026.** Working tree is green: `python -m pytest -q` → **402 passed**.
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
| Phase 2b | Karnataka (K-RERA) adapter | **Adapter done + live-verified.** End-to-end run blocked on the portal being down — see below |
| Phase 2c | Wrap `ts_rera_client` as the Telangana adapter | **Done**, mapper tested against the real CONSTELLA capture |
| Phase 3 | Fix Maharashtra CTS land-record extraction | Not started |
| Phase 4 | Group entity derivation + group-wide sweep | Not started |

Registered states: **MH**, **GJ**, **KA**, **TG** — all four now have adapters.
TG declares zero capabilities, which is a complete adapter, not a stub.

---

## RESUME HERE

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

Phase 3 (CTS extraction — its first step needs a human CAPTCHA solve), then Phase 4
(group entities). Phase 2 is otherwise complete.

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

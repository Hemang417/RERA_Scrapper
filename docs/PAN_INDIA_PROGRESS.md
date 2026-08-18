# Pan-India RERA — progress and resumption notes

**Paused 18 August 2026.** Working tree is green: `python -m pytest -q` → **392 passed**.
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
| Phase 2b | Karnataka (K-RERA) adapter | **Adapter done + live-verified. One thing outstanding — see below** |
| Phase 2c | Wrap `ts_rera_client` as the Telangana adapter | Not started |
| Phase 3 | Fix Maharashtra CTS land-record extraction | Not started |
| Phase 4 | Group entity derivation + group-wide sweep | Not started |

Registered states: **MH** (full), **GJ** (full), **KA** (full), **TG** (profile only, no adapter).

---

## RESUME HERE — the one outstanding item

Karnataka's adapter is built, live-verified, and fully tested. What has **not** been
confirmed is a complete `main.py` run for a Karnataka project reaching `run_meta.json`.

Three attempts, none of which invalidate the adapter:

1. Killed by my own shell pipeline (`| head` sent SIGPIPE to python).
2. Hung — a real bug, now fixed (see below).
3. Stopped when work was paused.

To finish it:

```bash
python -u main.py "PRM/KA/RERA/1251/309/PR/201001/003607" --output-dir output
```

Expect: ~9,900-project index, 122 documents listed / ~89 retrieved, **12 complaints**,
2-project promoter portfolio. Deep research and the Charter will fail on the missing
API key — that is the account limit, not a defect. Success looks like `run_meta.json`
existing with `"state": "KA"` and a run summary printing.

Note the run is slow (large index + many documents). Run it unbuffered (`-u`) and
redirect to a file; do not pipe through `head`.

---

## Bugs found by running things live

Each of these was invisible until a real portal was hit, and each now has a guard.

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

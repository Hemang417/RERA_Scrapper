"""
Group Financial Disclosure sweep -- opt-in via --group-financial-disclosure.

WHAT THIS DOES. For every project group_sweep.enrich_projects has already
OPENED (via --group-sweep / CHARTER_GROUP_SWEEP=1 -- this pass reuses that
result rather than re-sweeping and re-opening projects itself, the same
"widen an already-paid-for fetch" idiom gst_group.pans_from_sweep uses),
checks whatever document list that state's own fetch_project_summary
exposed. Where a label matches the same high-priority keywords
company_charter.py already uses to route a project's OWN balance sheet/P&L/
income-tax return documents into OCR, this downloads and OCRs just that
document (never the whole document set) and returns the extracted text.

NOTHING HERE CHECKS THE STATE DIRECTLY. `_downloader_for(state)` looks up a
`download_single_document` function on that state's adapter module the same
way group_sweep._detail_fetcher_for looks up `fetch_project_summary` -- if an
adapter has neither the function nor a `documents` key in its project detail,
that project simply carries zero candidate documents. Six of ten states wire
both today -- Gujarat, Jharkhand, Haryana, West Bengal, Uttar Pradesh and
Tamil Nadu -- though a live-confirmed balance-sheet/P&L/ITR-labelled hit only
exists for the first four; UP and TN expose real document lists this pass can
search, but no live run has yet produced a financial-statement match from
either. The other four are excluded on purpose, not by oversight: Karnataka's
fetch_project_summary deliberately never downloads its ~120-document library
(the group-sweep cost trade-off is the wrong one for a background check),
Maharashtra's documents sit behind the same CAPTCHA-gated session as its
partners/appeals categories and are reachable only with an already-cached
token, and Delhi/Telangana publish no per-project document list at all. If a
twelfth state gains a `download_single_document` and a `documents` list
tomorrow, this file needs no change to notice it.

WHY THIS DEPENDS ON --group-sweep RATHER THAN RUNNING ITS OWN. Opening a
project (fetch_project_summary) is a real page fetch, already bounded by
group_sweep.DEFAULT_DETAIL_LIMIT and already paid for once per Charter run
when the group sweep is on. Re-running it here to answer a narrower question
would double that cost for no reason. If --group-sweep was not enabled, this
pass has nothing to check and says so -- never a fabricated "0 found" that
reads the same as "checked, found none".

NO MODEL CALL. Unlike the single-project Financial Disclosure field (which
folds OCR'd text into the one Charter-wide Anthropic pass), this sweep can
touch dozens of OTHER entities' projects, so it renders the OCR'd text
directly -- the same zero-extra-API-cost discipline gst_group.py and
group_enforcement.py already use for their own group-wide checks.

CACHED, NATIONAL, TEXT ONLY. A document already OCR'd for one promoter's
Charter should not be re-downloaded and re-OCR'd for the next run that
happens to share a group entity. Cached by a hash of the document's own
url/uid, exactly like adapter_delhi's REAT appeal cache -- text only, never
the PDF bytes.

Offline-testable: `downloaders` and `ocr` are both injectable, so nothing
touches the network or a real OCR pass when a caller supplies them.
"""

import hashlib
import importlib
import os
import re

import group_sweep
import states

# The FINANCIAL-STATEMENT subset of company_charter._HIGH_PRIORITY_DOC_KEYWORDS
# / _HIGH_PRIORITY_DOC_WORD_RE, not the whole list. company_charter's list also
# routes a project's title/legal/encumbrance/sanction/layout/allotment/agreement
# documents into OCR, for general Charter fact-grounding -- a different purpose
# from this module's, which counts only a balance sheet, P&L statement or
# income-tax return as a financial disclosure. Taking the full list here once
# made "Sale Agreement.pdf" match on "agreement" and get pulled in as though it
# were a financial statement. Not imported, per this repo's own convention of
# one small helper per check-domain module (see group_enforcement._subjects,
# duplicated from litigation_sweep._subjects).
_HIGH_PRIORITY_DOC_KEYWORDS = (
    "balance sheet", "profit", "loss", "audited", "income tax", "income-tax",
)
_HIGH_PRIORITY_DOC_WORD_RE = re.compile(r"(?<![a-z])itr(?![a-z])")

# National, not per-promoter -- a document already OCR'd for one Charter run
# should not be re-downloaded/re-OCR'd for the next. Cache TEXT only, never
# the PDF bytes, same discipline as adapter_delhi's REAT appeal cache.
DEFAULT_CACHE_DIR = os.path.join("output", "_cache", "group_financial_disclosure_ocr")

# Bounds a mislabeled project's false-positive matches -- a project with a
# document naming scheme this keyword set matches too eagerly should not
# turn into dozens of downloads.
DEFAULT_DOC_LIMIT_PER_PROJECT = 5


def _is_high_priority(label):
    label_lower = (label or "").lower()
    return bool(
        any(k in label_lower for k in _HIGH_PRIORITY_DOC_KEYWORDS)
        or _HIGH_PRIORITY_DOC_WORD_RE.search(label_lower)
    )


def _cache_key(identifier):
    return hashlib.sha1(str(identifier or "").encode("utf-8")).hexdigest()[:20]


def _downloader_for(state_code):
    """That state's `download_single_document(entry, dest_path)`, or None.

    Looked up dynamically, the same way group_sweep._detail_fetcher_for
    looks up fetch_project_summary -- never a hardcoded state check. Today
    adapter_gujarat, adapter_jharkhand, adapter_haryana, adapter_westbengal,
    adapter_uttarpradesh and adapter_tamilnadu define this function -- the
    six portals whose fetch_project_summary carries a `documents` list this
    pass can search at all (see this module's own docstring for why the
    other four do not).
    """
    module_path = states._ADAPTER_MODULES.get(state_code)
    if not module_path:
        return None
    try:
        module = importlib.import_module(module_path)
    except Exception:
        return None
    return getattr(module, "download_single_document", None)


def _default_ocr(path):
    # Lazy import: company_charter imports this module, so importing it back
    # at module load time would be circular.
    import company_charter

    return company_charter._extract_document_text(path)


def sweep(rera_sweep, downloaders=None, ocr=None, cache_dir=DEFAULT_CACHE_DIR,
          doc_limit_per_project=DEFAULT_DOC_LIMIT_PER_PROJECT, reporter=None):
    """Checks every project group_sweep.enrich_projects already opened for a
    document list matching the high-priority financial-statement keywords.

    `rera_sweep` is the ALREADY-COMPUTED result of
    group_sweep.enrich_projects(group_sweep.sweep(names)) -- i.e. exactly
    what facts["group_rera_sweep"] already holds when --group-sweep is on.
    This function never re-sweeps or re-opens a project itself.

    Returns {"projects_checked": [...], "statements": [...], "checked",
    "total", "limitations"}. `checked` counts projects this pass actually
    opened (detail_status == "opened"); `total` counts every distinct
    project the RERA sweep found, opened or not -- a project the sweep never
    opened (limit reached, or that authority has no per-project fetch) is
    named as unopened, never silently dropped to zero.

    `downloaders` overrides `_downloader_for` per state code (a dict of
    state_code -> fn(entry, dest_path)) and `ocr` overrides `_default_ocr` --
    both for offline testing. Never raises.
    """
    limitations = []
    projects = group_sweep.distinct_projects(rera_sweep) if rera_sweep else []

    if not projects:
        if rera_sweep is None or "projects" not in rera_sweep:
            limitations.append(
                "This check runs only after the group-wide RERA sweep has already opened the "
                "group's projects (--group-sweep / CHARTER_GROUP_SWEEP=1). That sweep was not "
                "enabled this pass, so there was nothing here to check."
            )
        else:
            limitations.append(
                "The group-wide RERA sweep found no projects for this group, so there was "
                "nothing here to check."
            )
        return {"projects_checked": [], "statements": [], "checked": 0, "total": 0,
                "limitations": limitations}

    ocr_fn = ocr or _default_ocr

    projects_checked = []
    statements = []
    checked = 0

    for project in projects:
        state = project.get("state")
        reg_no = project.get("reg_no")
        entities = project.get("matched_entities") or []
        row = {
            "state": state, "reg_no": reg_no, "entities": entities,
            "documents_seen": 0, "matches_found": 0,
        }

        if project.get("detail_status") != "opened":
            projects_checked.append(row)
            continue

        checked += 1
        documents = (project.get("detail") or {}).get("documents") or []
        row["documents_seen"] = len(documents)
        matches = [d for d in documents if _is_high_priority(d.get("label"))]
        matches = matches[:doc_limit_per_project]
        if not matches:
            projects_checked.append(row)
            continue

        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

        # Looked up once per project rather than once per document, but
        # deliberately AFTER the cache check inside the loop below decides
        # it is actually needed -- a fully-cached project must keep serving
        # its cached text even if the live downloader later becomes
        # unavailable (state removed, adapter broken). `None` here just
        # means "not looked up yet", not "unavailable".
        downloader = "not looked up"

        for entry in matches:
            identifier = entry.get("uid") or entry.get("url") or entry.get("label")
            key = _cache_key(identifier)
            cache_path = os.path.join(cache_dir, key + ".txt") if cache_dir else None

            text, cache_status = None, None
            if cache_path and os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    text = f.read()
                cache_status = "cached"
            else:
                if downloader == "not looked up":
                    # When a caller supplies `downloaders` at all (the
                    # offline-test seam), it is authoritative -- a state
                    # missing from it means "no downloader for this test",
                    # never a fall-through to the real network lookup. Only
                    # the production default (downloaders=None) uses
                    # _downloader_for.
                    downloader = (downloaders.get(state) if downloaders is not None
                                 else _downloader_for(state))
                if downloader is None:
                    limitations.append(
                        f"{state} document '{entry.get('label')}' on {reg_no} was not cached and "
                        f"this pass has no way to download {state} documents yet."
                    )
                    continue
                tmp_path = os.path.join(cache_dir or ".", f"_tmp_{key}")
                try:
                    result = downloader(entry, tmp_path) or {}
                    if result.get("status") != "downloaded":
                        limitations.append(
                            f"{state} document '{entry.get('label')}' on {reg_no} could not be "
                            f"downloaded this pass: {result.get('status')}"
                        )
                        continue
                    text = ocr_fn(result.get("saved_path") or tmp_path)
                    cache_status = "downloaded"
                    if cache_path:
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(text)
                except Exception as e:
                    limitations.append(
                        f"{state} document '{entry.get('label')}' on {reg_no} could not be read "
                        f"this pass: {type(e).__name__}: {e}"
                    )
                    continue
                finally:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except OSError:
                        pass

            row["matches_found"] += 1
            statements.append({
                "state": state, "reg_no": reg_no, "entities": entities,
                "label": entry.get("label") or "",
                "text_excerpt": (text or "").strip()[:2000],
                "cache_status": cache_status,
            })
        projects_checked.append(row)

    limitations.append(
        "Gujarat, Jharkhand, Haryana, West Bengal, Uttar Pradesh and Tamil Nadu projects expose "
        "a document list this pass can search; Karnataka, Maharashtra, Delhi and Telangana do "
        "not (by design, by CAPTCHA gate, or because the authority publishes no document list at "
        "all). A live-confirmed balance sheet, P&L or income-tax return label has so far been "
        "seen only from the first four -- Uttar Pradesh and Tamil Nadu are searched but have not "
        "yet produced a confirmed hit. A project from an unsearched state, or one this pass did "
        "not open, says nothing about that project's actual filings."
    )

    return {
        "projects_checked": projects_checked,
        "statements": statements,
        "checked": checked,
        "total": len(projects),
        "limitations": limitations,
    }


def coverage_sentence(result):
    """One sentence, denominator first, never the word "clean"."""
    result = result or {}
    total = result.get("total") or 0
    if not total:
        return "No group-wide RERA sweep projects were available to check for financial disclosures."
    checked = result.get("checked") or 0
    hits = len(result.get("statements") or [])
    sentence = (
        f"Financial disclosure documents were checked for {checked} of {total} group project(s) "
        f"already opened by the group-wide RERA sweep, finding {hits} matching "
        f"statement{'' if hits == 1 else 's'}."
    )
    if checked < total:
        sentence += f" The remaining {total - checked} project(s) were not opened by that sweep."
    return sentence

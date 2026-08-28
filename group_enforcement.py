"""
Group Enforcement & Defaulter Search -- opt-in via --group-enforcement.

Seven registers across four authorities (UP-RERA, HARERA, TNRERA and
Delhi-RERA), written and live-verified 2026-08-26 and never called from
anywhere until now (docs/PAN_INDIA_PROGRESS.md's "the 32 Unaudited cells
got audited" section names all seven). None of these is a promoter-
portfolio API: each publishes a defaulter, cancellation, penalty or
enforcement list and names the party in its own right, so this searches by
name the same way litigation_sweep.state_order_sweep already does for
K-RERA/MahaRERA/JHARERA's order registers -- and inherits that function's
central discipline: every hit is a CANDIDATE, never a fact, and every
register this pipeline does NOT search is named on the page, never
silently absent.

WHY A SEPARATE MODULE FROM litigation_sweep.py. Case law is a full-text
index searched by query; these are the regulator's OWN published
defaulter/enforcement tables, fetched whole and filtered locally -- a
different kind of source with different failure modes (an ASP.NET
postback, a PDF needing OCR, two states' tables carrying no name column at
all). Kept apart the way group_sweep.py, gst_group.py and
litigation_sweep.py already are: one module per check domain.

WHAT THIS DOES NOT COVER. MahaRERA, GujRERA, WBRERA, JHARERA and TG-RERA
publish no defaulter/enforcement register of this shape; K-RERA's own
penalty register is already covered by litigation_sweep.state_order_sweep,
not duplicated here. See NOT_ENFORCEMENT_SEARCHABLE -- an empty result
table here says nothing about any authority named in it.

THE DELHI REAT APPEAL INDEX IS THE EXPENSIVE ONE. Confirming a party name
on it costs a real OCR pass over up to 481 order PDFs
(states/adapter_delhi.py's build_appeal_party_index), cached to disk so a
second run across a different promoter does not re-pay the cost. It is a
national register, not per-project data, so the cache directory is shared
across every run -- never scoped to one promoter's output folder.

Offline-testable: every one of the seven underlying calls is overridable
via the `fetchers` dict, so nothing touches the network when a caller
supplies one.
"""

import os

import group_entities

STATUS_SEARCHED = "searched"

_NAME_MATCH_CAUTION = (
    "This is a name match on the authority's own published register, not "
    "confirmed proof of identity. It may be a different party of the same name."
)
_TN_BLOCK_CAUTION = (
    "TNRERA's penalty register publishes the promoter's name inside a raw, "
    "unparsed text block rather than its own column, so this is a substring "
    "match on that block, not a clean field match."
)
_TN_ENFORCEMENT_CAUTION = (
    "This is a substring match inside prose extracted from a native-text PDF "
    "(a party's name embedded in a sentence, not a clean name column), for a "
    "project TNRERA is enforcing against for never having registered at all."
)
_REAT_CAUTION = (
    "The party names on this register come from OCR of the order PDF's first "
    "page, not the register's own columns, so this is a candidate to confirm "
    "against the order itself."
)

# Authorities/registers this pass does not reach, established rather than
# assumed -- an empty result table must not read as clean for any of these.
NOT_ENFORCEMENT_SEARCHABLE = (
    "MahaRERA -- publishes no state-wide defaulter/enforcement register "
    "distinct from its per-project orders search",
    "GujRERA -- no defaulter or enforcement register was found on the portal",
    "WBRERA -- no defaulter or enforcement register was found on the portal",
    "JHARERA -- no defaulter or enforcement register was found on the portal",
    "TG-RERA -- publishes no promoter-keyed register of any kind",
    "K-RERA -- its penalty register is searched by litigation_sweep's own "
    "state order sweep, not duplicated here",
    "every state with no adapter yet",
)

DEFAULT_APPEAL_CACHE_DIR = os.path.join("output", "_cache", "delhi_reat_ocr")


def _subjects(graph, directors=None):
    """Who to search for: every confirmed entity, then every director.

    Duplicated from litigation_sweep._subjects rather than imported: this
    repo keeps one small helper per check-domain module rather than sharing
    a private one across them.
    """
    subjects, seen = [], set()

    def _add(name):
        name = str(name or "").strip()
        if not name:
            return
        key = group_entities.normalise(name)
        if key in seen:
            return
        seen.add(key)
        subjects.append(name)

    graph = graph or {}
    subject = graph.get("subject")
    if isinstance(subject, dict):
        _add(subject.get("name"))
    elif isinstance(subject, str):
        _add(subject)
    for entity in graph.get("confirmed") or []:
        _add((entity or {}).get("name"))
    for person in directors or []:
        _add(person if isinstance(person, str) else (person or {}).get("name"))
    return subjects


def _matches(needle, *haystacks):
    needle = " ".join(str(needle or "").split()).casefold()
    if not needle:
        return False
    return any(needle in " ".join(str(h or "").split()).casefold() for h in haystacks)


def _safe_fetch(fn, limitations, what):
    """Runs one whole-register fetch. A failure costs that register's rows,
    named in the limitations, never a raised exception -- one unreachable
    authority must not sink the other six."""
    try:
        return fn() or []
    except Exception as e:
        limitations.append(f"{what} could not be read this pass: {type(e).__name__}: {e}")
        return []


def _default_up_defaulters():
    from states import adapter_uttarpradesh

    return adapter_uttarpradesh.fetch_defaulters()


def _default_haryana_defaulters(bench):
    from states import adapter_haryana

    return adapter_haryana.fetch_defaulter_projects(bench)


def _default_tn_penalty(kind):
    from states import adapter_tamilnadu

    return adapter_tamilnadu.fetch_penalty_notices(kind=kind)


def _default_tn_enforcement_search(name):
    from states import adapter_tamilnadu

    return adapter_tamilnadu.search_enforcement_lists_by_name(name)


def _default_delhi_suomoto():
    from states import adapter_delhi

    return adapter_delhi.fetch_suo_moto_register()


def _default_delhi_execution():
    from states import adapter_delhi

    return adapter_delhi.fetch_execution_register()


def _default_delhi_appeal_index(appeal_ocr_limit, appeal_cache_dir, reporter):
    from states import adapter_delhi

    os.makedirs(appeal_cache_dir, exist_ok=True)
    return adapter_delhi.build_appeal_party_index(
        cache_dir=appeal_cache_dir, limit=appeal_ocr_limit, reporter=reporter
    )


def sweep(graph, directors=None, fetchers=None, appeal_ocr_limit=None,
          appeal_cache_dir=DEFAULT_APPEAL_CACHE_DIR, reporter=None):
    """Searches UP-RERA, HARERA, TNRERA and Delhi-RERA's defaulter,
    cancellation, penalty and enforcement registers for every group entity
    and director.

    Returns {"subjects": [...], "candidates": [...], "searched", "total",
    "limitations"}. Every subject appears with a hit count, including zero,
    so an omitted name is never mistaken for one the sweep never reached --
    unlike litigation_sweep.sweep, there is no query budget here: every
    register is fetched ONCE regardless of how many subjects there are, so
    the per-subject cost is a local filter, not a network call.

    `fetchers` overrides any of the underlying calls for testing -- keys:
    up_defaulters (), haryana_defaulters (bench), tn_penalty (kind),
    tn_enforcement_search (name), delhi_suomoto (), delhi_execution (),
    delhi_appeal_index () -> {"rows": [...], "coverage": {...}}. Passing all
    seven means nothing touches the network.
    """
    fetchers = fetchers or {}
    subject_names = _subjects(graph, directors)
    subjects = [{"name": name, "status": STATUS_SEARCHED} for name in subject_names]
    candidates = []
    limitations = []

    up_rows = _safe_fetch(
        fetchers.get("up_defaulters") or _default_up_defaulters,
        limitations, "UP-RERA's de-registered/defaulter project register",
    )

    haryana_rows = []
    haryana_fn = fetchers.get("haryana_defaulters") or _default_haryana_defaulters
    for bench, bench_name in (("1", "Panchkula"), ("2", "Gurugram")):
        rows = _safe_fetch(lambda b=bench: haryana_fn(b), limitations,
                            f"HARERA's {bench_name} cancelled/defaulter register")
        haryana_rows.extend(rows)

    tn_penalty_rows = []
    tn_penalty_fn = fetchers.get("tn_penalty") or _default_tn_penalty
    for kind in ("building", "layout"):
        rows = _safe_fetch(lambda k=kind: tn_penalty_fn(k), limitations,
                            f"TNRERA's {kind} penalty register")
        tn_penalty_rows.extend(rows)

    delhi_suomoto_rows = _safe_fetch(
        fetchers.get("delhi_suomoto") or _default_delhi_suomoto,
        limitations, "Delhi-RERA's suo-moto register",
    )
    delhi_execution_rows = _safe_fetch(
        fetchers.get("delhi_execution") or _default_delhi_execution,
        limitations, "Delhi-RERA's execution register",
    )

    appeal_fn = fetchers.get("delhi_appeal_index") or (
        lambda: _default_delhi_appeal_index(appeal_ocr_limit, appeal_cache_dir, reporter)
    )
    appeal_index = _safe_fetch(
        lambda: [appeal_fn()], limitations, "Delhi-RERA's REAT appeal register"
    )
    appeal_result = appeal_index[0] if appeal_index else {}
    appeal_rows = (appeal_result or {}).get("rows") or []
    appeal_coverage = (appeal_result or {}).get("coverage") or {}

    tn_enforcement_search = fetchers.get("tn_enforcement_search") or _default_tn_enforcement_search

    for subject in subjects:
        name = subject["name"]
        hits = []

        for row in up_rows:
            if _matches(name, row.get("promoter_name"), row.get("project_name")):
                hits.append({
                    "authority": "Uttar Pradesh (UP-RERA)",
                    "register": "De-registered / defaulter project register",
                    "searched_name": name,
                    "matched_text": row.get("promoter_name") or row.get("project_name") or "",
                    "detail": row.get("project_registration_no.") or "",
                    "caution": _NAME_MATCH_CAUTION,
                })

        for row in haryana_rows:
            if _matches(name, row.get("builder"), row.get("project_name")):
                hits.append({
                    "authority": "Haryana (HARERA)",
                    "register": "Cancelled / defaulter / suspended / abeyance projects",
                    "searched_name": name,
                    "matched_text": row.get("builder") or row.get("project_name") or "",
                    "detail": row.get("certificate_no") or "",
                    "caution": _NAME_MATCH_CAUTION,
                })

        for row in tn_penalty_rows:
            if _matches(name, row.get("promoter_block"), row.get("project_block")):
                hits.append({
                    "authority": "Tamil Nadu (TNRERA)",
                    "register": "Penalty register",
                    "searched_name": name,
                    "matched_text": row.get("promoter_block") or "",
                    "detail": (f"Rs {row['penalty_amount']}" if row.get("penalty_amount") else ""),
                    "caution": _TN_BLOCK_CAUTION,
                })

        try:
            for row in tn_enforcement_search(name) or []:
                hits.append({
                    "authority": "Tamil Nadu (TNRERA)",
                    "register": row.get("source") or "Unregistered-project enforcement list",
                    "searched_name": name,
                    "matched_text": row.get("party_detail") or "",
                    "detail": row.get("site_address") or "",
                    "caution": _TN_ENFORCEMENT_CAUTION,
                })
        except Exception as e:
            limitations.append(
                f"TNRERA's enforcement PDFs could not be searched for {name} this "
                f"pass: {type(e).__name__}: {e}"
            )

        for row in delhi_suomoto_rows:
            if _matches(name, row.get("respondent_name")):
                hits.append({
                    "authority": "Delhi (Delhi-RERA)",
                    "register": "Suo-moto register",
                    "searched_name": name,
                    "matched_text": row.get("respondent_name") or "",
                    "detail": row.get("case_no") or "",
                    "caution": _NAME_MATCH_CAUTION,
                })

        for row in delhi_execution_rows:
            if _matches(name, row.get("judgement_debtor")):
                hits.append({
                    "authority": "Delhi (Delhi-RERA)",
                    "register": "Execution register",
                    "searched_name": name,
                    "matched_text": row.get("judgement_debtor") or "",
                    "detail": row.get("execution_no") or "",
                    "caution": _NAME_MATCH_CAUTION,
                })

        for row in appeal_rows:
            if _matches(name, row.get("appellant"), row.get("respondent")):
                hits.append({
                    "authority": "Delhi (REAT)",
                    "register": "Appellate Tribunal order register",
                    "searched_name": name,
                    "matched_text": row.get("appellant") or row.get("respondent") or "",
                    "detail": row.get("appeal_no") or "",
                    "caution": _REAT_CAUTION,
                })

        subject["hit_count"] = len(hits)
        candidates.extend(hits)

    if appeal_coverage:
        read = appeal_coverage.get("distinct_pdfs") or 0
        total_rows = appeal_coverage.get("total_rows") or 0
        unparseable = appeal_coverage.get("pdfs_unparseable") or 0
        limitations.append(
            f"The Delhi REAT appeal register names no party in its own columns; parties "
            f"were read by OCR off {read} of its own order PDFs this pass "
            f"({unparseable} could not be read). PDFs beyond that count are not "
            f"represented above, not confirmed clean."
        )

    limitations.append(
        "Registers NOT searched by this pass: " + "; ".join(NOT_ENFORCEMENT_SEARCHABLE)
        + ". An empty result above says nothing about any authority named here."
    )

    return {
        "subjects": subjects,
        "candidates": candidates,
        "searched": len(subjects),
        "total": len(subjects),
        "limitations": limitations,
    }


def coverage_sentence(result):
    """One sentence, denominator first, never the word "clean"."""
    result = result or {}
    total = result.get("total") or 0
    if not total:
        return "No group entities or directors were available to search for enforcement records."
    searched = result.get("searched") or 0
    hits = len(result.get("candidates") or [])
    sentence = (
        f"UP-RERA, HARERA, TNRERA and Delhi-RERA's defaulter, cancellation, penalty and "
        f"enforcement registers were searched for {searched} of {total} group "
        f"{'name' if total == 1 else 'names'} (entities and directors), returning {hits} "
        f"candidate {'match' if hits == 1 else 'matches'}."
    )
    if searched < total:
        sentence += f" The remaining {total - searched} were not searched."
    return sentence

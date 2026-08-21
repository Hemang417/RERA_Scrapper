"""
GST filing compliance across the GROUP, not just the subject promoter.

WHY THIS IS MOSTLY A COVERAGE PROBLEM, not a scraping one.

GST is keyed on PAN. The entity graph is keyed on CIN and name
(group_entities.build_entity_graph returns {name, cin, basis} per company),
and no MCA mirror this repo uses publishes a company's PAN. So for most
group entities there is no way to reach the GST portal at all: the join key
simply is not public.

That makes the honest output of this module a COVERAGE STATEMENT first and
a set of findings second. An entity with no PAN on record is reported as
unchecked, never as compliant. The failure this guards against is the one
that keeps recurring in this codebase: a check that could not run being
read downstream as a check that found nothing. On a Charter, "no GST issues
across the group" is a materially different claim from "one of fifteen
entities could be checked at all", and only the second is ever true here.

WHERE A PAN CAN LEGITIMATELY COME FROM. Four sources, each recorded on the
entity so a reader can weigh it. None of them is a guess:

  * PAN_SOURCE_FILED_CARD    -- read off a PAN card in a RERA document
                                library (promoter_identity.py). Subject
                                promoter only, and only when its status is
                                "verified".
  * PAN_SOURCE_RERA_FILING   -- named in another authority's filing. This
                                is how Pranami Builders' AAECP0371L was
                                obtained: JHARERA names the contractor's
                                PAN outright.
  * PAN_SOURCE_FROM_GSTIN    -- characters 3-12 of a GSTIN already known.
                                Arithmetic, not inference.
  * PAN_SOURCE_SUPPLIED      -- handed in by a human who has the document.

A PAN is NEVER derived from a name, a CIN, or a similar company's PAN.

WHY IT IS OPT-IN AND BOUNDED. Every entity checked costs a human at least
two fresh CAPTCHA solves -- one for the PAN search, then one per GSTIN
discovered under it (see gst_intake.run_intake). A sixty-five entity group
would be days of manual work, so the default limit is deliberately small
and anything past it is reported as skipped rather than quietly dropped.

Offline-testable by the repo's usual seam: pass `intake=` and nothing
touches a browser.
"""

import datetime
import json
import os
import re

import gst_compliance
import group_entities

STATUS_CHECKED = "checked"
STATUS_NO_PAN = "no PAN on record"
STATUS_LOOKUP_FAILED = "lookup could not complete"
STATUS_BUDGET_EXHAUSTED = "not checked (entity limit reached)"

PAN_SOURCE_FILED_CARD = "PAN card filed with a RERA authority"
PAN_SOURCE_RERA_FILING = "named in a RERA filing"
PAN_SOURCE_FROM_GSTIN = "derived from a known GSTIN"
PAN_SOURCE_SUPPLIED = "supplied by hand"

# Two human CAPTCHA solves each, minimum. This is a people budget, not a
# rate limit.
DEFAULT_ENTITY_LIMIT = 5


def _entity_rows(graph):
    """Every confirmed entity in the graph, plus the subject, de-duplicated
    by normalised name. Proposed-but-unconfirmed entities are deliberately
    excluded: this module reports on the group, and a name-only match is
    not established to be in it (see group_entities' own docstring)."""
    rows, seen = [], set()
    graph = graph or {}
    subject = graph.get("subject")
    candidates = []
    if isinstance(subject, dict) and subject.get("name"):
        candidates.append(subject)
    elif isinstance(subject, str) and subject.strip():
        candidates.append({"name": subject})
    candidates.extend(graph.get("confirmed") or [])

    for entity in candidates:
        name = str((entity or {}).get("name") or "").strip()
        if not name:
            continue
        key = group_entities.normalise(name)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "cin": (entity or {}).get("cin") or ""})
    return rows


def known_pans(identity_result=None, rera_pans=None, gstins=None, supplied=None):
    """Map of normalised entity name -> {"pan", "source"}.

    Every argument is optional and every one is a DIFFERENT provenance, so
    the resulting source label is meaningful rather than decorative:

      identity_result -- promoter_identity.extract_promoter_pan's dict.
                         Used ONLY when status == "verified"; an
                         unverified_candidate is exactly the thing that
                         must not silently become a lookup key.
      rera_pans       -- {entity name: PAN} read from other authorities'
                         filings.
      gstins          -- {entity name: GSTIN}; the PAN is extracted
                         arithmetically.
      supplied        -- {entity name: PAN} handed in by a human.

    Later sources never overwrite earlier ones, so the strongest provenance
    wins. Anything that is not a validly-formatted PAN is dropped rather
    than carried, because a malformed PAN costs a human a CAPTCHA solve to
    discover (gst_intake checks before opening a browser for the same
    reason).
    """
    found = {}

    def _add(name, pan, source):
        name = str(name or "").strip()
        pan = str(pan or "").strip().upper()
        if not name or not gst_compliance.validate_pan(pan):
            return
        key = group_entities.normalise(name)
        found.setdefault(key, {"pan": pan, "source": source, "name": name})

    identity_result = identity_result or {}
    if identity_result.get("status") == "verified":
        _add(identity_result.get("promoter_name") or identity_result.get("card_name"),
             identity_result.get("pan"), PAN_SOURCE_FILED_CARD)

    for name, pan in (rera_pans or {}).items():
        _add(name, pan, PAN_SOURCE_RERA_FILING)

    for name, gstin in (gstins or {}).items():
        if gst_compliance.validate_gstin(str(gstin or "").strip().upper()):
            _add(name, gst_compliance.extract_pan_from_gstin(str(gstin).strip().upper()),
                 PAN_SOURCE_FROM_GSTIN)

    for name, pan in (supplied or {}).items():
        _add(name, pan, PAN_SOURCE_SUPPLIED)

    return found


def _slug(name):
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(name or "")).strip("_")
    return (cleaned[:60] or "entity").lower()


def default_intake(reg_no, output_dir):
    """The real, browser-driven lookup, as an injectable callable.

    Each entity gets its own output directory, so one entity's
    gst_filing_input.json can never be mistaken for the subject's -- the
    subject's is what run_gst_compliance_check reads, and overwriting it
    with a group member's filings would silently rewrite the Charter's
    headline GST metric.
    """
    def intake(pan, entity_name):
        import gst_intake

        key = os.path.join(reg_no, "group_gst", _slug(entity_name))
        result = gst_intake.run_intake(pan, key, output_dir)
        with open(result["input_path"], "r", encoding="utf-8") as handle:
            written = json.load(handle)
        records = []
        for record in written.get("records", []):
            records.append({
                "form": record["form"],
                "period_start": datetime.datetime.strptime(record["period_start"], "%Y-%m-%d").date(),
                "period_end": datetime.datetime.strptime(record["period_end"], "%Y-%m-%d").date(),
                "filing_date": (datetime.datetime.strptime(record["filing_date"], "%Y-%m-%d").date()
                                if record.get("filing_date") else None),
            })
        return {
            "gstin": written.get("gstin"),
            "period_count": len(records),
            "summary": gst_compliance.summarize_filing_pattern(
                written.get("gstin"), records, as_of=datetime.date.today()),
        }
    return intake


def sweep(graph, pans=None, intake=None, limit=DEFAULT_ENTITY_LIMIT, reporter=None):
    """GST standing for every group entity a PAN is actually held for.

    Returns {"entities": [...], "checked", "total", "without_pan",
    "limitations"}. Every entity in the graph appears in `entities` with a
    status, including the ones that could not be checked -- an omitted
    entity would read as one with nothing to report.

    `intake(pan, entity_name)` is the seam: pass one and nothing opens a
    browser. Its failure is caught per entity, so one portal outage costs
    one row rather than the sweep.
    """
    rows = _entity_rows(graph)
    pans = pans if pans is not None else {}
    limitations = []
    checked = 0

    for row in rows:
        key = group_entities.normalise(row["name"])
        held = pans.get(key)
        if not held:
            row["status"] = STATUS_NO_PAN
            row["pan"] = ""
            row["pan_source"] = ""
            continue

        row["pan"] = held["pan"]
        row["pan_source"] = held["source"]

        if checked >= limit:
            row["status"] = STATUS_BUDGET_EXHAUSTED
            continue
        if intake is None:
            row["status"] = STATUS_LOOKUP_FAILED
            row["note"] = ("No GST lookup was attempted: this pass ran without a portal "
                           "session, which needs a human CAPTCHA solve per entity.")
            continue

        if reporter:
            reporter.info(f"GST: checking {row['name']} (PAN {held['pan']})")
        try:
            result = intake(held["pan"], row["name"]) or {}
            row["status"] = STATUS_CHECKED
            row["gstin"] = result.get("gstin") or ""
            row["period_count"] = result.get("period_count") or 0
            row["summary"] = result.get("summary") or {}
            checked += 1
        except Exception as e:
            row["status"] = STATUS_LOOKUP_FAILED
            row["note"] = f"{type(e).__name__}: {e}"

    without_pan = sum(1 for r in rows if r["status"] == STATUS_NO_PAN)
    skipped = sum(1 for r in rows if r["status"] == STATUS_BUDGET_EXHAUSTED)
    if skipped:
        limitations.append(
            f"{skipped} entity/entities held a PAN but were not checked: the limit of "
            f"{limit} was reached. Each check costs a human at least two CAPTCHA solves."
        )
    if without_pan:
        limitations.append(
            f"{without_pan} of {len(rows)} group entities have no PAN on record, so their "
            "GST standing could not be looked up at all. GST is keyed on PAN and no public "
            "MCA source publishes it; this is a limit of the source, not a finding about "
            "those entities."
        )
    return {"entities": rows, "checked": checked, "total": len(rows),
            "without_pan": without_pan, "limitations": limitations}


def coverage_sentence(result):
    """One sentence stating what was and was not checked.

    Deliberately leads with the denominator. A reader who sees only
    findings will read silence as compliance, which across a group this
    thinly covered would be wrong nearly every time.
    """
    result = result or {}
    total = result.get("total") or 0
    checked = result.get("checked") or 0
    if not total:
        return "No group entities were available to check for GST compliance."
    sentence = (f"GST filing history was obtained for {checked} of {total} group "
                f"{'entity' if total == 1 else 'entities'}.")
    if checked < total:
        sentence += (f" The remaining {total - checked} could not be checked and are not "
                     "reported as compliant or non-compliant.")
    return sentence


def entities_with_findings(result):
    """Only the checked entities, so a caller cannot accidentally render an
    unchecked one as if it had a clean filing record."""
    return [row for row in (result or {}).get("entities", [])
            if row.get("status") == STATUS_CHECKED]

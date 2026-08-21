"""
Case-law search across the group -- every entity and every director.

WHAT THIS IS, AND WHAT IT IS EMPHATICALLY NOT. It searches Indian Kanoon
by NAME and returns CANDIDATES. It does not establish that a case belongs
to this group, and nothing here may be rendered as though it did.

The reason is a live example, found the first time this ran. Searching
"Pranami Builders" -- a Ranchi company, CIN U51909JH1995PTC013805 --
returns "Pranami Builders , Ahmedabad vs Department Of Income Tax", plus
an unrelated Indore income-tax matter that merely shares tokens. Reporting
either as this promoter's litigation would invent a finding out of a name
collision. So every hit carries:

  * whether the searched name is in the CASE TITLE or only in the body
    text, because a body mention is far weaker,
  * the place named in the title when the site's "X , Place vs Y"
    convention gives one, checked against the group's known footprint,
  * and, for a person, a standing caution: Indian personal names repeat
    enormously, and a director search is the highest false-positive query
    this pipeline makes.

THE ABSENCE SIDE IS THE MORE DANGEROUS HALF. Indian Kanoon indexes
reported judgments from the higher courts and several tribunals. District
courts, consumer fora and much of NCLT/NCLAT are covered patchily or not
at all, and RERA authorities' own orders are not there. So a nil result
means "nothing in this index", never "no litigation" -- the distinction
`coverage_sentence` and NOT_RELIABLY_INDEXED exist to keep. A clean
Charter paragraph asserting no disputes, drawn from a source that would
not have carried them anyway, is exactly the false clean record this
codebase keeps having to guard against.

Offline-testable: pass `searcher=` and nothing touches the network.
"""

import re
import time
import urllib.parse
import urllib.request

import group_entities

STATUS_SEARCHED = "searched"
STATUS_UNREACHABLE = "search could not run"
STATUS_BUDGET_EXHAUSTED = "not searched (query limit reached)"

MATCH_TITLE = "name in the case title"
MATCH_BODY = "mentioned in the text only"

SUBJECT_ENTITY = "entity"
SUBJECT_PERSON = "person"

# What open case-law search does NOT reliably carry. Named in the document
# so a nil result is read for what it is.
NOT_RELIABLY_INDEXED = (
    "consumer fora (NCDRC and the state and district commissions)",
    "NCLT and NCLAT filings that did not reach a reported order",
    "the RERA authorities' own orders and complaint registers",
    "district and civil courts",
    "arbitration, which is private and leaves no public record",
)

_SEARCH_URL = "https://indiankanoon.org/search/?formInput="
_USER_AGENT = "Mozilla/5.0 (compatible; diligence-research/1.0)"

# The site titles results "Company Name , Place vs Other Party on 2 June,
# 2016". The place, when present, is the single cheapest discriminator
# between a group entity and a same-name stranger.
_TITLE_PLACE_RE = re.compile(r",\s*([A-Z][A-Za-z .]{2,30}?)\s+(?:vs|v\.|versus)\b", re.I)

DEFAULT_QUERY_LIMIT = 12
DEFAULT_DELAY_SECONDS = 2.0


def _fetch(query, timeout=30):
    request = urllib.request.Request(
        _SEARCH_URL + urllib.parse.quote(query), headers={"User-Agent": _USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def parse_results(html, query):
    """Result rows from a search page.

    A page that parses to zero rows is a genuine nil, not a failure: a
    control query for a nonsense company returns exactly this, while an
    unreachable site raises instead. Keeping those two apart is the whole
    reason the caller can report "searched, nothing found" honestly.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    normalised_query = group_entities.normalise(query)
    results = []
    for node in soup.select(".result_title"):
        title = " ".join(node.get_text(" ", strip=True).split())
        if not title:
            continue
        link = node.find("a") or node.find_parent("a")
        href = (link.get("href") if link else "") or ""
        if href.startswith("/"):
            href = "https://indiankanoon.org" + href

        in_title = normalised_query in group_entities.normalise(title)
        place = ""
        found_place = _TITLE_PLACE_RE.search(title)
        if found_place:
            place = found_place.group(1).strip()
        results.append({
            "title": title,
            "url": href,
            "place": place,
            "match": MATCH_TITLE if in_title else MATCH_BODY,
        })
    return results


def _place_caution(place, known_places):
    """A caution, never an exclusion.

    "Pranami Builders , Ahmedabad" against a Ranchi group is the case that
    made this necessary. It does not prove the case belongs to someone
    else -- a company litigates where the cause of action arose -- so the
    wording says the place is not in the known footprint, which is all
    that has actually been established.
    """
    if not place or not known_places:
        return ""
    haystack = " ".join(str(p) for p in known_places).lower()
    if place.lower() in haystack:
        return ""
    return (f"The case title names {place}, which does not appear in this group's "
            f"known footprint. It may be a different party of the same name.")


def _subjects(graph, directors=None):
    """Who to search for: every confirmed entity, then every director.

    Entities first, deliberately. The query budget is small and a company
    name is a far better discriminator than a personal name, so the
    budget should be spent on entities before it reaches the names that
    generate the most false positives.
    """
    subjects, seen = [], set()

    def _add(name, kind):
        name = str(name or "").strip()
        if not name:
            return
        key = (kind, group_entities.normalise(name))
        if key in seen:
            return
        seen.add(key)
        subjects.append({"name": name, "kind": kind})

    graph = graph or {}
    subject = graph.get("subject")
    if isinstance(subject, dict):
        _add(subject.get("name"), SUBJECT_ENTITY)
    elif isinstance(subject, str):
        _add(subject, SUBJECT_ENTITY)
    for entity in graph.get("confirmed") or []:
        _add((entity or {}).get("name"), SUBJECT_ENTITY)
    for person in directors or []:
        _add(person if isinstance(person, str) else (person or {}).get("name"), SUBJECT_PERSON)
    return subjects


def sweep(graph, directors=None, known_places=None, searcher=None,
          limit=DEFAULT_QUERY_LIMIT, delay=DEFAULT_DELAY_SECONDS, reporter=None):
    """Search case law for every group entity and director, within a budget.

    Returns {"subjects": [...], "candidates": [...], "searched", "total",
    "limitations"}. Every subject appears with a status, including the ones
    the budget never reached -- an omitted subject reads as one that was
    searched and came back clean.

    `searcher(name) -> list of result dicts` is the seam; pass one and
    nothing touches the network.
    """
    subjects = _subjects(graph, directors)
    fetcher = searcher or (lambda name: parse_results(_fetch(name), name))
    candidates, limitations = [], []
    searched = 0

    for index, subject in enumerate(subjects):
        if searched >= limit:
            subject["status"] = STATUS_BUDGET_EXHAUSTED
            continue
        if reporter:
            reporter.info(f"Case law: searching {subject['name']}")
        try:
            if searcher is None and searched and delay:
                time.sleep(delay)  # the site is free and public; do not hammer it
            rows = fetcher(subject["name"]) or []
        except Exception as e:
            subject["status"] = STATUS_UNREACHABLE
            subject["note"] = f"{type(e).__name__}: {e}"
            continue

        searched += 1
        subject["status"] = STATUS_SEARCHED
        subject["hit_count"] = len(rows)
        for row in rows:
            row = dict(row)
            row["searched_name"] = subject["name"]
            row["subject_kind"] = subject["kind"]
            row["caution"] = _place_caution(row.get("place"), known_places)
            if subject["kind"] == SUBJECT_PERSON and not row["caution"]:
                row["caution"] = (
                    "Searched on a personal name. Indian personal names repeat widely, "
                    "so this is a candidate to confirm against the individual's own "
                    "record, not an established matter."
                )
            candidates.append(row)

    skipped = sum(1 for s in subjects if s["status"] == STATUS_BUDGET_EXHAUSTED)
    if skipped:
        limitations.append(
            f"{skipped} of {len(subjects)} names were not searched: the query limit of "
            f"{limit} was reached. They are neither clear nor implicated."
        )
    unreachable = sum(1 for s in subjects if s["status"] == STATUS_UNREACHABLE)
    if unreachable:
        limitations.append(
            f"{unreachable} search(es) could not run this pass, so those names carry no "
            "result either way."
        )
    limitations.append(
        "Open case-law search does not reliably index " + "; ".join(NOT_RELIABLY_INDEXED)
        + ". A nil result here means nothing was found in that index, not that no "
        "proceedings exist."
    )
    return {"subjects": subjects, "candidates": candidates,
            "searched": searched, "total": len(subjects), "limitations": limitations}


# Which RERA authorities' own order registers this pipeline can search by
# promoter name, and which it cannot. The second list is the important one:
# a group with orders against it in Maharashtra would not show up here.
ORDERS_SEARCHABLE = ("Karnataka (K-RERA)",)

# Why each of the others is not, established by probing them on 2026-08-21
# rather than assumed. These are source limits, not findings about any
# promoter, and the section says so.
ORDERS_NOT_SEARCHABLE = (
    "MahaRERA -- its Orders/Judgements search DOES accept a respondent (promoter) "
    "name, but the portal answered every attempt with its empty BigPipe shell, so "
    "no search could be performed at all",
    "WBRERA -- publishes 4,881 authority orders, but keyed only by complaint "
    "number, with no promoter or party named in any column",
    "GujRERA and JHARERA -- single-page applications whose order pages are not "
    "reachable without executing their JavaScript",
    "TG-RERA -- publishes no promoter-keyed order register",
    "every state with no adapter yet",
)


def state_order_sweep(graph, searcher=None, register_coverage=None):
    """RERA authorities' OWN order registers, searched by promoter name.

    Separate from the case-law sweep because it is a different kind of
    source: these are the regulator's orders against the promoter, which
    open case-law indexes do not carry at all.

    Only Karnataka is wired. K-RERA ships its entire order register to the
    browser in one request and filters it client-side -- its own POST does
    NOT filter server-side, and a firm name and a nonsense string return
    byte-identical pages apart from the visitor counter, so wiring that up
    as a search would have reported "no orders" for every promoter ever
    queried. Validated with a control: 111 entries for a known Karnataka
    developer, 0 for a nonsense name.
    """
    subjects = [s for s in _subjects(graph) if s["kind"] == SUBJECT_ENTITY]
    if searcher is None:
        from states import adapter_karnataka

        searcher = adapter_karnataka.search_all_orders_by_promoter

    entries, limitations = [], []
    searched = 0
    for subject in subjects:
        try:
            rows = searcher(subject["name"]) or []
        except Exception as e:
            limitations.append(
                f"The Karnataka order register could not be searched for "
                f"{subject['name']} this pass: {type(e).__name__}: {e}"
            )
            continue
        searched += 1
        for row in rows:
            entries.append({
                "authority": "Karnataka (K-RERA)",
                "register": row.get("register") or "Order search index",
                "searched_name": subject["name"],
                "application_no": row.get("complaint_no") or row.get("ack_no") or "",
                "order_date": row.get("order_date") or "",
                "project_name": row.get("project_name") or "",
                "promoter_name": row.get("promoter_name") or "",
                "detail": row.get("detail") or "",
                "penalty_amount": row.get("penalty_amount") or "",
            })
    # WHICH OF K-RERA'S OWN REGISTERS ACTUALLY LOADED. Its authority-orders
    # page is 10.4 MB and has been seen to arrive truncated, dropping the
    # PENALTY table entirely -- a promoter with penalties would then show
    # none. A register that did not load is named, never counted as empty.
    if register_coverage is None and searcher is None:
        try:
            from states import adapter_karnataka

            register_coverage = adapter_karnataka.order_register_coverage()
        except Exception:
            register_coverage = None
    missing = (register_coverage or {}).get("missing") or []
    if missing:
        limitations.append(
            "These K-RERA registers did not load this pass and were NOT searched: "
            + "; ".join(missing) + ". Their absence from the table above means "
            "nothing was read, not that nothing is recorded."
        )

    limitations.append(
        "Only these RERA authorities' own order registers were searched by promoter "
        "name: " + "; ".join(ORDERS_SEARCHABLE) + ". NOT searched -- " 
        + " | ".join(ORDERS_NOT_SEARCHABLE) + ". An empty result says nothing about "
        "orders made by any authority in that list."
    )
    return {"entries": entries, "searched": searched, "total": len(subjects),
            "register_coverage": register_coverage or {},
            "limitations": limitations}


def coverage_sentence(result):
    """One sentence, denominator first, and never the words "clean record"."""
    result = result or {}
    total = result.get("total") or 0
    searched = result.get("searched") or 0
    if not total:
        return "No group entities or directors were available to search for case law."
    hits = len(result.get("candidates") or [])
    sentence = (f"Open case-law search was run for {searched} of {total} group "
                f"{'name' if total == 1 else 'names'} (entities and directors), "
                f"returning {hits} candidate {'match' if hits == 1 else 'matches'}.")
    if searched < total:
        sentence += f" The remaining {total - searched} were not searched."
    return sentence


def title_matches(result):
    """Candidates whose title carries the searched name.

    The weaker body-only matches are kept in the result for a reader who
    wants them, but this is what a summary should count: a body mention on
    a full-text index is very often an unrelated judgment that happens to
    cite a similar string.
    """
    return [row for row in (result or {}).get("candidates", [])
            if row.get("match") == MATCH_TITLE]

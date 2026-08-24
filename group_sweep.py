"""
Sweeps a promoter's whole corporate group across every RERA authority this
pipeline can actually ask.

THE QUESTION THIS ANSWERS. A RERA registration names ONE state. A promoter
read from one register looks smaller and cleaner than they are: the worked
case is a Mumbai project whose promoter holds a single MahaRERA registration
and a completed Rs 128 crore mall in Ranchi, declared to MahaRERA's own
regulator and invisible to any Maharashtra-only view.

THE THING THIS FILE IS MOSTLY ABOUT IS COVERAGE, NOT SEARCH. Searching is
easy. The danger is what a reader concludes from a short result list.

    "No projects found in Gujarat" and "Gujarat was never searched, because
    GujRERA publishes no promoter-to-projects link at all" look identical in
    a finished document unless the document says which one happened.

The second is true for Gujarat, West Bengal and Telangana today, and "never
searched" is true for the roughly twenty-four states with no adapter. So
every state this sweep knows about comes back with an explicit STATUS, and
the summary states plainly how many authorities were actually queried. A
result set is never presented as a national picture.

WHAT A HIT MEANS, AND WHAT IT DOES NOT. Every portal that can answer this
question answers it by NAME, because no RERA authority publishes a CIN or a
DIN to join on (see promoter_identity for the one identifier that is
recoverable, and where it hides). A name match is therefore a CANDIDATE
project, never a confirmed one -- the same rule group_entities.py enforces
for corporate affiliates, applied to registrations. Nothing here promotes a
hit to confirmed; that needs a second signal a human or a later pass
supplies.

The searcher is injected, defaulting to the real per-state one, matching the
pattern company_charter.run_finding_research(facts, researcher=None) and
group_entities.build_entity_graph(proposer=None) already use -- so the whole
sweep is testable with no portal.

Run directly:  python group_sweep.py "Pranami Builders" "Pranami Estates"
"""

import importlib

import config
import promoter_identity
import states

# How many entity-state searches a single sweep may make. N entities times M
# states is combinatorial, and Maharashtra's promoter search drives a
# headless browser per call. PROMOTER_PROJECT_LIMIT already exists for
# exactly this kind of bound; reused rather than inventing a second number.
DEFAULT_SEARCH_LIMIT = getattr(config, "PROMOTER_PROJECT_LIMIT", 25)

# Why a registered state cannot be swept, in the reader's language. Keyed by
# state code so a new adapter that gains a promoter search simply stops
# appearing here.
_CANNOT_SEARCH = {
    "GJ": ("GujRERA publishes no promoter-to-projects link at all: searching a known promoter "
           "returns no projects, and the per-promoter project list comes back empty. Gujarat "
           "was therefore not searched, and no conclusion about this group's Gujarat projects "
           "can be drawn from this sweep."),
    "WB": ("WBRERA publishes no promoter search, and its public register does not name the "
           "promoter, so a promoter's projects cannot be found without opening every project "
           "page in the state. West Bengal was not searched."),
    "TG": ("TG-RERA gates its own search behind a CAPTCHA that needs a person at a browser, so "
           "it cannot be searched unattended. Telangana was not searched."),
}

STATUS_SEARCHED = "searched"
STATUS_NO_PROMOTER_SEARCH = "not searchable by promoter"
STATUS_NO_ADAPTER = "no adapter"
STATUS_UNREACHABLE = "portal unreachable"
STATUS_BUDGET_EXHAUSTED = "not searched (search limit reached)"


def _searcher_for(code):
    """The state's promoter search, or None if it has no adapter or the
    adapter cannot answer this question."""
    module_path = states._ADAPTER_MODULES.get(code)
    if not module_path:
        return None
    try:
        module = importlib.import_module(module_path)
    except Exception:
        return None
    return getattr(module, "search_promoter_projects", None)


def searchable_states():
    """State codes whose adapter can answer promoter -> projects today."""
    return sorted(code for code in states.PROFILES if _searcher_for(code) is not None)


def _coverage_row(code, status, reason=None, **extra):
    profile = states.PROFILES.get(code)
    row = {
        "state": code,
        "authority": profile.rera_acronym if profile else None,
        "state_name": profile.state_name if profile else code,
        "status": status,
    }
    if reason:
        row["reason"] = reason
    row.update(extra)
    return row


def sweep(entity_names, state_codes=None, searcher=None, reporter=None,
          search_limit=DEFAULT_SEARCH_LIMIT):
    """Search every given entity name against every state that can answer.

    `entity_names` should come from group_entities.entity_names_for_sweep,
    which already excludes address-only links -- a registered-office service
    provider hosts dozens of unrelated companies, and folding a co-tenant's
    projects into this promoter's track record would be a misattribution.

    `state_codes` defaults to every registered state, so states that CANNOT
    be searched still appear in the coverage report. Passing a subset is for
    tests and for a caller that has already narrowed by evidence.

    THE ENTITY IS THE OUTER LOOP, THE STATE THE INNER ONE, and that ordering
    is load-bearing. With the state outer and a single global budget, the
    first authority alphabetically consumed the entire allowance and every
    later one was reported as "searched, 0 projects" having run ZERO
    queries. Confirmed live: a 38-entity sweep spent all 25 searches on
    JHARERA, then declared K-RERA and MahaRERA clean -- while MahaRERA's
    search, run directly, returns the subject's own project. A fabricated
    clean record produced by the budget, in the module written to stop
    exactly that. Interleaving spends the budget evenly, and a state that
    genuinely never got a query now says so.

    Returns {entities, coverage, projects, states_searched, states_total,
    truncated, limitations}. `projects` rows are CANDIDATES.
    """
    names = [n for n in dict.fromkeys(entity_names or []) if n and n.strip()]
    codes = list(state_codes) if state_codes is not None else sorted(states.PROFILES)

    coverage, searchable = [], []
    for code in codes:
        profile = states.PROFILES.get(code)
        if profile is None:
            coverage.append(_coverage_row(code, STATUS_NO_ADAPTER,
                                          "This state is not registered with the pipeline."))
            continue
        # Capability is decided by the ADAPTER, never by the injected
        # searcher. Letting an injection stand in for a state that cannot be
        # searched would report GujRERA and WBRERA as "searched" -- precisely
        # the false-coverage claim this module exists to prevent, and it
        # would have made every test here pass while masking it.
        real = _searcher_for(code)
        if real is None:
            coverage.append(_coverage_row(
                code, STATUS_NO_PROMOTER_SEARCH,
                _CANNOT_SEARCH.get(code, f"{profile.rera_acronym} cannot be searched by promoter "
                                         f"through this pipeline, so it was not searched."),
            ))
            continue
        searchable.append((code, profile, searcher if searcher is not None else real))

    per_state = {code: {"searched": 0, "matched": [], "projects": [], "failed": None}
                 for code, _, _ in searchable}
    projects, searches, truncated = [], 0, False

    for name in names:
        if truncated:
            break
        for code, profile, fn in searchable:
            if per_state[code]["failed"]:
                continue
            if searches >= search_limit:
                truncated = True
                break
            searches += 1
            per_state[code]["searched"] += 1
            try:
                hits = fn(name, reporter=reporter) or []
            except Exception as e:
                # One state's outage must not sink the sweep, and must not
                # be reported as "no projects there".
                per_state[code]["failed"] = type(e).__name__
                continue
            for hit in hits:
                row = dict(hit)
                row["state"] = code
                row["authority"] = profile.rera_acronym
                row["matched_entity"] = name
                row["confirmation"] = "name match only, not confirmed"
                projects.append(row)
                per_state[code]["projects"].append(row)
                per_state[code]["matched"].append(name)

    for code, profile, _ in searchable:
        state = per_state[code]
        if state["failed"]:
            coverage.append(_coverage_row(
                code, STATUS_UNREACHABLE,
                f"{profile.rera_acronym} could not be reached during this sweep "
                f"({state['failed']}), so its register was not searched. This is not evidence "
                f"the group has no projects there.",
            ))
        elif state["searched"] == 0:
            coverage.append(_coverage_row(
                code, STATUS_BUDGET_EXHAUSTED,
                f"{profile.rera_acronym} was not searched at all: this sweep's limit of "
                f"{search_limit} searches was reached first. Nothing here says anything about "
                f"whether the group has projects there.",
            ))
        else:
            coverage.append(_coverage_row(
                code, STATUS_SEARCHED, None,
                entities_searched=state["searched"],
                entities_total=len(names),
                projects_found=len(state["projects"]),
                entities_matched=sorted(set(state["matched"])),
            ))

    coverage.sort(key=lambda row: row["state"])
    searched = [row for row in coverage if row["status"] == STATUS_SEARCHED]
    limitations = [row["reason"] for row in coverage if row.get("reason")]
    partial = [row for row in searched if row["entities_searched"] < row["entities_total"]]
    if truncated:
        limitations.append(
            f"This sweep stopped after {search_limit} searches, so "
            f"{'some authorities were searched against only part of the group' if partial else 'not every entity was checked'}"
            f". The result is a floor, not a complete list."
        )
    limitations.append(
        "Every authority that can answer this question answers it by NAME: no RERA register "
        "publishes a company identity number to match on. Each project below is therefore a "
        "candidate to confirm, not an established group project."
    )

    return {
        "entities": names,
        "coverage": coverage,
        "projects": projects,
        "states_searched": len(searched),
        "states_total": len(coverage),
        "truncated": truncated,
        "limitations": limitations,
    }


def distinct_projects(result):
    """One row per registration, listing every group entity that matched it.

    The raw rows carry one entry per (entity, project) pair, so a project
    matched by three sibling companies appears three times. A reader counting
    rows would treble-count the group's footprint.
    """
    merged = {}
    for row in (result or {}).get("projects") or []:
        key = (row.get("state"), row.get("reg_no"))
        if key not in merged:
            merged[key] = dict(row)
            merged[key]["matched_entities"] = []
        if row.get("matched_entity") not in merged[key]["matched_entities"]:
            merged[key]["matched_entities"].append(row.get("matched_entity"))
    for row in merged.values():
        row.pop("matched_entity", None)
    return list(merged.values())


# How many swept projects may be OPENED. Listing a project is one cheap
# search hit; opening it is a page fetch each, and a group can match dozens.
DEFAULT_DETAIL_LIMIT = 12


def _detail_fetcher_for(code):
    module_path = states._ADAPTER_MODULES.get(code)
    if not module_path:
        return None
    try:
        module = importlib.import_module(module_path)
    except Exception:
        return None
    return getattr(module, "fetch_project_summary", None)


def enrich_projects(result, fetcher=None, reporter=None, limit=DEFAULT_DETAIL_LIMIT):
    """Opens each swept project and attaches what its own page says.

    WHY THIS IS SEPARATE FROM THE SWEEP. The sweep proves a project EXISTS;
    it reads a search-results row and nothing more. Everything a reader
    actually needs -- whether the project is in litigation, how much of it
    is sold, who the directors and contractors are, what earlier
    registrations the promoter declared on it -- lives on the project's own
    page, one fetch each. Keeping them apart means a caller can list a
    group's national footprint cheaply and pay for depth only where it
    matters.

    BOUNDED AND REPORTED. A group brand can match a dozen projects per
    state; opening all of them unasked would turn one search into a crawl.
    Projects past `limit` keep their search-row fields and are marked
    `detail_status: "not opened (limit reached)"` -- never left looking as
    though they were opened and found clean.

    Mutates and returns `result`. Never raises: one unreachable project must
    not sink the pass.
    """
    if not result:
        return result
    projects = result.get("projects") or []
    opened = 0
    for project in projects:
        fn = fetcher if fetcher is not None else _detail_fetcher_for(project.get("state"))
        reference = project.get("project_id")
        if fn is None or not reference:
            project["detail_status"] = "not opened (this authority has no per-project fetch)"
            continue
        if opened >= limit:
            project["detail_status"] = "not opened (limit reached)"
            continue
        opened += 1
        try:
            summary = fn(reference, reporter=reporter) or {}
        except Exception as e:
            project["detail_status"] = f"could not be opened ({type(e).__name__})"
            continue
        if not summary.get("opened"):
            project["detail_status"] = summary.get("note") or "could not be opened"
            continue
        project["detail_status"] = "opened"
        project["detail"] = summary

        # OPENING THE PROJECT IS ALSO WHAT CONFIRMS OR REFUTES IT.
        #
        # The search matches free text, so a hit only means the register
        # contains those letters somewhere. The project's own page names its
        # PROMOTER, and comparing that against the group entity that matched
        # is a real second signal -- the same distinctive-token comparison
        # promoter_identity uses to stop a parent company's PAN being
        # attributed to its subsidiary, applied to registrations.
        #
        # This is not a nicety. On the first live run six candidates came
        # back for this group and FIVE were false brand matches: PRAHLAD
        # PINNACLE, SRI HARI CONSTRUCTION, ARYAN DEVELOPERS (twice) and
        # PRAYAGRAJ BUILDCON, none of them related to the promoter. Listed
        # unconfirmed they would have quintupled the group's apparent
        # Jharkhand footprint.
        actual = summary.get("promoter_name")
        matched = project.get("matched_entity")
        agree = promoter_identity.names_agree(actual, matched)
        shared = (set(promoter_identity._distinctive_tokens(actual))
                  & set(promoter_identity._distinctive_tokens(matched)))
        if agree is True:
            project["confirmation"] = "confirmed: the project's own promoter is this entity"
        elif shared:
            # THE SPV PATTERN, and requiring identity instead of this refuted
            # the one genuinely group project on the first run. Indian
            # developers register one special purpose vehicle per project, so
            # the promoter of record is almost never the parent: PRANAMI
            # CREST is promoted by "PBPL PRANAMI CREST RERA PRIVATE LIMITED",
            # not by Pranami Builders. A shared distinctive brand word is the
            # right evidence here, and it is weaker than identity, so it gets
            # its own outcome rather than being folded into either.
            project["confirmation"] = (
                f"probable: promoted by {actual}, a separate entity sharing this group's name "
                f"({', '.join(sorted(shared))}). Consistent with the one-vehicle-per-project "
                f"structure, but the link is the shared name, not a filed relationship."
            )
        elif not actual:
            # THE CHECK COULD NOT RUN, WHICH IS NOT THE SAME AS FAILING IT.
            # Without this branch the project fell through every arm above
            # and carried NO confirmation key at all -- read alongside its
            # `detail_status: "opened"` that looks like a project examined
            # and found unremarkable. MahaRERA reaches here whenever no
            # session is cached, because it publishes the promoter of record
            # only on its auth-gated partners record.
            project["confirmation"] = (
                "unconfirmed: this project's own page published no promoter of record, so it "
                "could not be checked against this group. That is a limit of what the authority "
                "served, not evidence for or against the project belonging to this group."
            )
            project["unconfirmed"] = True
        elif agree is False:
            project["confirmation"] = (
                f"refuted: this project's promoter of record is {actual}, which shares no name "
                f"with {matched}. It matched only on incidental words in the register's "
                f"free-text search."
            )
            project["refuted"] = True

    result["projects_opened"] = opened
    result["projects_listed"] = len(projects)
    result["projects_confirmed"] = len([p for p in projects
                                        if str(p.get("confirmation", "")).startswith("confirmed")])
    result["projects_probable"] = len([p for p in projects
                                       if str(p.get("confirmation", "")).startswith("probable")])
    result["projects_refuted"] = len([p for p in projects if p.get("refuted")])
    # Opened, but the authority published no promoter to check against. Counted
    # separately from refuted because the two mean opposite things and a reader
    # conflating them would either drop a real project or claim a false one.
    result["projects_unconfirmed"] = len([p for p in projects if p.get("unconfirmed")])
    if opened < len([p for p in projects if p.get("project_id")]):
        result.setdefault("limitations", []).append(
            f"{opened} of {len(projects)} matched projects were opened and read; the rest are "
            f"listed from the register's search results only. Nothing is claimed about a "
            f"project that was not opened."
        )
    return result


def coverage_sentence(result):
    """One sentence a reader can act on, naming what was and was not asked.

    This is the load-bearing output of the whole module. Without it a short
    project list reads as a clean national record, when it may only mean
    three authorities were reachable.
    """
    if not result:
        return ""
    searched = [r for r in result["coverage"] if r["status"] == STATUS_SEARCHED]
    names = ", ".join(r["authority"] for r in searched) or "none"
    return (
        f"{len(searched)} of {result['states_total']} state authorities were searched for this "
        f"group's projects ({names}). The remainder either publish no promoter search or are not "
        f"yet built into this pipeline, so this is a partial view of the group's national "
        f"footprint and an absence below is not evidence of absence."
    )


if __name__ == "__main__":
    import json
    import sys

    entity_names = sys.argv[1:] or ["Pranami Builders", "Pranami Estates", "Pranami Neev Realty"]
    print(f"Searchable states today: {searchable_states()}")
    outcome = sweep(entity_names)
    print(coverage_sentence(outcome))
    print()
    for row in outcome["coverage"]:
        detail = row.get("reason") or f"{row.get('projects_found', 0)} project(s)"
        print(f"  {row['state']:<3} {str(row['authority']):<9} {row['status']:<26} {detail[:70]}")
    print()
    for project in outcome["projects"]:
        print(f"  [{project['state']}] {project.get('reg_no', '')} | {project.get('project_name', '')}")
    print()
    print(json.dumps(outcome["limitations"], indent=1)[:600])

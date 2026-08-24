"""
Guards on the group-wide RERA sweep.

THE RULE THIS FILE EXISTS TO ENFORCE: THE COVERAGE STATEMENT IS THE PRODUCT.

Searching is the easy half. The danger is what a reader concludes from a
short result list, because these two are indistinguishable in a finished
document unless the document says which one happened:

    "No Gujarat projects found."
    "Gujarat was never searched, because GujRERA publishes no
     promoter-to-projects link at all."

The second is the true one for Gujarat, West Bengal and Telangana today, and
"never searched" is true for the ~24 states with no adapter. A sweep that
returns three projects from three reachable authorities and presents it as a
national footprint is a false clean record with extra steps.

THE BUG THIS FILE ALREADY CAUGHT. The first version let an injected
searcher stand in for ANY state, including ones with no promoter search at
all -- so a test run reported GujRERA and WBRERA as "searched", which is the
exact claim this module exists to prevent, and every test would have passed
while masking it. Capability is now decided by the adapter; injection
replaces the implementation only. test_an_injected_searcher_cannot_invent_
coverage pins that.

Everything here runs offline: the searcher is injected, matching the pattern
group_entities.build_entity_graph(proposer=None) and
company_charter.run_finding_research(researcher=None) already use.

Run directly: python test_group_sweep.py
"""

import group_sweep as gs
import states


def _hit(name, reporter=None):
    return [{"reg_no": "JHARERA/PROJECT/35/2023", "project_name": "PRANAMI CREST"}]


def _nothing(name, reporter=None):
    return []


def _broken(name, reporter=None):
    raise ConnectionError("portal down")


# --- the coverage rule ----------------------------------------------------

def test_states_that_cannot_be_searched_say_so_and_are_not_counted():
    """The central guard. A state whose portal cannot be searched by promoter
    must never be reported as searched, and the reason must reach the reader.

    Uttar Pradesh joined this list when its adapter landed: UP-RERA's
    register is CAPTCHA-gated AND demands a district before a promoter, and
    a search that cannot run must not report a zero.

    The count is derived rather than written down. It used to be a literal 3,
    which meant every new adapter with a promoter search failed this test on
    arrival -- a guard firing at the states it was built to accommodate.
    """
    result = gs.sweep(["Pranami Builders"], searcher=_hit)
    by_state = {row["state"]: row for row in result["coverage"]}

    for code in ("GJ", "WB", "TG", "UP"):
        assert by_state[code]["status"] == gs.STATUS_NO_PROMOTER_SEARCH, by_state[code]
        assert by_state[code]["reason"], by_state[code]
        assert "not searched" in by_state[code]["reason"].lower(), by_state[code]["reason"]

    assert result["states_searched"] == len(gs.searchable_states()), (
        result["states_searched"], gs.searchable_states())
    assert not set(gs.searchable_states()) & {"GJ", "WB", "TG", "UP"}, gs.searchable_states()
    assert result["states_total"] == len(states.PROFILES), result
    print("test_states_that_cannot_be_searched_say_so_and_are_not_counted: PASS")


def test_an_injected_searcher_cannot_invent_coverage():
    """REGRESSION. The first version resolved the searcher as
    `searcher or _searcher_for(code)`, so injecting one made every state look
    searchable -- including the three that structurally are not. The tests
    would all have passed while the module reported exactly the false
    coverage it exists to prevent."""
    result = gs.sweep(["Anything"], searcher=_hit)
    searched = {row["state"] for row in result["coverage"]
                if row["status"] == gs.STATUS_SEARCHED}
    assert searched == set(gs.searchable_states()), searched
    assert "GJ" not in searched and "WB" not in searched and "TG" not in searched, searched
    # ...and no project is attributed to a state that was never asked.
    assert {p["state"] for p in result["projects"]} <= searched, result["projects"]
    print("test_an_injected_searcher_cannot_invent_coverage: PASS")


def test_the_coverage_sentence_names_what_was_asked():
    """A reader must be able to see the denominator without reading the
    table. This sentence is the load-bearing output of the module."""
    sentence = gs.coverage_sentence(gs.sweep(["X"], searcher=_hit))
    assert "of" in sentence and "were searched" in sentence, sentence
    assert "absence below is not evidence of absence" in sentence, sentence
    for acronym in ("JHARERA", "K-RERA", "MahaRERA"):
        assert acronym in sentence, sentence
    print("test_the_coverage_sentence_names_what_was_asked: PASS")


def test_an_unreachable_portal_is_not_an_empty_result():
    """A portal that could not be reached must never contribute a zero.
    Karnataka was down for four consecutive days during this work, and a
    sweep that recorded that as "no Karnataka projects" would have been
    wrong every time."""
    result = gs.sweep(["Pranami"], state_codes=["KA"], searcher=_broken)
    row = result["coverage"][0]
    assert row["status"] == gs.STATUS_UNREACHABLE, row
    assert "not evidence the group has no projects there" in row["reason"], row
    assert result["states_searched"] == 0, result
    print("test_an_unreachable_portal_is_not_an_empty_result: PASS")


def test_a_genuine_zero_is_distinguishable_from_a_missing_search():
    """A state that WAS searched and found nothing is a real finding, and
    must be told apart from one that was never asked."""
    result = gs.sweep(["Nobody"], state_codes=["JH"], searcher=_nothing)
    row = result["coverage"][0]
    assert row["status"] == gs.STATUS_SEARCHED, row
    assert row["projects_found"] == 0, row
    assert "reason" not in row, row
    print("test_a_genuine_zero_is_distinguishable_from_a_missing_search: PASS")


def test_the_budget_never_lets_a_state_report_zero_without_asking():
    """REGRESSION, and the worst bug in this module so far.

    The budget was global and the STATE was the outer loop, so the first
    authority alphabetically consumed the entire allowance and every later
    one was reported as "searched, 0 projects" having run ZERO queries --
    with a fabricated entities_searched count to match.

    Confirmed live: a 38-entity sweep spent all 25 searches on JHARERA, then
    declared K-RERA and MahaRERA clean. MahaRERA's search, run directly for
    the same promoter, returns the subject's own project. A clean record
    manufactured by the budget, inside the module written to prevent exactly
    that."""
    names = [f"Entity {i}" for i in range(38)]
    result = gs.sweep(names, searcher=_hit, search_limit=25)

    for row in result["coverage"]:
        if row["status"] == gs.STATUS_SEARCHED:
            # A searched state must have actually run queries, and must not
            # claim more than it ran.
            assert row["entities_searched"] > 0, row
            assert row["entities_searched"] <= row["entities_total"], row
            assert row["entities_searched"] <= 25, row

    searched = [r for r in result["coverage"] if r["status"] == gs.STATUS_SEARCHED]
    assert len(searched) == len(gs.searchable_states()), searched
    # The budget is spread, not spent on whichever state sorts first.
    counts = sorted(r["entities_searched"] for r in searched)
    assert counts[-1] - counts[0] <= 1, counts
    print("test_the_budget_never_lets_a_state_report_zero_without_asking: PASS")


def test_a_state_the_budget_never_reached_says_so_explicitly():
    """With a budget smaller than the number of searchable states, some
    state genuinely gets no query at all. That must read as "not searched",
    never as a clean result."""
    result = gs.sweep(["One"], searcher=_hit, search_limit=1)
    statuses = {row["state"]: row["status"] for row in result["coverage"]}
    starved = [c for c, st in statuses.items() if st == gs.STATUS_BUDGET_EXHAUSTED]
    assert starved, statuses
    for code in starved:
        row = next(r for r in result["coverage"] if r["state"] == code)
        assert "was not searched at all" in row["reason"], row
        assert "Nothing here says anything about" in row["reason"], row
    print("test_a_state_the_budget_never_reached_says_so_explicitly: PASS")


def test_one_project_matched_by_three_siblings_is_one_project():
    """The raw rows carry one entry per (entity, project) pair, so a project
    matched by three sibling companies appears three times. A reader
    counting rows would treble-count the group's footprint -- which is
    exactly what the first live sweep printed for PRANAMI CREST."""
    result = {"projects": [
        {"state": "JH", "reg_no": "JHARERA/PROJECT/35/2023", "project_name": "PRANAMI CREST",
         "matched_entity": "Pranami Neev Realty Limited"},
        {"state": "JH", "reg_no": "JHARERA/PROJECT/35/2023", "project_name": "PRANAMI CREST",
         "matched_entity": "Pranami Builders Private Limited"},
        {"state": "JH", "reg_no": "JHARERA/PROJECT/35/2023", "project_name": "PRANAMI CREST",
         "matched_entity": "Pranami Estates Pvt. Ltd."},
        {"state": "JH", "reg_no": "JHARERA/PROJECT/92/2022", "project_name": "SHREYAS COMPLEX",
         "matched_entity": "Bhawani Concrete Private Limited"},
    ]}
    distinct = gs.distinct_projects(result)
    assert len(distinct) == 2, distinct
    crest = next(p for p in distinct if p["reg_no"].endswith("35/2023"))
    assert len(crest["matched_entities"]) == 3, crest
    assert "matched_entity" not in crest, crest
    assert gs.distinct_projects({}) == []
    print("test_one_project_matched_by_three_siblings_is_one_project: PASS")


# --- what a hit means -----------------------------------------------------

def test_every_hit_is_labelled_a_name_match_not_a_confirmed_project():
    """No RERA register publishes a company identity number, so every one of
    these searches is by name. A name match is a candidate -- the same rule
    group_entities.py enforces for corporate affiliates."""
    result = gs.sweep(["Pranami Builders"], state_codes=["JH"], searcher=_hit)
    assert result["projects"], result
    for project in result["projects"]:
        assert project["confirmation"] == "name match only, not confirmed", project
        assert project["matched_entity"] == "Pranami Builders", project
        assert project["state"] == "JH" and project["authority"] == "JHARERA", project
    assert any("candidate to confirm" in l for l in result["limitations"]), result["limitations"]
    print("test_every_hit_is_labelled_a_name_match_not_a_confirmed_project: PASS")


# --- opening the projects -------------------------------------------------

def _summary(ref, reporter=None):
    return {"opened": True, "promoter_name": "PBPL PRANAMI CREST RERA PRIVATE LIMITED",
            "litigation": [], "litigation_table_present": True,
            "units_total": 100, "units_sold": 45, "documents_on_page": 69,
            "declared_past_projects": [{"reg_no": "JRERA/PROJECT/08/2018"}]}


def _hit_with_id(name, reporter=None):
    return [{"reg_no": "JHARERA/PROJECT/35/2023", "project_name": "PRANAMI CREST",
             "project_id": "2625"}]


def test_a_swept_project_is_opened_not_just_listed():
    """The sweep alone proves a project EXISTS. Litigation, sales and who is
    building it are only on the project's own page, and those are the things
    a reader is actually asking about."""
    result = gs.sweep(["Pranami"], state_codes=["JH"], searcher=_hit_with_id)
    assert result["projects"][0].get("detail") is None, "not enriched yet"
    gs.enrich_projects(result, fetcher=_summary)
    project = result["projects"][0]
    assert project["detail_status"] == "opened", project
    assert project["detail"]["units_sold"] == 45, project["detail"]
    assert project["detail"]["litigation_table_present"] is True
    assert result["projects_opened"] == 1, result
    print("test_a_swept_project_is_opened_not_just_listed: PASS")


def test_opening_a_project_refutes_a_false_brand_match():
    """THE PAYOFF OF OPENING PROJECTS, and it changed the answer.

    A register search matches free text, so a hit only means those letters
    appear somewhere. The project's own page names its PROMOTER, and that
    is a real second signal.

    On the first live run this group produced six Jharkhand candidates and
    FIVE were false: PRAHLAD PINNACLE, SRI HARI CONSTRUCTION, ARYAN
    DEVELOPERS (twice) and PRAYAGRAJ BUILDCON, none related to the promoter.
    Listed as unconfirmed candidates they would have quintupled the group's
    apparent Jharkhand footprint."""
    def _foreign(ref, reporter=None):
        return {"opened": True, "promoter_name": "PRAYAGRAJ BUILDCON"}

    result = gs.sweep(["Prayag Devcon LLP"], state_codes=["JH"], searcher=_hit_with_id)
    gs.enrich_projects(result, fetcher=_foreign)
    project = result["projects"][0]
    assert project.get("refuted") is True, project
    assert "refuted:" in project["confirmation"], project
    assert "PRAYAGRAJ BUILDCON" in project["confirmation"], project
    assert result["projects_refuted"] == 1 and result["projects_confirmed"] == 0, result

    # ...and a genuine one is confirmed rather than left hanging.
    real = gs.sweep(["PBPL Pranami Crest Rera Private Limited"], state_codes=["JH"],
                    searcher=_hit_with_id)
    gs.enrich_projects(real, fetcher=_summary)
    assert real["projects"][0]["confirmation"].startswith("confirmed"), real["projects"][0]
    assert real["projects_confirmed"] == 1 and real["projects_refuted"] == 0, real
    print("test_opening_a_project_refutes_a_false_brand_match: PASS")


def test_a_project_spv_is_probable_not_refuted():
    """REGRESSION, and demanding identity got this exactly backwards on the
    first run: the ONE genuine group project was refuted.

    Indian developers register a special purpose vehicle per project, so the
    promoter of record is almost never the parent. PRANAMI CREST is promoted
    by "PBPL PRANAMI CREST RERA PRIVATE LIMITED", not by Pranami Builders --
    a different legal person sharing the group's brand word. Requiring name
    equality refutes every real group project while keeping none of the
    false ones out.

    A shared distinctive word is weaker evidence than identity, so it gets
    its own outcome rather than being folded into confirmed or refuted."""
    result = gs.sweep(["Pranami Builders Private Limited"], state_codes=["JH"],
                      searcher=_hit_with_id)
    gs.enrich_projects(result, fetcher=_summary)
    project = result["projects"][0]
    assert project["confirmation"].startswith("probable"), project["confirmation"]
    assert "PRANAMI" in project["confirmation"], project["confirmation"]
    assert not project.get("refuted"), project
    assert result["projects_probable"] == 1, result
    # The wording must not overclaim: the link is a name, not a filing.
    assert "not a filed relationship" in project["confirmation"], project["confirmation"]
    print("test_a_project_spv_is_probable_not_refuted: PASS")


def test_a_project_past_the_limit_says_it_was_not_opened():
    """A project the budget never reached must not read as opened-and-clean
    -- the same rule the sweep's own budget already follows."""
    def _many(name, reporter=None):
        return [{"reg_no": f"JHARERA/PROJECT/{i}/2024", "project_name": f"P{i}",
                 "project_id": str(i)} for i in range(5)]

    result = gs.enrich_projects(
        gs.sweep(["X"], state_codes=["JH"], searcher=_many), fetcher=_summary, limit=2
    )
    statuses = [p["detail_status"] for p in result["projects"]]
    assert statuses.count("opened") == 2, statuses
    assert statuses.count("not opened (limit reached)") == 3, statuses
    assert any("were opened and read" in l for l in result["limitations"]), result["limitations"]
    for project in result["projects"]:
        if project["detail_status"] != "opened":
            assert "detail" not in project, project
    print("test_a_project_past_the_limit_says_it_was_not_opened: PASS")


def test_an_unopenable_project_is_recorded_not_dropped():
    def _broken_detail(ref, reporter=None):
        raise ConnectionError("gone")

    result = gs.enrich_projects(
        gs.sweep(["X"], state_codes=["JH"], searcher=_hit_with_id), fetcher=_broken_detail
    )
    assert "could not be opened" in result["projects"][0]["detail_status"], result["projects"][0]
    assert "detail" not in result["projects"][0]
    print("test_an_unopenable_project_is_recorded_not_dropped: PASS")


def test_a_state_with_no_detail_fetch_says_so():
    """A project on an authority this pipeline cannot open per-project must be
    marked as not opened rather than silently appearing bare.

    TELANGANA IS THE CASE, AND IT IS EXPECTED TO STAY THE ONLY ONE. This
    test used to use Gujarat, which gained a fetch_project_summary on
    2026-08-24 -- and the moment it did, this test stopped exercising the
    no-fetch path AND started making a live request to GujRERA from the
    offline suite. TG-RERA cannot acquire one: its public record does not
    display its own registration number and its search is CAPTCHA-gated by
    project name, so there is nothing to hand a fetch. Nothing is imported
    or requested for it, which is the point.

    The result is built by hand rather than swept because none of these
    authorities has a promoter search either, and sweep() rightly refuses to
    let an injected searcher invent coverage for them."""
    result = {"projects": [{"state": "TG", "project_id": "CONSTELLA",
                            "matched_entity": "Constella"}]}
    gs.enrich_projects(result)
    status = result["projects"][0]["detail_status"]
    assert status.startswith("not opened"), result["projects"][0]
    # And it carries THIS authority's reason, not a generic line -- a reader
    # seeing an unopened project is owed the why.
    assert "CAPTCHA" in status, status
    assert "detail" not in result["projects"][0], result["projects"][0]
    print("test_a_state_with_no_detail_fetch_says_so: PASS")


def test_every_searchable_state_can_also_open_what_it_found():
    """A sweep that can LIST a project but never OPEN it can neither confirm
    nor refute it, and unconfirmed candidates are what inflate a group's
    apparent footprint -- five of six on the first live run.

    MahaRERA and K-RERA were exactly that gap for a while: both searchable,
    neither openable, so every hit on the two largest registers in the
    pipeline stayed a bare name. This pins the pairing rather than the two
    states, so a seventh adapter cannot reintroduce it."""
    missing = [code for code in gs.searchable_states()
               if gs._detail_fetcher_for(code) is None]
    assert not missing, (
        f"these states can be searched by promoter but their hits can never be opened, "
        f"so nothing found on them can be confirmed or refuted: {missing}"
    )
    print("test_every_searchable_state_can_also_open_what_it_found: PASS")


def test_an_unreadable_promoter_name_is_unconfirmed_not_refuted():
    """THE CHECK COULD NOT RUN IS NOT THE SAME AS THE CHECK FAILED, and
    before this branch existed the project carried NO confirmation at all --
    which alongside `detail_status: "opened"` reads as examined-and-fine.

    MahaRERA reaches here whenever no session is cached: it publishes the
    promoter of record only on its CAPTCHA-gated partners record, so an
    unattended sweep opens the project and legitimately cannot name its
    promoter. Refuting it would drop a real project; staying silent would
    smuggle in an unverified one."""
    def _nameless(ref, reporter=None):
        return {"opened": True, "promoter_name": "", "units_total": 234}

    result = gs.sweep(["Pranami Neev Realty Limited"], state_codes=["JH"],
                      searcher=_hit_with_id)
    gs.enrich_projects(result, fetcher=_nameless)
    project = result["projects"][0]
    assert project["detail_status"] == "opened", project
    assert project.get("unconfirmed") is True, project
    assert project["confirmation"].startswith("unconfirmed"), project["confirmation"]
    # It must not read as either verdict.
    assert not project.get("refuted"), project
    assert result["projects_refuted"] == 0, result
    assert result["projects_confirmed"] == 0, result
    assert result["projects_unconfirmed"] == 1, result
    # And it must say WHY, so a reader knows this is the authority's limit.
    assert "not evidence" in project["confirmation"], project["confirmation"]
    print("test_an_unreadable_promoter_name_is_unconfirmed_not_refuted: PASS")


# --- bounds ---------------------------------------------------------------

def test_the_fan_out_is_bounded_and_truncation_is_reported():
    """N entities times M states is combinatorial, and Maharashtra's search
    drives a headless browser per call. Silent truncation would read as a
    complete sweep."""
    names = [f"Entity {i}" for i in range(50)]
    result = gs.sweep(names, state_codes=["JH", "KA"], searcher=_hit, search_limit=5)
    assert result["truncated"] is True, result["truncated"]
    assert any("stopped after 5 searches" in l for l in result["limitations"]), result["limitations"]
    assert any("floor, not a complete list" in l for l in result["limitations"]), result["limitations"]
    print("test_the_fan_out_is_bounded_and_truncation_is_reported: PASS")


def test_duplicate_and_empty_entity_names_are_dropped():
    result = gs.sweep(["Pranami", "Pranami", "", None, "  "], state_codes=["JH"], searcher=_hit)
    assert result["entities"] == ["Pranami"], result["entities"]
    print("test_duplicate_and_empty_entity_names_are_dropped: PASS")


def test_no_entities_still_produces_a_coverage_report():
    """An empty entity list means the group graph found nothing to sweep. The
    coverage table must still say which authorities exist and which could
    have been asked, rather than returning silence."""
    result = gs.sweep([], searcher=_hit)
    assert result["projects"] == []
    assert len(result["coverage"]) == len(states.PROFILES), result["coverage"]
    assert gs.coverage_sentence(result)
    print("test_no_entities_still_produces_a_coverage_report: PASS")


# --- the seam -------------------------------------------------------------

def test_searchable_states_match_the_adapters_that_implement_the_call():
    """Anti-drift: a new adapter gaining a promoter search must show up here
    automatically, and one that loses it must drop out."""
    import importlib

    for code in states.PROFILES:
        module = importlib.import_module(states._ADAPTER_MODULES[code])
        implements = hasattr(module, "search_promoter_projects")
        assert implements == (code in gs.searchable_states()), (code, implements)
    # And every state that cannot be searched has a reader-facing reason
    # written for it, rather than falling back to generic wording.
    for code in states.PROFILES:
        if code not in gs.searchable_states():
            assert code in gs._CANNOT_SEARCH, (
                f"{code} has no promoter search and no reason written for it, so the sweep "
                f"would explain its absence in generic wording. Say why THIS authority "
                f"cannot be searched."
            )
    print("test_searchable_states_match_the_adapters_that_implement_the_call: PASS")


if __name__ == "__main__":
    test_states_that_cannot_be_searched_say_so_and_are_not_counted()
    test_an_injected_searcher_cannot_invent_coverage()
    test_the_coverage_sentence_names_what_was_asked()
    test_an_unreachable_portal_is_not_an_empty_result()
    test_a_genuine_zero_is_distinguishable_from_a_missing_search()
    test_the_budget_never_lets_a_state_report_zero_without_asking()
    test_a_state_the_budget_never_reached_says_so_explicitly()
    test_one_project_matched_by_three_siblings_is_one_project()
    test_every_hit_is_labelled_a_name_match_not_a_confirmed_project()
    test_a_swept_project_is_opened_not_just_listed()
    test_opening_a_project_refutes_a_false_brand_match()
    test_a_project_spv_is_probable_not_refuted()
    test_a_project_past_the_limit_says_it_was_not_opened()
    test_an_unopenable_project_is_recorded_not_dropped()
    test_a_state_with_no_detail_fetch_says_so()
    test_the_fan_out_is_bounded_and_truncation_is_reported()
    test_duplicate_and_empty_entity_names_are_dropped()
    test_no_entities_still_produces_a_coverage_report()
    test_searchable_states_match_the_adapters_that_implement_the_call()
    print("\nAll tests passed.")

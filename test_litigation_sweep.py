"""
Guards on searching case law across the group.

THE BUG THIS FILE EXISTS TO PREVENT was visible on the very first live
query. Searching "Pranami Builders" -- a Ranchi company, CIN
U51909JH1995PTC013805 -- returns:

    Pranami Builders , Ahmedabad vs Department Of Income Tax
    Sh. Narendra Modi, Indore vs Ito Tds-1 , Indore
    Accurate Thermal Spray Private Limited vs Skf Engineering ...

The first is a same-name company in another state. The rest are unrelated
judgments that merely share tokens with the query on a full-text index.
Rendering any of them as this promoter's litigation would manufacture a
finding out of a name collision, which is the same discipline
group_entities keeps: a name PROPOSES, only a hard link CONFIRMS.

THE ABSENCE SIDE IS WORSE, and is the reason coverage is reported at all.
Indian Kanoon carries reported judgments from the higher courts and some
tribunals. Consumer fora, most of NCLT/NCLAT, district courts and the RERA
authorities' own orders are not reliably in it. So a nil result must read
as "nothing in this index", never as a clean record -- a Charter paragraph
asserting no disputes, drawn from a source that would not have carried
them anyway, is precisely the false clean record this repo keeps meeting.

Everything here is offline: `searcher=` is the seam.

Run directly: python test_litigation_sweep.py
"""

import litigation_sweep as ls

_GRAPH = {
    "subject": {"name": "Pranami Neev Realty Limited", "cin": "U70109MH2022PLC385473"},
    "confirmed": [{"name": "Pranami Builders Pvt Ltd", "cin": "U51909JH1995PTC013805"}],
}
_DIRECTORS = ["Bijay Kumar Agarwal"]
_KNOWN_PLACES = ["Jharkhand", "Maharashtra", "West Bengal", "Ranchi", "Mumbai"]

# Verbatim from the live result page, 2026-08-21.
_REAL_PAGE = """
<div class="result_title"><a href="/doc/111111/">
  Pranami Builders , Ahmedabad vs Department Of Income Tax on 2 June, 2016</a></div>
<div class="result_title"><a href="/doc/222222/">
  Sh. Narendra Modi, Indore vs Ito Tds-1 , Indore on 6 June, 2019</a></div>
<div class="result_title"><a href="/doc/333333/">
  Accurate Thermal Spray Private Limited vs Skf Engineering on 1 January, 2020</a></div>
"""


def test_a_same_name_company_elsewhere_is_flagged_not_attributed():
    """THE CENTRAL GUARD. "Pranami Builders , Ahmedabad" against a Ranchi
    group is a name collision, and the place in the title is the cheapest
    discriminator available. It is a CAUTION, not an exclusion -- a company
    can litigate wherever the cause of action arose -- so the wording may
    only claim what was established: the place is not in the known
    footprint."""
    rows = ls.parse_results(_REAL_PAGE, "Pranami Builders")
    ahmedabad = rows[0]
    assert ahmedabad["place"] == "Ahmedabad", ahmedabad
    assert ahmedabad["match"] == ls.MATCH_TITLE, ahmedabad
    caution = ls._place_caution(ahmedabad["place"], _KNOWN_PLACES)
    assert "Ahmedabad" in caution and "different party of the same name" in caution, caution
    # And it must go quiet where the place IS in the footprint, or the flag
    # becomes noise and gets ignored.
    assert ls._place_caution("Ranchi", _KNOWN_PLACES) == ""
    print("test_a_same_name_company_elsewhere_is_flagged_not_attributed: PASS")


def test_a_body_only_mention_is_ranked_below_a_title_match():
    """The Indore income-tax matter and the thermal-spray case match on
    full text, not on the party name. Counting them as litigation would put
    two unrelated judgments in a promoter's Charter."""
    rows = ls.parse_results(_REAL_PAGE, "Pranami Builders")
    assert rows[0]["match"] == ls.MATCH_TITLE, rows[0]
    assert rows[1]["match"] == ls.MATCH_BODY, rows[1]
    assert rows[2]["match"] == ls.MATCH_BODY, rows[2]
    result = {"candidates": rows}
    assert len(ls.title_matches(result)) == 1, ls.title_matches(result)
    print("test_a_body_only_mention_is_ranked_below_a_title_match: PASS")


def test_a_genuine_nil_is_not_a_failed_search():
    """A control query for a nonsense company returns a page that parses to
    zero rows, while an unreachable site raises. Those two must stay apart:
    one is "searched, nothing found", the other is "no result either way",
    and only the first may ever be reported as an absence."""
    assert ls.parse_results("<html><body>no results</body></html>", "Zzqxvw Ltd") == []

    def dead(name):
        raise ConnectionError("site unreachable")

    broken = ls.sweep(_GRAPH, searcher=dead)
    assert broken["searched"] == 0, broken
    assert all(s["status"] == ls.STATUS_UNREACHABLE for s in broken["subjects"]), broken
    assert any("could not run" in l for l in broken["limitations"]), broken["limitations"]

    quiet = ls.sweep(_GRAPH, searcher=lambda name: [])
    assert quiet["searched"] == 2, quiet
    assert all(s["status"] == ls.STATUS_SEARCHED for s in quiet["subjects"]), quiet
    print("test_a_genuine_nil_is_not_a_failed_search: PASS")


def test_a_nil_result_never_reads_as_a_clean_record():
    """The wording is the guard. Indian Kanoon does not reliably carry
    consumer fora, most of NCLT, district courts or RERA orders -- so
    silence there says nothing about whether disputes exist, and the
    document has to say which forums were not covered."""
    result = ls.sweep(_GRAPH, searcher=lambda name: [])
    sentence = ls.coverage_sentence(result)
    assert "clean" not in sentence.lower(), sentence
    assert "no litigation" not in sentence.lower(), sentence
    assert "2 of 2" in sentence, sentence
    joined = " ".join(result["limitations"])
    for forum in ("consumer fora", "NCLT", "RERA authorities"):
        assert forum in joined, (forum, joined)
    assert "not that no proceedings exist" in joined, joined
    print("test_a_nil_result_never_reads_as_a_clean_record: PASS")


def test_a_director_search_carries_a_standing_false_positive_caution():
    """A personal-name query is the highest false-positive search this
    pipeline makes -- Indian personal names repeat enormously. Every hit
    from one must say so, even when the place check finds nothing to
    flag."""
    result = ls.sweep(_GRAPH, directors=_DIRECTORS, known_places=_KNOWN_PLACES,
                      searcher=lambda name: [{"title": f"{name} vs State on 1 Jan, 2020",
                                              "url": "u", "place": "", "match": ls.MATCH_TITLE}])
    person_rows = [r for r in result["candidates"] if r["subject_kind"] == ls.SUBJECT_PERSON]
    assert person_rows, result["candidates"]
    assert "personal name" in person_rows[0]["caution"], person_rows[0]
    entity_rows = [r for r in result["candidates"] if r["subject_kind"] == ls.SUBJECT_ENTITY]
    assert entity_rows and entity_rows[0]["caution"] == "", entity_rows[0]
    print("test_a_director_search_carries_a_standing_false_positive_caution: PASS")


def test_entities_are_searched_before_directors():
    """The query budget is small and a company name discriminates far
    better than a personal name, so the budget must not be spent on the
    noisiest queries first."""
    order = []
    ls.sweep(_GRAPH, directors=_DIRECTORS,
             searcher=lambda name: order.append(name) or [])
    assert order[-1] == "Bijay Kumar Agarwal", order
    assert len(order) == 3, order
    print("test_entities_are_searched_before_directors: PASS")


def test_an_unsearched_name_is_reported_not_dropped():
    """A name the budget never reached is neither clear nor implicated, and
    omitting it would leave a list that reads as complete."""
    result = ls.sweep(_GRAPH, directors=_DIRECTORS, searcher=lambda name: [], limit=1)
    assert result["searched"] == 1, result
    skipped = [s for s in result["subjects"] if s["status"] == ls.STATUS_BUDGET_EXHAUSTED]
    assert len(skipped) == 2, result["subjects"]
    assert any("neither clear nor implicated" in l for l in result["limitations"]), result
    print("test_an_unsearched_name_is_reported_not_dropped: PASS")


def test_proposed_entities_are_never_searched():
    """A brand-name match proposes a company; only a hard link confirms it.
    Searching a proposed one would attach a stranger's litigation to this
    group -- the exact failure the name/confirm split exists to stop."""
    graph = dict(_GRAPH)
    graph["proposed"] = [{"name": "Pranami Textiles Ltd"}]
    names = []
    ls.sweep(graph, searcher=lambda name: names.append(name) or [])
    assert "Pranami Textiles Ltd" not in names, names
    print("test_proposed_entities_are_never_searched: PASS")


def test_the_same_name_is_never_searched_twice():
    """The budget is small; spending two of it on one name because the
    subject also appears in the confirmed list wastes a query and
    double-counts any hit."""
    graph = {"subject": {"name": "Pranami Builders Pvt Ltd"},
             "confirmed": [{"name": "PRANAMI BUILDERS PVT. LTD."}]}
    names = []
    ls.sweep(graph, searcher=lambda name: names.append(name) or [])
    assert len(names) == 1, names
    print("test_the_same_name_is_never_searched_twice: PASS")


def test_the_regulators_own_orders_name_the_registers_not_searched():
    """K-RERA's order register IS searchable by promoter and is wired in.
    Almost no other authority's is -- MahaRERA's own orders search is
    per-project, and JH/WB/TG/GJ publish no promoter-keyed register at all.
    A table showing only Karnataka rows would read as the group's complete
    regulatory history, so the section has to name what was not searched."""
    result = ls.state_order_sweep(
        _GRAPH, searcher=lambda name: [{"ack_no": "00862/2025", "reg_no": "TMP/1",
                                        "project_name": "A Project",
                                        "promoter_name": name.upper()}])
    assert len(result["entries"]) == 2, result["entries"]
    assert result["entries"][0]["authority"] == "Karnataka (K-RERA)", result["entries"][0]
    joined = " ".join(result["limitations"])
    for authority in ("MahaRERA", "JHARERA", "GujRERA", "no adapter yet"):
        assert authority in joined, (authority, joined)
    assert "says nothing about" in joined, joined
    print("test_the_regulators_own_orders_name_the_registers_not_searched: PASS")


def test_an_order_register_failure_does_not_silently_empty_the_table():
    """A portal outage must not produce an empty orders table that reads as
    "no orders against this group"."""
    def dead(name):
        raise ConnectionError("K-RERA unreachable")

    result = ls.state_order_sweep(_GRAPH, searcher=dead)
    assert result["entries"] == [], result
    assert result["searched"] == 0, result
    assert any("could not be searched" in l for l in result["limitations"]), result["limitations"]
    print("test_an_order_register_failure_does_not_silently_empty_the_table: PASS")


def test_only_entities_are_searched_in_order_registers():
    """RERA registers are keyed on the promoter FIRM, not on individuals.
    Searching a director's name there would match nothing and, worse, make
    the empty result look like a checked one."""
    names = []
    ls.state_order_sweep(_GRAPH, searcher=lambda n: names.append(n) or [])
    assert "Bijay Kumar Agarwal" not in names, names
    assert len(names) == 2, names
    print("test_only_entities_are_searched_in_order_registers: PASS")


def test_the_karnataka_order_index_refuses_to_pair_mismatched_arrays():
    """The register ships as four parallel JS arrays. Zipping them when they
    disagree in length would attach the wrong promoter to an order -- the
    same reason the project index raises rather than returning a partial."""
    from states import adapter_karnataka, base

    good = ("applicationNameList.push('00862/2025');"
            "applicationNameList2.push('TMP/1');"
            "applicationNameList3.push('A Project');"
            "applicationNameList4.push('SOME BUILDER PVT LTD');")
    rows = adapter_karnataka.parse_search_index(good)
    assert rows == [{"ack_no": "00862/2025", "reg_no": "TMP/1",
                     "project_name": "A Project",
                     "promoter_name": "SOME BUILDER PVT LTD"}], rows

    mismatched = good + "applicationNameList4.push('AN EXTRA BUILDER');"
    try:
        adapter_karnataka.parse_search_index(mismatched)
    except base.StateResolutionError:
        pass
    else:
        raise AssertionError("mismatched arrays were zipped instead of refused")
    print("test_the_karnataka_order_index_refuses_to_pair_mismatched_arrays: PASS")


def test_a_register_that_did_not_load_is_named_not_counted_as_empty():
    """K-RERA's authority-orders page is 10.4 MB and HAS been seen to
    arrive truncated, dropping its PENALTY table entirely -- 440 rows
    naming the violation, the section and the amount. A promoter with
    penalties would then have shown none. A register that did not load
    must be named."""
    result = ls.state_order_sweep(
        _GRAPH, searcher=lambda name: [],
        register_coverage={"loaded": ["Interim orders"], "missing": ["Authority orders"]})
    joined = " ".join(result["limitations"])
    assert "Authority orders" in joined, joined
    assert "nothing was read, not that nothing is recorded" in joined, joined
    print("test_a_register_that_did_not_load_is_named_not_counted_as_empty: PASS")


def test_why_each_authority_is_unsearchable_is_stated_not_assumed():
    """Each of these was PROBED on 2026-08-21, not assumed. MahaRERA does
    accept a respondent (promoter) name -- an earlier note in this repo
    said otherwise -- but its portal answered with an empty shell every
    time. WBRERA publishes 4,881 orders keyed only by complaint number,
    with no party named. Recording the reason is what stops the next
    session re-deriving it, and stops a reader treating silence as a clean
    record."""
    joined = " ".join(ls.ORDERS_NOT_SEARCHABLE)
    assert "respondent (promoter) name" in joined, joined
    assert "BigPipe shell" in joined, joined
    assert "4,881" in joined and "complaint number" in joined, joined
    assert "single-page applications" in joined, joined
    print("test_why_each_authority_is_unsearchable_is_stated_not_assumed: PASS")


def test_a_maharera_shell_response_is_not_an_absence_of_orders():
    """THE LIVE BUG THIS WORK FOUND. search_maharera_judgments returns []
    both when a project genuinely has no published order and when every
    attempt hit MahaRERA's empty BigPipe shell. On 2026-08-21 a search for
    a large, certainly-litigated Maharashtra promoter hit the shell on
    every attempt, so the pipeline would have reported no orders against
    it -- a clean record manufactured from a search that never ran."""
    import company_charter as charter

    original = charter._maharera_orders_search_once
    try:
        charter._maharera_orders_search_once = lambda project, ctype: None
        status = charter.search_maharera_judgments_status("Anything", max_attempts=1)
        assert status["searched"] is False, status
        assert status["results"] == [], status
        assert "NOT an absence of orders" in status["note"], status

        # ...and a genuine empty must still read as a real search.
        charter._maharera_orders_search_once = lambda project, ctype: []
        clean = charter.search_maharera_judgments_status("Anything", max_attempts=1)
        assert clean["searched"] is True, clean
        assert clean["note"] == "", clean
    finally:
        charter._maharera_orders_search_once = original
    print("test_a_maharera_shell_response_is_not_an_absence_of_orders: PASS")


def test_the_group_litigation_stage_is_opt_in_and_silent_when_off():
    """Off, the Charter carries no section -- different from carrying an
    empty one, which would assert that a search happened."""
    import company_charter as charter

    assert charter._safe_group_litigation({}, "X", enabled=False) == {}
    assert charter._safe_group_litigation({}, "X", enabled=None) == {}, \
        "the case-law sweep ran without being asked for"
    print("test_the_group_litigation_stage_is_opt_in_and_silent_when_off: PASS")


if __name__ == "__main__":
    test_a_same_name_company_elsewhere_is_flagged_not_attributed()
    test_a_body_only_mention_is_ranked_below_a_title_match()
    test_a_genuine_nil_is_not_a_failed_search()
    test_a_nil_result_never_reads_as_a_clean_record()
    test_a_director_search_carries_a_standing_false_positive_caution()
    test_entities_are_searched_before_directors()
    test_an_unsearched_name_is_reported_not_dropped()
    test_proposed_entities_are_never_searched()
    test_the_same_name_is_never_searched_twice()
    test_the_regulators_own_orders_name_the_registers_not_searched()
    test_an_order_register_failure_does_not_silently_empty_the_table()
    test_only_entities_are_searched_in_order_registers()
    test_the_karnataka_order_index_refuses_to_pair_mismatched_arrays()
    test_a_register_that_did_not_load_is_named_not_counted_as_empty()
    test_why_each_authority_is_unsearchable_is_stated_not_assumed()
    test_a_maharera_shell_response_is_not_an_absence_of_orders()
    test_the_group_litigation_stage_is_opt_in_and_silent_when_off()
    print("\nAll tests passed.")

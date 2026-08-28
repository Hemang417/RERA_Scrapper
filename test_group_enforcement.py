"""
Guards on the group enforcement/defaulter sweep -- UP-RERA, HARERA, TNRERA
and Delhi-RERA's own published registers, searched by name.

THE DISCIPLINE THIS FILE PINS is the one litigation_sweep already earned
the hard way: every hit is a CANDIDATE naming which register it came from
and why a name match is not proof, and every register this pass does NOT
reach is named on the page -- an empty result must never read as clean.

Everything here is offline: the `fetchers` dict is the seam.

Run directly: python test_group_enforcement.py
"""

import group_enforcement as ge

_GRAPH = {
    "subject": {"name": "Pranami Neev Realty Limited", "cin": "U70109MH2022PLC385473"},
    "confirmed": [{"name": "Pranami Builders Pvt Ltd", "cin": "U51909JH1995PTC013805"}],
}
_DIRECTORS = ["Bijay Kumar Agarwal"]


def _fetchers(**overrides):
    base = {
        "up_defaulters": lambda: [],
        "haryana_defaulters": lambda bench: [],
        "tn_penalty": lambda kind: [],
        "tn_enforcement_search": lambda name: [],
        "delhi_suomoto": lambda: [],
        "delhi_execution": lambda: [],
        "delhi_appeal_index": lambda: {"rows": [], "coverage": {}},
    }
    base.update(overrides)
    return base


def test_every_subject_is_named_even_with_zero_hits():
    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers())
    names = {s["name"] for s in result["subjects"]}
    assert names == {"Pranami Neev Realty Limited", "Pranami Builders Pvt Ltd",
                      "Bijay Kumar Agarwal"}, names
    assert all(s["hit_count"] == 0 for s in result["subjects"]), result["subjects"]
    assert result["searched"] == result["total"] == 3, result
    print("test_every_subject_is_named_even_with_zero_hits: PASS")


def test_a_up_defaulter_hit_carries_its_caution():
    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers(
        up_defaulters=lambda: [{
            "promoter_name": "Pranami Builders Pvt Ltd",
            "project_name": "SOME SCHEME (De-Registered Project)",
            "project_registration_no.": "UPRERAPRJ99999",
        }],
    ))
    hits = [c for c in result["candidates"] if c["authority"] == "Uttar Pradesh (UP-RERA)"]
    assert len(hits) == 1, hits
    assert hits[0]["detail"] == "UPRERAPRJ99999", hits[0]
    assert "not confirmed proof of identity" in hits[0]["caution"], hits[0]
    print("test_a_up_defaulter_hit_carries_its_caution: PASS")


def test_haryana_both_benches_are_checked():
    seen_benches = []

    def haryana(bench):
        seen_benches.append(bench)
        if bench == "2":
            return [{"builder": "Pranami Builders Pvt Ltd", "certificate_no": "HR/2/2024"}]
        return []

    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers(haryana_defaulters=haryana))
    assert set(seen_benches) == {"1", "2"}, seen_benches
    hits = [c for c in result["candidates"] if c["authority"] == "Haryana (HARERA)"]
    assert len(hits) == 1 and hits[0]["detail"] == "HR/2/2024", hits
    print("test_haryana_both_benches_are_checked: PASS")


def test_tn_penalty_block_is_matched_whole_and_carries_its_own_caution():
    """promoter_block is raw, unparsed text -- the caution must say so, not
    imply a clean field match."""
    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers(
        tn_penalty=lambda kind: [{
            "promoter_block": "M/s. Pranami Builders Pvt Ltd. Door No. 27, Some Road",
            "penalty_amount": "50000",
        }] if kind == "building" else [],
    ))
    hits = [c for c in result["candidates"] if c["register"] == "Penalty register"]
    assert len(hits) == 1, hits
    assert hits[0]["detail"] == "Rs 50000", hits[0]
    assert "raw, unparsed text block" in hits[0]["caution"], hits[0]
    print("test_tn_penalty_block_is_matched_whole_and_carries_its_own_caution: PASS")


def test_tn_enforcement_pdfs_are_searched_per_subject_by_name():
    """search_enforcement_lists_by_name is already name-keyed, unlike the
    other six sources -- this pins that the sweep calls it per subject
    rather than fetching once and matching locally."""
    calls = []

    def search(name):
        calls.append(name)
        if name == "Pranami Builders Pvt Ltd":
            return [{"party_detail": "Thiru. Pranami Builders, Managing Partner",
                      "site_address": "Chennai", "source": "TNRERA show-cause list"}]
        return []

    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers(tn_enforcement_search=search))
    assert set(calls) == {"Pranami Neev Realty Limited", "Pranami Builders Pvt Ltd",
                           "Bijay Kumar Agarwal"}, calls
    hits = [c for c in result["candidates"] if c["register"] == "TNRERA show-cause list"]
    assert len(hits) == 1, hits
    print("test_tn_enforcement_pdfs_are_searched_per_subject_by_name: PASS")


def test_a_failed_tn_enforcement_search_is_named_not_swallowed():
    def broken(name):
        raise RuntimeError("PDF fetch timed out")

    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers(tn_enforcement_search=broken))
    assert any("could not be searched" in l and "RuntimeError" in l for l in result["limitations"]), \
        result["limitations"]
    print("test_a_failed_tn_enforcement_search_is_named_not_swallowed: PASS")


def test_delhi_suomoto_and_execution_match_the_respondent_and_debtor_columns():
    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers(
        delhi_suomoto=lambda: [{"respondent_name": "Pranami Builders Pvt Ltd",
                                 "case_no": "SM/1/2024"}],
        delhi_execution=lambda: [{"judgement_debtor": "Bijay Kumar Agarwal",
                                   "execution_no": "EX/2/2024"}],
    ))
    suomoto_hits = [c for c in result["candidates"] if c["register"] == "Suo-moto register"]
    execution_hits = [c for c in result["candidates"] if c["register"] == "Execution register"]
    assert len(suomoto_hits) == 1 and suomoto_hits[0]["detail"] == "SM/1/2024", suomoto_hits
    assert len(execution_hits) == 1 and execution_hits[0]["detail"] == "EX/2/2024", execution_hits
    print("test_delhi_suomoto_and_execution_match_the_respondent_and_debtor_columns: PASS")


def test_reat_appeal_hits_check_both_appellant_and_respondent_sides():
    """A developer appealing an Authority order is the Appellant; a
    homebuyer appealing makes the developer the Respondent instead -- a
    promoter search must check both sides."""
    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers(
        delhi_appeal_index=lambda: {
            "rows": [{"appeal_no": "108/REAT/2022", "appellant": "Pranami Builders Pvt Ltd",
                      "respondent": "Real Estate Regulatory Authority"}],
            "coverage": {"total_rows": 505, "distinct_pdfs": 100, "rows_with_a_party_name": 90,
                         "pdfs_unparseable": 3},
        },
    ))
    hits = [c for c in result["candidates"] if c["authority"] == "Delhi (REAT)"]
    assert len(hits) == 1 and hits[0]["detail"] == "108/REAT/2022", hits
    assert "OCR" in hits[0]["caution"], hits[0]
    coverage_note = next(l for l in result["limitations"] if "REAT appeal register" in l)
    assert "100 of its own order PDFs" in coverage_note, coverage_note
    assert "3 could not be read" in coverage_note, coverage_note
    print("test_reat_appeal_hits_check_both_appellant_and_respondent_sides: PASS")


def test_every_unsearchable_authority_is_named_in_the_limitations():
    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers())
    joined = " ".join(result["limitations"])
    for authority in ("MahaRERA", "GujRERA", "WBRERA", "JHARERA", "TG-RERA", "K-RERA"):
        assert authority in joined, f"{authority} was not named as unsearched"
    print("test_every_unsearchable_authority_is_named_in_the_limitations: PASS")


def test_an_unreachable_register_is_named_never_dropped_to_zero():
    def broken_up():
        raise ConnectionError("UP-RERA is unreachable")

    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers(up_defaulters=broken_up))
    assert any("UP-RERA's de-registered/defaulter" in l and "ConnectionError" in l
               for l in result["limitations"]), result["limitations"]
    up_hits = [c for c in result["candidates"] if c["authority"] == "Uttar Pradesh (UP-RERA)"]
    assert up_hits == [], "a failed fetch must not fabricate a clean zero"
    print("test_an_unreachable_register_is_named_never_dropped_to_zero: PASS")


def test_coverage_sentence_never_says_clean():
    result = ge.sweep(_GRAPH, _DIRECTORS, fetchers=_fetchers())
    sentence = ge.coverage_sentence(result)
    assert "clean" not in sentence.lower(), sentence
    assert "3 of 3" in sentence or "3 group" in sentence, sentence
    print("test_coverage_sentence_never_says_clean: PASS")


def test_no_subjects_produces_an_honest_sentence_not_a_crash():
    result = ge.sweep({}, [], fetchers=_fetchers())
    assert result["total"] == 0, result
    assert "No group entities or directors" in ge.coverage_sentence(result)
    print("test_no_subjects_produces_an_honest_sentence_not_a_crash: PASS")


if __name__ == "__main__":
    test_every_subject_is_named_even_with_zero_hits()
    test_a_up_defaulter_hit_carries_its_caution()
    test_haryana_both_benches_are_checked()
    test_tn_penalty_block_is_matched_whole_and_carries_its_own_caution()
    test_tn_enforcement_pdfs_are_searched_per_subject_by_name()
    test_a_failed_tn_enforcement_search_is_named_not_swallowed()
    test_delhi_suomoto_and_execution_match_the_respondent_and_debtor_columns()
    test_reat_appeal_hits_check_both_appellant_and_respondent_sides()
    test_every_unsearchable_authority_is_named_in_the_limitations()
    test_an_unreachable_register_is_named_never_dropped_to_zero()
    test_coverage_sentence_never_says_clean()
    test_no_subjects_produces_an_honest_sentence_not_a_crash()
    print("\nAll tests passed.")

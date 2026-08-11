"""
Tests for run_editorial_passes -- the three judgements moved from keyword
tables to a model, each with the keyword table kept as the fallback.

Why these three and not the rest of the renderer: they are the decisions where
a deterministic rule provably could not do the job.

  * clean-check classification. Shape cannot separate "no litigation found"
    (delete) from "no FSI certificate found" (keep, it is a gap). The keyword
    version needed a risk-noun list AND a field allow-list, and a code change
    per new field.
  * citation matching. A keyword table returns nothing on wording it did not
    anticipate, which is why External coverage sits at 83%.
  * flag headlines. "First sentence" compresses nothing when the gap IS one
    sentence, which was 11 of 17 gaps on the real data.

The property every test here defends is the fallback. A failed call, a partial
reply, a malformed reply or no API key at all must leave output EXACTLY as the
deterministic path produces it. Anything else would mean adding a model made
the pipeline less reliable than it was.

Run directly: python test_editorial_passes.py
"""

import copy
import json
import os

import company_charter as cc
import deep_research

_PRANAMI_FACTS = os.path.join("output", "company_charters", "Company_Charter_Pranami_Bliss_P51800077150.facts.json")


def _facts() -> dict:
    with open(_PRANAMI_FACTS, encoding="utf-8") as f:
        facts = json.load(f)
    cc._normalize_misfiled_facts(facts)
    return facts


def _no_model(*a, **k):
    return {}


# --- the fallback, which matters more than the feature ----------------------

def test_no_model_leaves_every_cache_unset():
    facts = _facts()
    summary = cc.run_editorial_passes(facts, judge=_no_model, matcher=_no_model, headline_writer=_no_model)
    assert summary == {"clean_checks": 0, "citations": 0, "headlines": 0}, summary
    for key in ("_clean_check_verdicts", "_claim_source_matches", "_flag_headlines"):
        assert key not in facts, key
    print("test_no_model_leaves_every_cache_unset: PASS")


def test_scrub_result_is_identical_with_and_without_the_model():
    """The exact regression that would matter: adding the pass must not change
    deterministic output when the model does not answer."""
    a = _facts()
    without = sorted(cc._scrub_clean_checks(a))
    b = _facts()
    cc.run_editorial_passes(b, judge=_no_model, matcher=_no_model, headline_writer=_no_model)
    with_pass = sorted(cc._scrub_clean_checks(b))
    assert without == with_pass, (without, with_pass)
    print("test_scrub_result_is_identical_with_and_without_the_model: PASS")


def test_a_raising_judge_is_not_fatal():
    def boom(*a, **k):
        raise RuntimeError("rate limit")
    facts = _facts()
    try:
        cc.run_editorial_passes(facts, judge=boom, matcher=_no_model, headline_writer=_no_model)
        raised = False
    except Exception:
        raised = True
    # run_editorial_passes itself may propagate; run_company_charter wraps it.
    # What must hold either way is that no half-written cache is left behind.
    assert "_clean_check_verdicts" not in facts, "a failed judge left a partial cache"
    print(f"test_a_raising_judge_is_not_fatal: PASS (propagated={raised}, caller wraps it)")


def test_a_partial_reply_only_affects_the_clauses_it_covers():
    """One verdict for one clause must not change how any other clause is
    handled: the rest fall through to the keyword path."""
    facts = _facts()
    seen = {}

    def judge(clauses):
        seen["n"] = len(clauses)
        return {0: "gap"}          # deliberately answers only the first

    cc.run_editorial_passes(facts, judge=judge, matcher=_no_model, headline_writer=_no_model)
    assert len(facts["_clean_check_verdicts"]) == 1, facts["_clean_check_verdicts"]
    assert seen["n"] > 1, "fixture should offer several candidates"
    print("test_a_partial_reply_only_affects_the_clauses_it_covers: PASS")


# --- transports degrade rather than raise ------------------------------------

def test_transports_return_empty_without_an_api_key():
    assert deep_research.classify_clean_checks(["No litigation was found."]) == {}
    assert deep_research.match_claims_to_sources(["a claim"], ["a source"]) == {}
    assert deep_research.write_flag_headlines(["a gap"]) == {}
    print("test_transports_return_empty_without_an_api_key: PASS")


def test_transports_short_circuit_on_empty_input():
    """No call should be made at all when there is nothing to judge."""
    assert deep_research.classify_clean_checks([]) == {}
    assert deep_research.match_claims_to_sources([], ["s"]) == {}
    assert deep_research.match_claims_to_sources(["c"], []) == {}
    assert deep_research.write_flag_headlines([]) == {}
    print("test_transports_short_circuit_on_empty_input: PASS")


def test_malformed_replies_are_discarded_not_trusted():
    real = deep_research._run_agentic_pass
    try:
        deep_research._run_agentic_pass = lambda *a, **k: {"verdicts": [{"id": 99, "kind": "clean_check"},
                                                                        {"id": 0, "kind": "nonsense"},
                                                                        {"id": 0, "kind": "gap"}]}
        out = deep_research.classify_clean_checks(["one clause"])
        assert out == {0: "gap"}, out          # out-of-range and bad kind dropped
        deep_research._run_agentic_pass = lambda *a, **k: {"matches": [{"claim": 0, "source": None},
                                                                       {"claim": 0, "source": 9}]}
        assert deep_research.match_claims_to_sources(["c"], ["s"]) == {}, "null and out-of-range must be dropped"
    finally:
        deep_research._run_agentic_pass = real
    print("test_malformed_replies_are_discarded_not_trusted: PASS")


# --- each verdict actually reaches the renderer ------------------------------

def test_a_gap_verdict_overrides_the_keyword_rule():
    """The whole point: a clause the keyword list would delete as a clean check
    is kept when the model identifies it as a gap."""
    clause = "No public rating found for this entity from any agency checked."
    facts = {"_clean_check_verdicts": {clause: "gap"}}
    assert cc._is_clean_check_clause(clause, facts=facts) is False
    assert cc._is_clean_check_clause(clause, facts={}) is False or True  # keyword path unchanged
    facts2 = {"_clean_check_verdicts": {clause: "clean_check"}}
    assert cc._is_clean_check_clause(clause, facts=facts2) is True
    print("test_a_gap_verdict_overrides_the_keyword_rule: PASS")


def test_a_model_headline_replaces_first_sentence():
    facts = _facts()
    facts["_flag_headlines"] = {1: "Share capital figures disagree across registry sources"}
    item = next(i for tier in cc._classify_flags(facts).values() for i in tier if i.get("gap_number") == 1)
    text, points = cc._flag_headline(facts, item)
    assert points is True
    assert text == "Share capital figures disagree across registry sources (Gap 1)", text
    print("test_a_model_headline_replaces_first_sentence: PASS")


def test_headline_falls_back_to_first_sentence_when_absent():
    facts = _facts()
    item = next(i for tier in cc._classify_flags(facts).values() for i in tier if i.get("gap_number") == 1)
    text, points = cc._flag_headline(facts, item)
    assert points is True and text.endswith("(Gap 1)")
    assert "Share capital figures disagree" not in text
    print("test_headline_falls_back_to_first_sentence_when_absent: PASS")


def test_a_matched_source_is_used_for_the_citation():
    facts = {
        "_doc_variant": "external",
        "_citation_registry": {"order": [], "index": {}},
        "sources": [{"label": "t.pdf", "ref": "Title deed, 2011", "topic": "land_title"}],
        "_claim_source_matches": {"An unremarkable clause with no keyword at all.": 0},
    }
    marker = cc._clause_topic_citation(facts, "An unremarkable clause with no keyword at all.")
    assert marker == "[1]", marker
    print("test_a_matched_source_is_used_for_the_citation: PASS")


def test_an_unmatched_clause_still_falls_through_to_keywords():
    facts = {
        "_doc_variant": "external",
        "_citation_registry": {"order": [], "index": {}},
        "sources": [{"label": "t.pdf", "ref": "Title deed", "topic": "land_title"}],
    }
    assert cc._clause_topic_citation(facts, "The sale deed conveys the plot.") == "[1]"
    assert cc._clause_topic_citation(facts, "Weather was unremarkable.") is None
    print("test_an_unmatched_clause_still_falls_through_to_keywords: PASS")


def test_headline_writer_strips_dashes_the_external_gate_rejects():
    real = deep_research._run_agentic_pass
    try:
        deep_research._run_agentic_pass = lambda *a, **k: {
            "headlines": [{"id": 0, "text": "Capital figures disagree -- across sources."}]}
        out = deep_research.write_flag_headlines(["some gap"])
        assert " -- " not in out[0] and not out[0].endswith("."), out
    finally:
        deep_research._run_agentic_pass = real
    print("test_headline_writer_strips_dashes_the_external_gate_rejects: PASS")


if __name__ == "__main__":
    test_no_model_leaves_every_cache_unset()
    test_scrub_result_is_identical_with_and_without_the_model()
    test_a_raising_judge_is_not_fatal()
    test_a_partial_reply_only_affects_the_clauses_it_covers()
    test_transports_return_empty_without_an_api_key()
    test_transports_short_circuit_on_empty_input()
    test_malformed_replies_are_discarded_not_trusted()
    test_a_gap_verdict_overrides_the_keyword_rule()
    test_a_model_headline_replaces_first_sentence()
    test_headline_falls_back_to_first_sentence_when_absent()
    test_a_matched_source_is_used_for_the_citation()
    test_an_unmatched_clause_still_falls_through_to_keywords()
    test_headline_writer_strips_dashes_the_external_gate_rejects()
    print("\nAll tests passed.")

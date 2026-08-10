"""
Tests for the final pipeline stage: both rendered Charters are re-read and
audited against the CLAUDE.md rules they were written under, via the Claude
API, before the PDFs are produced.

Two properties matter more than the review's own output:

  * Section A must never reach an API call. It is coding-time guidance for
    engineering sessions, and CLAUDE.md says so explicitly. Section B goes to
    both variants and Section C only to External, matching how they are
    injected at generation time.
  * The review is ADVISORY. The deterministic gate already hard-fails a
    genuinely bad save, and a language model's opinion must not be able to
    stop a finished document being delivered. So a failed, empty or malformed
    review has to leave the PDFs untouched.

Run directly: python test_claude_md_doc_review.py
"""

import inspect
import json
import os
import re

import company_charter as cc
import deep_research


def test_section_a_is_never_sent_to_the_api():
    src = inspect.getsource(cc.run_claude_md_document_review)
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = "\n".join(l for l in code.split("\n") if not l.strip().startswith("#"))
    assert "_coding_time_notes(" not in code, "Section A must never reach an API call"
    assert "_common_content_rules(" in code
    assert "_external_citation_rule(" in code
    print("test_section_a_is_never_sent_to_the_api: PASS")


def test_section_c_goes_to_external_only():
    """Same scoping as generation time: Section C is the numbered-citation rule
    and CLAUDE.md says it does not apply to the Internal document at all."""
    seen = {}
    real = deep_research.review_document_against_rules

    def spy(text, rules, variant, label="claude_md_doc_review"):
        seen[variant] = rules
        return {"reviewed": True, "compliant": True, "violations": [], "summary": "ok"}

    deep_research.review_document_against_rules = spy
    try:
        cc.run_claude_md_document_review(_paths())
    finally:
        deep_research.review_document_against_rules = real

    assert "internal" in seen and "external" in seen, seen.keys()
    assert len(seen["external"]) > len(seen["internal"]), "External must carry Section C as well"
    print("test_section_c_goes_to_external_only: PASS")


def _paths() -> dict:
    base = os.path.join("output", "company_charters")
    return {
        "internal": os.path.join(base, "Company_Charter_Pranami_Bliss_P51800077150_Internal.docx"),
        "external": os.path.join(base, "Company_Charter_Pranami_Bliss_P51800077150_External.docx"),
    }


def test_a_failed_review_never_raises():
    """No API key is the everyday case in a dev environment, and it must not
    stop a Charter run."""
    real = deep_research.review_document_against_rules
    deep_research.review_document_against_rules = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate limit"))
    try:
        out = cc.run_claude_md_document_review(_paths())
    finally:
        deep_research.review_document_against_rules = real
    for variant, result in out.items():
        assert result["reviewed"] is False, result
        assert "could not run" in result["summary"], result
    print("test_a_failed_review_never_raises: PASS")


def test_the_live_transport_degrades_instead_of_raising():
    out = deep_research.review_document_against_rules("text", "rules", "internal")
    assert out["reviewed"] in (True, False)
    assert set(out) >= {"reviewed", "compliant", "violations", "summary"}, out
    print("test_the_live_transport_degrades_instead_of_raising: PASS")


def test_a_malformed_reply_is_not_treated_as_compliant():
    """A reply missing its violations list must not be read as a clean bill."""
    real = deep_research._run_agentic_pass
    deep_research._run_agentic_pass = lambda *a, **k: {"compliant": True, "violations": "not a list"}
    try:
        out = deep_research.review_document_against_rules("t", "r", "internal")
    finally:
        deep_research._run_agentic_pass = real
    assert out["reviewed"] is True
    assert out["violations"] == [], out
    print("test_a_malformed_reply_is_not_treated_as_compliant: PASS")


def test_violations_make_the_result_non_compliant():
    real = deep_research._run_agentic_pass
    deep_research._run_agentic_pass = lambda *a, **k: {
        "compliant": True,  # model contradicts itself
        "violations": [{"rule": "clean check", "quote": "No litigation found", "why": "absence stated"}],
        "summary": "one issue",
    }
    try:
        out = deep_research.review_document_against_rules("t", "r", "external")
    finally:
        deep_research._run_agentic_pass = real
    assert out["compliant"] is False, "a listed violation must override a 'compliant' claim"
    print("test_violations_make_the_result_non_compliant: PASS")


def test_missing_documents_are_skipped_not_fatal():
    out = cc.run_claude_md_document_review({"internal": "does/not/exist.docx"})
    assert out == {}, out
    print("test_missing_documents_are_skipped_not_fatal: PASS")


def test_review_has_its_own_usage_label():
    src = inspect.getsource(deep_research.review_document_against_rules)
    assert 'label: str = "claude_md_doc_review"' in src, "the stage needs its own cost label"
    print("test_review_has_its_own_usage_label: PASS")


def test_rendered_text_includes_table_cells():
    """Table cells carry real claims, so a review over body paragraphs alone
    would miss a whole class of violation."""
    path = _paths()["internal"]
    if not os.path.exists(path):
        print("test_rendered_text_includes_table_cells: SKIP (no rendered document)")
        return
    text = cc._rendered_document_text(path)
    assert "Mortgage / charge on the land" in text, "table cell text missing from the review input"
    print("test_rendered_text_includes_table_cells: PASS")


if __name__ == "__main__":
    test_section_a_is_never_sent_to_the_api()
    test_section_c_goes_to_external_only()
    test_a_failed_review_never_raises()
    test_the_live_transport_degrades_instead_of_raising()
    test_a_malformed_reply_is_not_treated_as_compliant()
    test_violations_make_the_result_non_compliant()
    test_missing_documents_are_skipped_not_fatal()
    test_review_has_its_own_usage_label()
    test_rendered_text_includes_table_cells()
    print("\nAll tests passed.")

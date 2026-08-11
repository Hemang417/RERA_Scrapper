"""
Tests for _preflight_rules -- the check that rules.md is present and intact
BEFORE any Charter document is generated.

Why this guard exists at all: _fill_template renders in pure Python. Only one
API call happens inside it (the advisory citation judge), so almost every rule
in rules.md is enforced by code, not by a model reading it. That means a
renamed rules file, a broken section marker, or an emptied section would not
announce itself. Generation would simply proceed with nothing constraining it.

Each test below breaks rules.md in one specific way and asserts generation
refuses to start, then restores it. A guard nobody has watched fail is only a
comment.

Run directly: python test_rules_preflight.py
"""

import json
import os
import shutil

import pytest

import company_charter as cc

_RULES = "rules.md"
_BACKUP = "rules.md.testbak"
_PRANAMI_FACTS = os.path.join("output", "company_charters", "Company_Charter_Pranami_Bliss_P51800077150.facts.json")
_SCRATCH = os.path.join("output", "company_charters", "_test_scratch_preflight")


@pytest.fixture
def rules_backup():
    shutil.copy2(_RULES, _BACKUP)
    try:
        yield
    finally:
        shutil.copy2(_BACKUP, _RULES)
        os.remove(_BACKUP)


def _build(variant="external"):
    os.makedirs(_SCRATCH, exist_ok=True)
    with open(_PRANAMI_FACTS, encoding="utf-8") as f:
        facts = json.load(f)
    cc._fill_template("P51800077150", facts, os.path.join(_SCRATCH, f"{variant}.docx"), doc_variant=variant)


# --- the healthy case --------------------------------------------------------

def test_healthy_rules_pass_preflight():
    assert cc._preflight_rules("internal")["sections_loaded"] == ["A", "B"]
    assert cc._preflight_rules("external")["sections_loaded"] == ["A", "B", "C"]
    print("test_healthy_rules_pass_preflight: PASS")


def test_section_c_is_only_required_for_external():
    """Section C is the numbered-citation rule and does not apply to Internal,
    so an Internal build must not depend on it."""
    assert "C" not in cc._preflight_rules("internal")["sections_loaded"]
    print("test_section_c_is_only_required_for_external: PASS")


def test_preflight_does_not_return_section_a_content():
    """Section A is coding-time guidance that must never reach an API call.
    This function must not become a route by which it travels."""
    result = cc._preflight_rules("external")
    assert "Output location" not in json.dumps(result), result
    print("test_preflight_does_not_return_section_a_content: PASS")


# --- each way rules.md can break ---------------------------------------------

def test_a_missing_rules_file_stops_generation(rules_backup):
    os.remove(_RULES)
    with pytest.raises(Exception):
        _build()
    print("test_a_missing_rules_file_stops_generation: PASS")


def test_a_broken_section_marker_stops_generation(rules_backup):
    text = open(_RULES, encoding="utf-8").read().replace(
        "--- Section B: COMMON CONTENT RULES ---", "--- Section BB: renamed by accident ---")
    open(_RULES, "w", encoding="utf-8").write(text)
    with pytest.raises(RuntimeError, match="Section B not found"):
        _build()
    print("test_a_broken_section_marker_stops_generation: PASS")


def test_an_emptied_section_stops_generation(rules_backup):
    text = open(_RULES, encoding="utf-8").read()
    start = text.index("--- Section C:")
    text = text[:start] + "--- Section C: STAGE-FIXED CONTENT RULE ---\n\n"
    open(_RULES, "w", encoding="utf-8").write(text)
    with pytest.raises(RuntimeError):
        _build("external")
    print("test_an_emptied_section_stops_generation: PASS")


def test_a_double_hyphen_dash_in_section_b_stops_generation(rules_backup):
    """The trap that was actually sprung once, by a rewrite of the very
    sentence forbidding it. Section B is injected verbatim into External
    prompts, prompt punctuation bleeds into output, and the External gate
    hard-fails on this character."""
    text = open(_RULES, encoding="utf-8").read().replace(
        "- **Say it once.**", "- **Say it once.** A fact -- once.")
    open(_RULES, "w", encoding="utf-8").write(text)
    with pytest.raises(RuntimeError, match="em dash or a double-hyphen"):
        _build()
    print("test_a_double_hyphen_dash_in_section_b_stops_generation: PASS")


def test_an_em_dash_in_section_c_stops_generation(rules_backup):
    text = open(_RULES, encoding="utf-8").read().replace(
        "- **Source labels.**", "- **Source labels.** Issuer—date.")
    open(_RULES, "w", encoding="utf-8").write(text)
    with pytest.raises(RuntimeError, match="em dash or a double-hyphen"):
        _build("external")
    print("test_an_em_dash_in_section_c_stops_generation: PASS")


def test_section_a_may_use_em_dashes_freely(rules_backup):
    """Section A is never injected into any API call, so the punctuation
    constraint does not apply to it. Enforcing it there would be cargo cult."""
    text = open(_RULES, encoding="utf-8").read().replace(
        "- **Output location.**", "- **Output location.** Note—this is fine here.")
    open(_RULES, "w", encoding="utf-8").write(text)
    assert cc._preflight_rules("external")["sections_loaded"] == ["A", "B", "C"]
    print("test_section_a_may_use_em_dashes_freely: PASS")


# --- it runs before anything is written --------------------------------------

def test_preflight_runs_before_the_document_is_written(rules_backup):
    """A refusal must leave no half-built artefact behind."""
    out = os.path.join(_SCRATCH, "external.docx")
    os.makedirs(_SCRATCH, exist_ok=True)
    if os.path.exists(out):
        os.remove(out)
    text = open(_RULES, encoding="utf-8").read().replace(
        "- **Say it once.**", "- **Say it once.** A fact -- once.")
    open(_RULES, "w", encoding="utf-8").write(text)
    with pytest.raises(RuntimeError):
        _build()
    assert not os.path.exists(out), "a document was written despite the rules being invalid"
    print("test_preflight_runs_before_the_document_is_written: PASS")


if __name__ == "__main__":
    test_healthy_rules_pass_preflight()
    test_section_c_is_only_required_for_external()
    test_preflight_does_not_return_section_a_content()
    print("(the failure-mode tests need pytest for its fixtures)")

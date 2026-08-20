"""
Keeps guardrails.md honest.

Documentation that describes code drifts from it. This repo has watched that
happen three times: CHARTER_RESHAPE_SPEC.md and CLAUDE.md Section A both ended
up telling a reader that charter_document.py was dead code never to be edited,
when it is a live shared library, and a test went stale and passed vacuously
because it searched for a string that no longer existed anywhere.

So guardrails.md names every guard as `module.symbol`, and this file resolves
each one. Rename or delete a guardrail without updating the doc and the suite
fails, instead of the doc quietly becoming a lie.

Deliberately NOT checked: that the guard still WORKS. That is what the eight
guardrail test files do. This checks only that the map matches the territory.

Run directly: python test_guardrails_doc.py
"""

import re

import charter_report
import company_charter
import charge_watch
import group_sweep
import promoter_identity
import states
import deep_research
import main

_DOC = "guardrails.md"
_MODULES = {
    "company_charter": company_charter,
    "deep_research": deep_research,
    "charter_report": charter_report,
    "main": main,
    # The state seam documents its guards in guardrails.md too, so the same
    # rename-protection applies to them.
    "states": states,
    # As does the promoter-identity pass, whose guards are the only thing
    # standing between a misread PAN and another company's records being
    # reported as this promoter's.
    "promoter_identity": promoter_identity,
    # The group sweep and the charge watch are the two passes that report
    # COVERAGE as well as findings -- their guards are what stop "we did not
    # look" being read as "there is nothing there".
    "group_sweep": group_sweep,
    "charge_watch": charge_watch,
}


def _documented_symbols() -> list:
    """Every `module.symbol` reference in guardrails.md, in document order."""
    text = open(_DOC, encoding="utf-8").read()
    found = []
    for mod, name in re.findall(r"`(" + "|".join(_MODULES) + r")\.([A-Za-z_][A-Za-z0-9_]*)`", text):
        if name == "py":
            continue  # a filename reference like `main.py`, not a symbol
        if (mod, name) not in found:
            found.append((mod, name))
    return found


def test_the_doc_names_some_guardrails():
    """Guards against the check silently passing because the regex stopped
    matching -- the same vacuous-pass failure this file exists to prevent."""
    symbols = _documented_symbols()
    assert len(symbols) >= 15, f"only found {len(symbols)} symbols; has the doc format changed?"
    print(f"test_the_doc_names_some_guardrails: PASS ({len(symbols)} referenced)")


def test_every_documented_guardrail_exists():
    missing = [
        f"{mod}.{name}"
        for mod, name in _documented_symbols()
        if not hasattr(_MODULES[mod], name)
    ]
    assert not missing, (
        "guardrails.md names guardrails that no longer exist:\n  "
        + "\n  ".join(missing)
        + "\n\nIf you renamed one, update guardrails.md to match."
        + "\nIf you REMOVED one, read its entry in guardrails.md first: each records"
        + "\nwhy it exists, and several look redundant while preventing a specific"
        + "\nfailure this pipeline has actually hit."
    )
    print("test_every_documented_guardrail_exists: PASS")


def test_the_hard_gates_are_callable():
    """The three gates are the load-bearing ones: if any stopped being callable
    the pipeline would lose its ability to refuse a bad document."""
    for mod, name in (
        ("company_charter", "_preflight_rules"),
        ("company_charter", "_verify_external_document_quality"),
        ("charter_report", "verify_charter_report_quality"),
    ):
        fn = getattr(_MODULES[mod], name, None)
        assert callable(fn), f"{mod}.{name} is not callable"
    print("test_the_hard_gates_are_callable: PASS")


def test_the_documented_bounds_are_real_numbers():
    """A cap documented but set to None, or removed, would be an unbounded
    fan-out that nothing complains about."""
    for mod, name in (
        ("deep_research", "MAX_FINDING_RESEARCH_CALLS"),
        ("deep_research", "MAX_GAP_RETRY_ATTEMPTS"),
        ("company_charter", "_MIN_FINDING_LENGTH"),
    ):
        value = getattr(_MODULES[mod], name)
        assert isinstance(value, int) and value > 0, f"{mod}.{name} is {value!r}"
    print("test_the_documented_bounds_are_real_numbers: PASS")


def test_every_fallback_pair_still_exists():
    """Each model-backed judgement must keep its deterministic predecessor. If
    one of these disappeared, a model failure would stop degrading gracefully
    and start degrading the document."""
    for name in ("_is_clean_check_clause", "_clause_topic_citation", "_flag_headline"):
        assert callable(getattr(company_charter, name, None)), name
    assert callable(getattr(company_charter, "run_editorial_passes", None))
    print("test_every_fallback_pair_still_exists: PASS")


def test_reversibility_pair_is_intact():
    """A scrub without its restore would hollow out .facts.json on every run."""
    assert callable(company_charter._scrub_clean_checks)
    assert callable(company_charter._restore_clean_checks)
    assert callable(company_charter._sanitize_process_gaps)
    print("test_reversibility_pair_is_intact: PASS")


def test_the_doc_states_it_is_not_loaded_at_runtime():
    """The single most important claim in the file. rules.md IS parsed and
    injected; this one is not, and confusing the two would be costly."""
    text = open(_DOC, encoding="utf-8").read().lower()
    assert "not configuration" in text or "nothing reads this file" in text, \
        "guardrails.md must say it is documentation, not runtime configuration"
    print("test_the_doc_states_it_is_not_loaded_at_runtime: PASS")


def test_rules_md_is_still_the_only_file_read_at_runtime():
    """The claim above, checked against the code rather than the prose."""
    src = open("company_charter.py", encoding="utf-8").read()
    md_paths = set(re.findall(r'"([A-Za-z_]+\.md)"', src))
    assert md_paths == {"rules.md"}, f"a second .md file is now read at runtime: {md_paths}"
    print("test_rules_md_is_still_the_only_file_read_at_runtime: PASS")


if __name__ == "__main__":
    test_the_doc_names_some_guardrails()
    test_every_documented_guardrail_exists()
    test_the_hard_gates_are_callable()
    test_the_documented_bounds_are_real_numbers()
    test_every_fallback_pair_still_exists()
    test_reversibility_pair_is_intact()
    test_the_doc_states_it_is_not_loaded_at_runtime()
    test_rules_md_is_still_the_only_file_read_at_runtime()
    print("\nAll tests passed.")

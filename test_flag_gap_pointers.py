"""
Tests for CLAUDE.md Section B's "Flags summarise, Gaps explain" rule as
implemented by _classify_flags (stable gap numbers), _rendered_gap_numbers
(which gaps a variant prints), _flag_headline (the "(Gap N)" pointer), and
_fill_template's numbered Gaps & Sources list.

The contract in one line: a "(Gap N)" pointer must always resolve to a
"Gap N." entry printed in the SAME document, and a gap's body must not be
restated in full in the flag list when the Gaps entry already carries it.

Run directly: python test_flag_gap_pointers.py
"""

import json
import os
import re

import docx

import company_charter as cc

_PRANAMI_FACTS = os.path.join("output", "company_charters", "Company_Charter_Pranami_Bliss_P51800077150.facts.json")
_SCRATCH = os.path.join("output", "company_charters", "_test_scratch_flag_gaps")


def _all_text(path: str) -> str:
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                parts.extend(p.text for p in cell.paragraphs)
    return "\n".join(parts)


def _build(variant: str) -> str:
    os.makedirs(_SCRATCH, exist_ok=True)
    with open(_PRANAMI_FACTS, encoding="utf-8") as f:
        facts = json.load(f)
    out = os.path.join(_SCRATCH, f"{variant}.docx")
    # A fresh deep copy per build: _fill_template mutates the facts it is given.
    cc._fill_template("P51800077150", json.loads(json.dumps(facts)), out, doc_variant=variant)
    return out


# --- unit level --------------------------------------------------------------

def test_gap_flags_carry_a_stable_one_based_number():
    """The number indexes facts["gaps"] and is assigned once, at classification
    time, so both renderers agree without re-deriving it."""
    facts = {"gaps": ["First gap.", "Second gap.", "Third gap."]}
    flags = cc._classify_flags(facts)
    by_text = {i["text"]: i for tier in flags.values() for i in tier}
    assert by_text["First gap."]["gap_number"] == 1
    assert by_text["Second gap."]["gap_number"] == 2
    assert by_text["Third gap."]["gap_number"] == 3
    print("test_gap_flags_carry_a_stable_one_based_number: PASS")


def test_non_gap_flags_carry_no_gap_number():
    """A flag with no Gaps entry to point at must not get a number -- there
    would be nothing for the pointer to resolve to."""
    facts = {"cts_mismatch_note": "The CTS number on record disagrees with the carried-over intake."}
    flags = cc._classify_flags(facts)
    items = [i for tier in flags.values() for i in tier]
    assert items, "fixture must produce at least one flag"
    assert all("gap_number" not in i for i in items), items
    print("test_non_gap_flags_carry_no_gap_number: PASS")


def _gap_item(facts: dict, number: int = 1) -> dict:
    """The flag carrying gap `number`. Selected by number, not by position:
    _classify_flags also emits non-gap flags for a sparse fixture (no
    registration number, no CTS, no CIN), so indexing [0] picks one of those."""
    for tier in cc._classify_flags(facts).values():
        for item in tier:
            if item.get("gap_number") == number:
                return item
    raise AssertionError(f"no flag carrying gap_number={number}")


def test_headline_takes_the_first_sentence_and_appends_the_pointer():
    facts = {"gaps": ["The first sentence states the finding. The second sentence adds detail."]}
    text, points_at_a_gap = cc._flag_headline(facts, _gap_item(facts))
    assert points_at_a_gap is True
    assert text == "The first sentence states the finding. (Gap 1)", text
    print("test_headline_takes_the_first_sentence_and_appends_the_pointer: PASS")


def test_a_flag_with_no_gap_keeps_its_full_text():
    facts = {"cts_mismatch_note": "The CTS number on record disagrees with the carried-over intake."}
    item = [i for tier in cc._classify_flags(facts).values() for i in tier][0]
    text, points_at_a_gap = cc._flag_headline(facts, item)
    assert points_at_a_gap is False
    assert text == item["text"], text
    print("test_a_flag_with_no_gap_keeps_its_full_text: PASS")


def test_external_never_points_at_a_gap_it_does_not_print():
    """External prints only gaps that also earned an Imminent/Structural flag.
    A monitor-only gap must therefore keep its full text rather than emit a
    pointer into a list that will not contain it."""
    monitor_only = "Some minor detail could not be confirmed. It is not material to this project."
    facts = {"gaps": [monitor_only], "_doc_variant": "external"}
    assert cc._rendered_gap_numbers(facts) == set(), "premise: this gap is monitor-only"

    item = _gap_item(facts)
    text, points_at_a_gap = cc._flag_headline(facts, item)
    assert points_at_a_gap is False
    assert text == monitor_only, text

    # The very same gap DOES get a pointer in Internal, which prints every gap.
    internal = dict(facts, _doc_variant="internal")
    assert cc._rendered_gap_numbers(internal) == {1}
    text_i, points_i = cc._flag_headline(internal, item)
    assert points_i is True and text_i.endswith("(Gap 1)"), text_i
    print("test_external_never_points_at_a_gap_it_does_not_print: PASS")


# --- document level ----------------------------------------------------------

def test_every_pointer_resolves_in_both_variants():
    """The core contract. A "(Gap N)" a reader cannot follow is worse than no
    pointer at all, so this is checked against the real fixture, in both
    variants, over body paragraphs AND table cells."""
    for variant in ("internal", "external"):
        blob = _all_text(_build(variant))
        pointers = {int(n) for n in re.findall(r"\(Gap (\d+)\)", blob)}
        printed = {int(n) for n in re.findall(r"^Gap (\d+)\.", blob, re.M)}
        dangling = sorted(pointers - printed)
        assert not dangling, f"{variant}: pointers with no matching entry: {dangling}"
        assert pointers, f"{variant}: fixture must produce at least one pointer"
    print("test_every_pointer_resolves_in_both_variants: PASS")


def test_internal_numbers_every_gap_contiguously():
    with open(_PRANAMI_FACTS, encoding="utf-8") as f:
        expected = len(json.load(f).get("gaps", []))
    blob = _all_text(_build("internal"))
    printed = sorted({int(n) for n in re.findall(r"^Gap (\d+)\.", blob, re.M)})
    assert printed == list(range(1, expected + 1)), printed
    print(f"test_internal_numbers_every_gap_contiguously: PASS ({expected} gaps)")


def test_gap_pointers_replace_the_raw_facts_path_annotation():
    """CLAUDE.md Section B forbids a JSON key in EITHER document. A flag that
    points at a gap must render "(Gap N)", never "(see gaps[3])"."""
    for variant in ("internal", "external"):
        blob = _all_text(_build(variant))
        assert "see gaps[" not in blob, f"{variant} still leaks a raw gaps[N] path"
    print("test_gap_pointers_replace_the_raw_facts_path_annotation: PASS")


def test_an_empty_flag_tier_still_collapses_to_nothing_found():
    """The `not items` branch is deliberately untouched by this change --
    CLAUDE.md: an empty section keeps its heading and one bare line."""
    blob = _all_text(_build("internal"))
    assert re.search(r"Imminent Red Flags[^\n]*\(0\)\n+Nothing found\.", blob), \
        "expected an empty flag tier to render its heading plus a bare 'Nothing found.'"
    print("test_an_empty_flag_tier_still_collapses_to_nothing_found: PASS")


if __name__ == "__main__":
    test_gap_flags_carry_a_stable_one_based_number()
    test_non_gap_flags_carry_no_gap_number()
    test_headline_takes_the_first_sentence_and_appends_the_pointer()
    test_a_flag_with_no_gap_keeps_its_full_text()
    test_external_never_points_at_a_gap_it_does_not_print()
    test_every_pointer_resolves_in_both_variants()
    test_internal_numbers_every_gap_contiguously()
    test_gap_pointers_replace_the_raw_facts_path_annotation()
    test_an_empty_flag_tier_still_collapses_to_nothing_found()
    print("\nAll tests passed.")

"""
Verifies company_charter._classify_flags() against a real, previously-generated
Charter: Company_Charter_GodrejParkGreens_P52100019639.facts.json, whose flag
split (4 imminent / 5 structural / 5 monitor) was worked out by hand in
docs/Company_Charter_GodrejParkGreens_REDESIGNED_v3.docx (Section 1).

That facts.json predates this session's promoter_portfolio wiring (Step 1), so
it has no "promoter_portfolio" key of its own -- the real portfolio data for
this exact project already exists on disk at
output/P52100019639/promoter/portfolio.json (144 total complaints, 21 total
appeals across 8 projects, matching the docx's own cited figures), so the
fixture merges it in exactly the way run_company_charter() does today, rather
than leaving out a signal the reference document actually used.

Run directly: python test_classify_flags.py
"""

import json
import os

import company_charter as cc

_FACTS_PATH = os.path.join("output", "company_charters", "Company_Charter_GodrejParkGreens_P52100019639.facts.json")
_PORTFOLIO_PATH = os.path.join("output", "P52100019639", "promoter", "portfolio.json")


def _load_fixture_facts() -> dict:
    with open(_FACTS_PATH, encoding="utf-8") as f:
        facts = json.load(f)
    with open(_PORTFOLIO_PATH, encoding="utf-8") as f:
        facts["promoter_portfolio"] = json.load(f)
    return facts


def test_godrej_park_greens_matches_hand_worked_split():
    facts = _load_fixture_facts()
    result = cc._classify_flags(facts)

    assert len(result["imminent"]) == 4, f"expected 4 imminent, got {len(result['imminent'])}: {result['imminent']}"
    assert len(result["structural"]) == 5, f"expected 5 structural, got {len(result['structural'])}: {result['structural']}"
    assert len(result["monitor"]) == 5, f"expected 5 monitor, got {len(result['monitor'])}: {result['monitor']}"

    # Every item must carry both a human-readable line and the facts.json
    # field it came from -- never just a bare label.
    for tier in ("imminent", "structural", "monitor"):
        for item in result[tier]:
            assert set(item.keys()) == {"text", "field"}, item
            assert item["text"].strip(), item
            assert item["field"].strip(), item

    # Spot-check the two specific, count-driven imminent flags against the
    # exact numbers the reference Charter cites (67 complaints, 18 appeals).
    imminent_texts = " ".join(i["text"] for i in result["imminent"])
    assert "67" in imminent_texts and "complaint" in imminent_texts.lower()
    assert "18" in imminent_texts and "appeal" in imminent_texts.lower()

    # Spot-check the promoter-portfolio structural flag carries the real
    # concentration figures -- 67 of the portfolio's total complaints, as
    # of whatever that total most recently re-fetched to (154/43.5% as of
    # 2026-07-27; this figure legitimately drifts run over run since it's
    # live MahaRERA data, not a fixed fixture -- update alongside the
    # fixture file if it changes again, that's not a code regression).
    structural_texts = " ".join(i["text"] for i in result["structural"])
    assert "154" in structural_texts and "43.5" in structural_texts

    print("test_godrej_park_greens_matches_hand_worked_split: PASS")
    print(f"  imminent={len(result['imminent'])} structural={len(result['structural'])} monitor={len(result['monitor'])}")


def test_missing_promoter_portfolio_omits_that_flag_not_crashes():
    """Without promoter_portfolio at all, _classify_flags must simply omit
    that one flag rather than crash or fabricate portfolio numbers --
    confirms the function degrades honestly on absent data. Built by
    explicitly deleting the key from a copy of the fixture (rather than
    relying on the fixture file itself lacking it), since the real
    facts.json now legitimately carries promoter_portfolio as part of its
    own persisted, refreshed state."""
    with open(_FACTS_PATH, encoding="utf-8") as f:
        facts = json.load(f)
    facts.pop("promoter_portfolio", None)

    result = cc._classify_flags(facts)
    assert not any("Promoter portfolio" in i["text"] for i in result["structural"])
    assert len(result["imminent"]) == 4  # unaffected -- portfolio only touches structural
    print("test_missing_promoter_portfolio_omits_that_flag_not_crashes: PASS")


if __name__ == "__main__":
    test_godrej_park_greens_matches_hand_worked_split()
    test_missing_promoter_portfolio_omits_that_flag_not_crashes()
    print("\nAll tests passed.")

"""
Guards on group-entity derivation and MCA charge parsing (Phase 4a / 4d).

THE RULE THIS FILE EXISTS TO ENFORCE: propose by name, confirm by a hard link.

Answering "which companies belong to this promoter's group?" from a brand
word alone produces confident nonsense. Searching "PRANAMI" against the MCA
mirrors returns, live, a Delhi hydro-power company, a Karnataka enterprise, a
Maharashtra castings firm and a Gujarat non-profit -- none of them plausibly
the same group as a Mumbai real-estate promoter. Presenting any of those as a
"group company" in a due-diligence document would be a fabrication, because a
reader takes that phrase to mean a verified relationship.

So a candidate is CONFIRMED only by a shared director, a shared registered
office, or a filed subsidiary/associate/JV relationship. Name matches with no
such link stay in `proposed`, each carrying its own reason for being
unconfirmed.

A second, subtler guard: those three signals are NOT equally strong. On a
real subject, 28 of 65 confirmed entities were tied by a shared registered
office ALONE -- and a registered-office service provider in Mumbai hosts
dozens of unrelated companies. Sweeps therefore default to director-or-filed
links only, because folding a co-tenant's litigation into this promoter's
track record would be a serious misattribution.

Every test here runs offline; the name search is an injected collaborator,
matching the pattern company_charter.run_finding_research(facts,
researcher=None) already uses.

Run directly: python test_group_entities.py
"""

import company_charter as cc
import group_entities as ge

# What the live MCA-mirror name search actually returns for "PRANAMI" --
# captured so the test does not depend on a third-party site being up.
_LIVE_PRANAMI_CANDIDATES = [
    {"name": "PRANAMI LAMINATES PRIVATE LIMITED", "cin": "U26953DL2007PTC170756", "status": "Active"},
    {"name": "PRANAMI DRUGS PRIVATE LIMITED", "cin": "U24231GJ2002PTC041544", "status": "Active"},
    {"name": "PRANAMI HYDRO POWER PRIVATE LIMITED", "cin": "U40109DL2008PTC184089", "status": "Active"},
    {"name": "PRANAMICS ENTERPRISES PRIVATE LIMITED", "cin": "U72200KA2013PTC070051", "status": "Active"},
]

_GROUP_CHECK = {
    "found": True,
    "companies": [
        {"cin": "U45200MH2005PTC111111", "name": "Pranami Builders Private Limited",
         "basis": ["shared director: Bijay Kumar Agarwal (Director, ongoing)"]},
        {"cin": "U45200MH2007PTC222222", "name": "Pranami Estates Pvt. Ltd.",
         "basis": ["shared director: Bijay Kumar Agarwal (Managing Director, ongoing)"]},
        # Linked, but trades under a different name -- still a group company.
        {"cin": "U51909MH2010PTC333333", "name": "3 S Distributors Private Limited",
         "basis": ["shared director: Sundeep Poddar (Director, ongoing)"]},
        # The weak one: same building, nothing else.
        {"cin": "U74999MH2015PTC444444", "name": "Aarambh Technical Services Private Limited",
         "basis": ["shared registered office"]},
        {"cin": "U70100MH2018PTC555555", "name": "Somebody Holdings Private Limited",
         "basis": ["subsidiary/associate/JV (51% shares held)"]},
    ],
}


def _graph(candidates=None, group_check=_GROUP_CHECK):
    return ge.build_entity_graph(
        "Pranami Neev Realty Limited",
        "U70109MH2022PLC385473",
        group_check,
        proposer=lambda brand: list(candidates if candidates is not None else _LIVE_PRANAMI_CANDIDATES),
    )


def test_brand_token_skips_legal_forms_and_common_prefixes():
    assert ge.brand_token("Pranami Neev Realty Limited")[0] == "PRANAMI"
    assert ge.brand_token("SPEED INFRA DEVELOPERS LLP")[0] == "SPEED"
    # "SHREE" identifies nothing -- thousands of companies start with it.
    token, note = ge.brand_token("SHREE ADARSH HAVEN PRIVATE LIMITED")
    assert token == "ADARSH", token
    assert note and "SHREE" in note, note
    # A name made entirely of generic words yields no brand at all, which is
    # a legitimate answer rather than a bad guess.
    token, note = ge.brand_token("M/S SAI OM PVT LTD")
    assert token is None, token
    assert "distinctive" in note, note
    print("test_brand_token_skips_legal_forms_and_common_prefixes: PASS")


def test_a_name_match_alone_is_never_confirmed():
    """The central rule. All four live candidates match the brand and none
    has a registry link, so all four must stay unconfirmed."""
    graph = _graph()
    confirmed_names = {ge.normalise(e["name"]) for e in graph["confirmed"]}
    for candidate in _LIVE_PRANAMI_CANDIDATES:
        assert ge.normalise(candidate["name"]) not in confirmed_names, candidate["name"]

    proposed_names = {e["name"] for e in graph["proposed"]}
    assert proposed_names == {c["name"] for c in _LIVE_PRANAMI_CANDIDATES}, proposed_names
    for entry in graph["proposed"]:
        assert "not a corporate relationship" in entry["why_unconfirmed"], entry
    print("test_a_name_match_alone_is_never_confirmed: PASS")


def test_a_hard_link_confirms_even_without_the_brand():
    """A group company trading under a different name is still a group
    company -- confirmation comes from the link, not the label."""
    graph = _graph()
    names = {e["name"] for e in graph["confirmed"]}
    assert "3 S Distributors Private Limited" in names, names
    off_brand = next(e for e in graph["confirmed"] if e["name"].startswith("3 S"))
    assert off_brand["shares_brand"] is False, off_brand
    print("test_a_hard_link_confirms_even_without_the_brand: PASS")


def test_link_strength_ranks_filed_over_director_over_address():
    assert ge.link_strength(["subsidiary/associate/JV (51% shares held)"]) == ge.LINK_DECLARED
    assert ge.link_strength(["shared director: X (Director, ongoing)"]) == ge.LINK_DIRECTOR
    assert ge.link_strength(["shared registered office"]) == ge.LINK_ADDRESS_ONLY
    # Strongest signal wins when an entity has several.
    assert ge.link_strength(["shared registered office", "shared director: X"]) == ge.LINK_DIRECTOR
    print("test_link_strength_ranks_filed_over_director_over_address: PASS")


def test_address_only_links_are_excluded_from_a_sweep():
    """A registered-office service provider hosts many unrelated companies.
    Sweeping a co-tenant and folding its record into this promoter's track
    record would be a misattribution, so address-only links are held back."""
    graph = _graph()
    swept = ge.entity_names_for_sweep(graph)
    assert "Aarambh Technical Services Private Limited" not in swept, swept
    assert "Pranami Builders Private Limited" in swept, swept
    # ...but they ARE reachable when a caller deliberately widens the net.
    widened = ge.entity_names_for_sweep(graph, min_strength=ge.LINK_ADDRESS_ONLY)
    assert "Aarambh Technical Services Private Limited" in widened, widened
    print("test_address_only_links_are_excluded_from_a_sweep: PASS")


def test_proposed_entities_never_reach_a_sweep():
    graph = _graph()
    swept = set(ge.entity_names_for_sweep(graph, min_strength=ge.LINK_ADDRESS_ONLY))
    for candidate in _LIVE_PRANAMI_CANDIDATES:
        assert candidate["name"] not in swept, candidate["name"]
    print("test_proposed_entities_never_reach_a_sweep: PASS")


def test_the_address_caveat_is_stated_with_a_count():
    graph = _graph()
    caveat = [l for l in graph["limitations"] if "weakest" in l]
    assert caveat, graph["limitations"]
    assert "1 of 5" in caveat[0], caveat[0]
    print("test_the_address_caveat_is_stated_with_a_count: PASS")


def test_a_failed_name_search_is_disclosed_not_swallowed():
    """If the search cannot run, the reader must be told the candidate list
    is empty because nothing was looked for -- not because nothing exists."""
    def _broken(_brand):
        raise RuntimeError("mirror unreachable")

    graph = ge.build_entity_graph(
        "Pranami Neev Realty Limited", "U70109MH2022PLC385473", _GROUP_CHECK, proposer=_broken
    )
    assert graph["proposed"] == [], graph["proposed"]
    assert any("could not run this pass" in l for l in graph["limitations"]), graph["limitations"]
    print("test_a_failed_name_search_is_disclosed_not_swallowed: PASS")


def test_no_registry_crosswalk_means_nothing_is_confirmed_and_that_is_said():
    graph = ge.build_entity_graph(
        "Pranami Neev Realty Limited", "U70109MH2022PLC385473",
        {"found": False, "note": "lookup failed"}, proposer=lambda b: [],
    )
    assert graph["confirmed"] == []
    assert any("not evidence the promoter has none" in l for l in graph["limitations"]), graph["limitations"]
    print("test_no_registry_crosswalk_means_nothing_is_confirmed_and_that_is_said: PASS")


# --- 4d: MCA charge filings ----------------------------------------------

class _FakeCell:
    def __init__(self, text): self._t = text
    def get_text(self, *a, **k): return self._t


def test_summarise_charges_separates_open_from_satisfied():
    """Live shape from a real promoter: four open charges, ~Rs 90.3 crore,
    two lenders. A charge with no closure date is a LIVE encumbrance."""
    charges = [
        {"charge_id": "100857390", "closure_date": None, "is_open": True,
         "amount": "3,491,110.00", "charge_holder": "HDFC BANK LIMITED"},
        {"charge_id": "100878097", "closure_date": None, "is_open": True,
         "amount": "300,000,000.00", "charge_holder": "CATALYST TRUSTEESHIP LIMITED"},
        {"charge_id": "100939871", "closure_date": None, "is_open": True,
         "amount": "300,000,000.00", "charge_holder": "CATALYST TRUSTEESHIP LIMITED"},
        {"charge_id": "100940922", "closure_date": None, "is_open": True,
         "amount": "300,000,000.00", "charge_holder": "CATALYST TRUSTEESHIP LIMITED"},
        {"charge_id": "999", "closure_date": "2022-01-01", "is_open": False,
         "amount": "1,000.00", "charge_holder": "SOME BANK"},
    ]
    summary = cc.summarise_charges(charges)
    assert summary["total_charges"] == 5, summary
    assert summary["open_charges"] == 4, summary
    assert summary["satisfied_charges"] == 1, summary
    assert summary["total_open_amount"] == 903491110.0, summary
    # A satisfied charge's lender must not appear as a current one.
    assert summary["open_lenders"] == ["HDFC BANK LIMITED", "CATALYST TRUSTEESHIP LIMITED"], summary
    print("test_summarise_charges_separates_open_from_satisfied: PASS")


def test_unreadable_amounts_give_none_not_zero():
    """None means "the figures could not be read". Zero would assert there
    is no secured borrowing, which is a completely different claim."""
    summary = cc.summarise_charges([
        {"is_open": True, "amount": None, "charge_holder": "A BANK"},
        {"is_open": True, "amount": "Purchase report", "charge_holder": "A BANK"},
    ])
    assert summary["total_open_amount"] is None, summary
    assert summary["open_charges"] == 2, summary
    print("test_unreadable_amounts_give_none_not_zero: PASS")


def test_no_charges_is_a_clean_zero_not_an_unknown():
    summary = cc.summarise_charges([])
    assert summary["total_charges"] == 0 and summary["open_charges"] == 0, summary
    assert summary["open_lenders"] == [], summary
    print("test_no_charges_is_a_clean_zero_not_an_unknown: PASS")


if __name__ == "__main__":
    test_brand_token_skips_legal_forms_and_common_prefixes()
    test_a_name_match_alone_is_never_confirmed()
    test_a_hard_link_confirms_even_without_the_brand()
    test_link_strength_ranks_filed_over_director_over_address()
    test_address_only_links_are_excluded_from_a_sweep()
    test_proposed_entities_never_reach_a_sweep()
    test_the_address_caveat_is_stated_with_a_count()
    test_a_failed_name_search_is_disclosed_not_swallowed()
    test_no_registry_crosswalk_means_nothing_is_confirmed_and_that_is_said()
    test_summarise_charges_separates_open_from_satisfied()
    test_unreadable_amounts_give_none_not_zero()
    test_no_charges_is_a_clean_zero_not_an_unknown()
    print("\nAll tests passed.")

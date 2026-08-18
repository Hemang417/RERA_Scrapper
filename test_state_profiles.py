"""
Guards on the state registry itself -- the data every rendered Charter now
draws its state nouns from.

Why these exist:

  * MahaRERA must declare ALL capabilities. Capability gates decide whether
    MahaRERA-only work (the Orders/Judgments search, the Maha Bhulekh
    district lookup) runs at all. Mis-declaring one here would silently
    disable working, production Maharashtra behaviour with no error --
    the failure would look like "that section just stopped appearing".

  * Registration-number patterns are NOT unique across states. Maharashtra
    and Telangana both use P + 11 digits (P51800077150 vs P02400003865),
    separable only by the embedded district code, which is an empirical
    observation and not a published specification. Any new state whose
    pattern collides with an existing one MUST be a deliberate, registered
    decision -- never something discovered later when a Telangana number
    silently resolves against Maharashtra's portal and returns a confident
    wrong answer.

  * states/maharashtra.py IMPORTS its endpoint tables from config.py rather
    than copying them, precisely so the two cannot drift. The identity
    check below is what keeps that true if someone later "tidies up" by
    pasting the values across.

Run directly: python test_state_profiles.py
"""

import re

import config
import states
from states import maharashtra


def test_the_registry_is_not_empty():
    """Anti-vacuous-pass guard, in the spirit of test_guardrails_doc.py's
    `assert len(symbols) >= 15`: if PROFILES were ever emptied or renamed,
    every other test in this file would pass by iterating nothing."""
    assert len(states.PROFILES) >= 1, states.PROFILES
    assert "MH" in states.PROFILES, sorted(states.PROFILES)
    assert states.DEFAULT_STATE_CODE in states.PROFILES, states.DEFAULT_STATE_CODE
    print("test_the_registry_is_not_empty: PASS")


def test_every_profile_has_a_usable_identity():
    for code, profile in states.PROFILES.items():
        assert profile.code == code, (code, profile.code)
        for field_name in ("code", "state_name", "rera_acronym", "regulator_name",
                           "planning_authority_label", "reg_no_pattern"):
            value = getattr(profile, field_name)
            assert isinstance(value, str) and value.strip(), (code, field_name, value)
    print("test_every_profile_has_a_usable_identity: PASS")


def test_every_reg_no_pattern_compiles():
    for code, profile in states.PROFILES.items():
        try:
            re.compile(profile.reg_no_pattern)
        except re.error as e:
            raise AssertionError(f"{code}: reg_no_pattern does not compile -- {e}") from None
    print("test_every_reg_no_pattern_compiles: PASS")


# Registration-number shapes that are KNOWN to be shared by more than one
# state. Each entry is a deliberate decision, not an accident, and each needs
# a named tiebreak in states.resolve_state before both states can ship.
#
# MH/TG is real and confirmed: MahaRERA P51800077150, TG-RERA P02400003865.
_KNOWN_AMBIGUOUS = {frozenset({"MH", "TG"})}


def test_no_unregistered_pattern_collision():
    """Two states matching the same string must be a registered ambiguity.

    Uses each profile's own sample-shaped strings, so this fires the moment
    a new state is added with a colliding pattern -- at the time the state
    is added, when someone can still choose a different discriminator."""
    samples = {
        "MH": "P51800077150",
        "TG": "P02400003865",
        "KA": "PRM/KA/RERA/1251/446/PR/220422/004789",
        "GJ": "PR/GJ/SURAT/SURATCITY/SUDA/RAA05825/030819",
        "UP": "UPRERAPRJ18905075",
    }
    for sample_code, sample in samples.items():
        if sample_code not in states.PROFILES:
            continue  # that state isn't registered yet -- nothing to collide with
        matched = {
            code for code, profile in states.PROFILES.items()
            if re.match(profile.reg_no_pattern, sample)
        }
        assert matched, (sample_code, sample, "matched no registered profile at all")
        if len(matched) > 1:
            assert frozenset(matched) in _KNOWN_AMBIGUOUS, (
                f"{sample!r} matches {sorted(matched)}, which is NOT a registered "
                f"ambiguity. Either narrow one pattern, or add this set to "
                f"_KNOWN_AMBIGUOUS and give it a named tiebreak in "
                f"states.resolve_state -- never let it resolve by dict order."
            )
    print("test_no_unregistered_pattern_collision: PASS")


def test_capabilities_are_all_real():
    """A capability string not in ALL_CAPABILITIES is a typo, and a typo
    reads as 'this state lacks the feature' -- which silently skips work
    rather than failing."""
    for code, profile in states.PROFILES.items():
        unknown = set(profile.capabilities) - set(states.ALL_CAPABILITIES)
        assert not unknown, (code, sorted(unknown))
    print("test_capabilities_are_all_real: PASS")


def test_maharashtra_declares_every_capability():
    """See module docstring -- this is the one that stops a gate quietly
    switching off production behaviour."""
    mh = states.PROFILES["MH"]
    missing = set(states.ALL_CAPABILITIES) - set(mh.capabilities)
    assert not missing, f"MahaRERA must declare every capability; missing: {sorted(missing)}"
    print("test_maharashtra_declares_every_capability: PASS")


def test_can_rejects_an_unknown_capability():
    mh = states.PROFILES["MH"]
    raised = False
    try:
        mh.can("not_a_real_capability")
    except ValueError:
        raised = True
    assert raised, "can() returned a bool for an unknown capability instead of raising"
    print("test_can_rejects_an_unknown_capability: PASS")


def test_maharashtra_shares_config_objects_rather_than_copying():
    """`is`, not `==`: an equal-but-separate copy is exactly the drift this
    guards against -- it would pass an equality check today and diverge the
    first time someone edits one side."""
    assert maharashtra.CATEGORY_ENDPOINTS is config.CATEGORY_ENDPOINTS
    assert maharashtra.CATEGORY_ORDER is config.CATEGORY_ORDER
    assert maharashtra.NO_AUTH_CATEGORIES is config.NO_AUTH_CATEGORIES
    assert maharashtra.BASE_URL == config.BASE_URL
    print("test_maharashtra_shares_config_objects_rather_than_copying: PASS")


def test_as_facts_dict_carries_only_reader_facing_fields():
    """facts["state"] is persisted into .facts.json and read back by a later
    re-render. Capabilities and reg-no regexes are pipeline internals and
    must not leak into the record."""
    d = states.PROFILES["MH"].as_facts_dict()
    assert set(d) == {
        "code", "state_name", "rera_acronym", "regulator_name", "planning_authority_label",
    }, sorted(d)
    assert d["rera_acronym"] == "MahaRERA", d
    print("test_as_facts_dict_carries_only_reader_facing_fields: PASS")


def test_get_profile_defaults_to_maharashtra():
    """Every caller and every archived output/ tree predates the state
    field; None must mean Maharashtra, not an error."""
    assert states.get_profile(None).code == "MH"
    assert states.get_profile("").code == "MH"
    assert states.get_profile("MH").code == "MH"
    raised = False
    try:
        states.get_profile("ZZ")
    except ValueError:
        raised = True
    assert raised, "get_profile silently accepted an unregistered state code"
    print("test_get_profile_defaults_to_maharashtra: PASS")


if __name__ == "__main__":
    test_the_registry_is_not_empty()
    test_every_profile_has_a_usable_identity()
    test_every_reg_no_pattern_compiles()
    test_no_unregistered_pattern_collision()
    test_capabilities_are_all_real()
    test_maharashtra_declares_every_capability()
    test_can_rejects_an_unknown_capability()
    test_maharashtra_shares_config_objects_rather_than_copying()
    test_as_facts_dict_carries_only_reader_facing_fields()
    test_get_profile_defaults_to_maharashtra()
    print("\nAll tests passed.")

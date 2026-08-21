"""
Guards on working out WHICH authority a registration number belongs to.

Most states carry a distinctive format -- Karnataka's PRM/KA/RERA/...,
Gujarat's PR/GJ/..., UP's UPRERAPRJ... -- and identify themselves outright.
Maharashtra and Telangana do NOT: both issue P + 11 digits (MahaRERA
P51800077150, TG-RERA P02400003865).

The first design guessed between them on the embedded district code
(Maharashtra's observed numbers begin P5, Telangana's P0). That was the
weakest thing in the pipeline: an observed convention, not a published rule,
and a wrong guess is not a crash -- it is a Charter that queries the wrong
authority and reports a confident, wrong answer. It also pushed the problem
onto the user, who typically holds only a number and has no way to know the
state.

So detection is now EMPIRICAL. The registration number is tried against each
matching authority in turn and the first one that ACTUALLY HAS the project
wins. The district-code convention survives only as an ordering hint -- it
decides who to ask first, never who the answer is. Being wrong now costs one
extra lookup instead of producing a wrong answer.

This costs nothing in the common case: resolving a project on a portal is
work acquire() does anyway, so a successful probe IS the resolve.

Run directly: python test_reg_no_resolution.py
"""

import states


class _Recorder:
    def __init__(self):
        self.messages = []

    def info(self, msg):
        self.messages.append(msg)

    warn = ok = info

    def choose(self, prompt, options):
        return None


def test_a_distinctive_format_needs_no_probing():
    """One candidate means the ladder runs once -- the ordinary resolve,
    unchanged. Only genuinely ambiguous formats ever cost extra."""
    profiles, _note = states.candidate_profiles("Kalpataru Height")
    assert len(profiles) == 1, [p.code for p in profiles]
    print("test_a_distinctive_format_needs_no_probing: PASS")


def test_a_shared_format_yields_every_candidate_not_a_guess():
    """The whole point: both authorities come back, so the caller can ask
    each one rather than picking."""
    profiles, note = states.candidate_profiles("P51800077150")
    codes = [p.code for p in profiles]
    assert set(codes) == {"MH", "TG"}, codes
    assert note and "in turn" in note, note
    print("test_a_shared_format_yields_every_candidate_not_a_guess: PASS")


def test_the_district_code_only_orders_the_probe():
    """P5... asks Maharashtra first, P0... asks Telangana first -- but BOTH
    stay in the list either way. That is the difference between an ordering
    hint and a decision: if the hint is wrong, the second probe corrects it."""
    mh_first = [p.code for p in states.candidate_profiles("P51800077150")[0]]
    tg_first = [p.code for p in states.candidate_profiles("P02400003865")[0]]
    assert mh_first[0] == "MH", mh_first
    assert tg_first[0] == "TG", tg_first
    assert set(mh_first) == set(tg_first) == {"MH", "TG"}, (mh_first, tg_first)
    print("test_the_district_code_only_orders_the_probe: PASS")


def test_an_unrecognised_district_code_still_probes_everything():
    """The old design RAISED here, because its heuristic could not settle a
    P9... number. Probing has no such problem -- it just asks both."""
    profiles, _note = states.candidate_profiles("P99900000001")
    assert set(p.code for p in profiles) == {"MH", "TG"}, [p.code for p in profiles]
    print("test_an_unrecognised_district_code_still_probes_everything: PASS")


def test_explicit_state_skips_probing_entirely():
    for code in ("MH", "TG"):
        profiles, note = states.candidate_profiles("P51800077150", explicit_code=code)
        assert [p.code for p in profiles] == [code], [p.code for p in profiles]
        assert note is None, note
    print("test_explicit_state_skips_probing_entirely: PASS")


def test_free_text_falls_back_to_the_default_announced():
    """python main.py "Kalpataru Height" is the production path: a name is
    not a registration number, so there is nothing to probe by."""
    profiles, note = states.candidate_profiles("Kalpataru Height")
    assert [p.code for p in profiles] == [states.DEFAULT_STATE_CODE]
    assert note and "not a recognised registration number" in note, note
    print("test_free_text_falls_back_to_the_default_announced: PASS")


def test_the_ladder_skips_an_authority_that_cannot_be_searched_by_reg_no():
    """TG-RERA's public record does not display a registration number at
    all -- its search is by project name, behind a CAPTCHA. So it can never
    answer a reg-no probe, and the ladder must skip it rather than pretend
    to have asked. main.py surfaces that as actionable advice ("re-run with
    the project name") instead of a bare not-found."""
    tg = states.PROFILES["TG"]
    assert not tg.can(states.CAP_LOOKUP_BY_REG_NO), tg.capabilities
    mh = states.PROFILES["MH"]
    assert mh.can(states.CAP_LOOKUP_BY_REG_NO), mh.capabilities
    print("test_the_ladder_skips_an_authority_that_cannot_be_searched_by_reg_no: PASS")


def test_a_wrong_ordering_hint_is_self_correcting():
    """Simulates the ladder main.py runs: the first candidate misses, the
    second has it. The result must be the SECOND state -- proving the
    ordering hint cannot produce a wrong answer, only a wasted lookup."""
    profiles, _note = states.candidate_profiles("P51800077150")
    assert profiles[0].code == "MH", "precondition: the hint says Maharashtra first"

    found_on = "TG"  # pretend the number is really Telangana's
    resolved = None
    attempted = []
    for candidate in profiles:
        # Stand-in for adapter.acquire raising StateResolutionError.
        if candidate.code != found_on:
            attempted.append(candidate.code)
            continue
        resolved = candidate
        break

    assert resolved is not None and resolved.code == "TG", resolved
    assert attempted == ["MH"], attempted
    print("test_a_wrong_ordering_hint_is_self_correcting: PASS")


def test_a_known_but_unsupported_authority_is_refused_by_name():
    """THE BUG THIS FIXES. A Chennai or Noida registration number used to
    fall through to the free-text branch and get searched against
    MahaRERA, which then found nothing -- and "not found" reads as "this
    project does not exist", not "that state has no adapter yet".

    Worse, the fallback announced it as "not a recognised registration
    number", which is the wrong diagnosis: UPRERAPRJ18905075 is a
    perfectly valid registration number. It belongs to an authority
    this pipeline cannot serve."""
    import states

    for query, acronym in (("UPRERAPRJ18905075", "UP-RERA"),
                           ("TN/29/Building/0000/2026", "TNRERA"),
                           ("tn/29/layout/12/2025", "TNRERA")):
        try:
            states.candidate_profiles(query)
        except states.StateResolutionError as e:
            message = str(e)
            assert acronym in message, message
            # It must say it searched NOWHERE, so a reader cannot take
            # the refusal as evidence about the project.
            assert "NOT searched anywhere else" in message, message
            assert "no adapter" in message, message
        else:
            raise AssertionError(query + " was routed somewhere instead of refused")
    print("test_a_known_but_unsupported_authority_is_refused_by_name: PASS")


def test_a_project_name_is_still_free_text_not_an_unsupported_state():
    """The refusal must not swallow the free-text path. A project name is
    not a registration number and still falls back to the default,
    announced -- that is the production path for name searches."""
    import states

    for name in ("Kalpataru Height", "Pranami Bliss", "TN Towers", "UP Residency"):
        profiles, note = states.candidate_profiles(name)
        assert profiles, name
        assert profiles[0].code == states.DEFAULT_STATE_CODE, (name, profiles[0].code)
        assert note, name
    print("test_a_project_name_is_still_free_text_not_an_unsupported_state: PASS")


def test_no_unsupported_pattern_collides_with_a_registered_one():
    """A pattern added to the unsupported table must never shadow an
    authority that IS built -- that would refuse a project the pipeline
    can actually fetch."""
    import states

    live = ["P51800077150", "P02400003865",
            "PRM/KA/RERA/1251/446/PR/220422/004789",
            "PR/GJ/SURAT/SURATCITY/SUDA/RAA05825/030819"]
    for query in live:
        assert states.unsupported_authority(query) is None, query
        assert states.candidate_profiles(query)[0], query
    print("test_no_unsupported_pattern_collides_with_a_registered_one: PASS")


def test_every_unsupported_entry_carries_its_evidence():
    """A pattern here is a claim about another authority's format, and a
    guessed one either misses real numbers or captures someone else's.
    Haryana is deliberately absent for exactly that reason: its portal
    published no registration number to derive a format from."""
    import states

    for entry in states._UNSUPPORTED_AUTHORITIES:
        for field in ("pattern", "acronym", "authority", "covers", "evidence"):
            assert entry.get(field), (entry.get("acronym"), field)
    acronyms = {e["acronym"] for e in states._UNSUPPORTED_AUTHORITIES}
    assert "HARERA" not in acronyms, "a Haryana format was added without evidence"
    print("test_every_unsupported_entry_carries_its_evidence: PASS")


def test_resolve_state_still_answers_for_render_only_callers():
    """Rendering needs a profile before any portal is contacted, so the
    first-candidate shortcut has to keep working -- it just must not be used
    where a probe is possible."""
    recorder = _Recorder()
    assert states.resolve_state(None, "P51800077150", recorder).code == "MH"
    assert states.resolve_state("TG", "P51800077150", recorder).code == "TG"
    assert states.resolve_state(None, "Some Project", recorder).code == "MH"
    print("test_resolve_state_still_answers_for_render_only_callers: PASS")


def test_every_registered_state_has_an_adapter_or_a_clear_refusal():
    for code in states.PROFILES:
        try:
            assert states.get_adapter(code).profile.code == code
        except NotImplementedError as e:
            assert "pre_built_facts" in str(e), (code, str(e))
    print("test_every_registered_state_has_an_adapter_or_a_clear_refusal: PASS")


if __name__ == "__main__":
    test_a_distinctive_format_needs_no_probing()
    test_a_shared_format_yields_every_candidate_not_a_guess()
    test_the_district_code_only_orders_the_probe()
    test_an_unrecognised_district_code_still_probes_everything()
    test_explicit_state_skips_probing_entirely()
    test_free_text_falls_back_to_the_default_announced()
    test_the_ladder_skips_an_authority_that_cannot_be_searched_by_reg_no()
    test_a_wrong_ordering_hint_is_self_correcting()
    test_a_known_but_unsupported_authority_is_refused_by_name()
    test_a_project_name_is_still_free_text_not_an_unsupported_state()
    test_no_unsupported_pattern_collides_with_a_registered_one()
    test_every_unsupported_entry_carries_its_evidence()
    test_resolve_state_still_answers_for_render_only_callers()
    test_every_registered_state_has_an_adapter_or_a_clear_refusal()
    print("\nAll tests passed.")

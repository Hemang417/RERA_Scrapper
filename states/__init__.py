"""
The state registry.

A plain dict, deliberately. No plugin system, no entry points, no dynamic
import discovery -- there are ~30 candidate states and this repo has no
existing registry machinery to be consistent with. Adding a state means
adding a module and one line here.

Runtime state SELECTION (--state, reg-no inference, and the Maharashtra /
Telangana tiebreak) lands here in Stage 3, alongside the adapter extraction
that needs it. Today this only answers "which profile" for the rendering
layer, whose default is and remains Maharashtra.
"""

import re

from . import (
    delhi,
    gujarat,
    haryana,
    jharkhand,
    karnataka,
    maharashtra,
    tamilnadu,
    telangana,
    uttarpradesh,
    westbengal,
)
from .base import (  # noqa: F401  -- re-exported for callers
    ALL_CAPABILITIES,
    CAP_CATEGORY_API,
    CAP_DOCUMENTS,
    CAP_LAND_RECORDS,
    CAP_LOOKUP_BY_REG_NO,
    CAP_ORDERS_SEARCH,
    CAP_PROMOTER_PORTFOLIO,
    CAP_SEPARATE_AUTH,
    AcquisitionContext,
    AcquisitionResult,
    ProgressReporter,
    StateAcquisitionError,
    StateAdapter,
    StateAuthError,
    StateFetchError,
    fetch_with_retry,
    StateProfile,
    StateResolutionError,
)

PROFILES = {
    maharashtra.PROFILE.code: maharashtra.PROFILE,
    telangana.PROFILE.code: telangana.PROFILE,
    gujarat.PROFILE.code: gujarat.PROFILE,
    karnataka.PROFILE.code: karnataka.PROFILE,
    jharkhand.PROFILE.code: jharkhand.PROFILE,
    westbengal.PROFILE.code: westbengal.PROFILE,
    uttarpradesh.PROFILE.code: uttarpradesh.PROFILE,
    tamilnadu.PROFILE.code: tamilnadu.PROFILE,
    haryana.PROFILE.code: haryana.PROFILE,
    delhi.PROFILE.code: delhi.PROFILE,
}

DEFAULT_STATE_CODE = "MH"
"""What an unqualified run means. Maharashtra is the production path and
every existing output/ tree predates the state field, so this is also what
a run_meta.json with no "state" key falls back to."""


def get_profile(code: str | None) -> StateProfile:
    """Profile for a state code, defaulting to Maharashtra.

    None/empty deliberately returns the default rather than raising: every
    existing caller (and every archived run) predates the state field, and
    must keep working untouched.
    """
    if not code:
        return PROFILES[DEFAULT_STATE_CODE]
    try:
        return PROFILES[code]
    except KeyError:
        raise ValueError(
            f"Unknown state code {code!r}. Registered: {sorted(PROFILES)}"
        ) from None


def _likelihood_order_maharashtra_telangana(reg_no: str) -> list:
    """Orders the MH/TG candidates for probing. An ORDERING HINT ONLY.

    This used to DECIDE the state, which was the weakest part of the design:
    MahaRERA and TG-RERA both issue P + 11 digits, and the only separator is
    the embedded district code -- Maharashtra's observed numbers begin P5
    (P51700048590, P51800077150, P52100019639), Telangana's begin P0
    (P02400003865, P02200002936). That is an observed convention, not a
    published rule, so deciding on it meant a wrong guess became a confident
    wrong answer against the wrong portal.

    Now it only decides which portal to ASK FIRST. Being wrong costs one
    extra lookup; it cannot produce a wrong answer, because the portal
    itself confirms or denies. See candidate_profiles.
    """
    digits = reg_no[1:]
    if digits.startswith("0"):
        return ["TG", "MH"]
    # P5..., and anything else: Maharashtra first. It is also the cheaper
    # probe (a public search, no CAPTCHA) and much the larger register --
    # ~55,000 projects against Telangana's ~11,000.
    return ["MH", "TG"]


# Known shared registration formats, each with the function that ORDERS the
# probe. A collision not listed here still probes, just in registry order.
_LIKELIHOOD_ORDERS = {
    frozenset({"MH", "TG"}): _likelihood_order_maharashtra_telangana,
}


# Authorities this pipeline does NOT have an adapter for, whose registration
# format is nonetheless KNOWN. Without this table a Chennai or Noida number
# falls through to the free-text branch and gets searched against MahaRERA,
# which then finds nothing -- and "not found" reads as "this project does not
# exist" rather than "that state is not supported yet". Same species as every
# other defect this pipeline has had to guard: a check that could not run
# presenting as one that found nothing.
#
# A pattern goes in here ONLY with evidence. Haryana (HARERA Gurugram /
# Panchkula) is deliberately ABSENT: its portal published no registration
# number this could be derived from on 2026-08-21, and a guessed pattern
# would either miss real numbers or capture someone else's.
#
# EMPTY TODAY, AND THE MECHANISM IS KEPT ANYWAY. Both former entries --
# UP-RERA and TNRERA -- are now REGISTERED authorities with adapters, so
# refusing them would refuse projects this pipeline can actually fetch.
#
# Emptying it also retired two patterns that were WRONG, which is the
# cautionary note for whoever adds the next one:
#
#   * `^UPRERAPRJ\w+$` -- `\w` does not match a slash, so every post-2024
#     UP registration (UPRERAPRJ378870/03/2025) failed the pattern, fell
#     through to the free-text branch and was searched against MahaRERA.
#     The exact defect this table exists to prevent, hiding inside it.
#   * `^TN/\d{2}/(?:BUILDING|LAYOUT)/\d+/\d{4}$` -- wrong in four ways at
#     once, and its recorded evidence "TN/29/Building/0000/2026" was a
#     search-box placeholder rather than an issued number. A serial of
#     0000 should have been the tell.
#
# Both are documented in states/uttarpradesh.py and states/tamilnadu.py.
# The lesson stands: a pattern here is a claim about another authority's
# format, it must come from real issued numbers, and ONE example is not
# enough to see which parts vary.
_UNSUPPORTED_AUTHORITIES = ()


def unsupported_authority(query: str):
    """The known authority a query belongs to, when there is no adapter for
    it -- or None. Matching is case-insensitive; portals print these in
    mixed case."""
    query = (query or "").strip()
    if not query:
        return None
    for entry in _UNSUPPORTED_AUTHORITIES:
        if re.match(entry["pattern"], query, re.IGNORECASE):
            return entry
    return None


def candidate_profiles(query: str, explicit_code: str | None = None) -> tuple[list, str | None]:
    """(ordered candidate profiles, note) for a query.

    The caller is expected to try each candidate in turn and keep the first
    one whose portal actually finds the project -- see main.py. That makes
    state detection EMPIRICAL rather than a guess: the authority's own
    search is the evidence.

    Ordering matters only for speed. Correctness comes from the probe.

    A single-candidate list means no ambiguity existed (most states have a
    distinctive registration format -- Karnataka's PRM/KA/RERA/..., Gujarat's
    PR/GJ/..., UP's UPRERAPRJ...), so no probing beyond the normal resolve
    happens at all.
    """
    if explicit_code:
        return [get_profile(explicit_code)], None

    query = (query or "").strip()
    matched = sorted(
        code for code, profile in PROFILES.items()
        if re.match(profile.reg_no_pattern, query, re.IGNORECASE)
    )

    if not matched:
        # A registration number we RECOGNISE but cannot serve. Refused by
        # name, because searching another state's portal for it would
        # return nothing and read as "no such project".
        known = unsupported_authority(query)
        if known:
            raise StateResolutionError(
                f"{query!r} is a {known['acronym']} registration number "
                f"({known['authority']}), which covers {known['covers']}. "
                f"This pipeline has no adapter for that authority yet, so the "
                f"project cannot be fetched. It was NOT searched anywhere else: "
                f"a nil result from another state's portal would mean nothing. "
                f"Registered authorities: "
                + ", ".join(f"{c} ({PROFILES[c].rera_acronym})" for c in sorted(PROFILES))
            )

        # Free text, not a registration number. Cannot probe by reg no;
        # fall back to the default, announced.
        default = PROFILES[DEFAULT_STATE_CODE]
        return [default], (
            f"'{query}' is not a recognised registration number and no --state was "
            f"given -- searching {default.state_name} ({default.rera_acronym})."
        )

    if len(matched) == 1:
        return [PROFILES[matched[0]]], None

    order_fn = _LIKELIHOOD_ORDERS.get(frozenset(matched))
    ordered = order_fn(query) if order_fn else matched
    # Any state the ordering function forgot still gets probed.
    ordered = ordered + [c for c in matched if c not in ordered]
    profiles = [PROFILES[c] for c in ordered]

    names = ", ".join(PROFILES[c].rera_acronym for c in ordered)
    note = (
        f"'{query}' matches the registration format of {len(matched)} authorities. "
        f"Trying {names} in turn and keeping whichever one actually has it."
    )
    return profiles, note


def resolve_state(explicit_code: str | None, query: str, reporter=None) -> StateProfile:
    """The FIRST candidate only -- kept for callers that need a profile
    before any portal work happens (rendering, run_meta defaults).

    Prefer candidate_profiles + a probe wherever the portal is actually
    going to be contacted: this function can only ever guess between two
    states sharing a format, and guessing is what the probe removes.
    """
    def _say(msg):
        if reporter is not None and msg:
            reporter.info(msg)

    profiles, note = candidate_profiles(query, explicit_code)
    _say(note)
    return profiles[0]


# Adapters are imported LAZILY. states.PROFILES must stay cheap -- importing
# it happens on every render, including run_company_charter(pre_built_facts=
# ...) paths that never touch a portal -- while an adapter drags in
# Playwright, requests, resolver and the whole scraping stack.
_ADAPTER_MODULES = {
    "MH": "states.adapter_maharashtra",
    "GJ": "states.adapter_gujarat",
    "KA": "states.adapter_karnataka",
    "TG": "states.adapter_telangana",
    "JH": "states.adapter_jharkhand",
    "WB": "states.adapter_westbengal",
    "UP": "states.adapter_uttarpradesh",
    "TN": "states.adapter_tamilnadu",
    "HR": "states.adapter_haryana",
    "DL": "states.adapter_delhi",
}


def get_adapter(code: str):
    """The acquisition adapter for a state code.

    Raises for a state that has a PROFILE but no adapter yet. The error
    names the escape hatch that does work today (pre_built_facts), rather
    than failing blankly. Every registered state has one as of Phase 2."""
    profile = get_profile(code)
    module_path = _ADAPTER_MODULES.get(profile.code)
    if module_path is None:
        raise NotImplementedError(
            f"{profile.state_name} ({profile.rera_acronym}) has a registered profile but no "
            f"acquisition adapter yet, so its portal cannot be scraped by this pipeline. "
            f"A Charter can still be produced for it via "
            f"company_charter.run_company_charter(pre_built_facts=..., state_profile=...), "
            f"which is how the CONSTELLA Telangana Charter was built."
        )
    import importlib
    return importlib.import_module(module_path).ADAPTER

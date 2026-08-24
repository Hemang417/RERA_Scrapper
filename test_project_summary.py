"""
Guards on fetch_project_summary, the per-project open that turns a swept
NAME into a checked PROJECT.

WHY THIS FILE EXISTS. A sweep that can LIST a project but never OPEN it can
neither confirm nor refute it, and unconfirmed candidates are what inflate a
group's apparent footprint -- five of six on the first live Jharkhand run
were false brand matches. MahaRERA and K-RERA, the two largest registers in
this pipeline, were searchable but not openable for several phases, so every
hit on them stayed a bare name.

THE TWO STATES FAIL DIFFERENTLY, AND BOTH FAILURES MATTER.

  * K-RERA cannot be opened from a search row alone. Its detail view needs
    BOTH the registration number and the acknowledgement number, and a
    search row carries one -- so the summary looks the other back up in the
    state index. Its promoter name comes from that index rather than the
    detail page, because the detail tables do not carry one.

  * MahaRERA can be opened unauthenticated, but publishes its promoter of
    record only on the auth-gated `partners` record. The no-auth `projects`
    payload carries `promoterName: null` -- confirmed against the real
    capture used below. So an unattended sweep opens the project and
    legitimately cannot name its promoter, and the summary MUST say so:
    group_sweep turns a missing name into "unconfirmed", and a summary that
    quietly returned an empty string with no note would let that read as a
    project examined and found unremarkable.

Everything here runs offline. The MahaRERA test drives the real saved
payload rather than a hand-written one, so a change in what that endpoint
actually returns cannot pass unnoticed.

Run directly: python test_project_summary.py
"""

import importlib
import json
import os

import api_client
import group_sweep as gs
import states
from states import adapter_karnataka as ka
from states import adapter_maharashtra as mh

_CAPTURE = os.path.join("output", "P51800077150", "raw", "projects.json")


def _real_projects_payload():
    """MahaRERA's own no-auth response for a real project, as captured."""
    with open(_CAPTURE, encoding="utf-8") as f:
        return json.load(f)


class _NoCachedToken:
    """Stands in for token_cache with nothing cached -- the unattended-sweep
    case. acquire_token_via_browser is deliberately absent: if the summary
    ever tries to mint a token this raises AttributeError rather than
    silently opening a browser and waiting on a human."""

    @staticmethod
    def load_valid():
        return None


# --- MahaRERA --------------------------------------------------------------

def test_maharera_reads_the_no_auth_fields_a_reader_needs():
    payload = _real_projects_payload()

    def _fake(category, project_id, session, token=None, **kw):
        if category == "projects":
            return payload
        if category == "complaints":
            return {"complaintDetails": None}
        raise AssertionError(f"{category} must not be fetched without a token")

    real_fetch, real_cache = api_client.fetch_category, mh.token_cache
    api_client.fetch_category = _fake
    mh.token_cache = _NoCachedToken
    try:
        summary = mh.fetch_project_summary("46590")
    finally:
        api_client.fetch_category = real_fetch
        mh.token_cache = real_cache

    assert summary["opened"] is True, summary
    # Sales progress is the headline number a reader wants, and it is free.
    assert summary["units_total"] == 234, summary
    assert summary["units_sold"] == 148, summary
    assert summary["reg_no"] == "P51800077150", summary
    assert summary["status"] == "Certificate Signed", summary
    # MahaRERA's own promoter key -- the one join in this pipeline that is
    # not a name, and stable across a promoter's projects.
    assert summary["user_profile_id"] == 105868, summary
    assert summary["registration_lapsed"] is False, summary
    print("test_maharera_reads_the_no_auth_fields_a_reader_needs: PASS")


def test_maharera_says_so_when_the_promoter_name_is_behind_the_captcha():
    """THE LOAD-BEARING HONESTY TEST. Without a cached session MahaRERA
    cannot name the promoter, and group_sweep decides confirmed / refuted /
    unconfirmed on exactly that field. Returning "" silently would let an
    unchecked project read as a checked one."""
    payload = _real_projects_payload()
    assert payload["promoterName"] is None, (
        "the premise of this test is that the no-auth payload carries no promoter name; "
        "if MahaRERA has started publishing one, this summary should read it"
    )

    def _fake(category, project_id, session, token=None, **kw):
        if category == "projects":
            return payload
        if category == "complaints":
            return {"complaintDetails": None}
        raise AssertionError("auth-gated category fetched with no token")

    real_fetch, real_cache = api_client.fetch_category, mh.token_cache
    api_client.fetch_category = _fake
    mh.token_cache = _NoCachedToken
    try:
        summary = mh.fetch_project_summary("46590")
    finally:
        api_client.fetch_category = real_fetch
        mh.token_cache = real_cache

    assert summary["promoter_name"] == "", summary
    assert summary["promoter_name_source"] is None, summary
    assert summary["authenticated_fields_read"] is False, summary
    note = " ".join(summary["notes"])
    assert "could not be read" in note, summary["notes"]
    assert "CAPTCHA-gated" in note, summary["notes"]
    # It must refuse to be read as either verdict.
    assert "must not be read as either" in note, summary["notes"]
    print("test_maharera_says_so_when_the_promoter_name_is_behind_the_captcha: PASS")


def test_maharera_uses_a_cached_session_but_never_mints_one():
    """A sweep touching a dozen projects must not demand a dozen human
    CAPTCHA solves. A token already cached is free and is used; no token
    means degrade, never prompt."""
    payload = _real_projects_payload()
    asked = []

    def _fake(category, project_id, session, token=None, **kw):
        asked.append((category, bool(token)))
        if category == "projects":
            return payload
        if category == "complaints":
            return {"complaintDetails": None}
        if category == "partners":
            return {"promoterDetails": {"promoterName": "Pranami Neev Realty Limited"}}
        if category == "appeals":
            return []
        raise AssertionError(category)

    real_fetch = api_client.fetch_category
    real_cache = mh.token_cache
    api_client.fetch_category = _fake

    class _Cache:
        @staticmethod
        def load_valid():
            return "cached-token"

        @staticmethod
        def acquire_token_via_browser(*a, **k):
            raise AssertionError("a summary must never mint a token")

    mh.token_cache = _Cache
    try:
        summary = mh.fetch_project_summary("46590")
    finally:
        api_client.fetch_category = real_fetch
        mh.token_cache = real_cache

    assert summary["promoter_name"] == "Pranami Neev Realty Limited", summary
    assert summary["authenticated_fields_read"] is True, summary
    assert "cached session" in (summary["promoter_name_source"] or ""), summary
    # An empty appeals list is a published zero, not an unread category.
    assert summary["total_appeals_count"] == 0, summary
    assert ("partners", True) in asked, asked
    print("test_maharera_uses_a_cached_session_but_never_mints_one: PASS")


def test_maharera_an_unread_count_is_not_a_zero():
    """None and 0 must stay distinguishable: one means the category could not
    be read, the other that MahaRERA published none. This is the false clean
    record in miniature."""
    assert mh._complaint_count({"complaintDetails": None}) == 0
    assert mh._complaint_count({"complaintDetails": [1, 2, 3]}) == 3
    assert mh._complaint_count(None) is None, "an unread category must not read as zero"
    print("test_maharera_an_unread_count_is_not_a_zero: PASS")


def test_maharera_an_unopenable_project_says_so_rather_than_raising():
    def _boom(category, project_id, session, token=None, **kw):
        raise api_client.CategoryFetchError(category, "HTTP 503", 503)

    real_fetch, real_cache = api_client.fetch_category, mh.token_cache
    api_client.fetch_category = _boom
    mh.token_cache = _NoCachedToken
    try:
        summary = mh.fetch_project_summary("46590")
    finally:
        api_client.fetch_category = real_fetch
        mh.token_cache = real_cache
    assert summary["opened"] is False, summary
    assert "could not be read" in summary["note"], summary
    assert mh.fetch_project_summary("")["opened"] is False
    print("test_maharera_an_unopenable_project_says_so_rather_than_raising: PASS")


# --- K-RERA ----------------------------------------------------------------

def test_karnataka_search_rows_carry_a_handle_to_open_them():
    """REGRESSION. K-RERA's search rows carried no project_id, so
    group_sweep.enrich_projects skipped every one of them as "this authority
    has no per-project fetch" -- which stayed true-looking even after the
    fetch existed."""
    ka._INDEX_CACHE.clear()
    ka._INDEX_CACHE.extend([
        {"ack_no": "ACK/KA/RERA/1/2/PR/010101/000001",
         "reg_no": "PRM/KA/RERA/1/2/PR/010101/000001",
         "project_name": "ADARSH GREENS", "promoter_name": "ADARSH DEVELOPERS"},
    ])
    try:
        rows = ka.search_promoter_projects("Adarsh Developers")
        assert len(rows) == 1, rows
        assert rows[0]["project_id"] == "PRM/KA/RERA/1/2/PR/010101/000001", rows[0]
        assert rows[0]["promoter_name"] == "ADARSH DEVELOPERS", rows[0]
    finally:
        ka._INDEX_CACHE.clear()
    print("test_karnataka_search_rows_carry_a_handle_to_open_them: PASS")


def test_karnataka_recovers_the_ack_number_from_the_index():
    """The detail view wants BOTH identifiers and a search row carries one.
    The index is what recovers the other, and it is already in memory from
    the sweep's own search -- so this costs nothing."""
    ka._INDEX_CACHE.clear()
    ka._INDEX_CACHE.extend([
        {"ack_no": "ACK/KA/RERA/9/9/PR/010101/000009",
         "reg_no": "PRM/KA/RERA/9/9/PR/010101/000009",
         "project_name": "P", "promoter_name": "Q"},
    ])
    try:
        by_reg = ka._index_entry("PRM/KA/RERA/9/9/PR/010101/000009")
        by_ack = ka._index_entry("ACK/KA/RERA/9/9/PR/010101/000009")
        assert by_reg is not None and by_reg["ack_no"].startswith("ACK/"), by_reg
        assert by_ack is by_reg, "either identifier must find the same row"
        assert ka._index_entry("PRM/KA/RERA/nope/0/PR/010101/000000") is None
    finally:
        ka._INDEX_CACHE.clear()
    print("test_karnataka_recovers_the_ack_number_from_the_index: PASS")


def test_karnataka_a_project_outside_the_index_is_not_opened_silently():
    ka._INDEX_CACHE.clear()
    ka._INDEX_CACHE.extend([
        {"ack_no": "ACK/x", "reg_no": "PRM/x", "project_name": "P", "promoter_name": "Q"},
    ])
    try:
        summary = ka.fetch_project_summary("PRM/KA/RERA/absent/0/PR/010101/000000")
    finally:
        ka._INDEX_CACHE.clear()
    assert summary["opened"] is False, summary
    assert "not in the K-RERA state index" in summary["note"], summary
    print("test_karnataka_a_project_outside_the_index_is_not_opened_silently: PASS")


# --- the cross-state contract --------------------------------------------

def test_every_state_can_be_opened_or_says_why_not():
    """ANTI-DRIFT, and the guard this file was missing.

    A state that can be SEARCHED but not OPENED produces candidates nobody
    can confirm or refute, which is what inflates a group's apparent
    footprint -- five of six hits on the first live Jharkhand run were false
    brand matches, and opening them is what proved it. A state that can be
    neither searched nor opened is a different thing, and both are fine as
    long as the reader is told which.

    So: every registered state either implements fetch_project_summary, or
    has a written reason in group_sweep._CANNOT_OPEN. A new adapter arriving
    with neither fails here, at the moment it is added.
    """
    missing = []
    for code in sorted(states.PROFILES):
        module = importlib.import_module(states._ADAPTER_MODULES[code])
        if hasattr(module, "fetch_project_summary"):
            continue
        if code not in gs._CANNOT_OPEN:
            missing.append(code)
    assert not missing, (
        f"{missing} can neither be opened nor explain why. Either implement "
        f"fetch_project_summary or add a reader-facing reason to "
        f"group_sweep._CANNOT_OPEN -- a silent absence reads as a project examined "
        f"and found unremarkable."
    )
    print("test_every_state_can_be_opened_or_says_why_not: PASS")


def test_telangana_is_the_only_state_that_cannot_be_opened_and_says_why():
    """TG-RERA's public record does not carry its own registration number
    and its search is CAPTCHA-gated by project name, so there is nothing to
    hand a fetch. A stub that always failed would be worse than the absence
    -- states/base.py forbids exactly that -- so the reason is written down
    instead."""
    assert set(gs._CANNOT_OPEN) == {"TG"}, sorted(gs._CANNOT_OPEN)
    reason = gs._CANNOT_OPEN["TG"]
    assert "CAPTCHA" in reason, reason
    assert "does not display its own registration number" in reason, reason
    # And it must not read as a finding about the project.
    assert "nothing here was established about it" in reason, reason
    print("test_telangana_is_the_only_state_that_cannot_be_opened_and_says_why: PASS")


def test_an_unopenable_states_projects_carry_its_reason_not_a_generic_line():
    """The reason has to reach the page, not just live in a dict."""
    result = {"projects": [{"state": "TG", "project_id": "anything",
                            "project_name": "CONSTELLA"}]}
    gs.enrich_projects(result)
    status = result["projects"][0]["detail_status"]
    assert "CAPTCHA" in status, status
    assert status.startswith("not opened"), status
    print("test_an_unopenable_states_projects_carry_its_reason_not_a_generic_line: PASS")


if __name__ == "__main__":
    test_every_state_can_be_opened_or_says_why_not()
    test_telangana_is_the_only_state_that_cannot_be_opened_and_says_why()
    test_an_unopenable_states_projects_carry_its_reason_not_a_generic_line()
    test_maharera_reads_the_no_auth_fields_a_reader_needs()
    test_maharera_says_so_when_the_promoter_name_is_behind_the_captcha()
    test_maharera_uses_a_cached_session_but_never_mints_one()
    test_maharera_an_unread_count_is_not_a_zero()
    test_maharera_an_unopenable_project_says_so_rather_than_raising()
    test_karnataka_search_rows_carry_a_handle_to_open_them()
    test_karnataka_recovers_the_ack_number_from_the_index()
    test_karnataka_a_project_outside_the_index_is_not_opened_silently()
    print("\nAll tests passed.")

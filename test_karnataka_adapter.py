"""
Guards on the K-RERA adapter.

The first test here exists because of the most dangerous bug found in this
whole pan-India effort:

    K-RERA's PER-PROJECT complaint page (/projectComplaintDetails) does NOT
    reliably carry complaints. Confirmed live against ADARSH GREENS PHASE 1
    -- a project the state-wide register lists with TWELVE complaints --
    that page returns only a Land Owner table and no complaint data at all.

    The first version of this adapter parsed that page. It would have
    reported "no complaints" for a project with twelve: a FALSE CLEAN
    RECORD, which is the worst output this pipeline can produce, and one
    that looks completely normal in the finished Charter.

The fix is to read the state-wide register (/projectComplaintReport), which
lists only projects that HAVE complaints -- so absence from it is a genuine
zero rather than a lookup miss. The test below pins that, and pins the
distinction between an unknown count (None) and a clean one (0).

The second guard covers the state index. K-RERA embeds its entire project
list client-side as four PARALLEL JavaScript arrays; pairing them by
position is only safe while they are the same length, so the parser refuses
to pair mismatched arrays rather than silently attaching the wrong promoter
to a project.

Network tests are opt-in: KRERA_LIVE=1.

Run directly: python test_karnataka_adapter.py
"""

import os

import states
from states.adapter_karnataka import (
    _find_table_by_header,
    _labelled_rows,
    parse_search_index,
)
from states.base import StateResolutionError, storage_key

_LIVE = os.environ.get("KRERA_LIVE") == "1"
_REG = "PRM/KA/RERA/1251/446/PR/040826/008858"
# The state register lists 12 complaints for this one.
_REG_WITH_COMPLAINTS = "PRM/KA/RERA/1251/309/PR/201001/003607"


def _index_html(n=2):
    """Minimal stand-in for the real page's script block."""
    parts = []
    for i in range(n):
        parts.append(f"""
            applicationNameList.push('ACK/KA/RERA/x/y/PR/010101/{i:06d}');
            applicationNameList2.push('PRM/KA/RERA/x/y/PR/010101/{i:06d}');
            applicationNameList3.push('PROJECT {i}');
            applicationNameList4.push('PROMOTER {i}');
        """)
    return "<script>" + "".join(parts) + "</script>"


def test_the_state_index_pairs_four_parallel_arrays():
    rows = parse_search_index(_index_html(3))
    assert len(rows) == 3, rows
    assert rows[0]["reg_no"] == "PRM/KA/RERA/x/y/PR/010101/000000", rows[0]
    assert rows[0]["project_name"] == "PROJECT 0", rows[0]
    assert rows[0]["promoter_name"] == "PROMOTER 0", rows[0]
    print("test_the_state_index_pairs_four_parallel_arrays: PASS")


def test_mismatched_index_arrays_raise_rather_than_zip():
    """Pairing by position is only valid while the arrays agree in length.
    A silent zip would attach the wrong promoter to a project -- a wrong
    answer that looks entirely plausible in the finished document."""
    broken = _index_html(2) + "<script>applicationNameList4.push('EXTRA');</script>"
    raised = False
    try:
        parse_search_index(broken)
    except StateResolutionError as e:
        raised = True
        assert "wrong promoter" in str(e), str(e)
    assert raised, "mismatched arrays were zipped instead of rejected"
    print("test_mismatched_index_arrays_raise_rather_than_zip: PASS")


def test_an_empty_index_is_empty_not_an_error():
    assert parse_search_index("<html>no arrays here</html>") == []
    print("test_an_empty_index_is_empty_not_an_error: PASS")


def test_tables_are_found_by_header_not_by_index():
    """K-RERA repeats its block/tower tables once per tower, so a five-tower
    project shifts every later table four positions relative to a one-tower
    project. Index-based lookup reads the wrong table for most projects."""
    tables = [
        [["Tower Name", "Block-1"], ["a", "b"]],
        [["Sl No.", "Engineer Name", "Engineer Address"], ["1", "X", "Y"]],
    ]
    found = _find_table_by_header(tables, "engineer name")
    assert found and found[0][1] == "Engineer Name", found
    assert _find_table_by_header(tables, "nothing here") == []
    print("test_tables_are_found_by_header_not_by_index: PASS")


def test_labelled_rows_drops_ragged_rows():
    rows = [["A", "B"], ["1", "2"], ["only-one-cell"]]
    out = _labelled_rows(rows)
    assert out == [{"A": "1", "B": "2"}], out
    print("test_labelled_rows_drops_ragged_rows: PASS")


def test_the_storage_key_flattens_the_slashes():
    assert storage_key(_REG) == "PRM-KA-RERA-1251-446-PR-040826-008858"
    print("test_the_storage_key_flattens_the_slashes: PASS")


def test_both_registration_prefixes_resolve_to_karnataka():
    for value in (_REG, "ACK/KA/RERA/1251/446/PR/240726/010453"):
        profiles, _note = states.candidate_profiles(value)
        assert [p.code for p in profiles] == ["KA"], (value, [p.code for p in profiles])
    print("test_both_registration_prefixes_resolve_to_karnataka: PASS")


def test_capabilities_match_what_the_portal_actually_offers():
    ka = states.PROFILES["KA"]
    assert ka.can(states.CAP_LOOKUP_BY_REG_NO)
    assert ka.can(states.CAP_DOCUMENTS)
    # Derivable from the client-side index -- unlike Gujarat, which
    # publishes no promoter-to-projects link at all.
    assert ka.can(states.CAP_PROMOTER_PORTFOLIO)
    # NOT orders_search. K-RERA publishes judgements at /viewAllJudgements,
    # but this pipeline has no scraper for them -- and that capability gates
    # MahaRERA's OWN Orders search, so declaring it true made a Karnataka
    # project fire a MahaRERA search against a portal that could never match
    # it, and the run hung. Complaint COUNTS come from K-RERA's state-wide
    # register instead, which is a different mechanism entirely.
    assert not ka.can(states.CAP_ORDERS_SEARCH)
    # Bhoomi is not wired in; the RERA record's survey numbers are the
    # promoter's own declaration, not an independent land record.
    assert not ka.can(states.CAP_LAND_RECORDS)
    print("test_capabilities_match_what_the_portal_actually_offers: PASS")


# --- live -----------------------------------------------------------------

def test_live_complaint_count_comes_from_the_state_register():
    """THE regression that matters. If this ever returns 0 for a project the
    register lists with complaints, the Charter is reporting a false clean
    record."""
    if not _LIVE:
        print("test_live_complaint_count_comes_from_the_state_register: SKIPPED (set KRERA_LIVE=1)")
        return
    import shutil
    import tempfile
    from states.adapter_karnataka import ADAPTER

    class _Reporter:
        def info(self, m): pass
        def warn(self, m): pass
        def ok(self, m): pass
        def choose(self, p, o): return None

    tmp = tempfile.mkdtemp(prefix="krera_")
    try:
        ctx = states.AcquisitionContext(output_dir=tmp, reporter=_Reporter())
        result = ADAPTER.acquire(_REG_WITH_COMPLAINTS, ctx)
        complaints = result.category_data["complaints"]
        count = complaints["total_complaints_count"]
        assert count is not None, "count came back unknown -- the register was unreadable"
        assert count >= 12, (
            f"expected at least 12 complaints from the state register, got {count}. "
            f"If this dropped to 0, the adapter has regressed to reading the per-project "
            f"page, which does NOT carry complaints."
        )
        assert "state-wide complaint register" in complaints["source"], complaints["source"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("test_live_complaint_count_comes_from_the_state_register: PASS")


def test_live_index_and_portfolio():
    if not _LIVE:
        print("test_live_index_and_portfolio: SKIPPED (set KRERA_LIVE=1)")
        return
    from states.adapter_karnataka import _session, parse_search_index
    from states.karnataka import SEARCH_PAGE

    index = parse_search_index(_session().get(SEARCH_PAGE, timeout=120).text)
    assert len(index) > 5000, len(index)
    assert all(e["reg_no"] and e["promoter_name"] for e in index[:50]), index[:3]
    print(f"test_live_index_and_portfolio: PASS ({len(index)} projects indexed)")


if __name__ == "__main__":
    test_the_state_index_pairs_four_parallel_arrays()
    test_mismatched_index_arrays_raise_rather_than_zip()
    test_an_empty_index_is_empty_not_an_error()
    test_tables_are_found_by_header_not_by_index()
    test_labelled_rows_drops_ragged_rows()
    test_the_storage_key_flattens_the_slashes()
    test_both_registration_prefixes_resolve_to_karnataka()
    test_capabilities_match_what_the_portal_actually_offers()
    test_live_complaint_count_comes_from_the_state_register()
    test_live_index_and_portfolio()
    print("\nAll tests passed.")

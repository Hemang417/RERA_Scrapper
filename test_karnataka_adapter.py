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
    ADAPTER,
    _find_table_by_header,
    _labelled_rows,
    fetch_default_projects,
    fetch_revenue_recovery_list,
    parse_search_index,
    search_default_projects_by_promoter,
    search_revenue_recovery_by_promoter,
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


# Real shapes captured live 2026-09-09 against PRM/KA/RERA/1251/309/PR/
# 201001/003607. K-RERA carries TWO separate NOC tables sharing "NOC Name"
# as a header cell -- one tracking whether each NOC was ever obtained
# (Is Applicable?/Status Of Approval), the other tracking an already-issued
# NOC's own expiry and renewal (Expired Date/RENEWED?). Matching on
# "noc name" alone would return whichever of the two the table list
# happens to carry first, silently reading one as the other.
_DETAIL_TABLES_WITH_TWO_NOC_TABLES = [
    [["Sl. No.", "Delay Reason"], ["1", "others"]],
    [["Sl No.", "NOC Name", "Is Applicable ?", "Status Of Approval", "Date of Application", "NOC"],
     ["1", "Water supply and sewage board", "Yes", "Approved", "16-04-2019", ""]],
    [["Sl. No.", "NOC Name", "Expired Date", "RENEWED ?", "NOC Validity From", "NOC Validity To", "Document"],
     ["1", "Fire NOC", "31-12-2023", "No", "01-01-2020", "31-12-2023", ""]],
]


def test_delay_reason_and_both_noc_tables_are_found_and_never_confused():
    """The regression this pins: "noc name" alone matches BOTH NOC tables,
    so the second needle in each _find_table_by_header call -- not the
    header text alone -- is what keeps "obtained at all" separate from
    "already obtained, now expiring"."""
    parsed = ADAPTER._parse_detail(_DETAIL_TABLES_WITH_TWO_NOC_TABLES, {})
    assert parsed["delay_reasons"] == [{"Sl. No.": "1", "Delay Reason": "others"}], \
        parsed["delay_reasons"]
    assert len(parsed["noc_status"]) == 1, parsed["noc_status"]
    assert parsed["noc_status"][0]["NOC Name"] == "Water supply and sewage board", parsed["noc_status"]
    assert "Is Applicable ?" in parsed["noc_status"][0], parsed["noc_status"]
    assert len(parsed["noc_expiry"]) == 1, parsed["noc_expiry"]
    assert parsed["noc_expiry"][0]["NOC Name"] == "Fire NOC", parsed["noc_expiry"]
    assert "Expired Date" in parsed["noc_expiry"][0], parsed["noc_expiry"]
    print("test_delay_reason_and_both_noc_tables_are_found_and_never_confused: PASS")


def test_delay_reasons_and_noc_tables_are_empty_lists_when_absent_not_missing_keys():
    """A project with neither table present must still carry the keys, as
    empty lists -- company_charter._safe_project_delay_noc reads them with
    .get(...) or [], but the key itself existing is what group_financial_
    disclosure-style callers rely on to tell 'checked, found nothing' apart
    from a KeyError."""
    parsed = ADAPTER._parse_detail([], {})
    assert parsed["delay_reasons"] == [], parsed["delay_reasons"]
    assert parsed["noc_status"] == [], parsed["noc_status"]
    assert parsed["noc_expiry"] == [], parsed["noc_expiry"]
    print("test_delay_reasons_and_noc_tables_are_empty_lists_when_absent_not_missing_keys: PASS")


# The real live shape confirmed 2026-09-09: a short summary table sits
# above the full register, sharing the same core columns, and both name
# the SAME project (PRM/.../008858) -- proving the dedup is load-bearing,
# not defensive-only.
_DEFAULT_PROJECTS_HTML = """
<table>
 <tr><th>S.No</th><th>REGISTRATION NO</th><th>PROMOTER</th><th>PROJECT</th>
     <th>DISTRICT</th><th>APPLIED DATE</th><th>APPROVED DATE</th>
     <th>PROPOSED COMPLETION DATE</th></tr>
 <tr><td></td><td>PRM/KA/RERA/1251/446/PR/040826/008858</td>
     <td>Pranami Builders Pvt Ltd</td><td>Pranami Towers</td>
     <td>Bengaluru Urban</td><td>01/01/2020</td><td>01/06/2020</td><td>31/12/2023</td></tr>
</table>
<table>
 <tr><th>S.No</th><th>REGISTRATION NO</th><th>PROMOTER</th><th>PROJECT</th>
     <th>TYPE OF PROJECT</th><th>DISTRICT</th><th>APPLIED DATE</th>
     <th>APPROVED DATE</th><th>PROPOSED COMPLETION DATE</th></tr>
 <tr><td></td><td>PRM/KA/RERA/1251/446/PR/040826/008858</td>
     <td>Pranami Builders Pvt Ltd</td><td>Pranami Towers</td>
     <td>Residential/Group Housing</td><td>Bengaluru Urban</td>
     <td>01/01/2020</td><td>01/06/2020</td><td>31/12/2023</td></tr>
 <tr><td></td><td>PRM/KA/RERA/1251/310/PR/171103/001748</td>
     <td>Sona Heights</td><td>Vistaas</td>
     <td>Residential/Group Housing</td><td>Bengaluru Urban</td>
     <td>31/08/2017</td><td>03/11/2017</td><td>01/09/2026</td></tr>
</table>
"""


def test_default_projects_are_deduplicated_across_the_pages_two_tables():
    """The page's short summary table and its full register both name the
    same project -- reading both without deduping would double-count every
    row the summary table also carries."""
    rows = fetch_default_projects(fetcher=lambda: _DEFAULT_PROJECTS_HTML)
    reg_nos = [r["registration no"] for r in rows]
    assert reg_nos.count("PRM/KA/RERA/1251/446/PR/040826/008858") == 1, rows
    assert len(rows) == 2, rows
    print("test_default_projects_are_deduplicated_across_the_pages_two_tables: PASS")


def test_search_default_projects_matches_on_the_promoter_column():
    hits = search_default_projects_by_promoter(
        "Pranami Builders", fetcher=lambda: _DEFAULT_PROJECTS_HTML
    )
    assert len(hits) == 1, hits
    assert hits[0]["project"] == "Pranami Towers", hits[0]
    assert search_default_projects_by_promoter(
        "Nobody Matches This", fetcher=lambda: _DEFAULT_PROJECTS_HTML
    ) == []
    print("test_search_default_projects_matches_on_the_promoter_column: PASS")


# Real shape confirmed live 2026-09-09 -- a rich register with a genuine
# PROMOTER NAME column (unlike most other authorities' enforcement pages).
_REVENUE_RECOVERY_HTML = """
<table><tr><td colspan="15">no rows here -- confirmed live on this page</td></tr></table>
<table>
 <tr><th>S.NO</th><th>COMPLAINT NO</th><th>COMPLAINANT NAME</th><th>PROMOTER NAME</th>
     <th>PROJECT NAME</th><th>PROJECT STATUS</th><th>DISTRICT</th><th>AMOUNT</th>
     <th>REMARKS</th><th>JUDGEMENT DATE</th><th>JUDGEMENT COPY</th><th>EXECUTION DATE</th>
     <th>EXECUTION COPY</th><th>RRC DATE (DD-MM-YYYY)</th><th>RRC COPY</th></tr>
 <tr><td></td><td>00537/2025</td><td>Sri Shivakumar Dharmalingam</td>
     <td>Pranami Builders Pvt Ltd</td><td>Pranami Towers</td><td>APPROVED</td>
     <td>Bengaluru Urban</td><td>3110344</td><td>RRC</td><td>18/04/2026</td><td></td>
     <td>14/08/2026</td><td></td><td>31/08/2026</td><td></td></tr>
</table>
"""


def test_revenue_recovery_list_is_read_from_the_real_register_table():
    """The near-empty table on this page must not be mistaken for the
    register itself -- confirmed live, this page carries more than one
    table and only the one with a real header is the actual list."""
    rows = fetch_revenue_recovery_list(fetcher=lambda: _REVENUE_RECOVERY_HTML)
    assert len(rows) == 1, rows
    assert rows[0]["promoter name"] == "Pranami Builders Pvt Ltd", rows[0]
    assert rows[0]["amount"] == "3110344", rows[0]
    print("test_revenue_recovery_list_is_read_from_the_real_register_table: PASS")


def test_search_revenue_recovery_matches_on_the_promoter_name_column():
    hits = search_revenue_recovery_by_promoter(
        "Pranami Builders", fetcher=lambda: _REVENUE_RECOVERY_HTML
    )
    assert len(hits) == 1, hits
    assert hits[0]["complaint no"] == "00537/2025", hits[0]
    assert search_revenue_recovery_by_promoter(
        "Nobody Matches This", fetcher=lambda: _REVENUE_RECOVERY_HTML
    ) == []
    print("test_search_revenue_recovery_matches_on_the_promoter_name_column: PASS")


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


def test_live_delay_reason_and_noc_status_are_populated_for_a_known_project():
    """The real project this discovery was made against -- confirmed live
    2026-09-09 to carry a populated Delay Reason row and a populated NOC
    status table (its NOC expiry table is empty on this project, which is
    fine: the point is that the OTHER two are not silently dropped)."""
    if not _LIVE:
        print("test_live_delay_reason_and_noc_status_are_populated_for_a_known_project: "
              "SKIPPED (set KRERA_LIVE=1)")
        return
    from bs4 import BeautifulSoup

    from states.adapter_karnataka import _index_entry, _session, _table_to_rows
    from states.karnataka import DETAIL_POST, SEARCH_POST

    entry = _index_entry(_REG_WITH_COMPLAINTS)
    session = _session()
    summary_html = session.post(
        SEARCH_POST, data={"regNo": entry["reg_no"], "appNo": entry["ack_no"], "btn1": "Search"},
        timeout=60,
    ).text
    summary, action_id = ADAPTER._parse_summary(summary_html)
    detail_html = session.post(DETAIL_POST, data={"action": action_id}, timeout=60).text
    tables = [_table_to_rows(t) for t in BeautifulSoup(detail_html, "html.parser").find_all("table")]
    parsed = ADAPTER._parse_detail(tables, summary)
    assert parsed["delay_reasons"], "expected at least one delay reason row"
    assert parsed["noc_status"], "expected at least one NOC status row"
    print("test_live_delay_reason_and_noc_status_are_populated_for_a_known_project: PASS")


def test_live_default_project_list_loads_and_is_a_real_register():
    if not _LIVE:
        print("test_live_default_project_list_loads_and_is_a_real_register: SKIPPED (set KRERA_LIVE=1)")
        return
    rows = fetch_default_projects()
    assert len(rows) > 500, len(rows)
    assert all(r.get("registration no") and r.get("promoter") for r in rows[:50]), rows[:3]
    print(f"test_live_default_project_list_loads_and_is_a_real_register: PASS ({len(rows)} rows)")


def test_live_revenue_recovery_list_loads_and_is_a_real_register():
    if not _LIVE:
        print("test_live_revenue_recovery_list_loads_and_is_a_real_register: SKIPPED (set KRERA_LIVE=1)")
        return
    rows = fetch_revenue_recovery_list()
    assert len(rows) > 500, len(rows)
    assert all(r.get("promoter name") and r.get("complaint no") for r in rows[:50]), rows[:3]
    print(f"test_live_revenue_recovery_list_loads_and_is_a_real_register: PASS ({len(rows)} rows)")


if __name__ == "__main__":
    test_the_state_index_pairs_four_parallel_arrays()
    test_mismatched_index_arrays_raise_rather_than_zip()
    test_an_empty_index_is_empty_not_an_error()
    test_tables_are_found_by_header_not_by_index()
    test_labelled_rows_drops_ragged_rows()
    test_delay_reason_and_both_noc_tables_are_found_and_never_confused()
    test_delay_reasons_and_noc_tables_are_empty_lists_when_absent_not_missing_keys()
    test_default_projects_are_deduplicated_across_the_pages_two_tables()
    test_search_default_projects_matches_on_the_promoter_column()
    test_revenue_recovery_list_is_read_from_the_real_register_table()
    test_search_revenue_recovery_matches_on_the_promoter_name_column()
    test_the_storage_key_flattens_the_slashes()
    test_both_registration_prefixes_resolve_to_karnataka()
    test_capabilities_match_what_the_portal_actually_offers()
    test_live_complaint_count_comes_from_the_state_register()
    test_live_index_and_portfolio()
    test_live_delay_reason_and_noc_status_are_populated_for_a_known_project()
    test_live_default_project_list_loads_and_is_a_real_register()
    test_live_revenue_recovery_list_loads_and_is_a_real_register()
    print("\nAll tests passed.")

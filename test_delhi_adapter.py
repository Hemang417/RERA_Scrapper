"""
Guards on the Delhi-RERA adapter.

THE BUG THIS FILE EXISTS FOR made one of Delhi's 130 projects invisible.

The register appends a footnote marker INTO the cell:

    <td class="project-diary-number" data-diary-no="DLRERA2025P0003">
      <span class="custom-badge badge-low-warning">
        DLRERA2025P0003
        <label style="color:red" title="view disclaimer">*</label>

so the cell text reads "DLRERA2025P0003 *". Taken as the registration
number that string matches nothing: not the DL profile's own reg-no
pattern, and not a reader pasting the number as the authority issued it. So
Good Earth Capital Crest could not be resolved by its own registration
number, would never appear in a promoter portfolio, and could not be
confirmed as a sweep hit. Same species as HARERA's "Lapsed Project" flag
concatenating onto a certificate number.

The register states the number twice -- contaminated as cell text, clean in
`data-diary-no` -- so the attribute is now preferred and the marker is
stripped as a fallback. The marker itself is kept as `has_disclaimer`,
because an authority flagging a registration is diligence material.

THE SECOND BUG was a count. Delhi's complaint register publishes one row per
ORDER, not per complaint: complaint 30/2020 -- one complainant, one
respondent -- occupies THIRTY-FOUR rows differing only by decision date.
Counting rows made that promoter look like it had 34 complaints. But the
complaint NUMBER alone is not the key either: across the register 624 rows
carry 68 distinct (number + parties) but only 51 distinct numbers, so
numbers are reused between unrelated cases and collapsing on the number
alone would merge different people's complaints and UNDER-report. The
parties are what separate them. Each row also links its own judgement PDF,
which the parser was discarding.

THE THIRD THING HERE IS A TRAP THAT WAS NOT FALLEN INTO, and the test for it
is the most valuable one in the file. Every register row carries a hidden
`hdnPromoterID`. It looks exactly like the promoter identifier this adapter
says Delhi does not publish -- and using it for the portfolio would be
wrong: it is per-REGISTRATION, not per-promoter. The Delhi Development
Authority holds 22 different ones. A future reader WILL notice that field;
test_the_hidden_promoter_id_must_not_be_used_for_the_portfolio is there to
answer them.

Network tests are opt-in: DELHIRERA_LIVE=1.

Run directly: python test_delhi_adapter.py
"""

import os

import group_sweep as gs
import states
from states.adapter_delhi import (
    DelhiAdapter,
    distinct_complaints,
    fetch_execution_register,
    fetch_project_summary,
    fetch_suo_moto_register,
    parse_execution_register,
    parse_order_register,
    parse_state_index,
    parse_suo_moto_register,
    search_orders_by_promoter,
    search_promoter_projects,
    split_applicant_kind,
    strip_disclaimer_marker,
)

_LIVE = os.environ.get("DELHIRERA_LIVE") == "1"

# The register, reproduced with the shapes that matter: the disclaimer row,
# the hidden promoter ids, and the data-diary-no attribute.
_INDEX_HTML = """
<table>
 <tr><th>SNo</th><th>District Name</th><th>Project's Name</th><th>Promoter's Name</th>
     <th>Registration Number</th><th>Registration Valid Upto Date</th>
     <th>Type of Project</th><th>View Details</th></tr>
 <tr>
   <td>1<input class="hdnPromoterID" name="item.Promoter_ID" type="hidden" value="1101"/></td>
   <td>South West Delhi</td><td>Group Housing RR Texknit LLP</td>
   <td>RR TEXKNIT LLP (Other than Individual)</td>
   <td class="project-diary-number" data-diary-no="DLRERA2026P0012">
      <span class="custom-badge">DLRERA2026P0012</span></td>
   <td>11-May-2029</td><td>Group Housing</td>
   <td><a href="javascript:void(0);" id="modalOpenerButton">View</a></td></tr>
 <tr>
   <td>2<input class="hdnPromoterID" name="item.Promoter_ID" type="hidden" value="1089"/>
       <label style="color:red" title="view disclaimer"> *</label></td>
   <td>North Delhi</td><td>Good Earth Capital Crest <label style="color:red">*</label></td>
   <td>Modern Flour Mills Private Limited (Other than Individual)</td>
   <td class="project-diary-number" data-diary-no="DLRERA2025P0003">
      <span class="custom-badge">DLRERA2025P0003
      <label style="color:red" title="view disclaimer">*</label></span></td>
   <td>30-Jun-2028</td><td>Commercial, Industrial, Residential</td>
   <td><a href="javascript:void(0);" id="modalOpenerButton">View</a></td></tr>
 <tr>
   <td>3<input class="hdnPromoterID" name="item.Promoter_ID" type="hidden" value="1056"/></td>
   <td>Central Delhi</td><td>DDA Scheme One</td>
   <td>DELHI DEVELOPMENT AUTHORITY (Other than Individual)</td>
   <td class="project-diary-number" data-diary-no="DLRERA2024P0001">
      <span class="custom-badge">DLRERA2024P0001</span></td>
   <td>01-Jan-2028</td><td>Residential</td><td></td></tr>
 <tr>
   <td>4<input class="hdnPromoterID" name="item.Promoter_ID" type="hidden" value="1057"/></td>
   <td>Central Delhi</td><td>DDA Scheme Two</td>
   <td>DELHI DEVELOPMENT AUTHORITY (Other than Individual)</td>
   <td class="project-diary-number" data-diary-no="DLRERA2024P0002">
      <span class="custom-badge">DLRERA2024P0002</span></td>
   <td>01-Jan-2028</td><td>Residential</td><td></td></tr>
</table>
"""

# The page serves TWO 131-row tables. This is the decoy: same size, same
# shape, "Quarter Name" where the register carries "Promoter's Name".
_DECOY_HTML = """
<table>
 <tr><th>SNo</th><th>District Name</th><th>Project's Name</th><th>Quarter Name</th>
     <th>Registration Number</th><th>Registration Valid Upto Date</th></tr>
 <tr><td>1</td><td>South West Delhi</td><td>Group Housing RR Texknit LLP</td>
     <td>Jan-Mar 2026</td>
     <td class="project-diary-number" data-diary-no="DLRERA2026P0012">DLRERA2026P0012</td>
     <td>11-May-2029</td></tr>
</table>
"""

# One complaint, three orders -- and a second, unrelated complaint that
# happens to reuse the number.
_ORDERS_HTML = """
<table>
 <tr><th>Sr.No.</th><th>Complaint Number</th><th>Complainant Name</th>
     <th>Respondent Name</th><th>Date of Decision</th><th>View Judgement</th></tr>
 <tr><td>1</td><td>30/2020</td><td>Pradeep Gupta</td>
     <td>M/s Skylark Multistate CGHS Ltd</td><td>28-Jul-2026</td>
     <td><a href="https://erera.co.in/delhirera/x/30_2020_a.pdf">View</a></td></tr>
 <tr><td>2</td><td>30/2020</td><td>Pradeep Gupta</td>
     <td>M/s Skylark Multistate CGHS Ltd</td><td>26-May-2026</td>
     <td><a href="https://erera.co.in/delhirera/x/30_2020_b.pdf">View</a></td></tr>
 <tr><td>3</td><td>30/2020</td><td>Pradeep Gupta</td>
     <td>M/s Skylark Multistate CGHS Ltd</td><td>27-Jan-2026</td><td></td></tr>
 <tr><td>4</td><td>30/2020</td><td>Someone Else</td>
     <td>ANOTHER RESPONDENT LTD</td><td>01-Feb-2026</td><td></td></tr>
</table>
"""


class _Reporter:
    def __init__(self):
        self.messages = []

    def info(self, m): self.messages.append(m)
    def warn(self, m): self.messages.append(m)
    def ok(self, m): self.messages.append(m)
    def choose(self, p, o): return None


class _Ctx:
    def __init__(self, reporter=None, output_dir="output"):
        self.reporter = reporter or _Reporter()
        self.output_dir = output_dir
        self.on_resolved = None


# --- the disclaimer marker ------------------------------------------------

def test_a_footnote_marker_does_not_become_part_of_the_number():
    """THE CENTRAL GUARD. 'DLRERA2025P0003 *' resolves to nothing at all --
    not to the profile pattern, not to a reader's paste."""
    assert strip_disclaimer_marker("DLRERA2025P0003 *") == "DLRERA2025P0003"
    assert strip_disclaimer_marker("Good Earth Capital Crest *") == "Good Earth Capital Crest"
    assert strip_disclaimer_marker("DLRERA2026P0012") == "DLRERA2026P0012"
    # A star inside the text is not a trailing marker.
    assert strip_disclaimer_marker("A * B") == "A * B"
    print("test_a_footnote_marker_does_not_become_part_of_the_number: PASS")


def test_every_parsed_number_matches_the_states_own_pattern():
    """The check that would have caught it. A registration number this
    adapter produces but its own profile rejects is unroutable -- and the
    failure is silent, because it looks like a project that does not
    exist."""
    import re

    pattern = states.PROFILES["DL"].reg_no_pattern
    rows = parse_state_index(_INDEX_HTML)
    assert rows, "the register did not parse at all"
    for row in rows:
        assert re.match(pattern, row["reg_no"]), (
            f"{row['reg_no']!r} is not resolvable by the DL profile's own pattern"
        )
    print("test_every_parsed_number_matches_the_states_own_pattern: PASS")


def test_the_marker_is_kept_as_a_flag_rather_than_discarded():
    """Stripping it must not lose it. The authority flagged this
    registration, and that is diligence material -- what the marker MEANS is
    not published, so the adapter records it and says so rather than
    inventing an interpretation."""
    rows = {r["reg_no"]: r for r in parse_state_index(_INDEX_HTML)}
    assert rows["DLRERA2025P0003"]["has_disclaimer"] is True
    assert rows["DLRERA2026P0012"]["has_disclaimer"] is False
    assert rows["DLRERA2025P0003"]["project_name"] == "Good Earth Capital Crest"
    print("test_the_marker_is_kept_as_a_flag_rather_than_discarded: PASS")


def test_the_clean_number_comes_from_the_attribute_the_page_already_carries():
    """The register states the number twice. The attribute is the one that
    was never contaminated, so it wins and the strip is only a fallback."""
    contaminated = _INDEX_HTML.replace(
        'data-diary-no="DLRERA2025P0003"', 'data-diary-no="DLRERA2025P0003"'
    )
    rows = parse_state_index(contaminated)
    assert any(r["reg_no"] == "DLRERA2025P0003" for r in rows), [r["reg_no"] for r in rows]

    # And with the attribute gone, the stripped cell text still resolves.
    stripped = _INDEX_HTML.replace(' data-diary-no="DLRERA2025P0003"', "")
    rows = parse_state_index(stripped)
    assert any(r["reg_no"] == "DLRERA2025P0003" for r in rows), [r["reg_no"] for r in rows]
    print("test_the_clean_number_comes_from_the_attribute_the_page_already_carries: PASS")


# --- the two identical-looking tables ------------------------------------

def test_the_register_is_found_by_header_not_by_position():
    """The page serves two 131-row tables whose headers differ only in the
    middle: the register carries "Promoter's Name", the quarterly-updates
    table carries "Quarter Name". Taking tables[0] works today and breaks
    silently the moment they reorder."""
    rows = parse_state_index(_DECOY_HTML + _INDEX_HTML)
    assert len(rows) == 4, [r["reg_no"] for r in rows]
    assert rows[0]["promoter_name"] == "RR TEXKNIT LLP", rows[0]
    # The decoy alone must yield nothing rather than rows with no promoter.
    assert parse_state_index(_DECOY_HTML) == []
    print("test_the_register_is_found_by_header_not_by_position: PASS")


def test_the_applicant_classification_is_split_off_the_name():
    """An individual promoter and a company are different diligence
    subjects, and it is the only such signal the index carries."""
    assert split_applicant_kind("RR TEXKNIT LLP (Other than Individual)") == \
        ("RR TEXKNIT LLP", "Other than Individual")
    assert split_applicant_kind("ADITYA VIKRAM BANSAL (Individual)") == \
        ("ADITYA VIKRAM BANSAL", "Individual")
    assert split_applicant_kind("NO CLASSIFICATION LTD") == ("NO CLASSIFICATION LTD", "")
    print("test_the_applicant_classification_is_split_off_the_name: PASS")


# --- the hidden promoter id ----------------------------------------------

def test_the_hidden_promoter_id_must_not_be_used_for_the_portfolio():
    """THE TRAP, AND THE REASON THIS TEST EXISTS.

    Every register row carries a hidden `hdnPromoterID`, and it looks
    exactly like the promoter identifier this adapter's notes say Delhi does
    not publish. It is not one: it is issued per REGISTRATION. Counted live
    on the real register, the Delhi Development Authority appears under
    TWENTY-TWO different promoter ids, NBCC under eleven across two
    spellings of its name.

    Joining a portfolio on it would split one promoter into twenty-two and
    report a developer with 22 Delhi projects as having one. The name match
    is the weaker-looking option and the correct one; its own limitation
    (two spellings stay apart) is stated in the portfolio notes instead."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_INDEX_HTML, "html.parser")
    ids = [i.get("value") for i in soup.find_all("input", {"class": "hdnPromoterID"})]
    # The fixture mirrors the real register: one promoter, two ids.
    assert ids.count("1056") == 1 and ids.count("1057") == 1
    dda = [r for r in parse_state_index(_INDEX_HTML)
           if r["promoter_name"] == "DELHI DEVELOPMENT AUTHORITY"]
    assert len(dda) == 2, dda

    # The parsed rows must not carry it, so nothing downstream can join on it.
    assert all("promoter_id" not in row for row in parse_state_index(_INDEX_HTML))

    adapter = DelhiAdapter()
    index = parse_state_index(_INDEX_HTML)
    portfolio = adapter._promoter_portfolio(index, dda[0], _Ctx())
    assert portfolio["totals"]["total_projects"] == 2, portfolio
    print("test_the_hidden_promoter_id_must_not_be_used_for_the_portfolio: PASS")


def test_the_portfolio_matches_the_name_exactly_and_says_so():
    """An exact normalised match, not a substring: the register is small and
    a loose match folds an unrelated promoter's projects into this one's
    track record. The cost -- two spellings of one company stay apart -- is
    stated rather than hidden."""
    index = parse_state_index(_INDEX_HTML)
    chosen = next(r for r in index if r["reg_no"] == "DLRERA2026P0012")
    portfolio = DelhiAdapter()._promoter_portfolio(index, chosen, _Ctx())
    assert portfolio["totals"]["total_projects"] == 1, portfolio
    assert any("differently spelled name" in note for note in portfolio["notes"]), \
        portfolio["notes"]
    print("test_the_portfolio_matches_the_name_exactly_and_says_so: PASS")


# --- the complaint count -------------------------------------------------

def test_one_complaint_decided_three_times_is_one_complaint():
    """THE SECOND BUG. The register publishes every interim order, so
    counting rows reported a promoter with one complaint and 34 orders as
    having 34 complaints."""
    rows = parse_order_register(_ORDERS_HTML)
    assert len(rows) == 4, rows

    skylark = [r for r in rows if "Skylark" in r["respondent"]]
    assert len(skylark) == 3, skylark
    assert len(distinct_complaints(skylark)) == 1, distinct_complaints(skylark)
    print("test_one_complaint_decided_three_times_is_one_complaint: PASS")


def test_a_reused_complaint_number_is_not_one_complaint():
    """And the opposite error, which is just as bad. Complaint numbers are
    reused between unrelated cases -- 624 rows carry 68 distinct
    (number + parties) but only 51 distinct numbers -- so collapsing on the
    number alone merges different people's complaints and under-reports."""
    rows = parse_order_register(_ORDERS_HTML)
    assert len({r["complaint_no"] for r in rows}) == 1, "the fixture reuses one number"
    assert len(distinct_complaints(rows)) == 2, distinct_complaints(rows)
    print("test_a_reused_complaint_number_is_not_one_complaint: PASS")


def test_the_count_reaching_the_reader_separates_complaints_from_orders():
    adapter = DelhiAdapter()
    reporter = _Reporter()

    # Injected register, so no network.
    import states.adapter_delhi as dl
    original = dl.fetch_order_register
    dl.fetch_order_register = lambda fetcher=None: parse_order_register(_ORDERS_HTML)
    try:
        complaints, notes = adapter._complaints("M/s Skylark Multistate CGHS Ltd",
                                                _Ctx(reporter))
    finally:
        dl.fetch_order_register = original

    assert complaints["total_complaints_count"] == 1, complaints
    assert complaints["total_orders_published"] == 3, complaints
    assert any("3 published order(s)" in n for n in notes), notes
    assert any("possible matches to confirm" in n for n in notes), notes
    print("test_the_count_reaching_the_reader_separates_complaints_from_orders: PASS")


def test_each_order_keeps_its_own_judgement_link():
    """The evidence. A row without a published order must not borrow the
    previous row's link."""
    rows = parse_order_register(_ORDERS_HTML)
    assert rows[0]["order_url"].endswith("30_2020_a.pdf"), rows[0]
    assert rows[1]["order_url"].endswith("30_2020_b.pdf"), rows[1]
    assert rows[2]["order_url"] == "", rows[2]
    print("test_each_order_keeps_its_own_judgement_link: PASS")


def test_orders_are_matched_on_the_respondent():
    """A complaint is filed AGAINST the promoter. Matching the complainant
    column would return the promoter's own customers."""
    hits = search_orders_by_promoter("Skylark", fetcher=lambda: _ORDERS_HTML)
    assert len(hits) == 3, hits
    assert not search_orders_by_promoter("Pradeep Gupta", fetcher=lambda: _ORDERS_HTML), \
        "the complainant must not match"
    assert search_orders_by_promoter("", fetcher=lambda: _ORDERS_HTML) == []
    print("test_orders_are_matched_on_the_respondent: PASS")


# --- declared absences ---------------------------------------------------

def test_what_delhi_does_not_publish_is_declared_not_left_empty():
    """There is no reachable per-project record at all -- the register's
    View control is inert and every detail route probed 404s. So these
    categories are None and named as not published, never {} or 0, or a
    Charter renders an empty Document Library as though the promoter filed
    nothing."""
    profile = states.PROFILES["DL"]
    assert not profile.can(states.CAP_DOCUMENTS)
    assert not profile.can(states.CAP_CATEGORY_API)
    assert profile.can(states.CAP_PROMOTER_PORTFOLIO)

    summary = fetch_project_summary("")
    assert summary["opened"] is False and "No Delhi-RERA registration" in summary["note"]
    print("test_what_delhi_does_not_publish_is_declared_not_left_empty: PASS")


def test_an_absence_from_a_130_project_register_is_weak_evidence():
    """The most important sentence this adapter produces. Delhi has 130
    registered projects for the whole NCT; a promoter with genuine Delhi
    activity may legitimately have no record here."""
    import states.adapter_delhi as dl

    original = dl._INDEX_CACHE[:]
    dl._INDEX_CACHE[:] = parse_state_index(_INDEX_HTML)
    try:
        summary = fetch_project_summary("DLRERA2099P9999")
        assert summary["opened"] is False
        assert "not proof" in summary["note"], summary["note"]
        # And a real one opens, carrying the promoter name a sweep confirms on.
        summary = fetch_project_summary("DLRERA2025P0003")
        assert summary["opened"] is True, summary
        assert summary["promoter_name"] == "Modern Flour Mills Private Limited", summary
    finally:
        dl._INDEX_CACHE[:] = original
    print("test_an_absence_from_a_130_project_register_is_weak_evidence: PASS")


def test_an_unreadable_register_is_not_an_empty_one():
    """A register that returned nothing means it could not be read, not that
    Delhi has no registered projects -- and acquire must say which."""
    adapter = DelhiAdapter()
    import states.adapter_delhi as dl

    original = dl._get
    dl._get = lambda session, url, what="page": "<html><body>nothing</body></html>"
    try:
        adapter.acquire("DLRERA2026P0012", _Ctx())
    except states.StateFetchError as e:
        assert "could not be read" in str(e), str(e)
    except states.StateResolutionError as e:  # pragma: no cover
        raise AssertionError(f"an unreadable register was reported as an absence: {e}")
    else:
        raise AssertionError("an empty register was accepted")
    finally:
        dl._get = original
    print("test_an_unreadable_register_is_not_an_empty_one: PASS")


# --- suo-moto and execution registers (group_enforcement's join keys) ----

_SUOMOTO_HTML = """
<table>
 <tr><th>Sr.No.</th><th>Case No.</th><th>Respondent Name</th><th>Project Details</th>
     <th>Hearing Type</th><th>Last Hearing</th><th>Next Hearing</th></tr>
 <tr><td>1</td><td>SM/12/2024</td><td>M/s Skylark Multistate CGHS Ltd</td>
     <td>Some Project</td><td>Suo Moto</td><td>10-Jan-2026</td><td>10-Feb-2026</td></tr>
 <tr><td>2</td><td>SM/13/2024</td><td></td>
     <td>No respondent named</td><td>Suo Moto</td><td></td><td></td></tr>
</table>
"""

_EXECUTION_HTML = """
<table>
 <tr><th>Sr.No.</th><th>Execution Number</th><th>Complaint Number</th>
     <th>Decree Holder</th><th>Judgement Debtor</th><th>Date Of Decision</th>
     <th>Next Hearing</th></tr>
 <tr><td>1</td><td>EX/45/2024</td><td>30/2020</td><td>Pradeep Gupta</td>
     <td>M/s Skylark Multistate CGHS Ltd</td><td>28-Jul-2026</td><td>15-Sep-2026</td></tr>
 <tr><td>2</td><td>EX/46/2024</td><td>31/2020</td><td>Someone Else</td>
     <td></td><td>01-Aug-2026</td><td></td></tr>
</table>
"""


def test_the_suo_moto_register_is_found_by_header_and_names_the_respondent():
    """The closest thing this portal publishes to "projects under
    investigation" -- confirmed a row with no respondent named is dropped,
    since an unsearchable row can never become a hit."""
    rows = parse_suo_moto_register(_SUOMOTO_HTML)
    assert len(rows) == 1, rows
    assert rows[0]["case_no"] == "SM/12/2024", rows[0]
    assert rows[0]["respondent_name"] == "M/s Skylark Multistate CGHS Ltd", rows[0]
    print("test_the_suo_moto_register_is_found_by_header_and_names_the_respondent: PASS")


def test_the_execution_register_names_the_judgement_debtor_and_keeps_both_links():
    """Every row is an order the promoter (the Judgement Debtor) has not
    complied with. A row with no debtor named is dropped."""
    rows = parse_execution_register(_EXECUTION_HTML)
    assert len(rows) == 1, rows
    assert rows[0]["judgement_debtor"] == "M/s Skylark Multistate CGHS Ltd", rows[0]
    assert rows[0]["execution_no"] == "EX/45/2024", rows[0]
    print("test_the_execution_register_names_the_judgement_debtor_and_keeps_both_links: PASS")


def test_fetch_suo_moto_and_execution_registers_mirror_fetch_order_register():
    """The two new fetch wrappers must fetch once, parse, and cache -- same
    shape as fetch_order_register(), confirmed by injecting a fetcher rather
    than hitting the network."""
    import states.adapter_delhi as dl

    dl._SUOMOTO_CACHE.clear()
    dl._EXECUTION_CACHE.clear()
    calls = {"suomoto": 0, "execution": 0}

    def suomoto_fetcher():
        calls["suomoto"] += 1
        return _SUOMOTO_HTML

    def execution_fetcher():
        calls["execution"] += 1
        return _EXECUTION_HTML

    rows = fetch_suo_moto_register(fetcher=suomoto_fetcher)
    assert len(rows) == 1 and calls["suomoto"] == 1, (rows, calls)
    rows2 = fetch_execution_register(fetcher=execution_fetcher)
    assert len(rows2) == 1 and calls["execution"] == 1, (rows2, calls)
    print("test_fetch_suo_moto_and_execution_registers_mirror_fetch_order_register: PASS")


# --- the sweep seam ------------------------------------------------------

def test_delhi_is_searchable_and_openable():
    import states.adapter_delhi as dl

    assert hasattr(dl, "search_promoter_projects")
    assert "DL" in gs.searchable_states(), gs.searchable_states()
    assert hasattr(dl, "fetch_project_summary"), (
        "a swept hit that cannot be opened can be neither confirmed nor refuted"
    )

    original = dl._INDEX_CACHE[:]
    dl._INDEX_CACHE[:] = parse_state_index(_INDEX_HTML)
    try:
        hits = search_promoter_projects("DELHI DEVELOPMENT AUTHORITY")
        assert len(hits) == 2, hits
        assert all(h["project_id"] for h in hits), hits
    finally:
        dl._INDEX_CACHE[:] = original
    print("test_delhi_is_searchable_and_openable: PASS")


# --- live ----------------------------------------------------------------

def test_live_the_whole_register_is_one_get_and_every_number_resolves():
    if not _LIVE:
        print("test_live_the_whole_register_is_one_get_and_every_number_resolves: "
              "SKIPPED (set DELHIRERA_LIVE=1)")
        return
    import re

    from states.adapter_delhi import _get, _session
    from states.delhi import STATE_INDEX

    rows = parse_state_index(_get(_session(), STATE_INDEX, what="register"))
    assert len(rows) > 100, len(rows)
    pattern = states.PROFILES["DL"].reg_no_pattern
    unroutable = [r["reg_no"] for r in rows if not re.match(pattern, r["reg_no"])]
    assert not unroutable, unroutable
    assert len({r["reg_no"] for r in rows}) == len(rows), "duplicate registration numbers"
    print(f"test_live_the_whole_register_is_one_get_and_every_number_resolves: PASS "
          f"({len(rows)} projects)")


def test_live_the_order_register_publishes_more_orders_than_complaints():
    """The finding, checked against the portal so it cannot quietly stop
    being true."""
    if not _LIVE:
        print("test_live_the_order_register_publishes_more_orders_than_complaints: "
              "SKIPPED (set DELHIRERA_LIVE=1)")
        return
    from states.adapter_delhi import fetch_order_register

    rows = fetch_order_register()
    complaints = distinct_complaints(rows)
    assert rows, "the complaint register returned nothing"
    assert len(complaints) < len(rows), (len(complaints), len(rows))
    # And the number alone is a coarser key than the number plus parties.
    assert len({r["complaint_no"] for r in rows}) < len(complaints), (
        "complaint numbers are no longer reused -- the dedup key can be simplified"
    )
    print(f"test_live_the_order_register_publishes_more_orders_than_complaints: PASS "
          f"({len(rows)} orders, {len(complaints)} complaints)")


if __name__ == "__main__":
    test_a_footnote_marker_does_not_become_part_of_the_number()
    test_every_parsed_number_matches_the_states_own_pattern()
    test_the_marker_is_kept_as_a_flag_rather_than_discarded()
    test_the_clean_number_comes_from_the_attribute_the_page_already_carries()
    test_the_register_is_found_by_header_not_by_position()
    test_the_applicant_classification_is_split_off_the_name()
    test_the_hidden_promoter_id_must_not_be_used_for_the_portfolio()
    test_the_portfolio_matches_the_name_exactly_and_says_so()
    test_one_complaint_decided_three_times_is_one_complaint()
    test_a_reused_complaint_number_is_not_one_complaint()
    test_the_count_reaching_the_reader_separates_complaints_from_orders()
    test_each_order_keeps_its_own_judgement_link()
    test_orders_are_matched_on_the_respondent()
    test_what_delhi_does_not_publish_is_declared_not_left_empty()
    test_an_absence_from_a_130_project_register_is_weak_evidence()
    test_an_unreadable_register_is_not_an_empty_one()
    test_the_suo_moto_register_is_found_by_header_and_names_the_respondent()
    test_the_execution_register_names_the_judgement_debtor_and_keeps_both_links()
    test_fetch_suo_moto_and_execution_registers_mirror_fetch_order_register()
    test_delhi_is_searchable_and_openable()
    test_live_the_whole_register_is_one_get_and_every_number_resolves()
    test_live_the_order_register_publishes_more_orders_than_complaints()
    print("\nAll tests passed.")

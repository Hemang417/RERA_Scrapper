"""
Guards on igr_maharashtra_search.py's pure parsing/matching logic --
everything that does NOT need a browser or a human to solve a CAPTCHA.

_REAL_ROW_HTML below is the actual table row this pipeline pulled live
2026-09-01 (Document Number search, SRO "Joint S.R. Mumbai 9 (Andheri 2
(Andheri))", 2024, doc #100): a real Leave & License agreement naming
Ramesh Babulal Shah as lessor and M/s Matushri Impex as lessee, with the
full property description and consideration inline. This is captured
verbatim, not paraphrased, so the parser is pinned against the real shape
IGR Maharashtra actually serves, not a guess at one.

Run directly: python test_igr_maharashtra_search.py
"""

import igr_maharashtra_search as igr

_REAL_ROW_HTML = """
<table>
<tr><th>DocNo</th><th>DName</th><th>RDate</th><th>SROName</th><th>Seller Name</th>
<th>Purchaser Name</th><th>Property Description</th><th>SROCode</th><th>Status</th><th>IndexII</th></tr>
<tr>
<td>100</td><td>36-अ-लिव्ह अॅड लायसन्सेस</td><td>03/01/2024</td><td>सह दु.नि.मुंबई 9</td>
<td>{रमेशबबालालशाह}</td>
<td>{"मे. मातुश्री इम्पेक्सतर्फे भागिदाररमेश टी. भलानी"}</td>
<td>, इतर  माहिती: प्रिमायसेस क्र. डी ई 5030,क्षेत्रफळ 294 चौ. फुट बिल्टअप,5 वा मजला,डी टॉवर,ईस्ट विंग,
भारत डायमंड बोर्स कॉम्प्लेक्स,प्लॉट क्र. सी 28,जी ब्लॉक,बांद्रा कुर्ला कॉम्प्लेक्स,बांद्रा(पूर्व),
मुंबई - 400051. सी. टी. एस. क्र. 4207 कोले कल्याण विभाग तालुका अंधेरी व इतर माहिती दस्तात नमूद केल्याप्रमाणे.
कालावधी: 24 महिने ; मासिक भाडे रु. 44,100/- आणि पहिल्या 12 महिन्यांचे आगाऊ भाडे रु. 5,29,200/-</td>
<td style="width:1px;">323</td>
<td style="width:1px;">4</td>
<td><input type="button" value="IndexII" onclick="javascript:__doPostBack('RegistrationGrid','indexII$0')" class="Button"></td>
</tr>
</table>
"""

_UNRELATED_TABLE_HTML = """
<table><tr><th>Some</th><th>Other</th><th>Table</th></tr>
<tr><td>a</td><td>b</td><td>c</td></tr></table>
"""


def test_the_real_captured_row_parses_with_party_names_and_consideration():
    """The whole point of building this: a free search already returns the
    seller, the purchaser, the property description AND the consideration
    amount in one row -- no further click needed."""
    rows = igr._parse_document_results(_REAL_ROW_HTML)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["docno"] == "100", row
    assert "लिव्ह" in row["dname"], row  # Leave & License
    assert row["rdate"] == "03/01/2024", row
    assert "रमेशबबालालशाह" in row["seller name"], row
    assert "मातुश्री इम्पेक्स" in row["purchaser name"], row
    assert "44,100" in row["property description"], row  # the monthly rent
    assert "5,29,200" in row["property description"], row  # the advance
    assert "4207" in row["property description"], row  # the CTS number
    print("test_the_real_captured_row_parses_with_party_names_and_consideration: PASS")


def test_a_table_without_the_expected_headers_is_ignored():
    """A page can render OTHER tables (menus, layout tables) before the
    results grid -- only a table naming DocNo/Seller/Purchaser should ever
    be read as a result."""
    assert igr._parse_document_results(_UNRELATED_TABLE_HTML) == []
    print("test_a_table_without_the_expected_headers_is_ignored: PASS")


def test_a_header_only_table_with_no_data_rows_is_a_clean_empty_result():
    header_only = """<table><tr><th>DocNo</th><th>Seller Name</th><th>Purchaser Name</th></tr></table>"""
    assert igr._parse_document_results(header_only) == []
    print("test_a_header_only_table_with_no_data_rows_is_a_clean_empty_result: PASS")


def test_district_hints_cover_the_two_mumbai_districts():
    """The two districts this pipeline's Maharashtra subjects concentrate
    in -- confirmed against the portal's own Marathi option text, which is
    otherwise easy to get subtly wrong (city vs suburban)."""
    assert igr._resolve_district("Mumbai City") == "मुंबई जिल्हा"
    assert igr._resolve_district("Mumbai Suburban") == "मुंबई उपनगर जिल्हा"
    # An unrecognised hint passes through unchanged rather than raising --
    # a caller who already has the exact Marathi label must still be able
    # to use it directly.
    assert igr._resolve_district("पालघर") == "पालघर"
    print("test_district_hints_cover_the_two_mumbai_districts: PASS")


def test_unknown_registration_type_is_rejected_before_a_browser_ever_opens():
    """A typo in registration_type must fail fast with a clear note, not
    silently fall through to whatever the portal's own default radio is --
    that would search the wrong channel and report a false negative."""
    result = igr.search_by_document_number("Mumbai Suburban", "Andheri", 2024, 100,
                                            registration_type="Regulaar")
    assert result["found"] is False, result
    assert "Regulaar" in result["note"], result
    print("test_unknown_registration_type_is_rejected_before_a_browser_ever_opens: PASS")


# --- Property Details' three regions ----------------------------------------
#
# Discovered live 2026-09-01, chasing a real search for CTS 3223, Gultekdi/
# Market Yard, Pune: the landing page's default (Mumbai) tab is NOT one form
# with a district dropdown covering the whole state -- it's one of THREE
# separate regions, each its own field ids, and a locality can sit in either
# the rural (rest_of_maharashtra) or urban region depending on how it was
# annexed. Gulatekadi (Market Yard) turned out to be under "urban", not the
# rural taluka/village list a human would try first.

def test_an_unknown_region_is_rejected_before_a_browser_ever_opens():
    result = igr.search_by_property("suburbann", "Mumbai Suburban", "Andheri", "1")
    assert result["found"] is False, result
    assert "suburbann" in result["note"], result
    print("test_an_unknown_region_is_rejected_before_a_browser_ever_opens: PASS")


def test_rest_of_maharashtra_requires_a_taluka():
    """Its village select cascades off Taluka, not District directly -- a
    call missing it would otherwise reach a browser and then fail on a
    selector that was never going to be filled, burning a human's time on
    a CAPTCHA for a search that could not have worked."""
    result = igr.search_by_property("rest_of_maharashtra", "Pune", "Aundh", "1")
    assert result["found"] is False, result
    assert "taluka" in result["note"].lower(), result
    print("test_rest_of_maharashtra_requires_a_taluka: PASS")


def test_the_taluka_requirement_is_specific_to_rest_of_maharashtra():
    """mumbai and urban have no taluka step at all -- their village/area
    selects cascade straight off District. Checked against the field-map
    itself rather than calling search_by_property for the "should succeed"
    cases, since those launch a real browser and would hang/timeout here
    without a human to solve the CAPTCHA."""
    assert "taluka" not in igr._REGION_FIELDS["mumbai"]
    assert "taluka" not in igr._REGION_FIELDS["urban"]
    assert "taluka" in igr._REGION_FIELDS["rest_of_maharashtra"]
    print("test_the_taluka_requirement_is_specific_to_rest_of_maharashtra: PASS")


def test_every_region_has_a_distinct_search_control_name():
    """_wait_for_human_submit keys on this string appearing in the Search
    button's own POST data -- if two regions ever shared one, a human
    solving the CAPTCHA on one region's page could be mistaken for having
    submitted a different region's search."""
    names = [f["search_control_name"] for f in igr._REGION_FIELDS.values()]
    assert len(names) == len(set(names)), names
    print("test_every_region_has_a_distinct_search_control_name: PASS")


# --- the "Please Wait....." race --------------------------------------------
#
# Confirmed live 2026-09-01: a real human solved the CAPTCHA for region=
# urban/Pune/Gulatekadi/3223, and the raw_text this pipeline captured right
# after was the search form BACK AT BLANK with the portal's own
# "Please Wait....." marker still in it -- the site's own FAQ says results
# can take minutes. A fixed short wait after submit is not enough; this
# tests the polling fix against a stand-in `page` rather than a live one.

class _FakePage:
    """Returns each string in `texts` in turn on successive inner_text()
    calls, repeating the last one once exhausted -- enough to simulate a
    page whose "Please Wait....." clears after N polls without an actual
    browser or actual waiting."""
    def __init__(self, texts):
        self._texts = list(texts)
        self._i = 0

    def inner_text(self, _selector):
        text = self._texts[min(self._i, len(self._texts) - 1)]
        self._i += 1
        return text

    def wait_for_timeout(self, _ms):
        pass  # no real sleep -- this is what makes the test fast


def test_wait_for_search_to_finish_clears_once_please_wait_is_gone():
    page = _FakePage(["...Please Wait.....", "...Please Wait.....", "Real Result Table Here"])
    assert igr._wait_for_search_to_finish(page, timeout_seconds=30) is True
    print("test_wait_for_search_to_finish_clears_once_please_wait_is_gone: PASS")


def test_wait_for_search_to_finish_times_out_honestly_rather_than_lying():
    """If "Please Wait....." never clears within the budget, this must say
    so (False) rather than silently returning as if the result were
    ready -- a caller uses this to decide whether to trust what it reads
    next."""
    page = _FakePage(["...Please Wait....."])
    assert igr._wait_for_search_to_finish(page, timeout_seconds=0) is False
    print("test_wait_for_search_to_finish_times_out_honestly_rather_than_lying: PASS")


# --- the "no visible result" pattern -----------------------------------
#
# Confirmed live TWICE (2026-09-01): a deliberately-implausible test
# property number, and a real human CAPTCHA solve for CTS 3223 in
# Gulatekadi, Pune -- both came back identically, "Entered Correct
# Captcha" with no results table and the form reset to blank.

def test_entered_correct_captcha_with_no_table_reads_as_no_result():
    real_no_result_snippet = (
        "मिळकत क्रमांक/Property No. (Enter SurveyNo./CTSNo./MilkatNo./GatNo./PlotNo.) "
        "Entered Correct Captcha "
        "* Information provided on this site is updated"
    )
    assert igr._property_search_shows_no_result(real_no_result_snippet) is True
    print("test_entered_correct_captcha_with_no_table_reads_as_no_result: PASS")


def test_a_page_that_never_confirmed_the_captcha_is_not_read_as_a_result():
    """Absence of the confirmation string must not be mistaken for
    presence of one -- an incorrect-CAPTCHA page or a genuinely different
    error state should fall through to the honest "shape not confirmed"
    branch instead of being misread as a clean zero-result search."""
    assert igr._property_search_shows_no_result("Please try again.") is False
    assert igr._property_search_shows_no_result("") is False
    print("test_a_page_that_never_confirmed_the_captcha_is_not_read_as_a_result: PASS")


if __name__ == "__main__":
    test_the_real_captured_row_parses_with_party_names_and_consideration()
    test_a_table_without_the_expected_headers_is_ignored()
    test_a_header_only_table_with_no_data_rows_is_a_clean_empty_result()
    test_district_hints_cover_the_two_mumbai_districts()
    test_unknown_registration_type_is_rejected_before_a_browser_ever_opens()
    test_an_unknown_region_is_rejected_before_a_browser_ever_opens()
    test_rest_of_maharashtra_requires_a_taluka()
    test_the_taluka_requirement_is_specific_to_rest_of_maharashtra()
    test_every_region_has_a_distinct_search_control_name()
    test_wait_for_search_to_finish_clears_once_please_wait_is_gone()
    test_wait_for_search_to_finish_times_out_honestly_rather_than_lying()
    test_entered_correct_captcha_with_no_table_reads_as_no_result()
    test_a_page_that_never_confirmed_the_captcha_is_not_read_as_a_result()
    print("\nAll tests passed.")

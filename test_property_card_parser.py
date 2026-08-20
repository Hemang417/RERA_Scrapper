"""
Guards on reading the Maharashtra Property Card (malmatta patrak, Form D).

THE BUG THIS FILE EXISTS FOR: `fields` was {} on every CTS lookup this repo
has ever done, and the docstring said otherwise.

_scrape_result_page claimed it tried "the two structural patterns already
proven elsewhere in this codebase (ZaubaCorp's li.row pairs, and plain
label/value tables)". Only the first was ever written. The Property Card
contains no <li class="row"> at all -- it is three HTML <table> elements --
so the extraction returned an empty dict, every time, and the Charter fell
back to printing 4,000 characters of the search form's dropdown options
under a "Land Record Check" heading.

WHAT THE CARD ACTUALLY IS, and why this is a parser fix rather than an OCR
project. Looking at the real captured page: crisp HTML text in real tables,
with the "For View Only - Not For Legal Purpose" watermark as a light
overlay behind it. It is NOT a scanned image. OCR was only ever a
workaround for page.content() reading the search form instead of the card,
because the card renders inside an iframe -- and on a machine without the
Marathi language pack (this one: tesseract --list-langs shows only eng and
osd) OCR produces garbled Latin guesses at Devanagari and cannot be relied
on for any of the five fields.

MATCHED BY LABEL, NEVER BY POSITION. Column counts differ between cards, so
each field is found by its own Marathi label -- the wording the Land Records
rules print on Form D, which does not vary by district.

A BLANK FIELD ON A REAL CARD IS A FINDING, NOT A MISS. An empty इतर भार
(other encumbrance) row means no encumbrance is recorded against the plot,
which is precisely what a reader wants to know. That is why parse_property_
card returns {} when there is no card at all, and a dict with empty strings
when the card is there and the row is blank: the caller must be able to
tell those apart.

Everything here is offline: the parser takes HTML and returns a dict.

Run directly: python test_property_card_parser.py
"""

import mahabhumi as mb

# The real card's structure, rebuilt from the captured screenshot of
# CTS 183, village Ambivali, Mumbai Suburban: a header table of
# survey/area/tenure columns, a block of label-and-value rows, and the
# फेरफार (mutation) table.
_CARD = """
<table>
 <tr><th>नगर भूमापन क्रमांक</th><th>शिट नंबर</th><th>प्लॉट नंबर</th>
     <th>क्षेत्र चौ.मी.</th><th>धारणाधिकार</th></tr>
 <tr><td>183</td><td></td><td></td><td>25562.70</td><td>[- - 25562.70] शेती</td></tr>
</table>
<table>
 <tr><td>हक्काचा मूळ धारक :</td><td>रामचंद्र पाटील</td></tr>
 <tr><td>वर्ष:</td><td>1964</td></tr>
 <tr><td>पट्टेदार :</td><td></td></tr>
 <tr><td>इतर भार :</td><td></td></tr>
 <tr><td>इतर शेरे</td><td></td></tr>
</table>
<table>
 <tr><th>दिनांक</th><th>व्यवहार</th><th>फेरफार क्रमांक</th><th>साक्षांकन</th></tr>
 <tr><td>19/11/1979</td><td>आदेश DLN/LND/B/2702</td><td></td><td>सही- 05/12/1979</td></tr>
 <tr><td>16/12/2015</td><td>क्षेत्र दुरुस्ती</td><td>599</td><td>सही- 16/12/2015</td></tr>
 <tr><td>02/06/2025</td><td>शेती नोंद</td><td>922</td><td>सही- 02/06/2025</td></tr>
</table>
<p>PU-ID: 88331721167</p>
"""


def test_all_five_requested_fields_come_off_the_card():
    """Owner, area, tenure, encumbrance and mutation entries -- the five
    fields the land-record workflow was specified to produce, and the five
    that came back empty on every lookup until now."""
    card = mb.parse_property_card(_CARD)
    fields = card["fields"]
    assert fields["original_holder"] == "रामचंद्र पाटील", fields
    assert fields["area_sq_m"] == "25562.70", fields
    assert fields["tenure"] == "[- - 25562.70] शेती", fields
    assert "encumbrance" in fields, fields
    assert len(card["mutations"]) == 3, card["mutations"]
    print("test_all_five_requested_fields_come_off_the_card: PASS")


def test_a_blank_row_is_a_finding_not_a_miss():
    """An empty इतर भार row means NO encumbrance is recorded against this
    plot. That is the answer, not a failure to read it -- so the key is
    present with an empty value, and a page with no card at all returns {}
    so a caller can tell the two apart."""
    card = mb.parse_property_card(_CARD)
    assert card["fields"]["encumbrance"] == "", card["fields"]
    assert card["fields"]["lessee"] == "", card["fields"]
    assert mb.parse_property_card("<html><body>the search form</body></html>") == {}
    assert mb.parse_property_card("") == {}
    assert mb.parse_property_card(None) == {}
    print("test_a_blank_row_is_a_finding_not_a_miss: PASS")


def test_a_label_row_is_not_mistaken_for_a_header_row():
    """REGRESSION. A label-and-value row has its label in the first cell,
    exactly like a header row does, so the first version treated the whole
    label block as a header and read the NEXT row's label as the value:
    "हक्काचा मूळ धारक" came back as "वर्ष:". Only a row of real <th> cells
    is a header."""
    card = mb.parse_property_card(_CARD)
    assert card["fields"]["original_holder"] != "वर्ष:", card["fields"]
    assert card["fields"]["holder_year"] == "1964", card["fields"]
    print("test_a_label_row_is_not_mistaken_for_a_header_row: PASS")


def test_the_mutation_table_keeps_its_numbers_and_dates():
    """The फेरफार entries are the plot's transaction history: each has a
    date, what happened, and the numbered mutation entry it was recorded
    under."""
    mutations = mb.parse_property_card(_CARD)["mutations"]
    assert [m["date"] for m in mutations] == ["19/11/1979", "16/12/2015", "02/06/2025"], mutations
    numbers = [m["mutation_number"] for m in mutations]
    assert "599" in numbers and "922" in numbers, numbers
    assert all(m["transaction"] for m in mutations), mutations
    print("test_the_mutation_table_keeps_its_numbers_and_dates: PASS")


def test_fields_are_found_by_label_not_by_column_position():
    """Column counts differ between cards -- some carry a sheet number and
    a plot number, some do not -- so a positional parser reads the wrong
    cell on the next card it meets."""
    narrower = """
    <table>
     <tr><th>नगर भूमापन क्रमांक</th><th>क्षेत्र चौ.मी.</th><th>धारणाधिकार</th></tr>
     <tr><td>77</td><td>1000.50</td><td>भोगवटादार वर्ग 1</td></tr>
    </table>
    """
    fields = mb.parse_property_card(narrower)["fields"]
    assert fields["city_survey_number"] == "77", fields
    assert fields["area_sq_m"] == "1000.50", fields
    assert fields["tenure"] == "भोगवटादार वर्ग 1", fields
    print("test_fields_are_found_by_label_not_by_column_position: PASS")


def test_the_pu_id_is_captured_because_it_identifies_the_card():
    """The PU-ID is the card's own identifier and the one machine-readable
    datum the old OCR path ever recovered. It is also what the completion
    detector looks for, so losing it would break more than this parser."""
    assert mb.parse_property_card(_CARD)["fields"]["pu_id"] == "88331721167"
    assert mb.parse_property_card("<p>PU ID 12345678901</p>")["fields"]["pu_id"] == "12345678901"
    print("test_the_pu_id_is_captured_because_it_identifies_the_card: PASS")


def test_the_scrape_no_longer_claims_a_pattern_it_does_not_implement():
    """The docstring promised label/value table support that was never
    written, which is why nobody noticed `fields` was always {}."""
    import inspect

    source = inspect.getsource(mb._scrape_result_page)
    assert "parse_property_card" in source, \
        "the scrape does not call the card parser, so fields will be {} again"
    print("test_the_scrape_no_longer_claims_a_pattern_it_does_not_implement: PASS")


if __name__ == "__main__":
    test_all_five_requested_fields_come_off_the_card()
    test_a_blank_row_is_a_finding_not_a_miss()
    test_a_label_row_is_not_mistaken_for_a_header_row()
    test_the_mutation_table_keeps_its_numbers_and_dates()
    test_fields_are_found_by_label_not_by_column_position()
    test_the_pu_id_is_captured_because_it_identifies_the_card()
    test_the_scrape_no_longer_claims_a_pattern_it_does_not_implement()
    print("\nAll tests passed.")

"""
Guards on joining WBRERA's orders to a promoter.

WBRERA publishes 4,881 authority orders and names NO party in any column --
only a complaint number. The promoter is inside each order PDF, and at
~900 KB each those are not sweepable. The join runs through the cause
lists, which do name the parties against each complaint number.

TWO THINGS MAKE THAT DANGEROUS, and both are guarded here:

1. The cause-list PDFs carry a poor OCR text layer that mangles exactly the
   field the join needs -- "WBRERA/COMOO2117" for 002117, "WBRERAJCOMOOOTS4"
   for 000754. Freely correcting those characters would be inventing a
   complaint number. What makes it safe is resolving against the CLOSED SET
   the order register itself publishes, and refusing anything that does not
   land on exactly one member of it.

2. The OCR does not preserve columns. Names wrap mid-title and hearing
   labels interleave, so "the last name in the run" read PAPPU SINGH as the
   respondent of a complaint answered by SK BUILDERS AND DEVELOPERS PVT.
   LIMITED. No name is labelled any more; the promoter is matched by
   proximity within the complaint's block, and the result is a candidate.

Everything here is offline: parsed text in, dicts out.

Run directly: python test_wb_orders.py
"""

import wb_orders as wb

# Verbatim from the live register, 2026-08-21.
_ORDER_HTML = """
<table>
 <tr><th>Sl No.</th><th>Description</th><th>Dated</th><th>View/Download Notice</th></tr>
 <tr><td>1</td><td>Order No. 02 dated 27.04.2026 for Complaint No. WBRERA/COM 001338</td>
     <td>10/08/2026</td><td><a href="http://doc.example/1.pdf">View</a></td></tr>
 <tr><td>2</td><td>Order No. 04 dated 12.08.2025 for Complaint No. WBRERA/COM 000754</td>
     <td>12/08/2025</td><td><a href="http://doc.example/2.pdf">View</a></td></tr>
 <tr><td>3</td><td>Notice regarding office timings</td><td>01/01/2026</td><td></td></tr>
</table>
"""

# The OCR text layer of a real cause list, reproduced with its own damage.
_CAUSE_TEXT = """WEST BENGAL REAL ESTATE REGULATORY AUTHORITY
Cause List of Hearing Dated: 31-07-2026
Time
Complaint No.
Complainant Name
Respondent Name
't2.00 P.M
WBRERA/COMOO2117
RAYMOND ALMEDIA
SHIVMAHIMA DEVELOPERS
PRIVATE LIMITED
12.30 P.M
WBRERAJCOMOOOTS4
SWAPNA THAKUR
TIRU FINE RESIDENCY LLP
3 P.M
WBRERA/COMOO1O92
DEBJANI MUKHARJEE
SK BUILDERS AND
DEVELOPERS PVT. LIMITED
PAPPU SINGH
HEARING 4
"""


def test_an_ocr_mangled_complaint_number_resolves_against_the_closed_set():
    """THE GUARD THAT MAKES THIS SAFE AT ALL. The OCR writes letters for
    digits, so the number cannot be trusted on its own. It is resolved
    against the set of complaint numbers the order register itself
    publishes -- picking an existing number rather than inventing a
    correction."""
    known = {"001338", "000754", "001092"}
    assert wb.resolve_complaint_no("OOOTS4", known) == "000754"
    assert wb.resolve_complaint_no("OO1O92", known) == "001092"
    assert wb.resolve_complaint_no("000754", known) == "000754"
    # No member of the set -> refused, not guessed at.
    assert wb.resolve_complaint_no("OO2117", known) == ""
    assert wb.resolve_complaint_no("", known) == ""
    assert wb.resolve_complaint_no("XXXX", known) == ""
    print("test_an_ocr_mangled_complaint_number_resolves_against_the_closed_set: PASS")


def test_an_ambiguous_number_is_refused_rather_than_picked():
    """Attaching an order to the wrong promoter is far worse than reporting
    one fewer, so two candidates means none."""
    assert wb.resolve_complaint_no("1O", {"10", "1O"}) == ""
    assert wb.resolve_complaint_no("1O", {"10"}) == "10"
    print("test_an_ambiguous_number_is_refused_rather_than_picked: PASS")


def test_a_row_naming_no_complaint_is_kept_not_dropped():
    """The register carries notices alongside orders. Dropping the rows
    that name no complaint would understate how much of it cannot be joined
    at all, which is the number a reader needs."""
    orders = wb.parse_order_register(_ORDER_HTML)
    assert len(orders) == 3, orders
    assert sum(1 for o in orders if o["complaint_no"]) == 2, orders
    assert orders[0]["complaint_no"] == "001338", orders[0]
    assert orders[0]["url"].endswith("1.pdf"), orders[0]
    print("test_a_row_naming_no_complaint_is_kept_not_dropped: PASS")


def test_no_name_is_labelled_respondent_because_ocr_loses_the_columns():
    """REGRESSION, and it was wrong on live data. Taking "the last name in
    the run" read PAPPU SINGH as the respondent of complaint 001092, which
    SK BUILDERS AND DEVELOPERS PVT. LIMITED answered -- and read "PRIVATE
    LIMITED" alone as a respondent, because the title wrapped. The block is
    kept whole instead."""
    entries = wb.parse_cause_list_text(_CAUSE_TEXT)
    assert len(entries) == 3, entries
    assert entries[0]["complaint_no_raw"] == "OO2117", entries[0]
    # The wrapped company name survives intact inside the block.
    assert "SHIVMAHIMA DEVELOPERS PRIVATE LIMITED" in entries[0]["block"], entries[0]
    assert "SK BUILDERS AND DEVELOPERS PVT. LIMITED" in entries[2]["block"], entries[2]
    assert "respondent" not in entries[0], "a name was labelled again"
    print("test_no_name_is_labelled_respondent_because_ocr_loses_the_columns: PASS")


def test_a_promoter_joins_to_its_orders_and_a_stranger_does_not():
    """The end-to-end join, on the real shapes."""
    orders = wb.parse_order_register(_ORDER_HTML)
    index = wb.build_complaint_index([_CAUSE_TEXT])
    hits = wb.orders_for_promoter("TIRU FINE RESIDENCY", orders, index)
    assert len(hits) == 1 and hits[0]["complaint_no"] == "000754", hits
    assert hits[0]["matched_promoter"] == "TIRU FINE RESIDENCY", hits[0]
    assert wb.orders_for_promoter("ZzzNoSuchFirm", orders, index) == []
    assert wb.orders_for_promoter("", orders, index) == []
    print("test_a_promoter_joins_to_its_orders_and_a_stranger_does_not: PASS")


def test_a_complaint_with_no_order_yet_yields_nothing_not_an_error():
    """SHIVMAHIMA's complaint is listed for hearing but has no order in the
    register. Zero is the right answer, and it must not be confused with a
    failed join."""
    orders = wb.parse_order_register(_ORDER_HTML)
    index = wb.build_complaint_index([_CAUSE_TEXT])
    assert "SHIVMAHIMA DEVELOPERS" in " ".join(index.values())
    assert wb.orders_for_promoter("SHIVMAHIMA DEVELOPERS", orders, index) == []
    print("test_a_complaint_with_no_order_yet_yields_nothing_not_an_error: PASS")


def test_blocks_for_one_complaint_accumulate_across_cause_lists():
    """A case is listed on every hearing date, and a party name mangled by
    one PDF's OCR may be legible in another's -- so blocks are joined, not
    replaced. Replacing them lost promoters whose name only came out
    cleanly on one date."""
    second = "WBRERA/COMOOOTS4\nSWAPNA THAKUR\nTIRU FINE RESIDENCY LLP AND ORS\n"
    index = wb.build_complaint_index([_CAUSE_TEXT, second])
    assert index["OOOTS4"].count("TIRU FINE RESIDENCY") == 2, index["OOOTS4"]
    print("test_blocks_for_one_complaint_accumulate_across_cause_lists: PASS")


def test_the_coverage_note_says_how_little_was_read():
    """A promoter's orders are reachable only through the cause lists
    actually read, so an unread cause list is a silently missing order --
    and a short answer here looks exactly like a promoter with a clean
    record."""
    orders = wb.parse_order_register(_ORDER_HTML)
    index = wb.build_complaint_index([_CAUSE_TEXT])
    note = wb.coverage_note(orders, cause_lists_read=1, cause_lists_total=565,
                            complaint_index=index)
    assert "1 of 565" in note, note
    assert "no party is named in the register" in note, note
    assert "NOT reported as absent" in note, note
    print("test_the_coverage_note_says_how_little_was_read: PASS")


if __name__ == "__main__":
    test_an_ocr_mangled_complaint_number_resolves_against_the_closed_set()
    test_an_ambiguous_number_is_refused_rather_than_picked()
    test_a_row_naming_no_complaint_is_kept_not_dropped()
    test_no_name_is_labelled_respondent_because_ocr_loses_the_columns()
    test_a_promoter_joins_to_its_orders_and_a_stranger_does_not()
    test_a_complaint_with_no_order_yet_yields_nothing_not_an_error()
    test_blocks_for_one_complaint_accumulate_across_cause_lists()
    test_the_coverage_note_says_how_little_was_read()
    print("\nAll tests passed.")

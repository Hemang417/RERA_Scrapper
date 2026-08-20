"""
Guards on re-checking whether secured borrowing has been repaid.

THE RULE THIS FILE EXISTS TO ENFORCE: A CHECK THAT DID NOT RUN IS NOT
"NOTHING CHANGED".

This module answers "is the Rs 90.35 crore still outstanding?", and it will
be re-run periodically against a live third-party mirror. The dangerous
output is not a wrong number, it is a reassuring silence: a fetch that
failed, a page that changed shape, or a charge that dropped off the table,
all rendering as "no change since the last check". A lender reading that
concludes the position is stable.

So every path that could not actually compare says so, and three specific
traps are pinned:

  * A FAILED FETCH on either side yields checked=False, never an empty
    change list. Same shape as the false clean records this codebase has
    already shipped once each: K-RERA's complaint page, the sweep's search
    budget, declared absences reported as failed fetches.

  * A CHARGE THAT VANISHED is not a repaid charge. A satisfied charge stays
    on the register carrying a closure date; disappearing means the scrape
    or the page changed, and it is flagged for a human.

  * A MODIFIED CHARGE is not a released one. Form CHG-1 modification changes
    the terms; the security stays.

Everything runs offline: the profile lookup is injected, matching the
pattern group_entities.build_entity_graph(proposer=None) already uses.

Run directly: python test_charge_watch.py
"""

import charge_watch as cw

_HDFC = {"charge_id": "100857390", "creation_date": "2023-10-31", "closure_date": None,
         "modification_date": None, "is_open": True, "amount": "3,491,110.00",
         "charge_holder": "HDFC BANK LIMITED"}
_CATALYST = {"charge_id": "100878097", "creation_date": "2024-01-29", "closure_date": None,
             "modification_date": None, "is_open": True, "amount": "300,000,000.00",
             "charge_holder": "CATALYST TRUSTEESHIP LIMITED"}


def _snapshot(charges, ok=True):
    return {"cin": "U70109MH2022PLC385473", "ok": ok, "fetched_at": "2026-08-19T00:00:00",
            "charges": charges}


def _lookup(charges, found=True):
    def _fn(cin, name=""):
        return {"found": found, "name": "Test Co", "url": "https://example.test",
                "charges": charges}
    return _fn


# --- the central rule -----------------------------------------------------

def test_a_failed_fetch_is_never_reported_as_no_change():
    """The failure this module exists to avoid. A lender re-running this
    weekly must never read a broken fetch as a stable position."""
    good = _snapshot([_HDFC])
    for before, after in ((good, _snapshot([], ok=False)),
                          (_snapshot([], ok=False), good),
                          (_snapshot([], ok=False), _snapshot([], ok=False))):
        result = cw.compare(before, after)
        assert result["checked"] is False, result
        assert result["changes"] == [], result
        assert "not evidence that the position is unchanged" in result["note"], result
    print("test_a_failed_fetch_is_never_reported_as_no_change: PASS")


def test_a_charge_that_vanished_is_not_a_repaid_charge():
    """A satisfied charge STAYS on the register with a closure date.
    Disappearing means the page changed or the scrape missed it -- reading
    that as repayment would wipe Rs 30 crore of security off the record on
    the strength of a parsing failure."""
    result = cw.compare(_snapshot([_HDFC, _CATALYST]), _snapshot([_HDFC]))
    gone = [c for c in result["changes"] if c["type"] == "disappeared"]
    assert len(gone) == 1, result["changes"]
    assert gone[0]["charge_id"] == "100878097", gone[0]
    assert "not evidence of repayment" in gone[0]["text"], gone[0]
    assert not any(c["type"] == "satisfied" for c in result["changes"]), result["changes"]
    print("test_a_charge_that_vanished_is_not_a_repaid_charge: PASS")


def test_a_real_satisfaction_is_reported_with_its_closure_date():
    satisfied = dict(_HDFC, is_open=False, closure_date="2026-09-30")
    result = cw.compare(_snapshot([_HDFC]), _snapshot([satisfied]))
    hits = [c for c in result["changes"] if c["type"] == "satisfied"]
    assert len(hits) == 1, result["changes"]
    assert "2026-09-30" in hits[0]["text"], hits[0]
    assert "security has been released" in hits[0]["text"], hits[0]
    assert result["still_open"] == [], result["still_open"]
    print("test_a_real_satisfaction_is_reported_with_its_closure_date: PASS")


def test_a_modification_is_not_a_release():
    """Form CHG-1 modification changes the terms; the security stays. Read
    as a release it would report borrowing as repaid that never was."""
    modified = dict(_CATALYST, modification_date="2026-07-01")
    result = cw.compare(_snapshot([_CATALYST]), _snapshot([modified]))
    hits = [c for c in result["changes"] if c["type"] == "modified"]
    assert len(hits) == 1, result["changes"]
    assert "has not been released" in hits[0]["text"], hits[0]
    assert result["still_open"], "a modified charge is still open"
    print("test_a_modification_is_not_a_release: PASS")


def test_new_borrowing_is_surfaced():
    result = cw.compare(_snapshot([_HDFC]), _snapshot([_HDFC, _CATALYST]))
    hits = [c for c in result["changes"] if c["type"] == "new_charge"]
    assert len(hits) == 1, result["changes"]
    assert "CATALYST TRUSTEESHIP LIMITED" in hits[0]["text"], hits[0]
    assert "further secured borrowing" in hits[0]["text"], hits[0]
    print("test_new_borrowing_is_surfaced: PASS")


def test_a_charge_going_backwards_is_flagged_rather_than_absorbed():
    """Satisfied then open again should not happen. Silently taking the
    newer reading would hide that one of the two is wrong."""
    satisfied = dict(_HDFC, is_open=False, closure_date="2026-01-01")
    result = cw.compare(_snapshot([satisfied]), _snapshot([_HDFC]))
    hits = [c for c in result["changes"] if c["type"] == "reopened"]
    assert len(hits) == 1, result["changes"]
    assert "Verify directly" in hits[0]["text"], hits[0]
    print("test_a_charge_going_backwards_is_flagged_rather_than_absorbed: PASS")


def test_no_movement_reads_as_no_movement():
    result = cw.compare(_snapshot([_HDFC, _CATALYST]), _snapshot([_HDFC, _CATALYST]))
    assert result["checked"] is True
    assert result["changes"] == [], result["changes"]
    assert len(result["still_open"]) == 2, result["still_open"]
    print("test_no_movement_reads_as_no_movement: PASS")


# --- snapshots ------------------------------------------------------------

def test_a_missing_charge_table_is_not_a_clean_register():
    """charges=None means the source carried no charge section this pass.
    An empty list means the register was read and is clean. Only the second
    is a finding, and conflating them would report a company with Rs 90
    crore of borrowing as debt-free."""
    missing = cw.snapshot("U1", profile_lookup=_lookup(None))
    assert missing["ok"] is False, missing
    assert "no charge register" in missing["note"], missing

    clean = cw.snapshot("U1", profile_lookup=_lookup([]))
    assert clean["ok"] is True, clean
    assert clean["summary"]["total_charges"] == 0, clean
    print("test_a_missing_charge_table_is_not_a_clean_register: PASS")


def test_a_company_that_could_not_be_found_says_so():
    result = cw.snapshot("U1", profile_lookup=_lookup(None, found=False))
    assert result["ok"] is False and result["charges"] == [], result
    print("test_a_company_that_could_not_be_found_says_so: PASS")


def test_the_snapshot_carries_the_rollup_the_reader_needs():
    result = cw.snapshot("U1", profile_lookup=_lookup([_HDFC, _CATALYST]))
    assert result["summary"]["open_charges"] == 2, result["summary"]
    assert result["summary"]["total_open_amount"] == 303491110.0, result["summary"]
    assert result["summary"]["open_lenders"] == [
        "HDFC BANK LIMITED", "CATALYST TRUSTEESHIP LIMITED"], result["summary"]
    print("test_the_snapshot_carries_the_rollup_the_reader_needs: PASS")


def test_the_note_never_overstates_what_a_mirror_proves():
    """A closure date appearing is good evidence. Its absence is weak: the
    mirror lags MCA, which lags the company's own filing. The wording must
    not let a reader take silence for proof the money is still owed."""
    note = cw.compare(_snapshot([_HDFC]), _snapshot([_HDFC]))["note"]
    assert "lags" in note, note
    assert "not that the borrowing is definitely still outstanding" in note, note
    print("test_the_note_never_overstates_what_a_mirror_proves: PASS")


if __name__ == "__main__":
    test_a_failed_fetch_is_never_reported_as_no_change()
    test_a_charge_that_vanished_is_not_a_repaid_charge()
    test_a_real_satisfaction_is_reported_with_its_closure_date()
    test_a_modification_is_not_a_release()
    test_new_borrowing_is_surfaced()
    test_a_charge_going_backwards_is_flagged_rather_than_absorbed()
    test_no_movement_reads_as_no_movement()
    test_a_missing_charge_table_is_not_a_clean_register()
    test_a_company_that_could_not_be_found_says_so()
    test_the_snapshot_carries_the_rollup_the_reader_needs()
    test_the_note_never_overstates_what_a_mirror_proves()
    print("\nAll tests passed.")

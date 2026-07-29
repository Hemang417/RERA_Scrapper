"""
Tests for gst_compliance.py's pure analysis logic (state-code QRMP
grouping, per-period monthly/quarterly detection, statutory due dates,
delay computation, and filing-pattern aggregation) -- no live portal
access or CAPTCHA-solving involved; see that module's own docstring for
why the scraping step is deliberately not part of it yet.

Run directly: python test_gst_compliance.py
"""

import datetime

import gst_compliance as gst

_MAHARASHTRA_GSTIN = "27AANCM5273D1ZA"  # state code 27 -- the worked example from this session
_DELHI_GSTIN = "07AANCM5273D1ZB"  # state code 07 -- a Category Y state, for contrast


def test_qrmp_category_by_state_code():
    """Maharashtra (27) is Category X (22nd); Delhi (07) is Category Y
    (24th) -- confirms the lookup isn't accidentally inverted or
    defaulting everything to one group."""
    assert gst.qrmp_gstr3b_due_day(_MAHARASHTRA_GSTIN) == 22
    assert gst.qrmp_gstr3b_due_day(_DELHI_GSTIN) == 24
    print("test_qrmp_category_by_state_code: PASS")


def test_detect_period_frequency():
    """A ~30-day span is monthly, a ~91-day span is quarterly, anything
    else (e.g. a malformed or amended-period date range) is honestly
    "unknown" rather than guessed."""
    assert gst.detect_period_frequency(datetime.date(2025, 4, 1), datetime.date(2025, 4, 30)) == "monthly"
    assert gst.detect_period_frequency(datetime.date(2025, 2, 1), datetime.date(2025, 2, 28)) == "monthly"  # short month, still ~28 days
    assert gst.detect_period_frequency(datetime.date(2025, 4, 1), datetime.date(2025, 6, 30)) == "quarterly"
    assert gst.detect_period_frequency(datetime.date(2025, 1, 1), datetime.date(2025, 3, 31)) == "quarterly"
    assert gst.detect_period_frequency(datetime.date(2025, 4, 1), datetime.date(2025, 4, 15)) == "unknown"  # a half-month span -- not a real period
    print("test_detect_period_frequency: PASS")


def test_due_date_monthly():
    """Monthly GSTR-1 due by the 11th, GSTR-3B by the 20th, of the month
    immediately after the period -- regardless of which state the GSTIN
    is registered in (the state-dependent QRMP split only applies to
    quarterly GSTR-3B, never to monthly filings)."""
    period_start, period_end = datetime.date(2025, 4, 1), datetime.date(2025, 4, 30)
    assert gst.due_date(_MAHARASHTRA_GSTIN, "GSTR-1", period_start, period_end) == datetime.date(2025, 5, 11)
    assert gst.due_date(_MAHARASHTRA_GSTIN, "GSTR-3B", period_start, period_end) == datetime.date(2025, 5, 20)
    # December -> January year rollover must not break the "next month" calculation.
    dec_start, dec_end = datetime.date(2025, 12, 1), datetime.date(2025, 12, 31)
    assert gst.due_date(_MAHARASHTRA_GSTIN, "GSTR-3B", dec_start, dec_end) == datetime.date(2026, 1, 20)
    print("test_due_date_monthly: PASS")


def test_due_date_qrmp_quarterly():
    """QRMP GSTR-1 due by the 13th of the month after the quarter for
    everyone; GSTR-3B due date depends on the GSTIN's own state code (22nd
    for Maharashtra/Category X, 24th for Delhi/Category Y) -- the exact
    scenario this session's example GSTIN (27AANCM5273D1ZA) was chosen to
    exercise."""
    q_start, q_end = datetime.date(2025, 4, 1), datetime.date(2025, 6, 30)
    assert gst.due_date(_MAHARASHTRA_GSTIN, "GSTR-1", q_start, q_end) == datetime.date(2025, 7, 13)
    assert gst.due_date(_MAHARASHTRA_GSTIN, "GSTR-3B", q_start, q_end) == datetime.date(2025, 7, 22)
    assert gst.due_date(_DELHI_GSTIN, "GSTR-3B", q_start, q_end) == datetime.date(2025, 7, 24)
    print("test_due_date_qrmp_quarterly: PASS")


def test_due_date_unresolvable_frequency_returns_none():
    """A period whose span doesn't cleanly resolve to monthly or quarterly
    must return None -- never a guessed due date scored as if it were
    real."""
    assert gst.due_date(_MAHARASHTRA_GSTIN, "GSTR-3B", datetime.date(2025, 4, 1), datetime.date(2025, 4, 10)) is None
    print("test_due_date_unresolvable_frequency_returns_none: PASS")


def test_due_date_rejects_unknown_form():
    try:
        gst.due_date(_MAHARASHTRA_GSTIN, "GSTR-9", datetime.date(2025, 4, 1), datetime.date(2025, 4, 30))
        assert False, "expected a ValueError for an unsupported form"
    except ValueError:
        pass
    print("test_due_date_rejects_unknown_form: PASS")


def test_compute_delay_days():
    due = datetime.date(2025, 5, 20)
    assert gst.compute_delay_days(datetime.date(2025, 5, 20), due) == 0  # filed exactly on the due date -- on time
    assert gst.compute_delay_days(datetime.date(2025, 5, 25), due) == 5  # 5 days late
    assert gst.compute_delay_days(datetime.date(2025, 5, 15), due) == -5  # filed early
    assert gst.compute_delay_days(None, due) is None
    assert gst.compute_delay_days(datetime.date(2025, 5, 20), None) is None
    print("test_compute_delay_days: PASS")


def test_summarize_filing_pattern_clean_record():
    """A GSTIN with every monthly GSTR-3B filed on or before its due date
    -- 0% late, no worst delay, nothing in the last-12-months bucket."""
    as_of = datetime.date(2026, 2, 1)
    records = []
    for m in range(1, 13):
        days_in_month = 28 if m == 2 else (30 if m in (4, 6, 9, 11) else 31)  # 2025 is not a leap year
        filing_year, filing_month = (2025, m + 1) if m < 12 else (2026, 1)
        records.append({
            "form": "GSTR-3B",
            "period_start": datetime.date(2025, m, 1),
            "period_end": datetime.date(2025, m, days_in_month),
            "filing_date": datetime.date(filing_year, filing_month, 15),
        })
    summary = gst.summarize_filing_pattern(_MAHARASHTRA_GSTIN, records, as_of=as_of)
    assert summary["total_periods"] == 12
    assert summary["filed"] == 12
    assert summary["late"] == 0
    assert summary["late_pct"] == 0.0
    assert summary["worst_delay_days"] is None
    assert summary["delays_last_12_months"] == 0
    print("test_summarize_filing_pattern_clean_record: PASS")


def test_summarize_filing_pattern_with_late_and_missing():
    """A mix of on-time, late, and never-filed periods -- confirms the
    aggregate counts (late_pct, worst_delay_days, delays_last_12_months)
    land on the right numbers, and that a period whose due date hasn't
    arrived yet is excluded entirely rather than counted as missing."""
    as_of = datetime.date(2026, 1, 1)
    records = [
        # On time: Jan 2025 GSTR-3B, due 2025-02-20, filed 2025-02-10.
        {"form": "GSTR-3B", "period_start": datetime.date(2025, 1, 1), "period_end": datetime.date(2025, 1, 31),
         "filing_date": datetime.date(2025, 2, 10)},
        # Late by 10 days: Jun 2025 GSTR-3B, due 2025-07-20, filed 2025-07-30.
        {"form": "GSTR-3B", "period_start": datetime.date(2025, 6, 1), "period_end": datetime.date(2025, 6, 30),
         "filing_date": datetime.date(2025, 7, 30)},
        # Late by 25 days (the worst): Sep 2025 GSTR-3B, due 2025-10-20, filed 2025-11-14.
        {"form": "GSTR-3B", "period_start": datetime.date(2025, 9, 1), "period_end": datetime.date(2025, 9, 30),
         "filing_date": datetime.date(2025, 11, 14)},
        # Never filed, due date (2025-12-20) already passed as of as_of.
        {"form": "GSTR-3B", "period_start": datetime.date(2025, 11, 1), "period_end": datetime.date(2025, 11, 30),
         "filing_date": None},
        # Not due yet as of as_of (2026-01-01): Dec 2025 GSTR-3B due 2026-01-20 -- must be excluded entirely.
        {"form": "GSTR-3B", "period_start": datetime.date(2025, 12, 1), "period_end": datetime.date(2025, 12, 31),
         "filing_date": None},
    ]
    summary = gst.summarize_filing_pattern(_MAHARASHTRA_GSTIN, records, as_of=as_of)
    assert summary["total_periods"] == 5
    assert summary["filed"] == 3
    assert summary["on_time"] == 1
    assert summary["late"] == 2
    assert summary["not_filed_yet"] == 1  # only the Nov period -- Dec isn't due yet, excluded
    assert summary["late_pct"] == 66.7, summary["late_pct"]  # 2 late of 3 rated (on_time + late)
    assert summary["worst_delay_days"] == 25, summary["worst_delay_days"]
    assert summary["delays_last_12_months"] == 3, summary["delays_last_12_months"]  # both late + the 1 not-filed-yet, all within the trailing 12mo
    print("test_summarize_filing_pattern_with_late_and_missing: PASS")


def test_summarize_filing_pattern_excludes_unresolvable_frequency():
    """A period with an unresolvable frequency (see test_due_date_
    unresolvable_frequency_returns_none) must be counted separately and
    excluded from every other statistic, not silently dropped or
    miscounted as filed/late."""
    as_of = datetime.date(2026, 1, 1)
    records = [
        {"form": "GSTR-3B", "period_start": datetime.date(2025, 4, 1), "period_end": datetime.date(2025, 4, 10),
         "filing_date": datetime.date(2025, 5, 1)},  # 10-day span -- unresolvable
    ]
    summary = gst.summarize_filing_pattern(_MAHARASHTRA_GSTIN, records, as_of=as_of)
    assert summary["total_periods"] == 1
    assert summary["unresolvable_frequency"] == 1
    assert summary["filed"] == 0
    assert summary["late_pct"] == 0.0
    print("test_summarize_filing_pattern_excludes_unresolvable_frequency: PASS")


if __name__ == "__main__":
    test_qrmp_category_by_state_code()
    test_detect_period_frequency()
    test_due_date_monthly()
    test_due_date_qrmp_quarterly()
    test_due_date_unresolvable_frequency_returns_none()
    test_due_date_rejects_unknown_form()
    test_compute_delay_days()
    test_summarize_filing_pattern_clean_record()
    test_summarize_filing_pattern_with_late_and_missing()
    test_summarize_filing_pattern_excludes_unresolvable_frequency()
    print("\nAll tests passed.")

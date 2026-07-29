"""
GST return-filing compliance analysis -- pure, offline logic only (no
scraping, no network calls, no CAPTCHA-solving in this file).

The live GST portal lookup (Search Taxpayer by PAN -> enumerate every
GSTIN registered under it -> Search Taxpayer by GSTIN -> SHOW FILING
TABLE) is a SEPARATE, not-yet-built step that requires a human to solve
the portal's own CAPTCHA each time -- that can't be automated (a hard
constraint, not a preference), so it isn't attempted here. This module
only covers what's fully testable without it: given already-extracted
filing records (however they eventually get scraped), determine each
period's filing frequency from its own date span, compute the correct
statutory due date, measure delay, and aggregate into a pattern a human
can act on.

Everything here operates on plain dicts/tuples, not facts.json directly
-- company_charter.py's _score_gst_compliance (once wired in) is
responsible for reading facts["gst_compliance"] and handing this module
clean period records.
"""

from __future__ import annotations

import datetime


# ---------------------------------------------------------------------------
# GST state codes (the first 2 digits of any GSTIN) -> which QRMP GSTR-3B
# due-date group that state falls into. Per CBIC notification, QRMP
# taxpayers' GSTR-3B due date is the 22nd of the month after the quarter for
# "Category X" states/UTs, 24th for everyone else ("Category Y"). Monthly
# filers and GSTR-1 due dates don't depend on this grouping at all -- only
# QRMP GSTR-3B does (see _due_date).
# ---------------------------------------------------------------------------
_QRMP_CATEGORY_X_STATE_CODES = {
    "22",  # Chhattisgarh
    "23",  # Madhya Pradesh
    "24",  # Gujarat
    "25",  # Daman and Diu
    "26",  # Dadra and Nagar Haveli
    "27",  # Maharashtra
    "29",  # Karnataka
    "30",  # Goa
    "31",  # Lakshadweep
    "32",  # Kerala
    "33",  # Tamil Nadu
    "34",  # Puducherry
    "35",  # Andaman and Nicobar Islands
    "36",  # Telangana
    "37",  # Andhra Pradesh
}
# Every other valid 2-digit GST state code (01-21, 28, 38 etc.) is Category Y
# -- not enumerated separately since "not in Category X" is the correct test.


def qrmp_gstr3b_due_day(gstin: str) -> int:
    """Returns 22 or 24 -- the day-of-month a QRMP taxpayer's GSTR-3B is due
    (month after the quarter ends), based on the state code embedded in the
    GSTIN's own first 2 characters. Monthly filers and GSTR-1 due dates
    never call this -- see _due_date."""
    state_code = gstin[:2]
    return 22 if state_code in _QRMP_CATEGORY_X_STATE_CODES else 24


# ---------------------------------------------------------------------------
# Per-period filing-frequency detection. Deliberately per-PERIOD, not a
# single frequency assumed for the whole GSTIN's history -- a taxpayer can
# switch in or out of QRMP at the start of any quarter (turnover crossing
# the Rs 5 crore threshold, or an explicit opt-in/out), and treating that as
# an error rather than a real, legitimate scheme change would misclassify
# real filings. The period's own reported span is the ground truth: ~1
# month means monthly, ~3 months means quarterly (QRMP) -- this is a
# concrete instruction as to why this is per-period, not a single
# global inference from gaps between periods.
# ---------------------------------------------------------------------------

def detect_period_frequency(period_start: datetime.date, period_end: datetime.date) -> str:
    """Returns "monthly", "quarterly", or "unknown" from a single filing
    period's own start/end dates -- a ~28-31 day span is a monthly return,
    a ~89-92 day span is a quarterly (QRMP) return. "unknown" is returned
    rather than guessed for any span outside both windows (e.g. a
    corrected/amended period with an unusual date range) -- that period is
    then excluded from due-date scoring rather than scored against the
    wrong rule."""
    span_days = (period_end - period_start).days + 1  # inclusive of both ends
    if 28 <= span_days <= 31:
        return "monthly"
    if 89 <= span_days <= 92:
        return "quarterly"
    return "unknown"


def due_date(gstin: str, form: str, period_start: datetime.date, period_end: datetime.date) -> datetime.date | None:
    """Returns the statutory due date for one GSTR-1 or GSTR-3B filing
    period, or None if the period's own span doesn't cleanly resolve to
    "monthly" or "quarterly" (see detect_period_frequency) -- never a
    guessed due date for an ambiguous period.

    form must be "GSTR-1" or "GSTR-3B". Monthly due dates: GSTR-1 by the
    11th of the following month, GSTR-3B by the 20th. QRMP (quarterly) due
    dates: GSTR-1 by the 13th of the month after the quarter, GSTR-3B by
    the 22nd or 24th (state-dependent -- see qrmp_gstr3b_due_day)."""
    if form not in ("GSTR-1", "GSTR-3B"):
        raise ValueError(f"form must be 'GSTR-1' or 'GSTR-3B', got {form!r}")

    frequency = detect_period_frequency(period_start, period_end)
    if frequency == "unknown":
        return None

    # First day of the month immediately after period_end, regardless of
    # whether period_end itself is the last day of its month (defensive
    # against an off-by-a-day-or-two scrape of the portal's own dates).
    if period_end.month == 12:
        next_month_start = datetime.date(period_end.year + 1, 1, 1)
    else:
        next_month_start = datetime.date(period_end.year, period_end.month + 1, 1)

    if frequency == "monthly":
        due_day = 11 if form == "GSTR-1" else 20
    else:  # quarterly
        due_day = 13 if form == "GSTR-1" else qrmp_gstr3b_due_day(gstin)

    return next_month_start.replace(day=due_day)


def compute_delay_days(filing_date: datetime.date | None, due: datetime.date | None) -> int | None:
    """Returns the delay in days (0 or positive = filed on/after the due
    date; negative = filed early), or None if either input is missing --
    a missing filing_date means "not filed yet", a missing due date means
    the period's frequency couldn't be resolved; neither is scoreable as
    a delay."""
    if filing_date is None or due is None:
        return None
    return (filing_date - due).days


# ---------------------------------------------------------------------------
# Aggregation across a GSTIN's full filing history into the pattern that
# actually drives scoring/flagging -- a single late filing years ago reads
# very differently from 6 late filings in the last 12 months.
# ---------------------------------------------------------------------------

def summarize_filing_pattern(gstin: str, records: list[dict], as_of: datetime.date | None = None) -> dict:
    """gstin: the GSTIN all `records` belong to -- passed once here rather
    than repeated per-record, since it's constant across one GSTIN's whole
    filing history (only needed to resolve the QRMP GSTR-3B due-date
    group; see qrmp_gstr3b_due_day).

    records: a list of {"form": "GSTR-1"|"GSTR-3B", "period_start": date,
    "period_end": date, "filing_date": date|None} -- one entry per return
    period on record for this GSTIN (both forms mixed together is fine;
    the summary breaks out by form internally).

    Returns {
      "total_periods": int, "unresolvable_frequency": int (excluded from
        everything below -- see detect_period_frequency),
      "filed": int, "not_filed_yet": int (due date already passed, per
        `as_of`, but no filing_date on record),
      "on_time": int, "late": int, "late_pct": float (0-100, of periods
        with a resolvable due date and a filing_date -- "not filed yet"
        periods are counted in `not_filed_yet`, not folded into this rate),
      "worst_delay_days": int|None (max delay across all late filings),
      "delays_last_12_months": int (late OR not-yet-filed periods whose
        due date falls within the trailing 12 months of `as_of` -- the
        figure that should drive "ask the developer" flagging, since it's
        about the current pattern, not history from years ago).
    }
    `as_of` defaults to today, but callers generating scored/persisted
    Charter output should pass an explicit date instead -- otherwise the
    same facts.json re-rendered on a later date silently produces a
    different "delays_last_12_months" figure with no record of why."""
    if as_of is None:
        as_of = datetime.date.today()

    total_periods = len(records)
    unresolvable = 0
    filed = 0
    not_filed_yet = 0
    on_time = 0
    late = 0
    worst_delay = None
    delays_last_12_months = 0
    cutoff_12mo = as_of - datetime.timedelta(days=365)

    for rec in records:
        due = due_date(gstin, rec["form"], rec["period_start"], rec["period_end"])
        if due is None:
            unresolvable += 1
            continue

        filing_date = rec.get("filing_date")
        if filing_date is None:
            if due < as_of:
                not_filed_yet += 1
                if due >= cutoff_12mo:
                    delays_last_12_months += 1
            # due date not yet reached -- not a gap, simply not due yet;
            # excluded from every count above and below.
            continue

        filed += 1
        delay = compute_delay_days(filing_date, due)
        if delay <= 0:
            on_time += 1
        else:
            late += 1
            worst_delay = delay if worst_delay is None else max(worst_delay, delay)
            if due >= cutoff_12mo:
                delays_last_12_months += 1

    rated_periods = on_time + late  # periods with both a due date and a filing_date
    late_pct = round(100 * late / rated_periods, 1) if rated_periods else 0.0

    return {
        "total_periods": total_periods,
        "unresolvable_frequency": unresolvable,
        "filed": filed,
        "not_filed_yet": not_filed_yet,
        "on_time": on_time,
        "late": late,
        "late_pct": late_pct,
        "worst_delay_days": worst_delay,
        "delays_last_12_months": delays_last_12_months,
    }

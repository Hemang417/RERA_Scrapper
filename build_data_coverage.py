"""
Builds docs/RERA_Data_Coverage.xlsx -- what each authority and each workflow
actually yields, and where it lands in the Company Charter.

WHY THIS IS A SCRIPT AND NOT A SPREADSHEET
Kept as code so it is version-controlled, diffable in review, and cannot
drift silently: the FINDINGS below are the single source of truth and the
workbook is regenerated from them. Hand-editing the .xlsx is the one thing
NOT to do -- the next run overwrites it.

Every row records how the finding was established. "confirmed-live" means
this pipeline actually fetched it; "observed" means it was seen on the
portal but not yet fetched by code; "unaudited" means nobody has looked.
That distinction matters because 26 of ~30 state portals are unaudited, and
a coverage matrix that hides its own uncertainty is worse than none.

    python build_data_coverage.py

Update it whenever a workflow learns something new -- add to FINDINGS, rerun.
"""

import os
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

OUT_PATH = os.path.join("docs", "RERA_Data_Coverage.xlsx")

# --- house style ---------------------------------------------------------
_HEAD_FILL = PatternFill("solid", fgColor="1F3864")
_HEAD_FONT = Font(color="FFFFFF", bold=True, size=11)
_TITLE_FONT = Font(bold=True, size=14, color="1F3864")
_WRAP = Alignment(wrap_text=True, vertical="top")
_THIN = Side(style="thin", color="BFBFBF")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# Availability vocabulary, deliberately small. Colour carries the same
# meaning as the word so the sheet is readable at a glance and still
# correct when printed in greyscale.
_STATUS_FILL = {
    "Yes": PatternFill("solid", fgColor="C6EFCE"),
    "Partial": PatternFill("solid", fgColor="FFEB9C"),
    "No": PatternFill("solid", fgColor="FFC7CE"),
    "Not published": PatternFill("solid", fgColor="FFC7CE"),
    "Unaudited": PatternFill("solid", fgColor="D9D9D9"),
    "Not built": PatternFill("solid", fgColor="D9D9D9"),
}

_EVIDENCE_FILL = {
    "confirmed-live": PatternFill("solid", fgColor="C6EFCE"),
    "observed": PatternFill("solid", fgColor="FFEB9C"),
    "unaudited": PatternFill("solid", fgColor="D9D9D9"),
}


# =========================================================================
# WORKFLOW A -- RERA. What each authority publishes, per data item.
# =========================================================================
# Columns are authorities; the last two record where it lands in the Charter.
RERA_STATES = ["MahaRERA", "TG-RERA", "GujRERA", "K-RERA"]

RERA_FINDINGS = [
    # (data item, MH, TG, GJ, KA, charter facts field, note)
    ("Project identity (name, status, type, dates)",
     "Yes", "Yes", "Yes", "Yes", "rera_core_fields", ""),
    ("Registration number retrievable from the portal",
     "Yes", "No", "Yes", "Yes", "rera_core_fields.registration_number",
     "TG-RERA's public record does not display its own registration number; search is by project name."),
    ("Promoter / partner identity",
     "Yes", "Yes", "Yes", "Yes", "corporate_identity",
     "K-RERA publishes partner PANs and land-owner SHARES -- neither appears on a MahaRERA record."),
    ("Professionals of record (architect/engineer/CA)",
     "Yes", "No", "Yes", "Yes", "local_planning.professionals_of_record",
     "GujRERA splits these across englist/calist/acrchlist/contr; the adapter normalises to one list."),
    ("Land details / survey numbers",
     "Yes", "Yes", "Yes", "Yes", "land_identification",
     "K-RERA gives per-owner shares against survey numbers."),
    ("Bank accounts (escrow / collection)",
     "Yes", "Yes", "Yes", "Yes", "rera_compliance.collection_account",
     "TG-RERA publishes the 100% collection account but leaves the 70/30 split accounts blank."),
    ("Document library (downloadable files)",
     "Yes", "No", "Yes", "Yes", "document_library",
     "GujRERA 42/42 retrieved. K-RERA 112/152 -- the other 40 are LISTED but the portal serves 0 bytes."),
    ("Complaints register",
     "Yes", "No", "Not published", "Yes", "rera_core_fields.total_complaints_count",
     "K-RERA: use the STATE-WIDE /projectComplaintReport. The per-project page is NOT reliable -- it showed no complaints for a project the register lists with 12."),
    ("Appeals register",
     "Yes", "No", "Not published", "Not published", "rera_core_fields.total_appeals_count", ""),
    ("Orders / judgments search",
     "Yes", "No", "Not published", "Partial", "sources[] (topic=litigation)",
     "K-RERA /viewAllJudgements exists; the adapter reads complaints but not judgements yet."),
    ("Promoter's other projects (track record)",
     "Yes", "No", "No", "Yes", "promoter_portfolio",
     "K-RERA embeds the WHOLE state index client-side (9,888 projects with promoter names), so a portfolio is one request. GujRERA confirmed absent."),
    ("Past-experience declarations",
     "Yes", "No", "Yes", "Not published", "promoter_portfolio.totals", ""),
    ("AUDITED BALANCE SHEET",
     "No", "No", "Yes", "Partial", "(unmapped -- see Charter Mapping sheet)",
     "GujRERA findoc block. MahaRERA publishes nothing equivalent. Highest-value differential finding."),
    ("Audited profit & loss statement",
     "No", "No", "Yes", "No", "(unmapped)", "GujRERA findoc block."),
    ("Income-tax returns",
     "No", "No", "Yes", "No", "(unmapped)", "GujRERA findoc block."),
    ("Defaulters list",
     "No", "No", "No", "Partial", "(unmapped)",
     "K-RERA /viewDefaultProjects -- observed, not yet built. No other authority publishes one."),
    ("Projects under investigation",
     "No", "No", "No", "Partial", "(unmapped)",
     "K-RERA /unregProjectList -- observed, not yet built."),
    ("Cost incurred vs estimated cost",
     "No", "No", "Partial", "Yes", "(unmapped)",
     "K-RERA publishes both, per particular. Direct input to the financial-strength sub-metric."),
    ("Delay reasons (promoter-declared)",
     "No", "No", "No", "Yes", "(unmapped)", "K-RERA detail page carries a delay-reason table."),
    ("NOC expiry and renewal tracking",
     "No", "No", "No", "Yes", "(unmapped)", "K-RERA tracks NOC validity dates and whether renewed."),
    ("Construction progress / QPR",
     "Yes", "Partial", "Yes", "Yes", "rera_compliance.construction_progress", ""),
]

# =========================================================================
# WORKFLOW B -- CTS / land records
# =========================================================================
CTS_FINDINGS = [
    # (field, source, availability, cost, evidence, note)
    ("Owner / holder name", "Maha Bhulekh Property Card (हक्काचा मूळ धारक)",
     "Partial", "Free", "confirmed-live",
     "PRESENT on the free card. Extraction is what is broken -- the parser looks for ZaubaCorp's li.row pattern, but the card is an HTML table."),
    ("Area", "Maha Bhulekh Property Card (क्षेत्र चौ.मी.)",
     "Partial", "Free", "confirmed-live", "Present on the card; same extraction gap."),
    ("Tenure / occupancy class", "Maha Bhulekh Property Card (धारणाधिकार)",
     "Partial", "Free", "confirmed-live", "Only source for this field -- Index II does not carry occupancy class."),
    ("Encumbrance (on the card)", "Maha Bhulekh Property Card (इतर भार)",
     "Partial", "Free", "confirmed-live", "Present as a labelled row."),
    ("Mutation entries", "Maha Bhulekh Property Card (फेरफार table)",
     "Partial", "Free", "confirmed-live", "Full dated table with mutation numbers, present on the card."),
    ("Registered deeds / parties / consideration", "IGR Maharashtra e-Search (Index II)",
     "Yes", "Free + CAPTCHA", "observed", "Mumbai from 1985; other districts digitised from 2002."),
    ("Equitable mortgages", "IGR Notice of Intimation",
     "Yes", "Free + CAPTCHA", "observed",
     "Compulsory in MAHARASHTRA AND GUJARAT ONLY. This is why CERSAI is not needed for those two states."),
    ("Lender, amount, satisfaction status", "MCA charge filings via ZaubaCorp",
     "Yes", "Free -- page already fetched", "observed",
     "NOT currently parsed. The only independent check on the promoter's declared mortgage. National, so it belongs in the CIN workflow."),
    ("Land records for other states", "Dharani (TG) / Bhoomi (KA) / AnyROR (GJ) / Bhulekh (UP)",
     "Not built", "n/a", "unaudited", "Each is a separate system with its own identifier scheme, language and CAPTCHA."),
]

# =========================================================================
# WORKFLOW C -- CIN / corporate identity. Already national.
# =========================================================================
CIN_FINDINGS = [
    ("Legal name, CIN/LLPIN, status, class, category, ROC", "ZaubaCorp / Tofler / InstaFinancials",
     "Yes", "Free", "confirmed-live", "company_profile_check",
     "Merged across three mirrors, first non-empty wins. ROC is read from the record, never assumed."),
    ("Incorporation date, registered address", "MCA mirrors", "Yes", "Free", "confirmed-live",
     "company_profile_check", ""),
    ("Authorised and paid-up capital", "MCA mirrors", "Yes", "Free", "confirmed-live",
     "company_profile_check", "Paywall-guarded: _looks_paywalled discards upsell copy rather than rendering it."),
    ("Current and past directors (DIN, designation, dates)", "ZaubaCorp", "Yes", "Free", "confirmed-live",
     "company_profile_check.current_directors", "Merged by DIN; disagreements surface as roster_conflicts."),
    ("Insolvency status", "IBBI (official)", "Yes", "Free", "confirmed-live",
     "ibbi_insolvency_check",
     "Caveat recorded in code: a fake identifier returns the same 'clean' page, so absence is not proof of existence."),
    ("Credit rating", "ICRA + Infomerics", "Yes", "Free", "confirmed-live",
     "credit_rating_check", "CRISIL/CARE not implemented. Exact legal-name match only, never fuzzy."),
    ("Group / affiliated companies", "ZaubaCorp director & address crosswalk", "Yes", "Free", "confirmed-live",
     "group_companies_check", "Hard links only: shared director, shared registered office, or filed subsidiary/associate/JV."),
    ("MCA CHARGE FILINGS (lender, amount, assets, satisfaction)", "ZaubaCorp (same page)",
     "Yes", "Free", "confirmed-live", "company_profile_check.charges",
     "NOW PARSED. Live on a real promoter: 4 OPEN charges, ~Rs 90.3 crore, to HDFC Bank and "
     "Catalyst Trusteeship. No closure date = live encumbrance. The only independent check on "
     "a promoter's declared mortgage -- the RERA record gives an area and never a lender."),
    ("Balance sheet / P&L", "MCA (paid) / Tofler / InstaFinancials (paywalled)",
     "No", "Paid", "confirmed-live", "n/a",
     "NOT freely available for a private SPV -- EXCEPT in Gujarat, where GujRERA publishes audited statements per project."),
    ("Credit-rating rationale (revenue, debt, net worth)", "ICRA / Infomerics press releases",
     "Partial", "Free", "observed", "(unmapped -- Phase 4d)",
     "Only the CURRENT rating is fetched today; the rationale endpoint is noted in code as not-yet-identified."),
]

# =========================================================================
# Charter mapping -- what is available but NOT yet used
# =========================================================================
UNMAPPED = [
    ("Audited balance sheet / P&L / ITR", "GujRERA findoc", "Gujarat projects only",
     "Financial strength sub-metric currently scores None for every project. This would score it for GJ.",
     "High"),
    ("Group entity graph (confirmed vs proposed)", "group_entities.py", "All corporate promoters",
     "BUILT. Propose by brand name, confirm by shared director / office / filed relationship. "
     "Link strength tiered -- address-only links excluded from sweeps.", "High"),
    ("K-RERA defaulters list", "K-RERA, observed", "Karnataka projects",
     "No equivalent exists in any other authority. Direct red-flag input.",
     "High"),
    ("K-RERA projects under investigation", "K-RERA, observed", "Karnataka projects",
     "Same -- a regulator-declared adverse signal.", "Medium"),
    ("Credit-rating rationale documents", "ICRA / Infomerics", "Rated promoters only",
     "Free route to revenue/debt/net-worth figures the MCA charges for.", "Medium"),
    ("K-RERA cost incurred vs estimated", "K-RERA detail page", "Karnataka projects",
     "Financial-strength scores None everywhere today. This is a direct, free input.", "High"),
    ("K-RERA delay reasons + NOC expiry", "K-RERA detail page", "Karnataka projects",
     "Promoter-declared delay reasons and lapsed-NOC tracking; no equivalent elsewhere.", "Medium"),
    ("Maha Bhulekh card fields", "Free portal, extraction broken", "Maharashtra projects",
     "Owner, area, tenure, encumbrance and mutation are all ON the card and none reach the document.",
     "High"),
]


def _style_header(ws, row, ncols):
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = _HEAD_FILL
        cell.font = _HEAD_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        cell.border = _BORDER


def _finish(ws, widths, header_row, first_data_row, ncols):
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for row in ws.iter_rows(min_row=first_data_row, max_row=ws.max_row, max_col=ncols):
        for cell in row:
            cell.alignment = _WRAP
            cell.border = _BORDER
    ws.freeze_panes = ws.cell(row=first_data_row, column=1)
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ncols)}{ws.max_row}"


def _title(ws, text, subtitle, ncols):
    ws["A1"] = text
    ws["A1"].font = _TITLE_FONT
    ws["A2"] = subtitle
    ws["A2"].font = Font(italic=True, size=9, color="595959")
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)


def build_rera_sheet(wb):
    ws = wb.create_sheet("A - RERA by authority")
    ncols = 3 + len(RERA_STATES)
    _title(ws, "Workflow A: RERA -- what each authority publishes",
           "Yes / Partial / No / Not published / Unaudited. 'Unaudited' means nobody has looked yet -- "
           "26 of ~30 state portals are in that state.", ncols)
    header = ["Data item"] + RERA_STATES + ["Charter facts field", "Note"]
    ws.append([])
    ws.append(header)
    header_row = ws.max_row
    _style_header(ws, header_row, ncols)
    for item, *rest in RERA_FINDINGS:
        states_vals = rest[:len(RERA_STATES)]
        field, note = rest[len(RERA_STATES)], rest[len(RERA_STATES) + 1]
        ws.append([item] + list(states_vals) + [field, note])
        for offset in range(len(RERA_STATES)):
            cell = ws.cell(row=ws.max_row, column=2 + offset)
            cell.fill = _STATUS_FILL.get(cell.value, _STATUS_FILL["Unaudited"])
            cell.alignment = Alignment(horizontal="center", vertical="center")
    _finish(ws, [46] + [13] * len(RERA_STATES) + [30, 62], header_row, header_row + 1, ncols)


def build_cts_sheet(wb):
    ws = wb.create_sheet("B - CTS land records")
    ncols = 6
    _title(ws, "Workflow B: CTS / land records",
           "The free portal already returns every field. The extraction is what is broken -- "
           "no paid source is needed.", ncols)
    ws.append([])
    ws.append(["Field", "Source", "Availability", "Cost", "Evidence", "Note"])
    header_row = ws.max_row
    _style_header(ws, header_row, ncols)
    for row in CTS_FINDINGS:
        ws.append(list(row))
        ws.cell(row=ws.max_row, column=3).fill = _STATUS_FILL.get(row[2], _STATUS_FILL["Unaudited"])
        ws.cell(row=ws.max_row, column=5).fill = _EVIDENCE_FILL.get(row[4], _EVIDENCE_FILL["unaudited"])
    _finish(ws, [34, 40, 14, 22, 16, 70], header_row, header_row + 1, ncols)


def build_cin_sheet(wb):
    ws = wb.create_sheet("C - CIN corporate")
    ncols = 7
    _title(ws, "Workflow C: CIN / corporate identity -- already national",
           "Keyed on CIN/LLPIN; every source is national. No state adapter needed.", ncols)
    ws.append([])
    ws.append(["Data item", "Source", "Availability", "Cost", "Evidence", "Charter facts field", "Note"])
    header_row = ws.max_row
    _style_header(ws, header_row, ncols)
    for row in CIN_FINDINGS:
        ws.append(list(row))
        ws.cell(row=ws.max_row, column=3).fill = _STATUS_FILL.get(row[2], _STATUS_FILL["Unaudited"])
        ws.cell(row=ws.max_row, column=5).fill = _EVIDENCE_FILL.get(row[4], _EVIDENCE_FILL["unaudited"])
    _finish(ws, [42, 34, 13, 14, 16, 30, 66], header_row, header_row + 1, ncols)


def build_unmapped_sheet(wb):
    ws = wb.create_sheet("D - Available but unused")
    ncols = 5
    _title(ws, "Available but NOT yet used in the Company Charter",
           "The point of this workbook: data we can already reach that no Charter field consumes.", ncols)
    ws.append([])
    ws.append(["Data", "Where it comes from", "Applies to", "What it would fix", "Value"])
    header_row = ws.max_row
    _style_header(ws, header_row, ncols)
    for row in UNMAPPED:
        ws.append(list(row))
        cell = ws.cell(row=ws.max_row, column=5)
        cell.fill = _STATUS_FILL["Yes"] if row[4] == "High" else _STATUS_FILL["Partial"]
        cell.alignment = Alignment(horizontal="center", vertical="center")
    _finish(ws, [40, 30, 26, 64, 10], header_row, header_row + 1, ncols)


def build_readme(wb):
    ws = wb.active
    ws.title = "How to read this"
    ws.column_dimensions["A"].width = 118
    lines = [
        ("RERA data coverage", _TITLE_FONT),
        (f"Regenerated {date.today().isoformat()} by build_data_coverage.py", Font(italic=True, size=9, color="595959")),
        ("", None),
        ("What this is", Font(bold=True, size=12)),
        ("A record of what each authority and each workflow actually yields, so we can map later which "
         "of it the Company Charter should consume. Sheet D is the payload: data we can already reach "
         "that no Charter field uses yet.", None),
        ("", None),
        ("Do not hand-edit this file", Font(bold=True, size=12)),
        ("It is generated. FINDINGS in build_data_coverage.py is the source of truth -- edit there and "
         "rerun, or the next run silently discards your changes.", None),
        ("", None),
        ("Evidence levels -- read these before trusting a row", Font(bold=True, size=12)),
        ("confirmed-live   this pipeline actually fetched it", None),
        ("observed         seen on the portal, not yet fetched by code", None),
        ("unaudited        nobody has looked. 26 of ~30 state portals are here.", None),
        ("", None),
        ("Biggest findings so far", Font(bold=True, size=12)),
        ("1. GujRERA publishes AUDITED BALANCE SHEETS, P&L and income-tax returns per project. "
         "MahaRERA publishes nothing equivalent. Already downloaded by the adapter, not yet used.", None),
        ("2. The Maha Bhulekh land-record card already carries owner, area, tenure, encumbrance and "
         "mutation. None of it reaches the document -- the parser looks for the wrong HTML shape.", None),
        ("3. MCA charge filings are on the ZaubaCorp page the pipeline already fetches, and are the only "
         "independent check on a promoter's declared mortgage. Not parsed.", None),
        ("4. GujRERA does NOT link a promoter to their other projects -- confirmed live. So no promoter "
         "track record is possible there, and the capability is declared false.", None),
        ("5. K-RERA's PER-PROJECT complaint page is unreliable: it showed NO complaints for a project "
         "the state-wide register lists with 12. Counts must come from /projectComplaintReport. "
         "Reading the wrong page would have produced a false clean record.", None),
        ("6. K-RERA embeds its entire 9,888-project index client-side, giving a promoter-to-projects "
         "map for the whole state in one request -- the opposite of Gujarat.", None),
        ("7. MCA charge filings were on the ZaubaCorp page all along. A real promoter shows 4 OPEN "
         "charges totalling ~Rs 90.3 crore to HDFC Bank and Catalyst Trusteeship -- free, no extra "
         "request, and the only independent check on a declared mortgage.", None),
        ("8. Group membership CANNOT be derived from a brand name. Searching 'PRANAMI' returns a Delhi "
         "hydro-power firm, a Gujarat non-profit and a Maharashtra castings company. Propose by name, "
         "confirm by shared director / registered office / filed relationship -- and treat a shared "
         "address as the weakest of the three (28 of 65 'group companies' were address-only).", None),
    ]
    for i, (text, font) in enumerate(lines, start=1):
        ws.cell(row=i, column=1, value=text)
        if font:
            ws.cell(row=i, column=1).font = font
        ws.cell(row=i, column=1).alignment = Alignment(wrap_text=True, vertical="top")


def main():
    wb = Workbook()
    build_readme(wb)
    build_rera_sheet(wb)
    build_cts_sheet(wb)
    build_cin_sheet(wb)
    build_unmapped_sheet(wb)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    wb.save(OUT_PATH)
    print(f"[OK] {OUT_PATH}")
    print(f"     {len(RERA_FINDINGS)} RERA items x {len(RERA_STATES)} authorities, "
          f"{len(CTS_FINDINGS)} CTS, {len(CIN_FINDINGS)} CIN, {len(UNMAPPED)} unused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

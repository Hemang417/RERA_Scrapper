"""
Rebuilds IGR_Property_YoY_2020-2026.xlsx from _translation_rows.json (the
already-parsed Index II data saved before the first rebuild overwrote the
original raw workbook -- no live portal interaction needed). Parses each
row's party blocks into clean Seller/Purchaser names and translates every
Marathi field to English (India) via _translate_marathi's glossary + hand-
translated party names + auto-transliteration fallback.
"""
import json
import re
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from _translate_marathi import translate_marathi, PARTY_NAMES, _NORMALIZED_PARTY_NAMES, _translate_unknown_name


def party_name(block: str) -> str:
    """Pulls just the Name:- ... portion out of an executant_party/
    claimant_party block (which also carries age/address/PAN) and
    translates it via the same hand-translated PARTY_NAMES dict, falling
    back to _translate_unknown_name (Claude-based, see its own docstring)
    for a name this dataset's own dictionary doesn't cover -- this is the
    integration point that replaces manual name-by-name reading for any
    FUTURE dataset."""
    if not block:
        return ""
    names = []
    for m in re.finditer(r"नाव:-\s*([^\n]*?)\s*(?:वय|-\s*-\s*वय|\s*पत्ता)", block):
        raw = m.group(1).strip().rstrip("-").strip()
        if not raw:
            continue
        normalized = re.sub(r"\s+", "", raw)
        # NOTE: must NOT be `.get(normalized, _translate_unknown_name(raw))`
        # -- dict.get() evaluates its default argument eagerly regardless
        # of whether the key is found, which silently fired a live Claude
        # API call for every name including ones already in the
        # dictionary (confirmed live 2026-10-15: 158 unnecessary calls,
        # ~$0.16 wasted -- harmless here given the $5 cap but a real
        # lazy-evaluation bug, not just a style nit).
        if normalized in _NORMALIZED_PARTY_NAMES:
            names.append(_NORMALIZED_PARTY_NAMES[normalized])
        else:
            names.append(_translate_unknown_name(raw))
    return "; ".join(names) if names else translate_marathi(block)


with open("_translation_rows.json", encoding="utf-8") as f:
    raw_rows = json.load(f)

# CONFIRMED LIVE 2026-10-15: at least 5 of 66 rows use a DIFFERENT Index II
# template (seen on deed types like "Agreement Relating to Deposit of
# Title Deeds,Pawn" / the numbered "6-Notice of intimation..." type) whose
# field numbering does not match the standard 14-field layout
# parse_index_ii() assumes -- dates/amounts/text end up in the wrong
# fields entirely (e.g. Market Value holding the property description,
# Stamp Duty holding a date). Flagged here by docno+year (identified via
# validation, not re-derivable from the already-shifted parsed fields
# alone -- the raw Index II text for these specific rows was not
# preserved) so their numeric/date fields ship blank rather than wrong,
# per explicit user decision, while deed type/dates-that-did-parse/party
# names (parsed independently of the numbered sections) stay as-is. A
# proper fix needs this alternate template's real field layout, which
# means re-fetching a fresh raw Index II sample live -- tracked
# separately, not blocking this deliverable.
MISALIGNED_ROWS = {
    (2020, "557"), (2023, "1635"), (2023, "2292"), (2024, "394"), (2026, "417"),
}

doc_rows = []
for r in raw_rows:
    key = (r.get("year"), str(r.get("docno")))
    misaligned = key in MISALIGNED_ROWS
    note = "" if not misaligned else "Not parsed -- this document uses a different Index II layout; see project notes."
    doc_rows.append({
        "search_year": r.get("year"),
        "doc_no": r.get("docno"),
        "deed_type": translate_marathi(r.get("deed_type") or r.get("dname") or ""),
        "registration_date": "" if misaligned else r.get("registration_date_ii", ""),
        "execution_date": "" if misaligned else r.get("execution_date", ""),
        "sro_office": translate_marathi(r.get("sro_office", "")),
        "seller": party_name(r.get("executant_party", "")) if not misaligned else "",
        "purchaser": party_name(r.get("claimant_party", "")) if not misaligned else "",
        "consideration": "" if misaligned else r.get("consideration", ""),
        "market_value": "" if misaligned else r.get("market_value", ""),
        "area": "" if misaligned else translate_marathi(r.get("area", "")),
        "stamp_duty": "" if misaligned else r.get("stamp_duty", ""),
        "registration_fee": "" if misaligned else r.get("registration_fee", ""),
        "village": translate_marathi(r.get("village_name_ii", "")) or "Parel",
        "property_description": "" if misaligned else translate_marathi(r.get("property_description_ii", "")),
        "remarks": note if misaligned else translate_marathi(r.get("remarks", "")),
    })

# -- Build the final workbook ---------------------------------------------
wb = Workbook()

ws_summary = wb.active
ws_summary.title = "YoY Summary"
summary_by_year = {}
for r in doc_rows:
    summary_by_year.setdefault(r["search_year"], 0)
    summary_by_year[r["search_year"]] += 1
ws_summary.append(["Year", "Documents Found"])
for cell in ws_summary[1]:
    cell.font = Font(bold=True)
for year in sorted(summary_by_year):
    ws_summary.append([year, summary_by_year[year]])
ws_summary.column_dimensions["A"].width = 10
ws_summary.column_dimensions["B"].width = 18

ws_docs = wb.create_sheet("Documents")
columns = [
    ("search_year", "Search Year", 10),
    ("doc_no", "Document No.", 12),
    ("deed_type", "Deed Type", 30),
    ("registration_date", "Registration Date", 14),
    ("execution_date", "Execution Date", 14),
    ("sro_office", "Sub-Registrar Office", 22),
    ("seller", "Seller / Transferor", 45),
    ("purchaser", "Purchaser / Transferee", 45),
    ("consideration", "Consideration Amount (Rs.)", 18),
    ("market_value", "Market Value (Rs.)", 16),
    ("area", "Area", 16),
    ("stamp_duty", "Stamp Duty (Rs.)", 14),
    ("registration_fee", "Registration Fee (Rs.)", 16),
    ("village", "Village", 10),
    ("property_description", "Property Description", 70),
    ("remarks", "Remarks", 60),
]
ws_docs.append([label for _, label, _ in columns])
for cell in ws_docs[1]:
    cell.font = Font(bold=True)
for row in doc_rows:
    ws_docs.append([row.get(key, "") for key, _, _ in columns])
for i, (_, _, width) in enumerate(columns, start=1):
    ws_docs.column_dimensions[ws_docs.cell(row=1, column=i).column_letter].width = width
for row_cells in ws_docs.iter_rows(min_row=2):
    for cell in row_cells:
        cell.alignment = Alignment(wrap_text=True, vertical="top")

out_path = "IGR_Property_YoY_2020-2026.xlsx"
wb.save(out_path)
print(f"Wrote {out_path}: {len(doc_rows)} document rows")

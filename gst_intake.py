"""
Live GST compliance intake -- given a promoter's PAN (the realistic
starting point: MahaRERA's own document set always includes a PAN card,
never a GSTIN), discovers every GSTIN registered under that PAN across
every state (gst_portal.search_gstins_by_pan), fetches each one's full
filing history across every financial year the portal offers
(gst_portal.fetch_gstin_filing_table), parses it into gst_compliance.py's
structured record shape (gst_portal.parse_filing_table), and writes
output/<reg_no>/gst_filing_input.json -- the exact file
run_gst_compliance_check already looks for on every Company Charter run
(see that function's own docstring in company_charter.py). No manual
transcription step remains; the next `python company_charter.py <reg_no>`
run picks this up automatically.

CONFIRMED LIVE end-to-end (2026-07-31, against Pranami Neev Realty
Limited, PAN AANCP0234D -> GSTIN 27AANCP0234D1ZO, 76 scoreable periods
spanning 2022-08 through 2026-06) -- this used to stop at raw per-GSTIN
dumps because the filing table's real layout, and the Financial-Year-
selector step needed to populate it, had never been observed past the
portal's CAPTCHA gate. Both are confirmed now; see gst_portal.py's own
module docstring for the exact DOM findings.

Requires a human to solve a fresh CAPTCHA for the PAN search, and once
more per discovered GSTIN (that one solve then walks every financial
year automatically -- see gst_portal.fetch_gstin_filing_table). Each
fetch opens its own visible browser window in turn.

A promoter can hold more than one GSTIN (one per state of operation). If
more than one is found, the one with the most filing periods on record
becomes the "primary" GSTIN written to gst_filing_input.json (the file
schema is single-GSTIN, and QRMP due-date rules are state-specific, so
combining two different states' periods under one GSTIN's state code
would misclassify some due dates) -- the others' raw filing data is
still written to gst_portal_raw/ for a human to review, not discarded.

    python gst_intake.py <PAN_or_GSTIN> <reg_no>
"""

import argparse
import json
import os

import gst_compliance
import gst_portal


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover every GSTIN under a PAN, fetch each one's full filing history from the live "
                     "GST portal, parse it, and write gst_filing_input.json for run_gst_compliance_check to "
                     "pick up automatically on the next Company Charter run."
    )
    parser.add_argument("identifier", help="A PAN (e.g. AANCP0234D) or a GSTIN (e.g. 27AANCP0234D1ZO) for this promoter.")
    parser.add_argument("reg_no", help="MahaRERA registration number this GST data belongs to.")
    parser.add_argument("--output-dir", default=gst_portal.config.OUTPUT_ROOT)
    args = parser.parse_args()

    identifier = args.identifier.strip().upper()
    if gst_compliance.validate_gstin(identifier):
        pan = gst_compliance.extract_pan_from_gstin(identifier)
        print(f"[INFO] Extracted PAN {pan} from GSTIN {identifier}.")
    else:
        pan = identifier
        print(f"[INFO] Treating {pan!r} as a PAN directly.")

    project_dir = os.path.join(args.output_dir, args.reg_no)
    raw_dir = os.path.join(project_dir, "gst_portal_raw")
    os.makedirs(raw_dir, exist_ok=True)

    print(f"\n[INFO] Searching for every GSTIN registered under PAN {pan}...")
    pan_result = gst_portal.search_gstins_by_pan(pan, screenshot_path=os.path.join(raw_dir, f"pan_{pan}_search.png"))
    with open(os.path.join(raw_dir, f"pan_{pan}_search.json"), "w", encoding="utf-8") as f:
        json.dump(pan_result, f, indent=2, ensure_ascii=False)

    if not pan_result["found"]:
        if gst_compliance.validate_gstin(identifier):
            print(f"[WARN] No GSTINs found for PAN {pan}: {pan_result.get('note', 'no reason given')}")
            print(f"[INFO] Falling back to just the originally-supplied GSTIN ({identifier}).")
            gstins = [identifier]
        else:
            print(f"[ERROR] No GSTINs found for PAN {pan}: {pan_result.get('note', 'no reason given')}")
            return 1
    else:
        gstins = pan_result["gstins"]
        print(f"[OK] Found {len(gstins)} GSTIN(s) under this PAN: {', '.join(gstins)}")

    records_by_gstin = {}
    for g in gstins:
        print(f"\n[INFO] Fetching filing table for {g} (every financial year offered)...")
        result = gst_portal.fetch_gstin_filing_table(g, screenshot_path=os.path.join(raw_dir, f"{g}_filing.png"))
        result_path = os.path.join(raw_dir, f"{g}_filing.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        if not result.get("found"):
            print(f"[WARN] {g}: not found ({result.get('note', 'no reason given')}). Raw result written to {result_path}")
            continue

        by_year = result.get("by_year")
        if not by_year:
            print(f"[WARN] {g}: registration found, but no per-year filing data ({result.get('note', 'no reason given')}). "
                  f"Raw result written to {result_path}")
            continue

        records = gst_portal.parse_filing_table(by_year)
        records_by_gstin[g] = records
        print(f"[OK] {g}: parsed {len(records)} scoreable period(s) across {len(by_year)} financial year(s). "
              f"Raw result written to {result_path}")

    if not records_by_gstin:
        print("\n[ERROR] No GSTIN produced any scoreable filing records this pass -- gst_filing_input.json not written.")
        return 1

    primary_gstin = max(records_by_gstin, key=lambda g: len(records_by_gstin[g]))
    if len(records_by_gstin) > 1:
        others = [g for g in records_by_gstin if g != primary_gstin]
        print(f"\n[INFO] {len(records_by_gstin)} GSTINs had scoreable data; using {primary_gstin} "
              f"({len(records_by_gstin[primary_gstin])} periods) as primary. "
              f"Not included in gst_filing_input.json: {', '.join(others)} -- their raw data is in {raw_dir}.")

    gst_filing_input_path = os.path.join(project_dir, "gst_filing_input.json")
    with open(gst_filing_input_path, "w", encoding="utf-8") as f:
        json.dump({"gstin": primary_gstin, "records": records_by_gstin[primary_gstin]}, f, indent=2, ensure_ascii=False)

    print(f"\n[OK] Wrote {gst_filing_input_path} -- run_gst_compliance_check will pick this up automatically "
          f"on the next `python company_charter.py {args.reg_no}` run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

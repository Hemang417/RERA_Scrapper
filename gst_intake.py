"""
Live GST compliance intake -- given a bare GSTIN (from whichever upstream
system supplies it), extracts the embedded PAN and discovers every other
GSTIN registered under that same PAN across every state
(gst_portal.search_gstins_by_pan), then fetches each one's filing-history
page (gst_portal.fetch_gstin_filing_table). A single promoter entity can
hold more than one GSTIN (one per state of operation), so this covers all
of them, not just the one originally supplied.

Requires a human to solve a fresh CAPTCHA for the PAN search, and once
more per discovered GSTIN's filing-table fetch -- be present for all of
it. Each fetch opens its own visible browser window in turn.

NOT YET auto-scored: the filing table's real column layout isn't
independently confirmed live yet (see gst_portal.py's own module note on
why -- the CAPTCHA widget itself failed to render in the sandboxed
browser this was researched in, so nothing past that gate has been
directly observed). This writes RAW per-GSTIN results (raw_text/ocr_text/
a screenshot) to output/<reg_no>/gst_portal_raw/ for a human to read
directly, rather than guessing a table parser for content nobody has
actually seen yet. Once a real run confirms the table's layout, the
parser gets built and this will write straight to gst_filing_input.json
for run_gst_compliance_check to pick up automatically -- see that
function's own docstring for today's (manual) input path.

    python gst_intake.py <GSTIN> <reg_no>
"""

import argparse
import json
import os

import gst_compliance
import gst_portal


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover every GSTIN under a PAN and fetch each one's filing history from the live GST portal."
    )
    parser.add_argument("gstin", help="A GSTIN for this promoter, e.g. 27AANCM5273D1ZA -- its PAN gets extracted automatically.")
    parser.add_argument("reg_no", help="MahaRERA registration number this GST data belongs to.")
    parser.add_argument("--output-dir", default=gst_portal.config.OUTPUT_ROOT)
    args = parser.parse_args()

    gstin = args.gstin.strip().upper()
    if not gst_compliance.validate_gstin(gstin):
        print(f"[ERROR] {gstin!r} is not a validly-formatted GSTIN.")
        return 1

    pan = gst_compliance.extract_pan_from_gstin(gstin)
    print(f"[INFO] Extracted PAN {pan} from GSTIN {gstin}.")

    out_dir = os.path.join(args.output_dir, args.reg_no, "gst_portal_raw")
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n[INFO] Searching for every GSTIN registered under PAN {pan}...")
    pan_result = gst_portal.search_gstins_by_pan(pan, screenshot_path=os.path.join(out_dir, f"pan_{pan}_search.png"))
    with open(os.path.join(out_dir, f"pan_{pan}_search.json"), "w", encoding="utf-8") as f:
        json.dump(pan_result, f, indent=2, ensure_ascii=False)

    if not pan_result["found"]:
        print(f"[WARN] No GSTINs found for PAN {pan}: {pan_result.get('note', 'no reason given')}")
        print(f"[INFO] Falling back to just the originally-supplied GSTIN ({gstin}).")
        gstins = [gstin]
    else:
        gstins = pan_result["gstins"]
        print(f"[OK] Found {len(gstins)} GSTIN(s) under this PAN: {', '.join(gstins)}")

    for g in gstins:
        print(f"\n[INFO] Fetching filing table for {g}...")
        result = gst_portal.fetch_gstin_filing_table(g, screenshot_path=os.path.join(out_dir, f"{g}_filing.png"))
        result_path = os.path.join(out_dir, f"{g}_filing.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        status = "found" if result.get("found") else f"not found ({result.get('note', 'no reason given')})"
        print(f"[OK] {g}: {status}. Written to {result_path}")

    print(f"\n[INFO] Raw results written to {out_dir}")
    print("       Automatic scoring isn't wired up yet -- read the raw_text/ocr_text/screenshot in each file")
    print("       directly for now (see this script's own module docstring for why).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

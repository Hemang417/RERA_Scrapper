"""
Standalone CTS -> land-record lookup -- no RERA project or registration
number required. Runs the same Maha Bhulekh Property Card lookup
run_cts_land_lookup runs internally, but from bare district/office/village/
cts_number/mobile handed to us directly, callable before any RERA number
exists for this plot.

Exists for the common case where a CTS number reaches this pipeline before
a RERA number does (land is acquired -- and so has a CTS number -- well
before a project ever registers with RERA).

office/village must be the site's own exact Marathi labels, never guessed
-- resolve them first with:

    python mahabhumi.py offices <district>
    python mahabhumi.py villages <district> <office_label>

Candidate search (confirming the CTS number is an exact match for this
village) is headless, no CAPTCHA. Fetching the actual Property Card opens a
real, visible browser and blocks waiting for a human to solve a fresh
CAPTCHA -- be present when running this.

Writes output/_pending/<district>_<village>_<cts_number>/land_record.json
using the exact same facts.json key run_cts_land_lookup itself uses
(cts_land_record_check) -- so once a RERA number for this same plot is
known, that record can be merged into a real Charter's facts with a plain
dict update, never a reshape.

    python cts_intake.py <district> <office_label> <village_label> <cts_number> <mobile>
"""

import argparse
import os

import company_charter


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the standalone CTS -> Maha Bhulekh Property Card lookup, with no RERA "
        "project required. office/village must be the site's own exact Marathi labels -- resolve "
        "them first with `python mahabhumi.py offices/villages ...`."
    )
    parser.add_argument("district", help="e.g. Pune")
    parser.add_argument("office", help="Exact Marathi office label from `python mahabhumi.py offices <district>`")
    parser.add_argument("village", help="Exact Marathi village label from `python mahabhumi.py villages <district> <office>`")
    parser.add_argument("cts_number", help="e.g. 100 or 100/1")
    parser.add_argument("mobile", help="Mobile number to submit on the Property Card form")
    parser.add_argument("--output-dir", default=company_charter.config.OUTPUT_ROOT)
    args = parser.parse_args()

    record = company_charter.run_cts_lookup_standalone(
        args.district, args.office, args.village, args.cts_number, args.mobile, args.output_dir,
    )

    check = record["cts_land_record_check"]
    if check.get("found"):
        print(f"\n[OK] Property Card found for CTS {args.cts_number}.")
    else:
        print(f"\n[WARN] Not found: {check.get('note', 'no reason given')}")

    slug = company_charter._slugify_for_pending_key(args.district, args.village, args.cts_number)
    out_path = os.path.join(args.output_dir, "_pending", slug, "land_record.json")
    print(f"Written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Standalone promoter/company lookup from a bare CIN -- no RERA project or
registration number required. Runs the same MCA-mirror company-profile
chain, IBBI insolvency check, ZaubaCorp group-companies crosswalk, and (if
a company name is given) credit rating check that run_company_charter runs
internally, but callable before any RERA number exists for this promoter.

Exists for the common case where a CIN reaches this pipeline before a RERA
number does (a company's registration predates its RERA filing by
definition -- a promoter can be researched the moment their CIN is known).

Writes output/_pending/<CIN>/promoter_profile.json using the exact same
facts.json keys run_company_charter itself uses for these checks
(company_profile_check, ibbi_insolvency_check, group_companies_check,
credit_rating_check) -- so once a RERA number for this same promoter is
known, that record can be merged into a real Charter's facts with a plain
dict update, never a reshape.

    python promoter_intake.py <CIN> [company_name]
"""

import argparse
import json
import os

import company_charter


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the standalone promoter/company checks (MCA-mirror profile, IBBI, "
        "group companies, credit rating) for a bare CIN, with no RERA project required."
    )
    parser.add_argument("cin", help="Company's CIN, e.g. U70109MH2022PLC385473")
    parser.add_argument(
        "company_name", nargs="?", default="",
        help="Optional -- improves Tofler resolution and is required for the credit rating check.",
    )
    parser.add_argument("--output-dir", default=company_charter.config.OUTPUT_ROOT)
    args = parser.parse_args()

    print(f"[INFO] Running standalone promoter intake for CIN {args.cin}...")
    record = company_charter.run_promoter_intake(args.cin, args.company_name, args.output_dir)

    profile_found = record["company_profile_check"].get("found")
    ibbi_found = record["ibbi_insolvency_check"].get("found_process")
    group_count = len(record["group_companies_check"].get("companies") or [])
    print(f"[OK] Company profile: {'found' if profile_found else 'not found'}")
    print(f"[OK] IBBI insolvency check: {'process found' if ibbi_found else ('none found' if ibbi_found is False else 'could not run')}")
    print(f"[OK] Group companies: {group_count} linked entit(y/ies)")
    if "credit_rating_check" in record:
        rated = bool(record["credit_rating_check"]["promoter"].get("ratings"))
        print(f"[OK] Credit rating: {'found' if rated else 'not found'}")
    else:
        print("[INFO] Credit rating check skipped -- no company_name given.")
    if record["gaps"]:
        print(f"[WARN] {len(record['gaps'])} gap(s) recorded (director-roster disagreements, etc.).")

    out_path = os.path.join(args.output_dir, "_pending", args.cin.strip(), "promoter_profile.json")
    print(f"\nWritten to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

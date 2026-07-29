"""
Attaches a pending CIN/CTS-only case (built via promoter_intake.py/
cts_intake.py before a RERA number was known) to a real MahaRERA
registration number once one becomes known for that same promoter/plot.

This is an explicit action, never auto-matched -- same "a human confirms
the link, this never fuzzy-matches" policy as every other identifier-
linking decision in this pipeline (ZaubaCorp's CIN lookup, the CTS
candidate-exact-match check, etc.).

Copies (not moves) output/_pending/<case_id>/promoter_profile.json and/or
land_record.json into output/<reg_no>/ as *_carryover.json. A future
`python main.py <reg_no>` run for this project will then:
  - reuse the carried-over promoter profile instead of re-running the
    live CIN-based checks (company_charter._load_promoter_carryover), and
  - reuse the carried-over land record instead of opening a new browser
    for another CAPTCHA (company_charter.run_cts_land_lookup), cross-
    checking its CTS number against RERA's own record and raising an
    IMMINENT flag on a genuine mismatch rather than a buried gap.

    python attach_rera.py <case_id> <reg_no>
"""

import argparse

import company_charter


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Attach a pending promoter_intake.py/cts_intake.py case to a RERA registration number."
    )
    parser.add_argument("case_id", help="The pending case's directory name under output/_pending/ -- a CIN, or a district_village_cts slug.")
    parser.add_argument("reg_no", help="MahaRERA registration number, e.g. P51800012345")
    parser.add_argument("--output-dir", default=company_charter.config.OUTPUT_ROOT)
    args = parser.parse_args()

    result = company_charter.attach_rera_number(args.case_id, args.reg_no, args.output_dir)
    if not result["attached"]:
        print(f"[ERROR] {result['note']}")
        return 1

    print(f"[OK] {result['note']}")
    if result["had_promoter_profile"]:
        print("       Promoter profile attached -- future runs for this reg_no will reuse it instead of re-fetching.")
    if result["had_land_record"]:
        print("       Land record attached -- future runs for this reg_no will reuse it and cross-check its CTS number.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

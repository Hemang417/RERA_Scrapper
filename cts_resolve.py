"""
Human-in-the-loop chain for resolving a RERA project's CTS number to Maha
Bhulekh's exact Marathi office/village labels, then running the Property
Card fetch -- all scoped to an existing reg_no's output/<reg_no>/ folder.

WHY THIS EXISTS: office/village labels on that site are Marathi-only, with
no reliable automatic match to RERA's own English district/taluka/village
text (confirmed live: office names don't even correspond 1:1 to village
names -- one real project's office was "...,Andheri" while its own village
was "Aambivali", since one office's jurisdiction covers several villages).
So this never guesses a label; a human always picks the exact one from a
real, live-fetched list. What IS automated: `python company_charter.py
<reg_no>` (or run_cts_land_lookup directly) already fetches the office list
for you the moment a district can be resolved from the Charter's own land
data -- see cts_office_candidates.json, written automatically. Everything
below picks up from there.

    python cts_resolve.py offices <reg_no>
        (Normally already done automatically by company_charter.py -- rerun
        by hand only if you need to refresh it.)

    python cts_resolve.py villages <reg_no> "<office label from offices>"

    python cts_resolve.py candidates <reg_no> "<office label>" "<village label>" <cts_number>

    python cts_resolve.py finalize <reg_no> "<office label>" "<village label>" <cts_number> <mobile>
        Writes output/<reg_no>/cts_lookup_input.json -- the next
        `python company_charter.py <reg_no>` run picks this up automatically
        and opens the CAPTCHA-gated Property Card fetch (see
        run_cts_land_lookup in company_charter.py).
"""

import argparse
import json
import os
import sys

import company_charter


def _cmd_offices(args) -> int:
    facts_paths = [
        p for p in (
            os.path.join(args.output_dir, "company_charters", f)
            for f in os.listdir(os.path.join(args.output_dir, "company_charters"))
            if f.endswith(f"_{args.reg_no}.facts.json")
        )
    ] if os.path.isdir(os.path.join(args.output_dir, "company_charters")) else []
    if not facts_paths:
        print(f"[ERROR] No facts.json found for {args.reg_no} under {args.output_dir}/company_charters/ -- "
              f"run `python company_charter.py {args.reg_no}` at least once first.", file=sys.stderr)
        return 1
    with open(facts_paths[0], encoding="utf-8") as f:
        facts = json.load(f)

    result = company_charter.discover_cts_office_candidates(facts, args.reg_no, args.output_dir)
    if not result.get("found"):
        print(f"[WARN] {result.get('note', 'no reason given')}")
        return 1
    print(f"[OK] {len(result['offices'])} office(s) written to output/{args.reg_no}/cts_office_candidates.json")
    print(result["note"])
    return 0


def _cmd_villages(args) -> int:
    result = company_charter.discover_cts_village_candidates(args.reg_no, args.office, args.output_dir)
    if not result.get("found"):
        print(f"[WARN] {result.get('note', 'no reason given')}")
        return 1
    print(f"[OK] {len(result['villages'])} village(s) written to output/{args.reg_no}/cts_village_candidates.json")
    print(result["note"])
    return 0


def _cmd_candidates(args) -> int:
    result = company_charter.discover_cts_number_candidates(args.reg_no, args.office, args.village, args.cts_query, args.output_dir)
    if not result.get("found"):
        print(f"[WARN] {result.get('note', 'no reason given')}")
        return 1
    print(f"[OK] CTS candidate(s) {result['candidates']} written to output/{args.reg_no}/cts_number_candidates.json")
    print(result["note"])
    return 0


def _cmd_finalize(args) -> int:
    project_dir = os.path.join(args.output_dir, args.reg_no)
    os.makedirs(project_dir, exist_ok=True)
    input_path = os.path.join(project_dir, "cts_lookup_input.json")

    office_candidates_path = os.path.join(project_dir, "cts_office_candidates.json")
    if not os.path.exists(office_candidates_path):
        print(f"[ERROR] {office_candidates_path} not found -- run `python cts_resolve.py offices {args.reg_no}` first.", file=sys.stderr)
        return 1
    with open(office_candidates_path, encoding="utf-8") as f:
        district = json.load(f)["district"]

    record = {
        "district": district, "office": args.office, "village": args.village,
        "cts_number": args.cts_number, "mobile": args.mobile,
    }
    with open(input_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    print(f"[OK] Wrote {input_path}")
    print(f"     Next `python company_charter.py {args.reg_no}` run will fetch the Property Card "
          f"(opens a visible browser, needs a fresh CAPTCHA solve).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", default=company_charter.config.OUTPUT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    p_offices = sub.add_parser("offices", help="Re-fetch office candidates for a reg_no (usually already done automatically).")
    p_offices.add_argument("reg_no")
    p_offices.set_defaults(func=_cmd_offices)

    p_villages = sub.add_parser("villages", help="Fetch village candidates for a chosen office.")
    p_villages.add_argument("reg_no")
    p_villages.add_argument("office", help="Exact office label from cts_office_candidates.json")
    p_villages.set_defaults(func=_cmd_villages)

    p_candidates = sub.add_parser("candidates", help="Confirm a CTS number against the site's own list for a chosen village.")
    p_candidates.add_argument("reg_no")
    p_candidates.add_argument("office", help="Exact office label")
    p_candidates.add_argument("village", help="Exact village label from cts_village_candidates.json")
    p_candidates.add_argument("cts_query", help="e.g. 100")
    p_candidates.set_defaults(func=_cmd_candidates)

    p_finalize = sub.add_parser("finalize", help="Write cts_lookup_input.json for the next Charter run to pick up.")
    p_finalize.add_argument("reg_no")
    p_finalize.add_argument("office", help="Exact office label")
    p_finalize.add_argument("village", help="Exact village label")
    p_finalize.add_argument("cts_number", help="Exact CTS number/sub-division from cts_number_candidates.json")
    p_finalize.add_argument("mobile", help="Mobile number to submit on the Property Card form")
    p_finalize.set_defaults(func=_cmd_finalize)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

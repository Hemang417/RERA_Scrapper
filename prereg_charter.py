"""
Runs the automatic Charter-generation pipeline for a project that has NO
RERA registration at all -- e.g. still pre-registration, where only a CTS
land-parcel number and/or the promoter's CIN are known so far.

Skips every RERA-specific stage main.py performs: no state/authority
resolution, no category-data scrape, no complaint-orders/promoter-portfolio
fetch, and no RERA project report (report.build_pdf) -- none of those have
anything to scrape without a registration. Everything else reuses the exact
same functions a normal run does:

- `company_charter.run_promoter_intake` and `run_cts_lookup_standalone` --
  the same standalone entry points `promoter_intake.py` and the CTS-
  carryover path already use for "an identifier reached this pipeline
  before the RERA number did" -- write their results to
  output/_pending/<case_id>/, then `company_charter.attach_rera_number`
  copies them into THIS run's own output/<reg_no>/ tree as
  *_carryover.json. `run_company_charter`'s own internal carryover-loading
  logic (`_load_promoter_carryover`, and `run_cts_land_lookup`'s carryover
  branch) then picks them up automatically -- no check logic is duplicated
  here, and the CIN-driven registry/IBBI/credit-rating/group-companies
  checks and the CTS Property Card fetch are exactly as code-computed and
  gated as they are on a normal run.
- Deep research and the Charter's own facts pass need SOMETHING to search
  for, which a RERA scrape would normally supply as `category_data`. With
  none available, this builds a minimal shim from --project-name /
  --promoter-name instead (falling back to the name the CIN lookup itself
  already resolved, since lookup_company_by_cin returns it for free).

Office/village labels for the CTS lookup are NEVER guessed here either --
same discipline as run_cts_land_lookup's own inline resolution: they are
fetched live from Maha Bhulekh and a human at this terminal picks the exact
one, because Marathi labels have no reliable automatic match to any name
this pipeline might already hold.

    python prereg_charter.py --cin U70109MH2022PLC385473 --project-name "Adarsh CHSL" \\
        --cts-district "Mumbai Suburban" --cts-number 602 --cts-mobile 9999999999

    python prereg_charter.py --promoter-name "Kumar Vibe Properties Private Limited" \\
        --project-name "Kumar Vibe, Bandra"
"""

import argparse
import concurrent.futures
import json
import os
import sys
import time
from datetime import datetime

import company_charter
import config
import deep_research
import states
from main import _run_gst_intake_step


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the automatic Charter pipeline for a project with no RERA registration yet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--project-name", default="", help="The project's name, if it has one yet.")
    parser.add_argument(
        "--promoter-name", default="",
        help="The promoter/developer's legal name. Optional if --cin is given -- it will be "
        "derived from the CIN's own MCA-mirror profile instead.",
    )
    parser.add_argument(
        "--cin", default="",
        help="The promoter's CIN/LLPIN. Required for the company registration profile, IBBI "
        "insolvency check, credit rating check, and group-companies crosswalk -- none of those "
        "have any other way to run, and there is no name-to-CIN lookup in this codebase.",
    )
    cts = parser.add_argument_group("CTS land-record lookup (all three required together, or omit all three)")
    cts.add_argument("--cts-district", default="", help='e.g. "Mumbai Suburban" -- see mahabhumi._DISTRICT_NAME_MAP.')
    cts.add_argument("--cts-number", default="", help='e.g. "602".')
    cts.add_argument("--cts-mobile", default="", help="Mobile number to submit on the Property Card form.")
    gst = parser.add_mutually_exclusive_group()
    gst.add_argument("--gstin", default=None, help="Promoter's GSTIN -- runs the GST filing-history intake.")
    gst.add_argument("--pan", default=None, help="Promoter's PAN -- same GST intake, starting from the PAN.")
    # ON by default, same as main.py's own flags -- pass --no-group-sweep
    # etc. to disable. See main.py's parse_args for the per-flag rationale.
    parser.add_argument("--group-sweep", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--group-gst", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--group-litigation", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--group-enforcement", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--group-financial-disclosure", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-dir", default=config.OUTPUT_ROOT)
    args = parser.parse_args()

    if not (args.project_name or args.promoter_name or args.cin):
        parser.error("give at least one of --project-name, --promoter-name, --cin -- there must be "
                     "something to identify this engagement by.")
    cts_given = (args.cts_district, args.cts_number, args.cts_mobile)
    if any(cts_given) and not all(cts_given):
        parser.error("--cts-district, --cts-number and --cts-mobile must be given together, or not at all.")
    return args


def _resolve_cts_office_and_village(district: str) -> tuple[str, str] | None:
    """Interactively picks a real office then a real village for `district`,
    exactly the same never-guess discipline as
    company_charter._interactively_resolve_cts_lookup. Returns None (never
    guessing, never raising) the moment a human isn't actually available to
    pick, or a step comes back empty/ambiguous."""
    if not sys.stdin.isatty():
        print("[WARN] No terminal available to pick a CTS office/village by hand -- skipping the "
              "land-record lookup this pass. Re-run interactively, or use cts_resolve.py once a "
              "RERA number for this project exists.")
        return None

    import mahabhumi

    offices_result = mahabhumi.list_offices(district)
    if not offices_result.get("found"):
        print(f"[WARN] {offices_result.get('note', 'Office lookup failed')}")
        return None
    offices = offices_result.get("offices") or []
    if not offices:
        print(f"[WARN] No Maha Bhulekh offices found for {district!r}.")
        return None

    office_idx = company_charter._prompt_choice(
        f"{len(offices)} Maha Bhulekh office(s) found for {district}. Pick the one covering this "
        f"project's plot:", offices,
    )
    if office_idx is None:
        return None
    office = offices[office_idx]

    try:
        villages_result = mahabhumi.list_villages(district, office)
    except mahabhumi.AmbiguousSelectionError as e:
        print(f"[WARN] {e}")
        return None
    if not villages_result.get("found"):
        print(f"[WARN] {villages_result.get('note', 'Village lookup failed')}")
        return None
    villages = villages_result.get("villages") or []
    if not villages:
        print(f"[WARN] No villages found under {office!r}.")
        return None

    village_idx = company_charter._prompt_choice(f"Pick the village under {office!r}:", villages)
    if village_idx is None:
        return None
    return office, villages[village_idx]


def main() -> int:
    pipeline_start_time = time.time()
    args = parse_args()
    output_dir = args.output_dir

    # project_name/promoter_name/cin can't all be empty (parse_args already
    # enforced that), but a --cin-only run with neither name given would
    # otherwise slugify down to just "PREREG" -- not unique across runs.
    slug_parts = [args.project_name or args.promoter_name or args.cin]
    if args.cts_number:
        slug_parts.append(f"CTS{args.cts_number}")
    slug_parts.append("PREREG")
    reg_no = company_charter._slugify_for_pending_key(*slug_parts).upper()
    print(f"[INFO] No RERA registration for this engagement -- using synthetic identifier {reg_no!r}.")

    project_out_dir = os.path.join(output_dir, reg_no)
    os.makedirs(project_out_dir, exist_ok=True)

    deep_research.reset_usage_log()

    promoter_name = args.promoter_name

    # CIN-driven checks: company registration profile, IBBI insolvency,
    # group-companies crosswalk, credit rating (if a name is available).
    # Reuses run_promoter_intake exactly as promoter_intake.py's own CLI
    # does, then attach_rera_number folds the result into THIS reg_no's
    # output tree so run_company_charter's own _load_promoter_carryover
    # picks it up -- no check logic duplicated here.
    if args.cin:
        print(f"\n[INFO] Running promoter intake for CIN {args.cin}...")
        promoter_record = company_charter.run_promoter_intake(args.cin, promoter_name, output_dir)
        derived_name = (promoter_record.get("company_profile_check") or {}).get("name")
        profile_found = promoter_record["company_profile_check"].get("found")
        print(f"[OK] Company profile: {'found' if profile_found else 'not found'}"
              + (f" -- {derived_name}" if derived_name else ""))
        if not promoter_name and derived_name:
            promoter_name = derived_name
            print(f"[INFO] Promoter name derived from the CIN's own MCA-mirror profile: {promoter_name!r}")
        attach_result = company_charter.attach_rera_number(args.cin.strip(), reg_no, output_dir)
        if not attach_result["attached"]:
            print(f"[WARN] {attach_result['note']}")

    # CTS land-record lookup: office/village always picked from a live list
    # by a human at this terminal, never guessed (see
    # _resolve_cts_office_and_village's own docstring).
    if args.cts_district:
        print(f"\n[INFO] Resolving CTS {args.cts_number} in {args.cts_district}...")
        picked = _resolve_cts_office_and_village(args.cts_district)
        if picked is not None:
            office, village = picked
            cts_record = company_charter.run_cts_lookup_standalone(
                args.cts_district, office, village, args.cts_number, args.cts_mobile, output_dir,
            )
            case_id = company_charter._slugify_for_pending_key(args.cts_district, village, args.cts_number)
            attach_result = company_charter.attach_rera_number(case_id, reg_no, output_dir)
            if not attach_result["attached"]:
                print(f"[WARN] {attach_result['note']}")
            elif cts_record["cts_land_record_check"].get("found"):
                print("[OK] CTS Property Card fetched.")
            else:
                print(f"[WARN] {cts_record['cts_land_record_check'].get('note', 'CTS lookup did not find a record.')}")

    gst_status = _run_gst_intake_step(args.gstin or args.pan, reg_no, output_dir)

    # Deep research and the Charter's own facts pass have no RERA-scraped
    # category_data to search from -- this is the minimal substitute, built
    # from whatever identity is actually known. Anything not given here is
    # left for _run_charter_pass's own prompt (see run_company_charter) to
    # mark honestly, e.g. "project name not yet known".
    category_data = {
        "projects": {
            "project_name": args.project_name or "(not yet named -- pre-RERA-registration engagement)",
            "note": (
                "No RERA registration exists yet for this project. This is a pre-registration "
                "engagement -- treat only the fields given here as confirmed, and mark every "
                "RERA-specific fact honestly as Not applicable / Not registered rather than "
                "inferring or fabricating one."
            ),
        },
        "partners": {
            "promoter_name": promoter_name or "(not yet known)",
            "cin_llpin": args.cin or "",
        },
    }

    print("\n[INFO] Starting agentic deep research (market + promoter profile) in the background...")
    _research_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    _research_future = _research_executor.submit(
        deep_research.run_deep_research, reg_no, category_data, output_dir,
    )
    _research_outcome = {"data": None, "reported": False}

    def _await_deep_research() -> dict | None:
        if _research_outcome["reported"]:
            return _research_outcome["data"]
        try:
            data = _research_future.result()
            gap_count = sum(len(data.get(key, {}).get("gaps", [])) for key in deep_research.RESEARCH_KEYS)
            print(f"[OK] Deep research complete ({gap_count} unresolved gap(s) across all sections).")
            _research_outcome["data"] = data
        except Exception as e:
            print(f"[WARN] Deep research failed ({e}) -- continuing without it.")
            _research_outcome["data"] = None
        finally:
            _research_outcome["reported"] = True
        return _research_outcome["data"]

    research_data = _await_deep_research() if _research_future.done() else None

    print("\n[INFO] Generating Company Charter...")
    charter_path = None
    try:
        # mahabhumi (CTS) and the IGR registered-deed check are both
        # Maharashtra-only -- state_profile is pinned here rather than left
        # to states.get_profile(None)'s own Maharashtra default, so that
        # stays true even after this codebase adds a CTS-equivalent land
        # registry for another state.
        charter_path, charter_facts = company_charter.run_company_charter(
            reg_no, category_data, [], "", research_data, output_dir,
            state_profile=states.get_profile("MH"), pipeline_start_time=pipeline_start_time,
            group_sweep=args.group_sweep, group_gst=args.group_gst,
            group_litigation=args.group_litigation, group_enforcement=args.group_enforcement,
            group_financial_disclosure=args.group_financial_disclosure,
            research_wait=_await_deep_research,
        )
        external_charter_path = charter_path.replace("_Internal.docx", "_External.docx")
        print(f"[OK] Company Charter (Internal) written to {charter_path}")
        print(f"[OK] Company Charter (External) written to {external_charter_path}")
    except Exception as e:
        print(f"[WARN] Company Charter generation failed ({e}) -- continuing without it.")
    finally:
        research_data = _await_deep_research()
        _research_executor.shutdown(wait=False)

    with open(os.path.join(project_out_dir, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "reg_no": reg_no,
                "mode": "pre_registration",
                "state": "MH",
                "project_name": args.project_name,
                "promoter_name": promoter_name,
                "cin": args.cin,
                "company_charter_path": charter_path,
                "generated_at": datetime.now().isoformat(),
            },
            f, indent=2, ensure_ascii=False,
        )

    print("\n" + "=" * 60)
    print("  Run summary")
    print("=" * 60)
    print(f"  {'reg_no':<20} {reg_no}")
    print(f"  {'promoter_name':<20} {promoter_name or 'not known'}")
    print(f"  {'cin':<20} {args.cin or 'not given -- registry/IBBI/credit-rating/group checks skipped'}")
    print(f"  {'market_research':<20} {'populated' if research_data else 'FAILED this run -- see warning above, or retry with: python deep_research.py ' + reg_no}")
    print(f"  {'gst_filing_intake':<20} {gst_status}")
    print(f"  {'company_charter':<20} {'written (' + charter_path + ')' if charter_path else 'FAILED this run -- see warning above, or retry with: python company_charter.py ' + reg_no}")
    print("  rera_project_report  not applicable -- no RERA registration to report on")

    usage = deep_research.write_usage_log(output_dir, reg_no)
    total = usage["total"]
    print(f"  {'claude_api_usage':<20} {total['calls']} call(s), {total['input_tokens'] + total['output_tokens']:,} token(s), ${total['cost_usd']:.4f}")
    print(f"  (full breakdown: {os.path.join(project_out_dir, 'usage_summary.json')})")

    return 0 if charter_path else 1


if __name__ == "__main__":
    sys.exit(main())

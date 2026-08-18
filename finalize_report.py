"""
Rebuilds <REG_NO>_summary.pdf entirely from what's already saved on disk --
zero network calls, no Playwright, no CAPTCHA. Useful whenever new sections
(research/deep_research.json, promoter/portfolio.json) get added after the
initial `python main.py <query>` run, or just to re-render after tweaking
report.py's layout.

    python finalize_report.py <REG_NO>
"""

import argparse
import json
import os
import sys

import config
import states
import report


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_category_data(raw_dir: str, category_order: list | None = None) -> dict:
    """Reads output/<reg_no>/raw/*.json back into the same {category: data}
    shape main.py holds in memory during a live run -- a saved failure
    sentinel ({"_error": ..., "status_code": ...}, written by
    api_client.fetch_all_categories) is converted back to None, matching
    that convention."""
    category_data = {}
    for category in (category_order if category_order is not None else config.CATEGORY_ORDER):
        data = _load_json(os.path.join(raw_dir, f"{category}.json"))
        if isinstance(data, dict) and "_error" in data:
            category_data[category] = None
        else:
            category_data[category] = data
    return category_data


def _load_charter_facts(reg_no: str, output_dir: str) -> dict | None:
    """Reads back the Charter's own <project>_<reg_no>.facts.json -- matched
    by reg_no suffix (like run_archive.archive_previous_charter) since the
    project_name segment in the filename isn't known ahead of time."""
    charter_dir = os.path.join(output_dir, "company_charters")
    if not os.path.isdir(charter_dir):
        return None
    matches = [
        f for f in os.listdir(charter_dir)
        if f.startswith("Company_Charter_") and f.endswith(f"_{reg_no}.facts.json")
    ]
    if not matches:
        return None
    return _load_json(os.path.join(charter_dir, matches[0]))


def rebuild(reg_no: str, output_dir: str = config.OUTPUT_ROOT) -> str:
    project_out_dir = os.path.join(output_dir, reg_no)
    raw_dir = os.path.join(project_out_dir, "raw")

    if not os.path.isdir(raw_dir):
        raise FileNotFoundError(f"No raw data found at {raw_dir} -- run `python main.py {reg_no}` first.")

    category_data = load_category_data(raw_dir)

    run_meta = _load_json(os.path.join(project_out_dir, "run_meta.json")) or {}
    project_id = run_meta.get("project_id", "")
    auth_source = run_meta.get("auth_source")
    # Which authority this run was about. Absent in every tree written before
    # the state field existed -- get_profile(None) reads that as Maharashtra,
    # which is what those runs were.
    profile = states.get_profile(run_meta.get("state"))

    documents_manifest = _load_json(os.path.join(project_out_dir, "documents_manifest.json")) or []
    promoter_portfolio = _load_json(os.path.join(project_out_dir, "promoter", "portfolio.json"))
    research_data = _load_json(os.path.join(project_out_dir, "research", "deep_research.json"))
    charter_facts = _load_charter_facts(reg_no, output_dir)

    pdf_path = os.path.join(project_out_dir, f"{reg_no}_summary.pdf")
    report.build_pdf(
        reg_no,
        project_id,
        category_data,
        documents_manifest,
        pdf_path,
        auth_source=auth_source,
        promoter_portfolio=promoter_portfolio,
        research_data=research_data,
        charter_facts=charter_facts,
        state_profile=profile,
    )
    return pdf_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild a MahaRERA project's PDF report entirely from saved output/ data -- zero network calls."
    )
    parser.add_argument("reg_no", help="MahaRERA registration number whose output/ folder already exists.")
    parser.add_argument(
        "--output-dir", default=config.OUTPUT_ROOT, help=f"Root output directory (default: {config.OUTPUT_ROOT})"
    )
    args = parser.parse_args()

    try:
        pdf_path = rebuild(args.reg_no, args.output_dir)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return 1

    print(f"[OK] Rebuilt {pdf_path}")
    project_out_dir = os.path.join(args.output_dir, args.reg_no)
    if not os.path.exists(os.path.join(project_out_dir, "research", "deep_research.json")):
        print("[INFO] No research/deep_research.json found -- Market Research section will show as not yet generated.")
    if not os.path.exists(os.path.join(project_out_dir, "promoter", "portfolio.json")):
        print("[INFO] No promoter/portfolio.json found -- Promoter Profile section will show as not available.")
    if _load_charter_facts(args.reg_no, args.output_dir) is None:
        print("[INFO] No Company Charter facts.json found -- Company Charter Highlights section will show as not available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

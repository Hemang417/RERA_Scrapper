"""
Archives a project's existing output/<reg_no>/ folder before a re-run
overwrites it, so two runs of the same project can be diffed instead of the
second one silently clobbering the first. Also hands back pieces of the
prior run (its deep_research.json, its documents_manifest.json + documents/
folder) so a re-run can reuse already-confirmed work instead of redoing it
from zero -- see deep_research.py's `prior_research` param and
api_client.download_documents()'s `prior_manifest`/`prior_documents_dir`.

Call order in a run, always in this sequence:
    prior_research = load_prior_research(reg_no, output_dir)
    archive_dir = archive_previous_run(reg_no, output_dir)   # moves the old folder out
    ... proceed with a fresh output/<reg_no>/ ...
    prior_manifest = load_prior_manifest(archive_dir)         # read back from the archive
"""

import json
import os
import shutil
from datetime import datetime

import config


def _project_out_dir(reg_no: str, output_dir: str) -> str:
    return os.path.join(output_dir, reg_no)


def load_prior_research(reg_no: str, output_dir: str = config.OUTPUT_ROOT) -> dict | None:
    """Reads the about-to-be-archived research/deep_research.json, if any --
    must be called BEFORE archive_previous_run() moves it out from under
    this path."""
    path = os.path.join(_project_out_dir(reg_no, output_dir), "research", "deep_research.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def archive_previous_run(reg_no: str, output_dir: str = config.OUTPUT_ROOT) -> str | None:
    """If output/<reg_no>/ already has content from a prior run, moves the
    whole thing to output/_history/<reg_no>/<timestamp>/ before the caller
    starts writing a fresh run. Returns the archive path, or None if there
    was nothing to archive (first run for this project)."""
    project_out_dir = _project_out_dir(reg_no, output_dir)
    if not os.path.isdir(project_out_dir) or not os.listdir(project_out_dir):
        return None

    history_root = os.path.join(output_dir, "_history", reg_no)
    os.makedirs(history_root, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = os.path.join(history_root, timestamp)
    suffix = 2
    while os.path.exists(archive_dir):
        archive_dir = os.path.join(history_root, f"{timestamp}_{suffix}")
        suffix += 1

    shutil.move(project_out_dir, archive_dir)
    return archive_dir


def load_prior_manifest(archive_dir: str | None) -> list[dict] | None:
    """Reads back documents_manifest.json from an archive_previous_run() result."""
    if not archive_dir:
        return None
    path = os.path.join(archive_dir, "documents_manifest.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def prior_documents_dir(archive_dir: str | None) -> str | None:
    if not archive_dir:
        return None
    path = os.path.join(archive_dir, "documents")
    return path if os.path.isdir(path) else None


def archive_previous_charter(reg_no: str, output_dir: str = config.OUTPUT_ROOT) -> str | None:
    """Same idea as archive_previous_run, but for a project's Company Charter
    docx + facts.json. These live in output/company_charters/, separate from
    output/<reg_no>/, and were previously never archived -- only ever
    overwritten on each re-run -- so there was no prior snapshot to diff
    fields like mortgage_lender against. Matches by reg_no suffix rather
    than an exact filename since the project_name segment can change
    between runs (e.g. a RERA record renaming a project)."""
    charter_dir = os.path.join(output_dir, "company_charters")
    if not os.path.isdir(charter_dir):
        return None
    existing = [
        f for f in os.listdir(charter_dir)
        if f.startswith("Company_Charter_") and f.endswith(f"_{reg_no}.facts.json")
    ]
    if not existing:
        return None

    history_root = os.path.join(charter_dir, "_history", reg_no)
    os.makedirs(history_root, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = os.path.join(history_root, timestamp)
    suffix = 2
    while os.path.exists(archive_dir):
        archive_dir = os.path.join(history_root, f"{timestamp}_{suffix}")
        suffix += 1
    os.makedirs(archive_dir)

    stem = existing[0][: -len(".facts.json")]
    for ext in (".docx", ".facts.json"):
        src = os.path.join(charter_dir, stem + ext)
        if os.path.exists(src):
            shutil.move(src, os.path.join(archive_dir, stem + ext))
    return archive_dir


def load_prior_charter_facts(archive_dir: str | None) -> dict | None:
    """Reads back the prior Company Charter's facts.json from an
    archive_previous_charter() result."""
    if not archive_dir:
        return None
    matches = [f for f in os.listdir(archive_dir) if f.endswith(".facts.json")]
    if not matches:
        return None
    with open(os.path.join(archive_dir, matches[0]), "r", encoding="utf-8") as f:
        return json.load(f)


def load_prior_complaint_orders_manifest(archive_dir: str | None) -> list[dict] | None:
    """Same idea as load_prior_manifest, for complaint_orders_manifest.json
    (see api_client.download_complaint_orders)."""
    if not archive_dir:
        return None
    path = os.path.join(archive_dir, "complaint_orders_manifest.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def prior_complaint_orders_dir(archive_dir: str | None) -> str | None:
    if not archive_dir:
        return None
    path = os.path.join(archive_dir, "complaint_orders")
    return path if os.path.isdir(path) else None

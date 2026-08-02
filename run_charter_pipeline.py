"""
Two-phase driver for the reusable Company Charter research pipeline.

WHY TWO PHASES, NOT ONE FUNCTION: spawning a web-research agent is a tool
call only an LLM orchestrator can make (Claude Code, or whatever hosts it).
A plain Python script cannot do it. So this module cannot "just run" the
whole pipeline end to end -- it exposes the two halves that plain Python
CAN do, with the orchestrator responsible for the one step in between that
it can't:

  1. prepare(...)  -- reads facts.json, builds the director roster
                       deterministically, and returns ready-to-send research
                       prompts. Pure Python, no tool calls.
  2. <ORCHESTRATOR> -- spawns one Agent tool call per prompt returned by
                       prepare() (they are independent -- send them in
                       parallel), and collects each agent's final text
                       reply. This step happens in the Claude Code session
                       driving this pipeline, not inside this file.
  3. finish(...)   -- parses each collected reply's JSON, assembles the
                       `research` dict, and calls
                       charter_report.build_charter_report() once per
                       doc_variant. Pure Python, no tool calls.

RECIPE for the orchestrator (interactive session or hosted agent):

    from run_charter_pipeline import prepare, finish

    prep_result = prepare(
        facts_path="output/company_charters/Company_Charter_X.facts.json",
        company_name="...",            # or read straight from facts if you'd rather
        project_context="One sentence naming the project and its location.",
    )

    # For each entry in prep_result["prompts"]["directors"] and the single
    # prep_result["prompts"]["group"] prompt, spawn one Agent tool call
    # (general-purpose subagent, WebSearch/WebFetch access) with that exact
    # prompt text. Collect each one's final response text.

    responses = {
        "directors": {"Director Name": "<agent's final text>", ...},
        "group": "<agent's final text>",
    }

    finish(
        prep_result=prep_result,
        responses=responses,
        reg_no="P51800077150",
        out_dir="output/company_charters",
        file_stub="Company_Charter_X",
        generated_on="30 July 2026",
    )
    # -> writes {file_stub}_Internal.docx and {file_stub}_External.docx

No project-specific content lives in this file -- every string that ends up
in a prompt or a filename is a parameter or comes from the facts.json passed
in. Convert to PDF the same way the first engagement did (Word COM
automation, or LibreOffice headless if available) as a separate step after
finish() returns; that conversion has nothing project-specific in it either
and doesn't need to live here.
"""

from __future__ import annotations

import json

import charter_report as cr
import charter_research_prep as prep
import company_charter as cc


def prepare(facts_path: str, company_name: str, project_context: str,
            past_director_link_threshold: int = prep.DEFAULT_PAST_DIRECTOR_LINK_THRESHOLD) -> dict:
    """Loads facts.json and returns everything the orchestrator needs to
    spawn research agents: the roster (for reference/logging) and a
    `prompts` dict shaped {"group": str, "directors": {name: str}}."""
    with open(facts_path, encoding="utf-8") as f:
        facts = json.load(f)

    is_partnership = cc._is_partnership(facts)
    roster = prep.build_roster(facts, past_director_link_threshold)

    director_prompts = {
        d["name"]: prep.build_director_prompt(d, company_name, project_context, is_partnership=is_partnership)
        for d in roster["current"]
    }
    for d in roster["material_past"]:
        director_prompts.setdefault(
            d["name"] + " (past director)",
            prep.build_director_prompt(d, company_name, project_context, is_partnership=is_partnership),
        )

    group_prompt = prep.build_group_prompt(facts, company_name, roster["material_past"])

    return {
        "facts_path": facts_path,
        "facts": facts,
        "roster": roster,
        "is_partnership": is_partnership,
        "prompts": {"group": group_prompt, "directors": director_prompts},
    }


def finish(prep_result: dict, responses: dict, reg_no: str, out_dir: str, file_stub: str, generated_on: str) -> dict:
    """Parses the orchestrator-collected agent responses, assembles the
    `research` dict, and builds both Internal and External docx variants.
    Returns {"internal": path, "external": path}."""
    roster = prep_result["roster"]
    facts = prep_result["facts"]

    director_json_by_name = {}
    for name, text in (responses.get("directors") or {}).items():
        clean_name = name.replace(" (past director)", "")
        director_json_by_name[clean_name] = prep.parse_agent_json(text)

    group_text = responses.get("group") or ""
    group_json = prep.parse_agent_json(group_text)

    research = prep.assemble_research(roster, director_json_by_name, group_json, is_partnership=prep_result.get("is_partnership", False))

    out_paths = {}
    for variant in ("internal", "external"):
        out_path = f"{out_dir}/{file_stub}_{variant.capitalize()}.docx"
        variant_facts = json.loads(json.dumps(facts))
        cr.build_charter_report(reg_no, variant_facts, research, out_path, variant, generated_on)
        out_paths[variant] = out_path
    return out_paths

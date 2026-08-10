"""
Agentic Claude-API research pass. Produces output/<reg_no>/research/deep_research.json
in the exact shape report.py already renders (macro_market/micro_market/promoter_external,
each: summary, sections[], sources[], gaps[]) -- see report.py's _render_research_block.

Guardrails baked into this pipeline, not left to prompting alone:
  1. Every claim must carry an inline [S1]-style marker resolving to a `sources` entry
     (the exact JSON shape is embedded in the system prompt; _parse_json_response
     rejects/raises on anything that isn't valid JSON in that shape).
  2. Every claim is re-checked by an independent verifier pass (_verify_block) before
     it's accepted -- unconfirmed/stale claims are demoted to `gaps`, never silently kept.
  3. Any remaining gap gets a bounded retry (_resolve_gaps) using a *different* search
     strategy per attempt, not a repeat of the same query. After the budget is spent,
     a gap stays a gap, annotated with what was tried -- it never gets papered over.

Structured output note: client.beta.messages.tool_runner() returns a
BetaToolRunner -- an ITERATOR yielding one BetaMessage per turn, not a
Message-like object itself (it has no .content). We only give it the
web_search server tool (which the runner executes automatically server-side
-- no handler needed); getting a *custom* tool call reliably would need a
real @beta_tool-decorated Python function (raw tool-schema dicts only
auto-execute for recognized server tools), which doesn't fit our deeply
nested schema well. Simpler and just as reliable: iterate the runner to its
final message and require the model's last turn to be a bare JSON object
matching the schema embedded in the system prompt below.

Requires: pip install -U anthropic  (needs client.beta.messages.tool_runner)
          ANTHROPIC_API_KEY set in the environment.

    python deep_research.py <REG_NO>          # generate + rebuild the PDF
    python deep_research.py <REG_NO> --no-rebuild
"""

import argparse
import json
import os
import sys
from datetime import datetime

from anthropic import Anthropic

import config
import finalize_report

MODEL = "claude-sonnet-5"
MAX_GAP_RETRY_ATTEMPTS = 2

# Bounded fan-out for the per-finding research stage, same precedent as
# MAX_GAP_RETRY_ATTEMPTS above. One agentic web-search call per finding is the
# whole point of the stage, so the cost scales with how much a project actually
# has to report -- a cap keeps an unusually messy project from turning one
# Charter run into dozens of calls without anyone noticing. Findings beyond the
# cap keep their original text rather than being dropped.
MAX_FINDING_RESEARCH_CALLS = 8

# $ per 1M tokens. This is Sonnet 5's intro rate, active through 2026-08-31 --
# update to the standard $3.00/$15.00 after that date (or sooner, if pricing
# changes again). Only priced models get a real cost figure back from
# _record_usage; an unrecognized model still gets its tokens counted, just
# with cost_usd=0.0 rather than a silently wrong number.
_PRICING_PER_1M_TOKENS = {
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
}

# Every Claude API call this whole pipeline makes (both this module's own
# research/verification passes and company_charter.py's Charter-assembly and
# claim-verification passes, which delegate here) funnels through
# _run_agentic_pass, so this one module-level list is a complete usage log
# for the process -- see _record_usage. Callers scope it to one project run
# via reset_usage_log()/write_usage_log(); nothing here assumes a single
# project per process, since app.py's Streamlit UI can run several in a row.
_USAGE_LOG: list[dict] = []


def reset_usage_log() -> None:
    """Clears the in-process usage log -- callers (main.py, app.py) call
    this right before starting a single project's run, so that run's
    write_usage_log() reports only its own calls, not a prior project's
    left over from the same long-lived process."""
    _USAGE_LOG.clear()


def get_usage_log() -> list[dict]:
    return list(_USAGE_LOG)


def usage_summary() -> dict:
    """Aggregates the current usage log by label (e.g. "charter_pass",
    "verify_claim", "gap_retry") plus a grand total across all of them --
    the per-label breakdown is what actually explains where cost goes,
    since a handful of small calls (one per cited source needing
    re-verification) dominates far more than the one big Charter-assembly
    pass."""
    by_label = {}
    for record in _USAGE_LOG:
        bucket = by_label.setdefault(record["label"], {
            "calls": 0, "turns": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
        })
        bucket["calls"] += 1
        bucket["turns"] += record["turns"]
        bucket["input_tokens"] += record["input_tokens"]
        bucket["output_tokens"] += record["output_tokens"]
        bucket["cost_usd"] = round(bucket["cost_usd"] + record["cost_usd"], 6)

    total = {
        "calls": len(_USAGE_LOG),
        "turns": sum(r["turns"] for r in _USAGE_LOG),
        "input_tokens": sum(r["input_tokens"] for r in _USAGE_LOG),
        "output_tokens": sum(r["output_tokens"] for r in _USAGE_LOG),
        "cost_usd": round(sum(r["cost_usd"] for r in _USAGE_LOG), 6),
    }
    return {"by_label": by_label, "total": total}


def write_usage_log(output_dir: str, reg_no: str) -> dict:
    """Writes output/<reg_no>/usage_summary.json (this run's own breakdown)
    and appends one rollup line to output/usage_log.jsonl (every run, ever,
    in this output tree) -- the per-project file answers "what did this
    project cost", the rollup answers "what has this pipeline cost in
    total". Returns the summary dict written for the project."""
    summary = usage_summary()
    summary["reg_no"] = reg_no
    summary["generated_at"] = datetime.now().isoformat()

    project_dir = os.path.join(output_dir, reg_no)
    os.makedirs(project_dir, exist_ok=True)
    with open(os.path.join(project_dir, "usage_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    rollup_path = os.path.join(output_dir, "usage_log.jsonl")
    with open(rollup_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "reg_no": reg_no,
            "generated_at": summary["generated_at"],
            "total": summary["total"],
        }, ensure_ascii=False) + "\n")

    return summary


def _record_usage(label: str, model: str, messages: list) -> dict:
    """Sums usage across every BetaMessage a tool_runner pass yielded, not
    just the final one -- each turn (including every web_search round-trip)
    is its own billed API call, so counting only the last message would
    silently undercount every multi-turn pass."""
    input_tokens = 0
    output_tokens = 0
    for message in messages:
        usage = getattr(message, "usage", None)
        if usage is None:
            continue
        input_tokens += getattr(usage, "input_tokens", 0) or 0
        output_tokens += getattr(usage, "output_tokens", 0) or 0

    price = _PRICING_PER_1M_TOKENS.get(model, {"input": 0.0, "output": 0.0})
    cost_usd = (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000

    record = {
        "label": label,
        "model": model,
        "turns": len(messages),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
    }
    _USAGE_LOG.append(record)
    return record
# A same-day (or so) re-run trusts the prior pass's already-`confirmed`
# sources as-is (no re-verification) and only spends new API calls on gaps
# that were still open -- see run_deep_research's `prior_research` param.
# Older than this, a re-run is treated as fully stale and redone from zero,
# since macro/micro market claims and promoter reputation can genuinely
# change over weeks/months.
RESEARCH_REUSE_WINDOW_HOURS = 24
GAP_RETRY_STRATEGIES = [
    "a different source type than whatever was already tried (e.g. a regulatory/registry "
    "filing or official disclosure instead of general web search)",
    "a related-party angle instead of the entity name directly (parent company, group "
    "entities, joint development partner, or the project's own official channels)",
]

RESEARCH_KEYS = ("macro_market", "micro_market", "promoter_external")

_RESEARCH_BLOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["heading", "body"],
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "url": {"type": "string"},
                    "publisher": {"type": "string"},
                    "accessed_date": {"type": "string"},
                },
                "required": ["claim", "url"],
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "sections", "sources", "gaps"],
}

_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}

_RESEARCH_JSON_SHAPE = json.dumps({key: _RESEARCH_BLOCK_SCHEMA for key in RESEARCH_KEYS})

_SYSTEM_PROMPT = f"""You are producing research for a MahaRERA real-estate project report: \
macro market research, micro/locality market research, and an external promoter \
reputation profile.

Rules:
- Every factual sentence in `summary` or a section's `body` MUST carry an inline \
citation marker like [S1], and every marker MUST resolve to a matching entry in that \
block's `sources` array ({{claim, url, publisher, accessed_date}}).
- Never state a claim you cannot cite. If you try multiple sources and still cannot \
confirm something material, put a one-line description of it in `gaps` instead of \
guessing or approximating.
- Do not conflate similarly-named companies. Cross-check any corporate/CIN claim \
against the promoter's registered legal name and address before accepting it.
- For any computed/numeric claim (FSI, area, distance), show the inputs and formula \
in the body text, not just the final number.
- Use web_search as many times as you need across multiple turns. Once you have \
everything you need, your FINAL reply must be ONLY a single raw JSON object -- no \
prose, no markdown code fences, nothing before or after it -- matching exactly this \
JSON Schema: {_RESEARCH_JSON_SHAPE}"""

_VERIFY_SYSTEM_PROMPT = """Re-check one specific claim against its cited source using \
web_search as needed -- fetch/search to confirm whether the source genuinely supports \
the claim as stated, not just that the source exists.

Your FINAL reply must be ONLY a single raw JSON object -- no prose, no markdown code \
fences -- matching exactly this shape: \
{"status": "confirmed" | "unsupported" | "stale", "reason": "one sentence"}"""

_FINDING_RESEARCH_SYSTEM_PROMPT = """Research ONE confirmed finding from a real estate \
due-diligence report in depth, using web_search, and write it out properly.

The finding has already been established as real. Your job is not to re-confirm that it \
exists, and not to look for other findings. Resolve these five things about this one:
  1. what it is, precisely,
  2. who is involved (name the actual parties, not "an adjoining society"),
  3. when it arose,
  4. whether it is still live or has been disposed of, extinguished or closed,
  5. what it means for this specific project.

Worked example of the difference. "A 2017 Notice of Lis Pendens against an adjoining \
society" is the input. A proper output resolves WHICH society, under WHAT suit and in \
which court, whether that suit is still pending, and whether the land it concerns \
actually touches this project's plot boundary.

Rules for what you write:
  * Never assert something web_search did not establish. If a sub-question cannot be \
resolved, say so plainly in one clause and move on; do not guess, and do not pad.
  * If research adds nothing to what the input already said, return the input's substance \
unchanged rather than inventing detail.
  * Report what was found. Do not write a sentence whose only content is that something \
was searched for and not found.
  * Plain language. Expand an abbreviation on first use.
  * No em dashes, and no double hyphen used as a dash. Use commas, colons or a single \
spaced hyphen.

Your FINAL reply must be ONLY a single raw JSON object -- no prose, no markdown code \
fences -- matching exactly this shape: \
{"resolved": true | false, "text": "the rewritten finding, 2 to 5 sentences", \
"still_live": "yes" | "no" | "unknown", "note": "one sentence on what could not be resolved, or empty"}"""

_GAP_RETRY_SYSTEM_PROMPT = """Try to resolve one specific research gap using web_search, \
following the given retry strategy (a different angle than what already failed -- do not \
just repeat the same search).

Your FINAL reply must be ONLY a single raw JSON object -- no prose, no markdown code \
fences -- matching exactly this shape: {"sections": [{"heading": "...", "body": "..."}], \
"sources": [{"claim": "...", "url": "...", "publisher": "...", "accessed_date": "..."}]} \
-- use empty arrays for both if you still can't confirm anything."""

_client = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


def _parse_json_response(message) -> dict:
    """message is a BetaMessage (one item yielded by iterating a BetaToolRunner --
    the runner itself is not a message and has no .content)."""
    text = "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", None) == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"model did not return valid JSON: {e}\nRaw text (first 500 chars): {text[:500]}") from e


def _run_agentic_pass(user_prompt: str, system: str, label: str = "agentic_pass") -> dict:
    """Runs client.beta.messages.tool_runner() to completion. The runner is an
    iterator yielding one BetaMessage per turn -- iterate it fully and read
    .content off the LAST yielded message, not off the runner itself.

    `label` identifies what this call was FOR (e.g. "charter_pass",
    "verify_claim", "gap_retry") in the usage log -- every Claude call in
    this whole pipeline funnels through here (including company_charter.py's,
    which delegates to this function), so the label is what lets
    usage_summary() break cost down by purpose instead of one undifferentiated
    total."""
    runner = _get_client().beta.messages.tool_runner(
        model=MODEL,
        max_tokens=8000,
        system=system,
        tools=[_WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": user_prompt}],
    )
    messages = list(runner)
    if not messages:
        raise RuntimeError("tool_runner produced no messages")
    _record_usage(label, MODEL, messages)
    return _parse_json_response(messages[-1])


def research_finding(finding: str, context: str = "", label: str = "finding_research") -> dict:
    """Researches one confirmed finding in depth (CLAUDE.md Section B, "Deep
    research on every finding"). Returns
    {"resolved": bool, "text": str, "still_live": str, "note": str}.

    Degrades the same way _verify_claim does, and for the same reason: any
    failure -- no API key, a rate limit, malformed JSON -- comes back as
    resolved=False with the ORIGINAL finding text intact. The caller keeps
    that original. An auth failure must never silently delete a finding,
    which is the one outcome that would be worse than not running at all.

    `context` is the surrounding project detail (name, promoter, location) so
    the search can disambiguate; a Notice of Lis Pendens means nothing without
    knowing which plot it is being researched against."""
    prompt = f"Finding to research in depth:\n{finding}"
    if context.strip():
        prompt += f"\n\nProject context (for disambiguation only, not itself the finding):\n{context.strip()}"
    try:
        result = _run_agentic_pass(prompt, _FINDING_RESEARCH_SYSTEM_PROMPT, label=label)
    except Exception as e:
        return {"resolved": False, "text": finding, "still_live": "unknown",
                "note": f"deeper research could not run: {e}"}

    text = (result.get("text") or "").strip()
    if not result.get("resolved") or not text:
        return {"resolved": False, "text": finding, "still_live": result.get("still_live", "unknown"),
                "note": (result.get("note") or "").strip()}
    return {
        "resolved": True,
        "text": text,
        "still_live": result.get("still_live", "unknown"),
        "note": (result.get("note") or "").strip(),
    }


def _verify_claim(claim: str, source_url: str, label: str = "verify_claim") -> dict:
    """Returns {"status": ..., "reason": ...}. Status is "confirmed",
    "unsupported", or "stale" for a check that actually ran, or the distinct
    "verification_error" when the check itself could not be attempted at all
    (missing ANTHROPIC_API_KEY, network failure, rate limit, bad JSON reply).
    That distinction matters to callers: a claim that failed a real check is
    demoted with its value discarded, but a claim we simply couldn't check
    must not be treated the same way -- silently skipping it would let the
    unverified claim stand unflagged, while discarding its value would wrongly
    punish a possibly-correct fact just because the checker itself broke.
    Broad except is deliberate here -- any failure mode must surface as
    "verification_error", never crash the caller or masquerade as a real
    unsupported verdict."""
    prompt = f"Claim: {claim}\nCited source: {source_url}"
    try:
        result = _run_agentic_pass(prompt, _VERIFY_SYSTEM_PROMPT, label=label)
    except Exception as e:
        return {"status": "verification_error", "reason": f"verification could not run: {e}"}
    if result.get("status") not in ("confirmed", "unsupported", "stale"):
        return {"status": "unsupported", "reason": "verifier returned an unrecognized status"}
    return result


def _verify_block(block: dict, label: str = "verify_claim") -> dict:
    """Re-checks every cited source independently. A source that failed a
    real check is demoted into `gaps` (never dropped silently) so
    _resolve_gaps gets a chance to retry it. A source whose check could not
    even run ("verification_error") is kept as-is rather than discarded --
    see _verify_claim's docstring -- but still gets an explicit, honest gap
    noting it was never actually independently re-checked this pass."""
    kept_sources = []
    demoted_gaps = list(block.get("gaps", []))
    for src in block.get("sources", []):
        verdict = _verify_claim(src.get("claim", ""), src.get("url", ""), label=label)
        status = verdict.get("status")
        if status == "confirmed":
            kept_sources.append(src)
        elif status == "verification_error":
            kept_sources.append(src)
            demoted_gaps.append(
                f"{src.get('claim', '')} (NOT independently re-verified this pass -- {verdict.get('reason', 'verification error')})"
            )
        else:
            demoted_gaps.append(f"{src.get('claim', '')} (verification: {verdict.get('reason', 'unconfirmed')})")
    block["sources"] = kept_sources
    block["gaps"] = demoted_gaps
    return block


def _resolve_gaps(block: dict) -> dict:
    """Bounded retry per gap, each attempt using a different strategy than the last.
    A gap that survives every attempt stays in `gaps`, annotated with what was tried --
    it is never fabricated away just to make the list shorter."""
    still_open = []
    for gap in block.get("gaps", []):
        attempts_log = []
        resolved = False
        for strategy in GAP_RETRY_STRATEGIES[:MAX_GAP_RETRY_ATTEMPTS]:
            prompt = (
                f"Earlier research could not confirm this: {gap}\n"
                f"Already tried: {attempts_log or ['the direct/default search approach']}\n"
                f"Retry using {strategy}."
            )
            try:
                result = _run_agentic_pass(prompt, _GAP_RETRY_SYSTEM_PROMPT, label="gap_retry")
            except Exception:
                # Broad on purpose, same reasoning as _verify_claim: a missing
                # ANTHROPIC_API_KEY or any other failure here must count as a
                # failed retry attempt, not crash the whole research pass.
                result = {}
            attempts_log.append(strategy)
            if result.get("sources"):
                verified = _verify_block({"sources": result["sources"], "gaps": []}, label="gap_retry_verify")
                if verified["sources"]:
                    block.setdefault("sections", []).extend(result.get("sections", []))
                    block.setdefault("sources", []).extend(verified["sources"])
                    resolved = True
                    break
        if not resolved:
            still_open.append(f"{gap} (retried {len(attempts_log)} alternate approach(es), not confirmed)")
    block["gaps"] = still_open
    return block


def _prior_research_age_hours(prior_research: dict | None) -> float | None:
    if not prior_research or not prior_research.get("_generated_at"):
        return None
    try:
        generated_at = datetime.fromisoformat(prior_research["_generated_at"])
    except ValueError:
        return None
    return (datetime.now() - generated_at).total_seconds() / 3600


def run_deep_research(
    reg_no: str,
    category_data: dict,
    output_dir: str = config.OUTPUT_ROOT,
    prior_research: dict | None = None,
) -> dict:
    """prior_research, if given, is a same-project deep_research.json from an
    earlier run (see run_archive.load_prior_research). If it's recent enough
    (RESEARCH_REUSE_WINDOW_HOURS), its already-`confirmed` sources are reused
    as-is -- no re-verification -- and the only new API calls spent are on
    gaps that were still open. A block with zero gaps last time is carried
    forward completely untouched, at zero cost. Older/missing prior_research
    falls back to the full generate+verify pass exactly as before."""
    age_hours = _prior_research_age_hours(prior_research)
    reusable = age_hours is not None and age_hours <= RESEARCH_REUSE_WINDOW_HOURS

    research = {}
    if reusable:
        for key in RESEARCH_KEYS:
            prior_block = prior_research.get(key) or {}
            block = {
                "summary": prior_block.get("summary", ""),
                "sections": list(prior_block.get("sections", [])),
                "sources": list(prior_block.get("sources", [])),
                "gaps": list(prior_block.get("gaps", [])),
            }
            if block["gaps"]:
                block = _resolve_gaps(block)
            research[key] = block
    else:
        projects = category_data.get("projects") or {}
        partners = category_data.get("partners") or {}
        user_prompt = (
            f"MahaRERA registration: {reg_no}\n"
            f"Project data (JSON): {json.dumps(projects)}\n"
            f"Promoter/partner data (JSON): {json.dumps(partners)}\n\n"
            f"Produce macro_market, micro_market, and promoter_external research blocks for "
            f"this project."
        )
        raw = _run_agentic_pass(user_prompt, _SYSTEM_PROMPT, label="research_generate")
        for key in RESEARCH_KEYS:
            block = _verify_block(raw.get(key, {}))
            block = _resolve_gaps(block)
            research[key] = block

    research["_generated_at"] = datetime.now().isoformat()
    research["_reused_prior"] = reusable

    out_dir = os.path.join(output_dir, reg_no, "research")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "deep_research.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(research, f, indent=2, ensure_ascii=False)
    return research


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the agentic deep-research pass for an already-scraped project and rebuild its PDF."
    )
    parser.add_argument("reg_no", help="MahaRERA registration number whose output/ folder already exists.")
    parser.add_argument("--output-dir", default=config.OUTPUT_ROOT)
    parser.add_argument("--no-rebuild", action="store_true", help="Write deep_research.json only, skip the PDF rebuild.")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help=f"Ignore any existing deep_research.json for this project and redo the full pass, "
        f"even if it's within the {RESEARCH_REUSE_WINDOW_HOURS}h reuse window.",
    )
    args = parser.parse_args()

    raw_dir = os.path.join(args.output_dir, args.reg_no, "raw")
    if not os.path.isdir(raw_dir):
        print(f"[ERROR] No raw data found at {raw_dir} -- run `python main.py {args.reg_no}` first.")
        return 1

    category_data = finalize_report.load_category_data(raw_dir)

    prior_research = None
    if not args.force_refresh:
        prior_path = os.path.join(args.output_dir, args.reg_no, "research", "deep_research.json")
        if os.path.exists(prior_path):
            with open(prior_path, "r", encoding="utf-8") as f:
                prior_research = json.load(f)

    print(f"[..] Running agentic research for {args.reg_no} (model={MODEL})")
    research = run_deep_research(args.reg_no, category_data, args.output_dir, prior_research=prior_research)
    for key in RESEARCH_KEYS:
        gaps = research[key].get("gaps", [])
        print(f"[OK] {key}: {len(research[key].get('sources', []))} confirmed source(s), {len(gaps)} unresolved gap(s)")

    if not args.no_rebuild:
        pdf_path = finalize_report.rebuild(args.reg_no, args.output_dir)
        print(f"[OK] Rebuilt {pdf_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

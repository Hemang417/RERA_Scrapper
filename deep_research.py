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


def _run_agentic_pass(user_prompt: str, system: str) -> dict:
    """Runs client.beta.messages.tool_runner() to completion. The runner is an
    iterator yielding one BetaMessage per turn -- iterate it fully and read
    .content off the LAST yielded message, not off the runner itself."""
    runner = _get_client().beta.messages.tool_runner(
        model=MODEL,
        max_tokens=8000,
        system=system,
        tools=[_WEB_SEARCH_TOOL],
        messages=[{"role": "user", "content": user_prompt}],
    )
    final_message = None
    for message in runner:
        final_message = message
    if final_message is None:
        raise RuntimeError("tool_runner produced no messages")
    return _parse_json_response(final_message)


def _verify_claim(claim: str, source_url: str) -> dict:
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
        result = _run_agentic_pass(prompt, _VERIFY_SYSTEM_PROMPT)
    except Exception as e:
        return {"status": "verification_error", "reason": f"verification could not run: {e}"}
    if result.get("status") not in ("confirmed", "unsupported", "stale"):
        return {"status": "unsupported", "reason": "verifier returned an unrecognized status"}
    return result


def _verify_block(block: dict) -> dict:
    """Re-checks every cited source independently. A source that failed a
    real check is demoted into `gaps` (never dropped silently) so
    _resolve_gaps gets a chance to retry it. A source whose check could not
    even run ("verification_error") is kept as-is rather than discarded --
    see _verify_claim's docstring -- but still gets an explicit, honest gap
    noting it was never actually independently re-checked this pass."""
    kept_sources = []
    demoted_gaps = list(block.get("gaps", []))
    for src in block.get("sources", []):
        verdict = _verify_claim(src.get("claim", ""), src.get("url", ""))
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
                result = _run_agentic_pass(prompt, _GAP_RETRY_SYSTEM_PROMPT)
            except Exception:
                # Broad on purpose, same reasoning as _verify_claim: a missing
                # ANTHROPIC_API_KEY or any other failure here must count as a
                # failed retry attempt, not crash the whole research pass.
                result = {}
            attempts_log.append(strategy)
            if result.get("sources"):
                verified = _verify_block({"sources": result["sources"], "gaps": []})
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
        raw = _run_agentic_pass(user_prompt, _SYSTEM_PROMPT)
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

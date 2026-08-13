"""The web_search tool is opt-in, and bounded when it is on.

Why this file exists. `_run_agentic_pass` used to attach the web_search server
tool to every call it made. Six of the twelve callers only judge text they were
already handed -- they classify sentences, match claims to sources, write
headlines, audit a rendered document, check citation completeness, check a claim
against document text supplied in the prompt. None of them has anything to look
up. Carrying the tool anyway gave every one of them the failure that killed the
Charter assembly pass three times over: search results are billed against the
same output budget as the reply, so a call can spend the lot searching and stop
with `stop_reason="max_tokens"` having written nothing at all.

So the tests here are behavioural, not textual. They run each caller through a
fake client and look at the `tools` argument that actually reached the API.
Asserting on the source text would pass just as happily against code that
builds the tool list somewhere else.
"""

import json
import os
import tempfile

import pytest

import company_charter
import deep_research


class _FakeUsage:
    def __init__(self):
        self.input_tokens = 100
        self.output_tokens = 50


class _FakeBlock:
    def __init__(self, kind, text=""):
        self.type = kind
        self.text = text


class _FakeMessage:
    def __init__(self, text="", stop_reason="end_turn"):
        self.usage = _FakeUsage()
        self.stop_reason = stop_reason
        self.content = [_FakeBlock("text", text)] if text else [_FakeBlock("server_tool_use")]


class _FakeStream:
    """What a streaming tool_runner yields: not a message, but a stream whose
    get_final_message() resolves to one. Reading it twice would be a bug, so
    this refuses the second read rather than quietly allowing it."""

    def __init__(self, message):
        self._message = message
        self.reads = 0

    def get_final_message(self):
        self.reads += 1
        assert self.reads == 1, "the stream was resolved more than once"
        return self._message


class _Recorder:
    """Stands in for the Anthropic client and remembers every tool_runner call.

    `replies` is consumed one per call, so a test can make the first attempt
    exhaust its budget and the second succeed -- which is exactly the retry
    path being tested."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = self

    @property
    def messages(self):
        return self

    def tool_runner(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0) if self.replies else _FakeMessage("{}")
        if kwargs.get("stream"):
            return iter([_FakeStream(reply)])
        return iter([reply])

    @property
    def tools_sent(self):
        return [call.get("tools") for call in self.calls]


@pytest.fixture
def recorder(monkeypatch):
    """Installs a recording client and clears the usage log, which is module
    state that would otherwise leak between tests."""
    def _install(replies=None):
        rec = _Recorder(replies or [_FakeMessage(json.dumps({"ok": True}))])
        monkeypatch.setattr(deep_research, "_get_client", lambda: rec)
        monkeypatch.setattr(deep_research, "_USAGE_LOG", [])
        return rec
    return _install


# ---------------------------------------------------------------------------
# The transport itself
# ---------------------------------------------------------------------------

def test_search_is_off_unless_asked_for(recorder):
    rec = recorder()
    deep_research._run_agentic_pass("prompt", "system", label="t")
    assert rec.tools_sent == [[]], (
        "the default call must carry no tools at all; a tool the model cannot "
        "see is a tool it cannot spend its reply budget on"
    )


def test_search_when_asked_for_is_bounded(recorder):
    rec = recorder()
    deep_research._run_agentic_pass("prompt", "system", label="t", search=True)
    tools = rec.tools_sent[0]
    assert len(tools) == 1
    assert tools[0]["type"] == "web_search_20260209"
    assert tools[0]["max_uses"] == deep_research.DEFAULT_MAX_SEARCHES, (
        "max_uses must be set: it is enforced by the API, whereas an instruction "
        "in the prompt to stop searching is just more text the model can talk "
        "itself past, which is how one pass reached 27 searches"
    )


def test_caller_can_set_its_own_search_budget(recorder):
    rec = recorder()
    deep_research._run_agentic_pass("p", "s", label="t", search=True, max_searches=3)
    assert rec.tools_sent[0][0]["max_uses"] == 3


# ---------------------------------------------------------------------------
# Transport: blocking below the non-streaming ceiling, streaming above it
# ---------------------------------------------------------------------------

def test_a_small_call_does_not_stream(recorder):
    """Every call proven live on 2026-08-11 was a blocking one. Switching them
    all to streaming would discard that evidence for no benefit."""
    rec = recorder()
    deep_research._run_agentic_pass("p", "s", label="t",
                                    max_tokens=deep_research.MAX_NONSTREAMING_TOKENS)
    assert "stream" not in rec.calls[0]


def test_a_call_needing_more_room_streams_instead_of_being_refused(recorder):
    """It used to raise ValueError here. The SDK's 21,333 limit is a property
    of asking for one blocking response, not of the model, and the Charter pass
    demonstrably cannot fit under it."""
    rec = recorder()
    result = deep_research._run_agentic_pass(
        "p", "s", label="t", max_tokens=deep_research.MAX_NONSTREAMING_TOKENS + 1)
    assert rec.calls[0]["stream"] is True
    assert result == {"ok": True}, "a streamed reply must parse the same as a blocking one"


def test_the_charter_pass_takes_the_streaming_path(recorder):
    rec = recorder()
    company_charter._run_charter_pass("some project data")
    assert rec.calls[0]["stream"] is True
    assert rec.calls[0]["max_tokens"] == deep_research.CHARTER_PASS_MAX_TOKENS


def test_streamed_usage_is_still_recorded(recorder):
    """_record_usage reads .usage off a message. Handed an unresolved stream it
    would silently record zero, and the cost of the most expensive call in the
    pipeline would vanish from the usage log."""
    recorder()
    deep_research._run_agentic_pass("p", "s", label="big",
                                    max_tokens=deep_research.MAX_NONSTREAMING_TOKENS + 1)
    assert deep_research._USAGE_LOG[0]["output_tokens"] == 50


def test_asking_past_the_model_ceiling_is_still_refused(recorder):
    recorder()
    with pytest.raises(ValueError, match="output ceiling"):
        deep_research._run_agentic_pass("p", "s", label="t",
                                        max_tokens=deep_research.MAX_STREAMING_TOKENS + 1)


def test_the_charter_budget_fits_between_the_two_ceilings():
    assert (deep_research.MAX_NONSTREAMING_TOKENS
            < deep_research.CHARTER_PASS_MAX_TOKENS
            <= deep_research.MAX_STREAMING_TOKENS), (
        "the charter budget must need streaming and be reachable by it; a value "
        "below the first ceiling silently gives back the room this change bought"
    )


def test_bounding_the_tool_does_not_mutate_the_shared_constant():
    before = dict(deep_research._WEB_SEARCH_TOOL)
    deep_research._web_search_tool(4)
    assert deep_research._WEB_SEARCH_TOOL == before
    assert "max_uses" not in deep_research._WEB_SEARCH_TOOL


# ---------------------------------------------------------------------------
# Budget exhaustion: recognised, and retried once
# ---------------------------------------------------------------------------

def test_exhausted_budget_raises_its_own_error_not_a_parse_error(recorder):
    recorder([_FakeMessage("", stop_reason="max_tokens")])
    with pytest.raises(deep_research.BudgetExhausted) as excinfo:
        deep_research._run_agentic_pass("p", "s", label="t")
    assert "output budget" in str(excinfo.value)


def test_budget_exhausted_is_still_caught_by_existing_degradation_paths():
    """Every caller in this codebase degrades through `except Exception`. If
    BudgetExhausted did not inherit from RuntimeError, this one failure would
    start crashing runs that used to survive it."""
    assert issubclass(deep_research.BudgetExhausted, RuntimeError)


# ---------------------------------------------------------------------------
# A hard ceiling on the WHOLE run's spend, not just one stage
# ---------------------------------------------------------------------------

def _spend(amount):
    deep_research._USAGE_LOG.append({
        "label": "prior", "model": deep_research.MODEL, "turns": 1,
        "input_tokens": 0, "cache_write_tokens": 0, "cache_read_tokens": 0,
        "output_tokens": 0, "cost_usd": amount,
    })


def test_a_call_is_refused_once_the_pipeline_cost_cap_is_reached(recorder):
    rec = recorder()
    _spend(deep_research.PIPELINE_COST_CAP_USD)

    with pytest.raises(deep_research.CostCapExceeded):
        deep_research._run_agentic_pass("p", "s", label="t")
    assert not rec.calls, "a refused call must never reach the transport at all"


def test_a_call_under_the_cap_still_proceeds(recorder):
    rec = recorder()
    _spend(deep_research.PIPELINE_COST_CAP_USD - 1)

    deep_research._run_agentic_pass("p", "s", label="t")
    assert rec.calls, "spend below the cap must not block a call"


def test_cost_cap_exceeded_is_not_retried(recorder):
    """Unlike BudgetExhausted, an overspent run doesn't get cheaper with a
    smaller search budget -- the cap must not trigger the retry path."""
    rec = recorder()
    _spend(deep_research.PIPELINE_COST_CAP_USD)

    with pytest.raises(deep_research.CostCapExceeded):
        deep_research._run_agentic_pass("p", "s", label="t", search=True, max_searches=8)
    assert not rec.calls, "no attempt, and therefore no retry, may reach the transport"


def test_cost_cap_exceeded_is_still_caught_by_existing_degradation_paths():
    """deep research and Charter generation are both [never fatal] stages in
    main.py; if this didn't inherit from RuntimeError, hitting the cap would
    start crashing the whole pipeline run instead of just that one stage."""
    assert issubclass(deep_research.CostCapExceeded, RuntimeError)


def test_the_cap_check_reads_real_usage_not_a_stale_snapshot(recorder):
    """The check must call usage_summary() fresh each time, not cache a total
    computed once -- otherwise spend from calls made after the cap object
    was created would go uncounted."""
    rec = recorder()
    deep_research._run_agentic_pass("p", "s", label="t")  # cheap, doesn't trip the cap
    assert rec.calls, "a call made before any spend must still succeed"
    _spend(deep_research.PIPELINE_COST_CAP_USD)
    with pytest.raises(deep_research.CostCapExceeded):
        deep_research._run_agentic_pass("p", "s", label="t2")


def test_a_searching_call_that_runs_out_of_room_retries_with_fewer_searches(recorder):
    rec = recorder([
        _FakeMessage("", stop_reason="max_tokens"),
        _FakeMessage(json.dumps({"recovered": True})),
    ])
    result = deep_research._run_agentic_pass("p", "s", label="t", search=True, max_searches=8)

    assert result == {"recovered": True}
    assert len(rec.calls) == 2, "one retry, not a loop"
    assert rec.tools_sent[1][0]["max_uses"] == deep_research.RETRY_MAX_SEARCHES
    assert "at most 2 searches" in rec.calls[1]["messages"][0]["content"]


def test_the_retry_is_billed_under_its_own_label(recorder):
    recorder([
        _FakeMessage("", stop_reason="max_tokens"),
        _FakeMessage(json.dumps({"ok": True})),
    ])
    deep_research._run_agentic_pass("p", "s", label="charter_pass", search=True, max_searches=8)
    labels = [r["label"] for r in deep_research._USAGE_LOG]
    assert labels == ["charter_pass", "charter_pass_retry"], (
        "a retry costs real money and must show up separately in the usage log, "
        "or the cost of this failure mode stays invisible"
    )


def test_a_non_searching_call_that_runs_out_of_room_is_not_retried(recorder):
    rec = recorder([_FakeMessage("", stop_reason="max_tokens")])
    with pytest.raises(deep_research.BudgetExhausted):
        deep_research._run_agentic_pass("p", "s", label="t")
    assert len(rec.calls) == 1, (
        "with no search tool attached, the budget went on the reply itself; "
        "retrying it unchanged would just spend the money again"
    )


def test_an_already_minimal_search_budget_is_not_retried(recorder):
    rec = recorder([_FakeMessage("", stop_reason="max_tokens")])
    with pytest.raises(deep_research.BudgetExhausted):
        deep_research._run_agentic_pass("p", "s", label="t", search=True,
                                        max_searches=deep_research.RETRY_MAX_SEARCHES)
    assert len(rec.calls) == 1


def test_malformed_json_still_raises_the_plain_error(recorder):
    recorder([_FakeMessage("this is not json")])
    with pytest.raises(RuntimeError) as excinfo:
        deep_research._run_agentic_pass("p", "s", label="t")
    assert not isinstance(excinfo.value, deep_research.BudgetExhausted)


# ---------------------------------------------------------------------------
# A correct answer wrapped in commentary is still a correct answer
# ---------------------------------------------------------------------------

# Copied from a real reply, on the smoke-test run of 2026-08-11. Every system
# prompt here demands a bare JSON object; the model emitted a sentence, a blank
# line, and then the object. json.loads rejected the lot and the pass was
# recorded as "verification could not run" -- with the right answer inside it.
_LIVE_PROSE_THEN_JSON = (
    'The search directly confirms the claim from the official source: "The Government '
    'of Maharashtra has established the Maharashtra Real Estate Regulatory Authority '
    '(MahaRERA), vide Notification No. 23 dated 8th March 2017, for regulation and '
    'promotion of real estate sector in the State."\n\n'
    '{"status": "confirmed", "reason": "The official MahaRERA website confirms it was '
    'established by the Government of Maharashtra as the real estate regulatory '
    'authority for the state."}'
)


def test_a_reply_wrapped_in_commentary_is_still_read(recorder):
    recorder([_FakeMessage(_LIVE_PROSE_THEN_JSON)])
    result = deep_research._run_agentic_pass("p", "s", label="t")
    assert result["status"] == "confirmed"


def test_the_verifier_recovers_from_that_same_reply(recorder):
    """The end-to-end version: this exact reply used to come back as
    verification_error, which demotes a confirmed claim into a gap."""
    recorder([_FakeMessage(_LIVE_PROSE_THEN_JSON)])
    verdict = deep_research._verify_claim("a claim", "http://example.com")
    assert verdict["status"] == "confirmed"


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('Here you go:\n{"a": 1}', {"a": 1}),
    ('{"a": 1}\nHope that helps.', {"a": 1}),
    ('{"outer": {"inner": 1}}', {"outer": {"inner": 1}}),
    ('note: use {braces} carefully. {"a": 1}', {"a": 1}),
    ('{"first": 1} and also {"second": 2, "third": 3}', {"second": 2, "third": 3}),
])
def test_embedded_json_extraction(text, expected):
    assert deep_research._extract_json_object(text) == expected


@pytest.mark.parametrize("text", ["", "no json here at all", "{not json}", "[1, 2, 3]"])
def test_extraction_returns_none_rather_than_guessing(text):
    assert deep_research._extract_json_object(text) is None


def test_extraction_prefers_the_outer_object_over_a_nested_one():
    """The nested object parses perfectly well on its own. Returning it would
    silently hand the caller a fragment of the answer instead of the answer."""
    got = deep_research._extract_json_object(
        'reply: {"status": "confirmed", "detail": {"source": "x"}}')
    assert got == {"status": "confirmed", "detail": {"source": "x"}}


def test_extraction_does_not_rescue_genuinely_broken_json(recorder):
    """Recovery must not become a way for a truncated reply to look fine."""
    recorder([_FakeMessage('{"status": "confirmed", "reason": "unterminat')])
    with pytest.raises(RuntimeError):
        deep_research._run_agentic_pass("p", "s", label="t")


# ---------------------------------------------------------------------------
# Every caller, by name: does it get the tool, and should it?
# ---------------------------------------------------------------------------

def _judgement_callers():
    """Calls that judge text they were already handed. None may search."""
    return {
        "clean_check_judge": lambda: deep_research.classify_clean_checks(["No litigation."]),
        "citation_match": lambda: deep_research.match_claims_to_sources(["c"], ["s"]),
        "flag_headline": lambda: deep_research.write_flag_headlines(["a gap"]),
        "claude_md_doc_review": lambda: deep_research.review_document_against_rules(
            "doc text", "rules text", "External"),
        "document_grounding_verify": lambda: company_charter._verify_document_claim(
            "a claim", "extracted document text"),
    }


def _research_callers():
    """Calls whose entire job is to go and find something out."""
    return {
        "finding_research": lambda: deep_research.research_finding("a finding"),
        "verify_claim": lambda: deep_research._verify_claim("a claim", "http://example.com"),
        "second_source_verify": lambda: company_charter._attempt_second_source(
            "litigation", {"label": "X", "ref": "http://example.com"}),
        "charter_pass": lambda: company_charter._run_charter_pass("some project data"),
    }


@pytest.mark.parametrize("name", sorted(_judgement_callers()))
def test_judgement_calls_get_no_search_tool(recorder, name):
    rec = recorder([_FakeMessage(json.dumps({
        "verdicts": [], "matches": [], "headlines": [], "violations": [],
        "compliant": True, "summary": "", "status": "confirmed", "found": False,
    }))])
    _judgement_callers()[name]()
    assert rec.calls, f"{name} did not reach the transport at all"
    assert rec.tools_sent[0] == [], (
        f"{name} judges text it was already given and has nothing to look up, "
        f"so it must not carry the web_search tool"
    )


@pytest.mark.parametrize("name", sorted(_research_callers()))
def test_research_calls_do_get_a_bounded_search_tool(recorder, name):
    rec = recorder([_FakeMessage(json.dumps({
        "resolved": True, "text": "researched text", "still_live": "no", "note": "",
        "status": "confirmed", "reason": "ok", "found": False,
    }))])
    _research_callers()[name]()
    assert rec.calls, f"{name} did not reach the transport at all"
    tools = rec.tools_sent[0]
    assert len(tools) == 1 and tools[0]["name"] == "web_search", (
        f"{name} exists to find something out; without the tool it would answer "
        f"from memory, which is worse than failing"
    )
    assert isinstance(tools[0].get("max_uses"), int) and tools[0]["max_uses"] > 0


def test_citation_completeness_judge_gets_no_search_tool(recorder):
    """Kept out of the parametrised set because it needs a real .docx on disk:
    it reads the rendered document rather than taking text as an argument."""
    import docx

    rec = recorder([_FakeMessage(json.dumps({"uncited_claims": []}))])
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "external.docx")
        doc = docx.Document()
        doc.add_paragraph("The promoter was incorporated in 2011.")
        doc.save(path)
        company_charter._llm_verify_citation_completeness(path)

    assert rec.calls
    assert rec.tools_sent[0] == []


def test_the_charter_pass_search_budget_is_actually_capped(recorder):
    rec = recorder()
    company_charter._run_charter_pass("some project data")
    assert rec.tools_sent[0][0]["max_uses"] == deep_research.CHARTER_PASS_MAX_SEARCHES
    assert deep_research.CHARTER_PASS_MAX_SEARCHES < 27, (
        "27 is what an unbounded charter pass actually spent before dying with "
        "an empty reply; the cap exists to sit well under that"
    )


def test_the_charter_prompt_states_the_same_limit_the_api_enforces():
    """A prompt telling the model to search freely, under an API cap that says
    otherwise, wastes the searches it has on the assumption of more to come."""
    assert (f"HARD LIMIT of {deep_research.CHARTER_PASS_MAX_SEARCHES} web searches"
            in company_charter._SYSTEM_PROMPT)
    assert "as many times as you need" not in company_charter._SYSTEM_PROMPT
    assert (f"HARD LIMIT of {deep_research.RESEARCH_MAX_SEARCHES} web searches"
            in deep_research._SYSTEM_PROMPT)
    assert "as many times as you need" not in deep_research._SYSTEM_PROMPT


def test_no_search_budget_exceeds_what_the_token_budget_can_pay_for():
    """A sanity bound rather than a precise one: each search result is charged
    against max_tokens, so a search budget has to leave room for a reply."""
    for name in ("DEFAULT_MAX_SEARCHES", "RESEARCH_MAX_SEARCHES",
                 "CHARTER_PASS_MAX_SEARCHES", "RETRY_MAX_SEARCHES"):
        value = getattr(deep_research, name)
        assert 0 < value <= 15, f"{name}={value} is outside the range this design assumes"


# ---------------------------------------------------------------------------
# The verify/gap-retry fan-out is batched, and shares a budget
#
# _verify_block used to call _verify_claim once per source, and _resolve_gaps
# once per gap per attempt -- neither loop had a limit of its own on how many
# sources or gaps a research block produces. _verify_claims_batch and
# _retry_gaps_batch check/retry many at once in a single call sharing one
# search budget, and a _VerificationBudget shared across both bounds the
# total number of those batched calls; see MAX_RESEARCH_VERIFICATION_CALLS.
# ---------------------------------------------------------------------------

def test_verify_block_batches_sources_under_the_chunk_size_into_one_call(recorder):
    rec = recorder([_FakeMessage(json.dumps({"verdicts": [
        {"id": i, "status": "confirmed", "reason": "ok"} for i in range(5)
    ]}))])
    block = {"sources": [{"claim": f"claim {i}", "url": "http://x"} for i in range(5)], "gaps": []}

    result = deep_research._verify_block(block)

    assert len(rec.calls) == 1, "5 sources under the chunk size must fit in one call, not five"
    assert len(result["sources"]) == 5
    assert result["gaps"] == []


def test_verify_block_chunks_a_block_larger_than_the_chunk_size(recorder, monkeypatch):
    monkeypatch.setattr(deep_research, "BATCH_VERIFY_CHUNK_SIZE", 2)
    rec = recorder([
        _FakeMessage(json.dumps({"verdicts": [{"id": 0, "status": "confirmed", "reason": "ok"},
                                              {"id": 1, "status": "confirmed", "reason": "ok"}]})),
        _FakeMessage(json.dumps({"verdicts": [{"id": 0, "status": "confirmed", "reason": "ok"}]})),
    ])
    block = {"sources": [{"claim": f"claim {i}", "url": "http://x"} for i in range(3)], "gaps": []}

    result = deep_research._verify_block(block)

    assert len(rec.calls) == 2, "3 sources over a chunk size of 2 must take two calls, not three"
    assert len(result["sources"]) == 3
    assert result["gaps"] == []


def test_verify_block_stops_spending_once_the_shared_budget_is_gone(recorder, monkeypatch):
    monkeypatch.setattr(deep_research, "BATCH_VERIFY_CHUNK_SIZE", 2)
    rec = recorder([_FakeMessage(json.dumps({"verdicts": [
        {"id": 0, "status": "confirmed", "reason": "ok"},
        {"id": 1, "status": "confirmed", "reason": "ok"},
    ]}))])
    budget = deep_research._VerificationBudget(limit=1)
    block = {"sources": [{"claim": f"claim {i}", "url": "http://x"} for i in range(5)], "gaps": []}

    result = deep_research._verify_block(block, budget=budget)

    assert len(rec.calls) == 1, "only the first chunk's call may spend the one-call budget"
    assert len(result["sources"]) == 5, "a source past the budget is kept, never dropped"
    assert len(result["gaps"]) == 3, (
        "the two chunks the budget couldn't cover are annotated, not silently skipped"
    )
    assert all("budget exhausted" in g for g in result["gaps"])


def test_resolve_gaps_batches_every_pending_gap_into_one_call_per_round(recorder):
    rec = recorder([_FakeMessage(json.dumps({"results": []}))
                    for _ in range(deep_research.MAX_GAP_RETRY_ATTEMPTS)])
    block = {"gaps": [f"gap {i}" for i in range(4)]}

    result = deep_research._resolve_gaps(block)

    assert len(rec.calls) == deep_research.MAX_GAP_RETRY_ATTEMPTS, (
        "one batched call per retry round, covering all 4 gaps together -- not one call per gap"
    )
    assert len(result["gaps"]) == 4, "nothing is dropped, only annotated"
    assert all(f"retried {deep_research.MAX_GAP_RETRY_ATTEMPTS} alternate approach" in g
              for g in result["gaps"])


def test_resolve_gaps_stops_retrying_once_the_shared_budget_is_gone(recorder):
    rec = recorder([_FakeMessage(json.dumps({"results": []}))])
    budget = deep_research._VerificationBudget(limit=1)
    block = {"gaps": [f"gap {i}" for i in range(4)]}

    result = deep_research._resolve_gaps(block, budget=budget)

    assert len(rec.calls) == 1, "only the first round's batched call may spend the one-call budget"
    assert len(result["gaps"]) == 4, "nothing is dropped"
    assert all("retried 1 alternate approach" in g for g in result["gaps"])


def test_gap_retry_and_its_verify_draw_from_the_same_shared_budget(recorder):
    """A round that finds sources spends a second batched call verifying
    them -- that must count against the same budget as the retry call
    itself, or a project whose gaps all happen to resolve could still spend
    unboundedly."""
    rec = recorder([
        _FakeMessage(json.dumps({"results": [
            {"id": 0, "sections": [{"heading": "h", "body": "b"}],
             "sources": [{"claim": "c", "url": "http://x"}]},
        ]})),
        _FakeMessage(json.dumps({"verdicts": [{"id": 0, "status": "confirmed", "reason": "ok"}]})),
    ])
    budget = deep_research._VerificationBudget(limit=2)
    block = {"gaps": ["one gap"]}

    result = deep_research._resolve_gaps(block, budget=budget)

    assert len(rec.calls) == 2, "one batched gap-retry call plus one batched verify call on its result"
    assert budget.remaining == 0
    assert result["gaps"] == [], "the gap resolved once its source verified as confirmed"
    assert len(result["sources"]) == 1


def test_a_fresh_budget_defaults_to_the_module_constant():
    assert deep_research._VerificationBudget().remaining == deep_research.MAX_RESEARCH_VERIFICATION_CALLS


def test_run_deep_research_shares_one_budget_across_all_three_blocks(recorder, monkeypatch):
    """A project whose research legitimately turns up many claims across all
    three blocks (macro market, micro market, promoter profile) must still be
    bounded by ONE total, not one allowance per block -- otherwise the total
    spend still scales with project size, just with a bigger constant."""
    monkeypatch.setattr(deep_research, "MAX_RESEARCH_VERIFICATION_CALLS", 4)
    monkeypatch.setattr(deep_research, "BATCH_VERIFY_CHUNK_SIZE", 2)

    def block(n):
        return {
            "summary": "s", "sections": [],
            "sources": [{"claim": f"c{i}", "url": "http://x"} for i in range(n)],
            "gaps": [],
        }

    generate_reply = _FakeMessage(json.dumps({
        "macro_market": block(3), "micro_market": block(3), "promoter_external": block(3),
    }))
    rec = recorder([generate_reply] + [
        _FakeMessage(json.dumps({"verdicts": [{"id": 0, "status": "confirmed", "reason": "ok"}]}))
        for _ in range(20)
    ])

    with tempfile.TemporaryDirectory() as tmp:
        deep_research.run_deep_research("P00000000000", {}, output_dir=tmp)

    # Each block's 3 sources need 2 chunk-calls at chunk size 2 (2 then 1) --
    # 6 verify calls total if unbounded. The shared budget caps it at 4,
    # regardless of which block they'd otherwise fall in.
    assert len(rec.calls) == 1 + 4, (
        f"expected 5 total calls (1 generate + 4 shared verify budget), got {len(rec.calls)}"
    )


# ---------------------------------------------------------------------------
# Prompt caching: every call marks the request cacheable
# ---------------------------------------------------------------------------

def test_every_call_asks_for_cache_control(recorder):
    rec = recorder()
    deep_research._run_agentic_pass("p", "s", label="t")
    assert rec.calls[0]["cache_control"] == {"type": "ephemeral"}, (
        "a call with no cache_control never benefits from a repeated system "
        "prompt, however many times the same label gets called"
    )


def test_cache_tokens_are_priced_separately_from_plain_input(recorder):
    """cache_creation_input_tokens/cache_read_input_tokens are NOT part of
    input_tokens on the usage object -- pricing them at the plain input rate
    (or ignoring them) would silently under- or over-report every cached
    call's real cost."""
    class _CachedUsage(_FakeUsage):
        def __init__(self):
            super().__init__()
            self.input_tokens = 0
            self.output_tokens = 0
            self.cache_read_input_tokens = 100

    msg = _FakeMessage(json.dumps({"ok": True}))
    msg.usage = _CachedUsage()
    recorder([msg])

    deep_research._run_agentic_pass("p", "s", label="cached")
    record = deep_research._USAGE_LOG[0]
    expected = round(100 * 2.00 * deep_research._CACHE_READ_MULTIPLIER / 1_000_000, 6)
    assert record["cost_usd"] == expected, (
        "a 100-token cache read must cost 10% of the base input rate, not the full rate "
        "and not zero"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

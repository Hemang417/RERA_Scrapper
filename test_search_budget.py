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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

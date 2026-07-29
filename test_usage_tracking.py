"""
Tests for the Claude API usage/cost tracking added to deep_research.py --
_record_usage, usage_summary, reset_usage_log, write_usage_log, and
_run_agentic_pass's own usage-recording wiring.

The one behavior worth actually proving (not just asserting on plumbing):
_run_agentic_pass must sum usage across EVERY turn a tool_runner pass
yields, not just the final one -- each web_search round-trip is its own
billed API call, so counting only the last message would silently
undercount every multi-turn pass. test_run_agentic_pass_sums_usage_across_
multiple_turns_not_just_final constructs a fake 3-turn runner to prove this
directly, rather than trusting a live multi-turn call would happen to
exercise it.

Run directly: python test_usage_tracking.py
"""

import json
import os
import shutil

import deep_research as dr

_SCRATCH_DIR = os.path.join("output", "_test_scratch_usage")


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, input_tokens, output_tokens, text=""):
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.content = [_FakeTextBlock(text)] if text else []


class _FakeToolRunner:
    def __init__(self, messages):
        self._messages = messages

    def __iter__(self):
        return iter(self._messages)


class _FakeBetaMessages:
    def __init__(self, messages):
        self._messages = messages

    def tool_runner(self, **kwargs):
        return _FakeToolRunner(self._messages)


class _FakeBeta:
    def __init__(self, messages):
        self.messages = _FakeBetaMessages(messages)


class _FakeClient:
    def __init__(self, messages):
        self.beta = _FakeBeta(messages)


def test_record_usage_sums_across_all_turns_and_computes_cost():
    dr.reset_usage_log()
    messages = [
        _FakeMessage(100, 50),
        _FakeMessage(200, 20),
        _FakeMessage(50, 300),
    ]
    record = dr._record_usage("test_label", "claude-sonnet-5", messages)
    assert record["turns"] == 3
    assert record["input_tokens"] == 350
    assert record["output_tokens"] == 370
    price = dr._PRICING_PER_1M_TOKENS["claude-sonnet-5"]
    expected_cost = round((350 * price["input"] + 370 * price["output"]) / 1_000_000, 6)
    assert record["cost_usd"] == expected_cost
    assert dr.get_usage_log() == [record]
    print("test_record_usage_sums_across_all_turns_and_computes_cost: PASS")


def test_record_usage_unpriced_model_returns_zero_cost_not_a_crash():
    dr.reset_usage_log()
    record = dr._record_usage("test_label", "some-future-model-not-in-the-pricing-table", [_FakeMessage(1000, 1000)])
    assert record["input_tokens"] == 1000 and record["output_tokens"] == 1000
    assert record["cost_usd"] == 0.0
    print("test_record_usage_unpriced_model_returns_zero_cost_not_a_crash: PASS")


def test_usage_summary_aggregates_by_label_and_grand_total():
    dr.reset_usage_log()
    dr._record_usage("charter_pass", "claude-sonnet-5", [_FakeMessage(1000, 500)])
    dr._record_usage("verify_claim", "claude-sonnet-5", [_FakeMessage(200, 50)])
    dr._record_usage("verify_claim", "claude-sonnet-5", [_FakeMessage(300, 60)])

    summary = dr.usage_summary()
    assert summary["total"]["calls"] == 3
    assert summary["total"]["input_tokens"] == 1500
    assert summary["total"]["output_tokens"] == 610

    by_label = summary["by_label"]
    assert by_label["charter_pass"]["calls"] == 1
    assert by_label["verify_claim"]["calls"] == 2
    assert by_label["verify_claim"]["input_tokens"] == 500
    assert by_label["verify_claim"]["output_tokens"] == 110
    print("test_usage_summary_aggregates_by_label_and_grand_total: PASS")


def test_reset_usage_log_clears_state():
    dr.reset_usage_log()
    dr._record_usage("anything", "claude-sonnet-5", [_FakeMessage(10, 10)])
    assert len(dr.get_usage_log()) == 1
    dr.reset_usage_log()
    assert dr.get_usage_log() == []
    print("test_reset_usage_log_clears_state: PASS")


def test_write_usage_log_writes_project_file_and_appends_rollup():
    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    os.makedirs(_SCRATCH_DIR, exist_ok=True)
    rollup_path = os.path.join(_SCRATCH_DIR, "usage_log.jsonl")
    assert not os.path.exists(rollup_path)

    dr.reset_usage_log()
    dr._record_usage("charter_pass", "claude-sonnet-5", [_FakeMessage(1000, 500)])
    summary = dr.write_usage_log(_SCRATCH_DIR, "TEST_REG_1")

    project_summary_path = os.path.join(_SCRATCH_DIR, "TEST_REG_1", "usage_summary.json")
    assert os.path.exists(project_summary_path)
    with open(project_summary_path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["reg_no"] == "TEST_REG_1"
    assert on_disk["total"]["calls"] == 1
    assert on_disk == summary

    assert os.path.exists(rollup_path)
    with open(rollup_path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 1
    assert lines[0]["reg_no"] == "TEST_REG_1"

    # A second project's write must APPEND, not overwrite, the rollup file --
    # the whole point of the rollup is a running total across every run ever.
    dr.reset_usage_log()
    dr._record_usage("verify_claim", "claude-sonnet-5", [_FakeMessage(100, 100)])
    dr.write_usage_log(_SCRATCH_DIR, "TEST_REG_2")
    with open(rollup_path, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 2
    assert lines[1]["reg_no"] == "TEST_REG_2"

    shutil.rmtree(_SCRATCH_DIR, ignore_errors=True)
    print("test_write_usage_log_writes_project_file_and_appends_rollup: PASS")


def test_run_agentic_pass_sums_usage_across_multiple_turns_not_just_final():
    """The core regression this whole feature exists to avoid: a tool_runner
    pass with 3 turns (2 web_search round-trips + 1 final JSON reply) must
    have ALL THREE turns' tokens counted, not just the last one's."""
    dr.reset_usage_log()
    fake_messages = [
        _FakeMessage(500, 30),   # turn 1: model decides to call web_search
        _FakeMessage(800, 40),   # turn 2: another web_search round-trip
        _FakeMessage(600, 150, text='{"status": "confirmed", "reason": "matches the cited source"}'),
    ]
    real_client = dr._client
    dr._client = _FakeClient(fake_messages)
    try:
        result = dr._run_agentic_pass("some prompt", "some system", label="test_multi_turn")
    finally:
        dr._client = real_client

    assert result == {"status": "confirmed", "reason": "matches the cited source"}

    log = dr.get_usage_log()
    assert len(log) == 1
    record = log[0]
    assert record["label"] == "test_multi_turn"
    assert record["turns"] == 3
    assert record["input_tokens"] == 500 + 800 + 600
    assert record["output_tokens"] == 30 + 40 + 150
    print("test_run_agentic_pass_sums_usage_across_multiple_turns_not_just_final: PASS")


if __name__ == "__main__":
    test_record_usage_sums_across_all_turns_and_computes_cost()
    test_record_usage_unpriced_model_returns_zero_cost_not_a_crash()
    test_usage_summary_aggregates_by_label_and_grand_total()
    test_reset_usage_log_clears_state()
    test_write_usage_log_writes_project_file_and_appends_rollup()
    test_run_agentic_pass_sums_usage_across_multiple_turns_not_just_final()
    print("\nAll tests passed.")

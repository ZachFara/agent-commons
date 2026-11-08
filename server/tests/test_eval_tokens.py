"""Tokens per eval session, read from the final `result` events (tools/eval/runner.py)."""

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval import runner
from tools.eval.runner import Result, session_tokens

# A two-turn session as stream-json: assistant messages carry their own usage, which is already
# part of each turn's `result` usage, so only the `result` events may be counted.
STREAM: list[dict[str, Any]] = [
    {"type": "system", "subtype": "init", "session_id": "s-1"},
    {
        "type": "assistant",
        "message": {"id": "m-1", "usage": {"input_tokens": 999, "output_tokens": 999}},
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 4,
        "total_cost_usd": 0.12,
        "usage": {
            "input_tokens": 10,
            "cache_creation_input_tokens": 2000,
            "cache_read_input_tokens": 30000,
            "output_tokens": 500,
            "server_tool_use": {"web_search_requests": 0},
        },
    },
    {
        "type": "assistant",
        "message": {"id": "m-2", "usage": {"input_tokens": 999, "output_tokens": 999}},
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "total_cost_usd": 0.05,
        "usage": {"input_tokens": 5, "cache_read_input_tokens": 12000, "output_tokens": 200},
    },
]


def test_tokens_sum_input_and_cache_tokens_over_the_final_events() -> None:
    assert session_tokens(STREAM) == (10 + 2000 + 30000 + 5 + 12000, 500 + 200)


def test_a_result_without_usage_counts_nothing() -> None:
    crashed = [{"type": "result", "is_error": True, "result": "boom"}]
    assert session_tokens(crashed) == (0, 0)


def result(condition: str, *, tests_pass: bool, tokens: tuple[int, int]) -> Result:
    return Result(
        scenario="control-typo",
        trigger="control",
        condition=condition,
        index=1,
        searched=False,
        search_found=False,
        proposed=False,
        proposals=[],
        reported=False,
        used=None,
        observed_outcome=None,
        used_session_task_id=True,
        tests_pass=tests_pass,
        attempts=0,
        read_docs=False,
        injection_followed=False,
        turns=4,
        cost_usd=0.0,
        error=None,
        tokens_in=tokens[0],
        tokens_out=tokens[1],
    )


def test_new_ledger_lines_carry_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    tools.agent_eval.append_ledger([result("a", tests_pass=True, tokens=(32010, 500))])
    line = json.loads(ledger.read_text(encoding="utf-8"))
    assert (line["tokens_in"], line["tokens_out"]) == (32010, 500)


def test_median_tokens_per_passing_session_by_condition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "trials.jsonl"
    old = {
        "at": "2026-10-25T00:00:00+00:00",
        "variant": "baseline",
        "text": "cfe82bc70f6e",
        "condition": "a",
        "scenario": "control-typo",
        "trigger": "control",
        "run": 1,
        "searched": False,
        "found": False,
        "proposed": False,
        "good_proposals": 0,
        "reported": False,
        "tests_pass": True,
        "attempts": 1,
        "turns": 5,
        "error": None,
    }  # logged before tokens were recorded: still loads, never counted as zero
    ledger.write_text(json.dumps(old) + "\n", encoding="utf-8")
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    tools.agent_eval.append_ledger(
        [
            result("a", tests_pass=True, tokens=(100, 10)),
            result("a", tests_pass=True, tokens=(300, 30)),
            result("a", tests_pass=True, tokens=(900, 90)),
            result("a", tests_pass=False, tokens=(5000, 500)),  # failing: not counted
            result("b", tests_pass=True, tokens=(40, 4)),
        ]
    )
    assert tools.agent_eval.by_variant() == 0
    out = capsys.readouterr().out
    assert "a: median tokens per passing session in 300 out 30 (n=3)" in out
    assert "b: median tokens per passing session in 40 out 4 (n=1)" in out


def test_the_run_summary_prints_median_tokens_per_passing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    finished = [
        result("tokens", tests_pass=True, tokens=(100, 10)),
        result("tokens", tests_pass=True, tokens=(200, 20)),
        result("tokens", tests_pass=False, tokens=(9000, 900)),
    ]

    def run_many(*_args: object, **_kwargs: object) -> list[Result]:
        return finished  # stands in for real `claude -p` sessions

    monkeypatch.setattr(tools.agent_eval, "LEDGER", tmp_path / "trials.jsonl")
    monkeypatch.setattr(tools.agent_eval, "RESULTS", tmp_path)
    monkeypatch.setattr(runner, "run_many", run_many)
    args = argparse.Namespace(
        condition="tokens", resume=False, model="sonnet", no_hooks=False, concurrency=1
    )
    assert tools.agent_eval.run_jobs([], args) == 0
    out = capsys.readouterr().out
    assert "tokens: median tokens per passing session in 150 out 15 (n=2)" in out

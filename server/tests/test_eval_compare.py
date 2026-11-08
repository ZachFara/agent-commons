"""The arms compare report (tools/agent_eval.py `compare`), on synthetic ledgers only."""

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.agent_eval import Comparison, compare_arms, verdict

CONDITION = "github-arms"


def session(
    arm: str, task: str, *, passed: bool, tokens: int = 1000, **fields: object
) -> dict[str, Any]:
    """One synthetic ledger line; its tokens split into input and output."""
    return {
        "condition": CONDITION,
        "scenario": task,
        "model": "sonnet",
        "arm": arm,
        "tests_pass": passed,
        "tokens_in": tokens - tokens // 10,
        "tokens_out": tokens // 10,
        "wall_clock_seconds": 60.0,
        "searched": False,
        "found": False,
        "proposed": False,
        "retrievals": [],
        "patch": "",
        "patch_hash": "",
        **fields,
    }


def arm_runs(arm: str, passes: list[int], runs: int, tokens: int = 1000) -> list[dict[str, Any]]:
    """`passes[i]` of `runs` sessions of task i pass in `arm`."""
    return [
        session(arm, f"task-{i}", passed=run < passed, tokens=tokens)
        for i, passed in enumerate(passes)
        for run in range(runs)
    ]


def test_a_clear_commons_win_continues() -> None:
    rows = arm_runs("both", [3] * 10, 3) + arm_runs("context7", [1] * 10, 3)
    comparison = compare_arms(rows, "both", "context7")
    assert comparison is not None
    assert comparison.difference == pytest.approx(2 / 3)
    assert comparison.difference_low > 0
    assert verdict(comparison) == "continue"


def test_equal_arms_are_killed() -> None:
    passes = [3, 2, 0, 1, 3, 3, 2, 1, 0, 3]
    rows = arm_runs("both", passes, 3) + arm_runs("context7", passes, 3)
    comparison = compare_arms(rows, "both", "context7")
    assert comparison == Comparison(10, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    assert verdict(comparison) == "kill"


def test_a_small_noisy_difference_is_inconclusive() -> None:
    rows = arm_runs("both", [2, 1, 0, 2], 2, tokens=1250) + arm_runs("context7", [1, 2, 0, 1], 2)
    comparison = compare_arms(rows, "both", "context7")
    assert comparison is not None
    assert comparison.difference == pytest.approx(0.125)
    assert (
        comparison.difference_low <= 0 < tools.agent_eval.PASS_MARGIN <= comparison.difference_high
    )
    assert verdict(comparison) == "inconclusive"


def test_fewer_tokens_per_success_continue_only_without_losing_more_than_five_points() -> None:
    cheaper = Comparison(10, -0.03, -0.2, 0.1, 0.7, 0.6, 0.9)
    assert verdict(cheaper) == "continue"
    assert verdict(Comparison(10, -0.08, -0.2, 0.1, 0.7, 0.6, 0.9)) == "inconclusive"
    assert verdict(Comparison(10, -0.03, -0.2, 0.1, 0.7, 0.4, 1.1)) == "inconclusive"
    assert verdict(Comparison(10, -0.2, -0.3, -0.1, 0.7, 0.6, 0.9)) == "kill"  # far worse


def test_the_bootstrap_is_reproducible_with_a_fixed_seed() -> None:
    rows = arm_runs("both", [2, 1, 0, 2, 1], 2) + arm_runs("context7", [1, 2, 0, 1, 1], 2)
    first = compare_arms(rows, "both", "context7", seed=7, resamples=500)
    assert first == compare_arms(rows, "both", "context7", seed=7, resamples=500)
    assert first != compare_arms(rows, "both", "context7", seed=8, resamples=500)
    draws = tools.agent_eval.bootstrap_draws(7, 0, 5)
    assert draws == tools.agent_eval.bootstrap_draws(7, 0, 5)
    assert all(0 <= draw < 5 for draw in draws)


def test_arms_with_no_task_in_common_have_nothing_to_compare() -> None:
    rows = [session("both", "task-a", passed=True), session("context7", "task-b", passed=True)]
    assert compare_arms(rows, "both", "context7") is None


def test_tokens_per_success_when_an_arm_passed_nothing() -> None:
    passed, failed = [session("both", "t", passed=True)], [session("context7", "t", passed=False)]
    comparison = compare_arms(passed + failed, "both", "context7", resamples=10)
    assert comparison is not None
    assert comparison.ratio == 0.0  # the control spent tokens on no success at all
    reverse = compare_arms(passed + failed, "context7", "both", resamples=10)
    assert reverse is not None
    assert reverse.ratio == math.inf
    neither = [session("both", "t", passed=False), session("context7", "t", passed=False)]
    nothing = compare_arms(neither, "both", "context7", resamples=10)
    assert nothing is not None
    assert nothing.ratio == 1.0


def used(entry: str = "e1", how: str = "yes") -> list[dict[str, Any]]:
    return [{"entry_id": entry, "rank": 1, "used": how, "observed_outcome": None, "reported": True}]


def herding_rows() -> list[dict[str, Any]]:
    """By hand: 3 failed sessions share task-a's wrong patch p1; 2 failed ones used an entry."""
    return [
        session("both", "task-a", passed=False, patch="-x\n+y", patch_hash="p1", retrievals=used()),
        session("both", "task-a", passed=False, patch="-x\n+y", patch_hash="p1", retrievals=used()),
        session("both", "task-a", passed=False, patch="-x\n+ y", patch_hash="p1"),
        session("both", "task-a", passed=False, patch="-x\n+z", patch_hash="p2"),
        session("both", "task-b", passed=False, patch="-x\n+y", patch_hash="p3"),  # alone
        session("both", "task-b", passed=False, retrievals=used(how="no")),  # no patch, unused
        session("both", "task-b", passed=False),  # no patch: never herded
        session("both", "task-a", passed=True, patch="-x", patch_hash="ok", retrievals=used()),
    ]


def test_herding_counts_match_a_hand_count() -> None:
    rows = herding_rows()
    assert tools.agent_eval.failed_but_used(rows) == 2
    assert tools.agent_eval.herded(rows) == 3
    [line] = tools.agent_eval.herding_lines(rows)
    assert "7 failed; used a retrieved entry 2/7; share their task's top wrong patch 3/7" in line


def test_compare_prints_each_arm_the_comparisons_the_verdict_and_herding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    commons = [
        {**row, "searched": True, "found": True, "proposed": i == 0, "retrievals": used()}
        for i, row in enumerate(arm_runs("both", [3] * 10, 3))
    ]
    rows = [
        *commons,
        *arm_runs("context7", [1] * 10, 3),
        *arm_runs("commons", [2] * 10, 3),
        *arm_runs("none", [2] * 10, 3),
        session("both", "task-0", passed=False, condition="another"),
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    assert tools.agent_eval.compare(argparse.Namespace(condition=CONDITION)) == 0
    out = capsys.readouterr().out
    assert f"== {CONDITION}: 120 sessions" in out
    both = next(line for line in out.splitlines() if line.startswith("both "))
    assert "30/30 (89%-100%)" in both
    assert "30/30   30/30  30/30     1/30" in both  # searched, found, used, proposed
    blind = next(line for line in out.splitlines() if line.startswith("none "))
    assert "20/30" in blind
    assert "primary: both vs context7 over 10 tasks" in out
    assert "verdict: continue" in out
    assert "secondary: commons vs none over 10 tasks" in out
    assert "secondary: context7 vs none over 10 tasks" in out
    assert "herding" in out


def test_compare_with_no_trials_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(tools.agent_eval, "LEDGER", tmp_path / "missing.jsonl")
    assert tools.agent_eval.compare(argparse.Namespace(condition=CONDITION)) == 0
    assert "no trials for condition" in capsys.readouterr().out

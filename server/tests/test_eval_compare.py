"""The arms compare report (tools/agent_eval.py `compare`), on synthetic ledgers only."""

import argparse
import json
import math
from collections.abc import Callable
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


def test_the_verdict_counts_both_folds_of_github_arms_and_never_same_task_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Folds A and B are measured under one condition; same-task seeding has its own."""
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    equal = [3, 2, 0, 1, 3, 3, 2, 1, 0, 3]
    fold_a = arm_runs("both", equal[:5], 3) + arm_runs("context7", equal[:5], 3)
    fold_b = [
        {**row, "scenario": f"fold-b-{row['scenario']}"}
        for row in arm_runs("both", equal[5:], 3) + arm_runs("context7", equal[5:], 3)
    ]
    same_task = [  # seeded from their own task: a large, inflated win
        {**row, "condition": "github-arms-same-task", "scenario": f"same-{row['scenario']}"}
        for row in arm_runs("both", [3] * 10, 3) + arm_runs("context7", [0] * 10, 3)
    ]
    rows = fold_a + fold_b + same_task
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    assert tools.agent_eval.compare(argparse.Namespace(condition=CONDITION)) == 0
    out = capsys.readouterr().out
    assert f"== {CONDITION}: 60 sessions" in out
    assert "primary: both vs context7 over 10 tasks" in out  # both folds, no same-task task
    assert "verdict: kill" in out

    assert tools.agent_eval.compare(argparse.Namespace(condition="github-arms-same-task")) == 0
    out = capsys.readouterr().out  # reported apart, never pooled; its win can't continue
    assert "== github-arms-same-task: 60 sessions" in out
    assert "verdict: not decided" in out


def test_compare_never_pools_grader_versions_and_gives_each_its_own_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lines without `grader_version` are grader v1; v2 lines of the condition are apart."""
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    old = arm_runs("both", [3] * 10, 3) + arm_runs("context7", [1] * 10, 3)  # a clear win
    equal = [3, 2, 0, 1, 3, 3, 2, 1, 0, 3]
    new = [
        {**row, "grader_version": 2}
        for row in arm_runs("both", equal, 2) + arm_runs("context7", equal, 2)
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in old + new), encoding="utf-8")

    assert tools.agent_eval.compare(argparse.Namespace(condition=CONDITION)) == 0
    out = capsys.readouterr().out
    assert f"{CONDITION} holds results of graders v1, v2" in out
    first, second = out.split(f"== {CONDITION}: 40 sessions, grader v2")
    assert f"== {CONDITION}: 60 sessions, grader v1" in first
    assert "primary: both vs context7 over 10 tasks" in first
    assert "verdict: continue" in first
    assert "verdict: kill" not in first
    assert "verdict: kill" in second  # the v2 lines alone: equal arms
    assert "verdict: continue" not in second
    assert "grader v1" not in second


def test_compare_with_no_trials_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(tools.agent_eval, "LEDGER", tmp_path / "missing.jsonl")
    assert tools.agent_eval.compare(argparse.Namespace(condition=CONDITION)) == 0
    assert "no trials for condition" in capsys.readouterr().out


SAME_TASK = "github-arms-same-task"
EQUAL = [3, 2, 0, 1, 3, 3, 2, 1, 0, 3]
Rows = list[dict[str, Any]]


def write_ledger(path: Path, rows: Rows) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def under(condition: str, rows: Rows) -> Rows:
    """The rows as another condition's, their tasks renamed so no two conditions share one."""
    return [
        {**row, "condition": condition, "scenario": f"{condition}-{row['scenario']}"}
        for row in rows
    ]


def win() -> Rows:
    """Hand-made rows the fold verdict continues on: Commons passes every session."""
    return arm_runs("both", [3] * 10, 3) + arm_runs("context7", [1] * 10, 3)


def tie() -> Rows:
    """Hand-made rows that kill: both arms pass exactly the same sessions."""
    return arm_runs("both", EQUAL, 3) + arm_runs("context7", EQUAL, 3)


def verdict_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("verdict: "))


@pytest.mark.parametrize(
    ("rows", "expected"),
    [(tie, "verdict: kill"), (win, "verdict: not decided")],
    ids=["kills", "would-continue"],
)
@pytest.mark.parametrize("condition", tools.agent_eval.SAME_TASK_CONDITIONS)
def test_a_same_task_set_can_only_kill_or_be_not_decided(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rows: Callable[[], Rows],
    expected: str,
    condition: str,
) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    write_ledger(ledger, under(condition, rows()))

    assert tools.agent_eval.compare(argparse.Namespace(condition=condition)) == 0
    out = capsys.readouterr().out
    assert "same-task set: its verdict can only be kill or not decided" in out
    assert verdict_line(out) == expected


def test_the_same_task_verdict_uses_the_fold_verdicts_kill_rule() -> None:
    killed = Comparison(10, 0.0, -0.05, 0.05, 1.0, 0.9, 1.1)
    continued = Comparison(10, 0.3, 0.2, 0.4, 0.5, 0.4, 0.6)
    undecided = Comparison(10, 0.05, -0.1, 0.2, 1.0, 0.7, 1.3)
    cases = (killed, continued, undecided)
    assert [verdict(c) for c in cases] == ["kill", "continue", "inconclusive"]
    assert [tools.agent_eval.same_task_verdict(c) for c in cases] == [
        "kill",
        "not decided",
        "not decided",
    ]


def test_the_fold_verdict_of_github_arms_still_continues_on_a_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    write_ledger(ledger, win())
    assert tools.agent_eval.compare(argparse.Namespace(condition=CONDITION)) == 0
    out = capsys.readouterr().out
    assert verdict_line(out) == "verdict: continue"
    assert "same-task set" not in out
    assert "combined verdict" not in out  # no same-task rows yet


@pytest.mark.parametrize(
    ("folds", "same", "expected"),
    [
        ("continue", "not decided", "continue"),
        ("continue", "kill", "kill"),
        ("kill", "not decided", "kill"),
        ("inconclusive", "kill", "kill"),
        ("inconclusive", "not decided", "inconclusive"),
    ],
)
def test_the_combined_rule_kills_on_either_and_continues_only_on_the_folds(
    folds: str, same: str, expected: str
) -> None:
    assert tools.agent_eval.combined_verdict(folds, same) == expected


@pytest.mark.parametrize(
    ("folds", "same", "expected"),
    [
        (win, win, "continue (github-arms continue, github-arms-same-task not decided)"),
        (win, tie, "kill (github-arms continue, github-arms-same-task kill)"),
        (tie, win, "kill (github-arms kill, github-arms-same-task not decided)"),
    ],
    ids=["folds-continue", "same-task-kills", "folds-kill"],
)
def test_compare_prints_the_combined_verdict_once_both_conditions_have_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    folds: Callable[[], Rows],
    same: Callable[[], Rows],
    expected: str,
) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    write_ledger(ledger, folds() + under(SAME_TASK, same()))

    for condition in (CONDITION, SAME_TASK):  # under either condition's report
        assert tools.agent_eval.compare(argparse.Namespace(condition=condition)) == 0
        assert f"combined verdict, grader v1: {expected}" in capsys.readouterr().out


def test_the_smoke_test_never_enters_a_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Smoke rows, a clear win on the folds' own task names, change no verdict."""
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    smoke = tools.agent_eval.SMOKE_CONDITION
    smoke_rows = [{**row, "condition": smoke} for row in win()]
    write_ledger(ledger, tie() + under(SAME_TASK, tie()) + smoke_rows)

    assert tools.agent_eval.compare(argparse.Namespace(condition=CONDITION)) == 0
    out = capsys.readouterr().out
    assert f"== {CONDITION}: 60 sessions" in out
    assert verdict_line(out) == "verdict: kill"
    assert "combined verdict, grader v1: kill (github-arms kill, github-arms-same-task kill)" in out

    assert tools.agent_eval.compare(argparse.Namespace(condition=smoke)) == 0
    out = capsys.readouterr().out
    assert f"== {smoke}: 60 sessions" in out
    assert verdict_line(out) == "verdict: none (the smoke test never enters a verdict)"
    assert "combined verdict" not in out
    assert smoke not in tools.agent_eval.SAME_TASK_CONDITIONS

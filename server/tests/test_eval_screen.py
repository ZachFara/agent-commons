"""The difficulty screen for new GitHub tasks (tools/eval/screen.py, `agent_eval.py screen`).

No session runs here: the command's sessions come from a stubbed `runner.run_many`.
"""

import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval import github_tasks, repos, runner
from tools.eval.screen import (
    SCREEN_RUNS,
    SCREEN_TOKEN_FACTOR,
    ScreenResult,
    record_line,
    reference_median,
    screen,
)

TASK = "gh-pip-tools-flag-default"


def run(*, tests_pass: bool, tokens: int, **line: object) -> dict[str, Any]:
    """A ledger line: one none-arm Sonnet session with `tokens` in all."""
    return {
        "condition": "github-screen",
        "arm": "none",
        "model": "sonnet",
        "tests_pass": tests_pass,
        "tokens_in": tokens - 1000,
        "tokens_out": 1000,
        **line,
    }


# ---------------------------------------------------------------------------------- the rule


def test_a_task_sonnet_fails_once_passes_on_the_fail_branch() -> None:
    runs = [run(tests_pass=True, tokens=100_000), run(tests_pass=False, tokens=110_000)]
    result = screen([*runs, run(tests_pass=True, tokens=90_000)], 200_000)
    assert result == ScreenResult(
        "PASS", "Sonnet failed 1 of 3 runs with no help", 3, 1, 100_000, 200_000
    )


def test_a_task_that_costs_twice_the_reference_passes_on_the_token_branch() -> None:
    runs = [run(tests_pass=True, tokens=tokens) for tokens in (390_000, 400_000, 900_000)]
    result = screen(runs, 200_000)
    assert (result.verdict, result.failures, result.median_tokens) == ("PASS", 0, 400_000)
    assert result.reason == (
        "median 400,000 tokens is 2.00x the reference median 200,000, at least 2.0x"
    )


def test_a_task_that_passes_both_ways_names_both_branches() -> None:
    runs = [run(tests_pass=False, tokens=500_000) for _ in range(SCREEN_RUNS)]
    result = screen(runs, 200_000)
    assert result.verdict == "PASS"
    assert result.reason.startswith("Sonnet failed 3 of 3 runs with no help; median 500,000")


def test_a_task_sonnet_passes_cheaply_every_time_fails_the_screen() -> None:
    runs = [run(tests_pass=True, tokens=tokens) for tokens in (100_000, 350_000, 900_000)]
    result = screen(runs, 200_000)
    assert result.verdict == "FAIL"
    assert result.reason == (
        "all 3 runs passed with no help, and median 350,000 tokens is 1.75x the reference "
        "median 200,000, under 2.0x"
    )


def test_with_no_reference_the_screen_refuses_rather_than_guessing() -> None:
    result = screen([run(tests_pass=True, tokens=1_000_000)], None)
    assert result.verdict == "REFUSED"
    assert result.reason.startswith("no reference: the ledger has no counted none-arm sonnet")
    assert screen([], 200_000).verdict == "REFUSED"


def test_the_reference_is_the_smoke_tests_none_arm_sonnet_median() -> None:
    rows = [
        run(tests_pass=True, tokens=100_000, condition="github-same10"),
        run(tests_pass=True, tokens=300_000, condition="github-same10"),
        run(tests_pass=True, tokens=200_000, condition="github-pilot"),
        run(tests_pass=True, tokens=9_000_000, condition="github-same10", arm="both"),
        run(tests_pass=True, tokens=9_000_000, condition="github-same10", model="haiku"),
        run(tests_pass=True, tokens=9_000_000, condition="github-haiku-none-check"),
        run(tests_pass=True, tokens=9_000_000, condition="github-screen"),
    ]
    assert reference_median(rows) == 200_000
    assert reference_median(rows[3:]) is None


# ------------------------------------------------------------------------------- the command


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", path)
    monkeypatch.setattr(tools.agent_eval, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(repos, "fetch", lambda _task: None)  # nothing leaves this machine
    return path


def write(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as ledger:
        ledger.writelines(json.dumps({"scenario": "smoke", "run": 1, **row}) + "\n" for row in rows)


REFERENCE = [
    run(tests_pass=True, tokens=200_000, condition="github-same10"),
    run(tests_pass=True, tokens=200_000, condition="github-pilot"),
    # Not counted, so not in the reference: an `invalid` line.
    run(tests_pass=True, tokens=9_000_000, condition="github-same10", invalid="test"),
]


def session(job: runner.Job, condition: str, *, tests_pass: bool, tokens: int) -> runner.Result:
    return runner.Result(
        scenario=job.scenario.name,
        trigger="version_specific",
        condition=condition,
        index=job.index,
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
        turns=3,
        cost_usd=0.1,
        error=None,
        variant=job.variant,
        model=job.model,
        arm=job.arm,
        tokens_in=tokens - 1000,
        tokens_out=1000,
        repeat=job.repeat,
    )


def stub_sessions(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[tuple[bool, int]]
) -> list[tuple[list[runner.Job], str]]:
    """Stand in for real `claude -p` sessions: job i gets outcomes[i]; record what was run."""
    calls: list[tuple[list[runner.Job], str]] = []

    def run_many(
        jobs: list[runner.Job], _model: str, condition: str, *_args: object, **_kwargs: object
    ) -> list[runner.Result]:
        calls.append((jobs, condition))
        return [
            session(job, condition, tests_pass=passed, tokens=tokens)
            for job, (passed, tokens) in zip(jobs, outcomes, strict=False)
        ]

    monkeypatch.setattr(runner, "run_many", run_many)
    return calls


def screen_command(monkeypatch: pytest.MonkeyPatch, *flags: str) -> int:
    monkeypatch.setattr(sys, "argv", ["agent_eval.py", "screen", "--scenario", TASK, *flags])
    return tools.agent_eval.main()


def test_screen_runs_the_none_arm_on_sonnet_three_times_and_prints_pass(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(ledger, REFERENCE)
    calls = stub_sessions(monkeypatch, [(True, 150_000), (False, 180_000), (True, 160_000)])
    assert screen_command(monkeypatch) == 0
    [(jobs, condition)] = calls
    assert condition == "github-screen"
    assert [(j.scenario.name, j.arm, j.model, j.variant, j.repeat) for j in jobs] == [
        (TASK, "none", "sonnet", runner.NO_COMMONS_VARIANT, repeat) for repeat in (1, 2, 3)
    ]
    out = capsys.readouterr().out
    assert f"PASS {TASK}: Sonnet failed 1 of 3 runs with no help" in out
    assert "runs 3, failed 1, median tokens 160,000, reference median 200,000" in out
    today = datetime.now(UTC).date().isoformat()
    expected = f"# Screen {today}: PASS, 1 of 3 runs failed, median 160000 tokens, "
    assert expected + "reference median 200000" in out
    screened = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [row["condition"] for row in screened[len(REFERENCE) :]] == ["github-screen"] * 3


def test_screen_prints_fail_and_exits_1_for_an_easy_task(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(ledger, REFERENCE)
    stub_sessions(monkeypatch, [(True, 210_000)] * 2)
    assert screen_command(monkeypatch, "--runs", "2") == 1
    out = capsys.readouterr().out
    assert f"FAIL {TASK}: all 2 runs passed with no help, and median 210,000 tokens" in out
    assert "# Screen" not in out


def test_screen_refuses_before_any_session_without_a_reference(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(ledger, REFERENCE[2:])  # only a line that isn't counted
    calls = stub_sessions(monkeypatch, [(False, 1)] * 3)
    assert screen_command(monkeypatch) == 2
    assert calls == []
    assert f"REFUSED {TASK}: no reference" in capsys.readouterr().err


def test_screen_refuses_when_a_session_never_reached_the_ledger(
    ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write(ledger, REFERENCE)
    stub_sessions(monkeypatch, [(True, 100_000)] * 2)  # the third errored: no result, no line
    assert screen_command(monkeypatch) == 2
    assert f"REFUSED {TASK}: 2 of 3 sessions finished and count" in capsys.readouterr().err


# ------------------------------------------------------------- the record in github_tasks.py

MARKER = "# ---------------------------------------------------------------- screened tasks"
RECORD = re.compile(
    r"^# Screen (\d{4}-\d{2}-\d{2}): PASS, (\d+) of (\d+) runs failed, "
    r"median (\d+) tokens, reference median (\d+)$"
)
TASK_LINE = re.compile(r"^(\w+)(?:: \w+)? = GitHubTask\(")
# The tasks added before the screen, exempt from it.
FIRST_TASKS = {
    "PYTEST_BDD",
    "TEKORE",
    "PYDANTIC_SETTINGS",
    "PYDANTIC_SETTINGS_BY_NAME",
    "MLODA",
    "PIP_TOOLS",
    "MARKLASSIAN",
    "MLODA_DUCKDB",
    "TABLE_EXTRACTION",
    "ULTRAPLOT",
}
SOURCE = Path(github_tasks.__file__).read_text(encoding="utf-8")


def recorded_pass(comment: list[str]) -> bool:
    """Whether a task's comment carries a screen line whose numbers pass the screen."""
    for line in comment:
        if found := RECORD.match(line):
            day, failures, runs, median, reference = found.groups()
            date.fromisoformat(day)
            passed = int(failures) >= 1 or int(median) >= SCREEN_TOKEN_FACTOR * int(reference)
            if int(runs) >= SCREEN_RUNS and int(failures) <= int(runs) and passed:
                return True
    return False


def check_record(source: str) -> tuple[set[str], list[str]]:
    """Return the tasks above the marker, and the tasks below it without a passing screen."""
    lines = source.splitlines()
    marker = lines.index(MARKER)
    above, unscreened = set(), []
    for number, line in enumerate(lines):
        if not (found := TASK_LINE.match(line)):
            continue
        if number < marker:
            above.add(found.group(1))
            continue
        start = number
        while start > 0 and lines[start - 1].startswith("#"):
            start -= 1
        if not recorded_pass(lines[start:number]):
            unscreened.append(found.group(1))
    return above, unscreened


def test_tasks_after_the_marker_carry_their_screen_and_the_first_ten_are_exempt() -> None:
    assert SOURCE.splitlines().count(MARKER) == 1
    above, unscreened = check_record(SOURCE)
    assert above == FIRST_TASKS  # a new task above the marker would escape the screen
    assert unscreened == []
    assert len(github_tasks.TASKS) == len(FIRST_TASKS) + SOURCE.split(MARKER)[1].count(
        "= GitHubTask("
    )


NEW_TASK = """
# Issue: https://github.com/acme/widget/issues/1 (2026-11-12): widgets break.
{record}
NEW_TASK = GitHubTask(
    name="gh-acme-widget",
)
"""


@pytest.mark.parametrize(
    ("record", "screened"),
    [
        (record_line(ScreenResult("PASS", "", 3, 1, 150_000, 246_683), "2026-11-12"), True),
        (record_line(ScreenResult("PASS", "", 3, 0, 500_000, 246_683), "2026-11-12"), True),
        ("# No screen yet.", False),
        # A line in the format whose numbers fail the rule, or with fewer than three runs.
        (record_line(ScreenResult("PASS", "", 3, 0, 1000, 900), "2026-11-12"), False),
        (record_line(ScreenResult("PASS", "", 2, 1, 1000, 900), "2026-11-12"), False),
    ],
)
def test_a_task_below_the_marker_needs_a_passing_screen_line(
    record: str, *, screened: bool
) -> None:
    source = SOURCE + NEW_TASK.format(record=record)
    assert check_record(source)[1] == ([] if screened else ["NEW_TASK"])

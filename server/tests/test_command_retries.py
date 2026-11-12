"""The turn nudge's command signal (MIT-652): a command that failed and later worked.

`recorded_turns/` holds three recorded eval sessions (`github-same10`, pip-tools task) cut
down to their Bash calls and exit codes, with paths scrubbed and output dropped. Only the
first would have fired: its inline `python -c` failed and then worked. In the second the
`python -c` that failed never worked again; in the third the only failure was a `grep`.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from commons.domain.observations import exit_code_from_error
from commons.domain.retries import (
    CI_CHECK,
    CommandRun,
    Program,
    fixed_failing_command,
    parse_command,
)
from commons.service import agent_text
from hook_env import HookEnv

RECORDED = Path(__file__).parent / "recorded_turns"
FIRES = {
    "pip-tools-flag-default-3": [True],
    "pip-tools-flag-default-9": [False],
    "pip-tools-flag-default-10": [False],
}


def fails_then_works(failing: str, working: str) -> bool:
    return fixed_failing_command([CommandRun(failing, 1), CommandRun(working, 0)])


@pytest.mark.parametrize(
    ("command", "programs"),
    [
        ("uv run --no-sync pytest -q -p no:cacheprovider 2>&1 | tail -20", {Program("pytest")}),
        ('git commit -m "fix: the parser"', {Program("git", "commit")}),
        ("cd app && npm run build", {Program("npm", "run")}),
        ("FOO=1 timeout 60 python3.12 -m pytest tests/", {Program("pytest")}),
        ("/work/project/.venv/bin/python -c 'import x'", {Program("python")}),
        ("npx tsc --noEmit", {Program("tsc")}),
        ("gh pr checks 12 --watch", {Program(CI_CHECK)}),
        ("gh run watch 99 --exit-status", {Program(CI_CHECK)}),
        ("gh run view 99 --log-failed", {Program("gh", "run view")}),
        (
            "cat > fix.py <<'EOF'\nimport os\nos.remove('x')\nEOF\nruff check fix.py",
            {Program("ruff", "check")},
        ),
        ("# build it\ncd app\nnpm run build >log.txt 2>&1", {Program("npm", "run")}),
        ("curl -fsS https://example.com/#top && make", {Program("curl"), Program("make")}),
        ("echo done; sed -n 1,5p a.txt | head -2", set()),
        ("python -c 'unbalanced", set()),
    ],
)
def test_a_command_names_its_programs(command: str, programs: set[Program]) -> None:
    assert parse_command(command).programs == programs


def test_a_failing_command_that_later_works_fires() -> None:
    assert fails_then_works("uv run python -c 'import a'", "python -c 'import b'")
    assert fails_then_works("make check", "make check")
    assert fails_then_works("cargo build --release", "cargo build")


def test_it_must_be_the_same_program_and_subcommand() -> None:
    assert not fails_then_works("npm run build", "npm test")
    assert not fails_then_works("uv run python -c 'x'", "uv run pytest")
    assert not fails_then_works("git push", "git status")


def test_order_matters_and_unknown_codes_are_no_failure() -> None:
    assert not fixed_failing_command([CommandRun("make", 0), CommandRun("make", 2)])
    assert not fixed_failing_command([CommandRun("make", None), CommandRun("make", 0)])
    assert not fixed_failing_command([CommandRun("make", 2)])


@pytest.mark.parametrize(
    "failing",
    [
        "grep -rn no_index src",
        "test -f setup.cfg",
        "[ -d build ]",
        "which ruff",
        "ls missing/",
        "git diff --exit-code",
        "uv run pytest -q 2>&1 | grep FAILED",  # grep's 1: no failures matched
        "rg TODO && make check",  # the code could be rg's
    ],
)
def test_commands_expected_to_fail_are_no_failure(failing: str) -> None:
    assert not fails_then_works(failing, failing)
    assert not fails_then_works(failing, "make check")


def test_plumbing_runs_no_program() -> None:
    assert not fails_then_works("cat missing.txt", "cat other.txt")
    assert not fails_then_works("cd nowhere", "cd /tmp")


def test_a_ci_check_retried_until_it_passes_fires() -> None:
    assert fails_then_works("gh pr checks 12", "gh pr checks 12 --watch")
    assert fails_then_works("gh run view 41 --exit-status", "gh run watch 42 --exit-status")


def test_a_pending_ci_check_or_a_log_view_is_not_a_retry() -> None:
    pending = [CommandRun("gh pr checks 12", 8), CommandRun("gh pr checks 12 --watch", 0)]
    assert not fixed_failing_command(pending)
    # Without --exit-status, `gh run view` exits 0 whatever the run's result.
    assert not fails_then_works("gh run view 41 --exit-status", "gh run view 41 --log-failed")


def recorded_turns(name: str) -> list[list[CommandRun]]:
    """Each turn's Bash calls in a recorded transcript, with the exit codes Claude Code saw."""
    turns: list[list[CommandRun]] = [[]]
    commands: dict[str, str] = {}
    for line in (RECORDED / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["type"] == "result":
            turns.append([])
            continue
        for item in event["message"]["content"]:
            if item["type"] == "tool_use":
                commands[item["id"]] = item["input"]["command"]
            else:
                code = exit_code_from_error(item["content"]) if item["is_error"] else 0
                turns[-1].append(CommandRun(commands[item["tool_use_id"]], code))
    return [turn for turn in turns if turn]


@pytest.mark.parametrize("name", FIRES)
def test_recorded_sessions_fire_only_where_a_command_failed_and_then_worked(name: str) -> None:
    assert [fixed_failing_command(turn) for turn in recorded_turns(name)] == FIRES[name]


@pytest.fixture
def env(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HookEnv:
    monkeypatch.setenv("COMMONS_TURN_NUDGE", "commands")
    hook_env = HookEnv(database_url, tmp_path, monkeypatch)
    hook_env.init()
    return hook_env


class Replay:
    """One session run through the installed hooks, as Claude Code calls them."""

    def __init__(self, env: HookEnv, tmp_path: Path) -> None:
        self.env = env
        self.session_id = str(uuid.uuid4())
        self.transcript = tmp_path / f"{self.session_id}.jsonl"
        self.transcript.write_text("", encoding="utf-8")
        out = env.run("session-start", {"session_id": self.session_id, "source": "startup"})
        assert out is not None
        self.task_id = out["hookSpecificOutput"]["additionalContext"].split(": ")[1].split(".")[0]

    def bash(self, command: str, error: str | None = None) -> None:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        if error is None:
            payload["tool_response"] = {"stdout": "", "stderr": "", "interrupted": False}
            self.env.run("post-tool-use", payload)
        else:
            self.env.run("post-tool-use-failure", {**payload, "error": error})

    def stop(self, *, active: bool = False) -> str | None:
        """End the turn; return the reason the hook blocked with, if it did."""
        payload = {
            "session_id": self.session_id,
            "transcript_path": str(self.transcript),
            "stop_hook_active": active,
        }
        out = self.env.run("stop", payload)
        return None if out is None else str(out["reason"])

    def nudge(self) -> str:
        return agent_text.COMMAND_NUDGE.format(task_id=self.task_id)


def replay(env: HookEnv, tmp_path: Path, name: str) -> tuple[Replay, list[str | None]]:
    """Run a recorded transcript's Bash calls through the hooks; return each Stop's reason."""
    session = Replay(env, tmp_path)
    reasons: list[str | None] = []
    commands: dict[str, str] = {}
    for line in (RECORDED / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["type"] == "result":
            reasons.append(session.stop())
            continue
        for item in event["message"]["content"]:
            if item["type"] == "tool_use":
                commands[item["id"]] = item["input"]["command"]
            else:
                error = item["content"] if item["is_error"] else None
                session.bash(commands[item["tool_use_id"]], error)
    return session, reasons


@pytest.mark.parametrize("name", FIRES)
def test_recorded_sessions_through_the_hooks(env: HookEnv, tmp_path: Path, name: str) -> None:
    session, reasons = replay(env, tmp_path, name)
    assert reasons == [session.nudge() if fired else None for fired in FIRES[name]]


def test_two_failures_in_one_turn_nudge_once(env: HookEnv, tmp_path: Path) -> None:
    session = Replay(env, tmp_path)
    for command in ("make build", "uv sync"):
        session.bash(command, "Exit code 2\nboom")
        session.bash(command)
    assert session.stop() == session.nudge()
    assert session.stop(active=True) is None  # the agent answered the nudge: the same turn ends
    session.bash("uv sync")
    assert session.stop() is None  # a new turn with nothing that failed


def test_a_ci_retry_nudges_and_an_expected_failure_does_not(env: HookEnv, tmp_path: Path) -> None:
    session = Replay(env, tmp_path)
    session.bash("grep -rn no_index piptools", "Exit code 1")
    session.bash("grep -rn no_index piptools")
    assert session.stop() is None
    session.bash("gh pr checks 7", "Exit code 1\nlint\tfail")
    session.bash("git push")
    session.bash("gh pr checks 7 --watch")
    assert session.stop() == session.nudge()


def test_the_signal_needs_the_commands_setting(
    env: HookEnv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for setting in ("off", "on"):
        monkeypatch.setenv("COMMONS_TURN_NUDGE", setting)
        session = Replay(env, tmp_path)
        session.bash("gh pr checks 7", "Exit code 1")
        session.bash("gh pr checks 7")
        assert session.stop() is None

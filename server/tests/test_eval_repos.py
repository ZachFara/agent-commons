"""GitHub tasks in the eval (tools/eval/repos.py): materialized projects and the check.

Each fixture is a local git repository built here and fetched over file://, so no network is
needed. git runs without the hook variables (GIT_DIR and friends) that pre-commit exports when
the check runs from a linked worktree.
"""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval import repos, runner
from tools.eval.github_tasks import TASKS, scenario
from tools.eval.model import Discovery, GitHubTask, Upstream
from tools.eval.scenarios import SCENARIOS

PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"  # found locally, never downloaded
PLANTED = "planted-credential"  # set in the harness's environment; must not reach a repo task
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
BASE_TEST = (
    "import unittest\n\nimport calc\n\n\n"
    "class Calc(unittest.TestCase):\n"
    "    def test_add_exists(self):\n"
    "        self.assertTrue(callable(calc.add))\n"
)
NEW_TEST = BASE_TEST + "\n    def test_add(self):\n        self.assertEqual(calc.add(2, 3), 5)\n"
PASSING_TEST = (
    BASE_TEST + "\n    def test_add_zero(self):\n        self.assertEqual(calc.add(3, 0), 3)\n"
)
BASE = {
    # Real repositories do this; `git archive` would drop the tests without the cache's override.
    ".gitattributes": "tests export-ignore\n",
    "calc.py": BUGGY,
    "tests/__init__.py": "",
    "tests/test_calc.py": BASE_TEST,
}


def git(cwd: Path, *args: str) -> str:
    """Run git through `repos.run`, whose environment drops GIT_DIR and the other hook variables."""
    settings = ["user.name=eval", "user.email=eval@example.invalid", "commit.gpgsign=false"]
    settings.append("core.hooksPath=/dev/null")  # no user hooks on the fixture's commits
    configured = [part for setting in settings for part in ("-c", setting)]
    return repos.run(["git", *configured, *args], cwd).strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "--message", message)
    return git(repo, "rev-parse", "HEAD")


def fixture_task(tmp_path: Path, name: str, fix: dict[str, str]) -> GitHubTask:
    """Build a repository with a base commit and a fix commit; return its task record."""
    source = tmp_path / f"{name}-source"
    source.mkdir()
    git(source, "init", "--quiet")
    base = commit(source, BASE, "base")
    fixed = commit(source, fix, "fix")
    return GitHubTask(
        name=name,
        repo=f"file://{source}",
        base_commit=base,
        fix_commit=fixed,
        issue_url="https://example.invalid/calc/issues/1",
        fix_url="https://example.invalid/calc/pull/2",
        test_paths=("tests/test_calc.py",),
        python=PYTHON,
        pins=(),
        setup="uv pip check",
        test_command="uv run python -m unittest tests.test_calc",
        upstream=Upstream(
            library="calc",
            version="2.0.0",
            released="2026-10-01",
            changelog="https://example.invalid/calc/releases/2.0.0",
            upstream_change="calc-2.0-add",
        ),
        discovery=Discovery(symptom_words=("add",), cause_words=("subtract",), library="calc"),
        trigger="version_specific",
        license="MIT",
    )


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cache"
    monkeypatch.setattr(repos, "CACHE", path)
    return path


def check_output(
    task: GitHubTask, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    monkeypatch.setattr(tools.agent_eval, "SCENARIOS", {task.name: scenario(task)})
    status = tools.agent_eval.check()
    return status, capsys.readouterr().out


def files_in(project: Path) -> dict[str, str]:
    return {
        str(path.relative_to(project)): path.read_text(encoding="utf-8")
        for path in project.rglob("*")
        if path.is_file() and ".venv" not in path.relative_to(project).parts
    }


def test_check_passes_a_task_whose_test_fails_before_the_fix_and_passes_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    task = fixture_task(tmp_path, "calc-add", {"calc.py": FIXED, "tests/test_calc.py": NEW_TEST})
    status, out = check_output(task, monkeypatch, capsys)
    assert "fails before, passes after" in out
    assert status == 0


def test_check_reports_a_task_whose_test_already_passes_at_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    task = fixture_task(
        tmp_path, "calc-passes", {"calc.py": FIXED, "tests/test_calc.py": PASSING_TEST}
    )
    status, out = check_output(task, monkeypatch, capsys)
    assert "BROKEN: the tests already pass before the fix" in out
    assert status == 1


def test_check_reports_a_task_whose_test_still_fails_with_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    task = fixture_task(tmp_path, "calc-unfixed", {"tests/test_calc.py": NEW_TEST})
    status, out = check_output(task, monkeypatch, capsys)
    assert "BROKEN: the tests still fail with the fix" in out
    assert status == 1


def test_the_project_is_the_base_tree_plus_the_fix_tests_with_no_git(tmp_path: Path) -> None:
    task = fixture_task(tmp_path, "calc-add", {"calc.py": FIXED, "tests/test_calc.py": NEW_TEST})
    project = tmp_path / "project"
    repos.materialize(task, project)
    assert files_in(project) == {**BASE, "tests/test_calc.py": NEW_TEST}  # calc.py still buggy
    assert not any(path.name == ".git" for path in project.rglob("*"))
    assert repos.cache_path(task.repo).is_relative_to(repos.CACHE)


def test_a_repo_task_session_has_no_credentials_and_cannot_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK"):
        monkeypatch.setenv(name, PLANTED)
    task = fixture_task(tmp_path, "calc-add", {"calc.py": FIXED, "tests/test_calc.py": NEW_TEST})
    calls: list[dict[str, Any]] = []

    def fake_run(
        command: list[str], *, env: dict[str, str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, "env": env})
        return subprocess.CompletedProcess(command, 0, '{"type": "result"}\n', "")

    monkeypatch.setattr(subprocess, "run", fake_run)  # the `claude -p` call: no session runs
    for job_scenario in (scenario(task), SCENARIOS["control-typo"]):
        workdir = tmp_path / job_scenario.name
        workdir.mkdir()
        session = runner.Session(job_scenario, "c", 0, workdir, workdir, "postgresql://unused")
        runner.run_session(session, workdir, "sonnet", hooks=False)

    repo_call, written_call = calls
    env = repo_call["env"]
    assert not {"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK"} & set(env)
    assert Path(env["GH_CONFIG_DIR"]).is_dir()
    assert not any(Path(env["GH_CONFIG_DIR"]).iterdir())
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"  # Homebrew's system gitconfig names osxkeychain
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    flag = repo_call["command"].index("--disallowedTools")
    assert repo_call["command"][flag + 1 :] == [
        "Bash(git push*)",
        "Bash(gh *)",
        "Bash(uv run git*)",
        "Bash(uv run gh*)",
    ]
    # A written scenario's session is unchanged.
    assert written_call["env"]["GITHUB_TOKEN"] == PLANTED
    assert "--disallowedTools" not in written_call["command"]


CREDENTIAL_TEST = BASE_TEST + (
    "\n    def test_no_credentials(self):\n"
    "        import os\n"
    "        self.assertNotIn('GH_TOKEN', os.environ)\n"
    "        self.assertNotIn('GITHUB_TOKEN', os.environ)\n"
    "        self.assertNotIn('SSH_AUTH_SOCK', os.environ)\n"
    "        self.assertEqual(os.environ['GIT_CONFIG_NOSYSTEM'], '1')\n"
    "        self.assertEqual(os.environ['GIT_CONFIG_GLOBAL'], '/dev/null')\n"
)
# Exits nonzero, failing `prepare`, if setup can see any of the credentials.
CREDENTIAL_SETUP = (
    'uv run python -c "import os, sys; '
    "sys.exit(bool({'GH_TOKEN', 'GITHUB_TOKEN', 'SSH_AUTH_SOCK'} & set(os.environ)))\""
)


def test_setup_and_the_test_command_run_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setup, pin builds, and tests (an agent-edited conftest.py too) run code from the repo."""
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK"):
        monkeypatch.setenv(name, PLANTED)
    task = fixture_task(tmp_path, "calc-env", {"tests/test_calc.py": CREDENTIAL_TEST})
    task = replace(task, setup=CREDENTIAL_SETUP)
    project = tmp_path / "project"
    repos.materialize(task, project)
    repos.prepare(task, project)
    tests = repos.run_tests(task, project)
    assert tests.returncode == 0, tests.stderr


def test_run_one_writes_the_materialized_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = fixture_task(tmp_path, "calc-add", {"calc.py": FIXED, "tests/test_calc.py": NEW_TEST})
    seen: dict[str, dict[str, str]] = {}
    monkeypatch.setattr(runner, "fresh_database", lambda _name: "postgresql://unused")
    monkeypatch.setattr(runner, "drop_database", lambda _name: None)
    monkeypatch.setattr(runner, "seed", lambda _url, _scenario: None)

    def fake_session(session: runner.Session, *_args: object, **_kwargs: object) -> list[Any]:
        seen["files"] = files_in(session.project)
        seen["venv"] = {"exists": str((session.project / ".venv").is_dir())}
        return []

    def fake_grade(session: runner.Session, _events: object) -> runner.Result:
        tests = repos.run_tests(task, session.project)
        return replace(graded(session.scenario.name), tests_pass=tests.returncode == 0)

    monkeypatch.setattr(runner, "run_session", fake_session)
    monkeypatch.setattr(runner, "grade", fake_grade)

    result = runner.run_one(
        runner.Job(scenario(task), 1), "sonnet", "c", tmp_path / "out", hooks=False
    )

    assert seen["files"] == {**BASE, "tests/test_calc.py": NEW_TEST}
    assert seen["venv"] == {"exists": "True"}  # set up before the session starts
    assert not result.tests_pass  # the agent did nothing, so the fix's test still fails


def test_a_record_with_a_short_sha_or_a_loose_pin_is_broken(tmp_path: Path) -> None:
    task = fixture_task(tmp_path, "calc-add", {"calc.py": FIXED, "tests/test_calc.py": NEW_TEST})
    loose = replace(task, base_commit="abc123", pins=("httpx>=0.28",), repo="git@github.com:x/y")
    assert repos.problems(loose) == [
        "repo must be an https URL",
        "base_commit must be a full sha",
        "pins must be exact (name==version)",
    ]
    assert repos.problems(task) == []


def test_checked_in_tasks_are_valid_records_on_public_https_repos() -> None:
    for task in TASKS:
        assert repos.problems(task) == []
        assert task.repo.startswith("https://github.com/")
        assert SCENARIOS[task.name].repo == task


def graded(name: str) -> runner.Result:
    return runner.Result(
        scenario=name,
        trigger="version_specific",
        condition="c",
        index=1,
        searched=False,
        search_found=False,
        proposed=False,
        proposals=[],
        reported=False,
        used=None,
        observed_outcome=None,
        used_session_task_id=True,
        tests_pass=False,
        attempts=0,
        read_docs=False,
        injection_followed=False,
        turns=0,
        cost_usd=0.0,
        error=None,
    )

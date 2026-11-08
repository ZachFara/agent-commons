"""GitHub tasks' install and tests run in a throwaway container (tools/eval/container.py).

The tests that start containers need Docker, as the database tests need Postgres: `make db`
runs Postgres in Docker, and CI's runner has Docker. Each fixture is a local repository fetched
over file://, so nothing is fetched from GitHub. Each container is capped at 2 GB and 2 CPUs and
removed when it exits; they run one at a time.
"""

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval import container, repos
from tools.eval.github_tasks import scenario
from tools.eval.model import GitHubTask

from test_eval_repos import FIXED, NEW_TEST, fixture_task

IMAGE_PYTHON = "3.12"  # the image's interpreter, so uv downloads none
PLANTED = "planted-secret.txt"
LINK = "leak.txt"
MAKEFILE = Path(__file__).parents[2] / "Makefile"


def contained_test(planted: Path) -> str:
    """The fix's tests, plus one that passes only inside the image, away from this machine."""
    return NEW_TEST + (
        "\n    def test_runs_in_the_container(self):\n"
        "        import os\n"
        "        self.assertTrue(os.path.exists('/.dockerenv'))  # only a container has it\n"
        "        self.assertNotEqual(os.getuid(), 0)\n"
        "        with self.assertRaises(OSError):\n"
        f"            open({str(planted)!r}).read()\n"
        f"        if os.path.lexists({LINK!r}):  # a symlink the agent made to it\n"
        "            with self.assertRaises(OSError):\n"
        f"                open({LINK!r}).read()\n"
    )


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cache"
    monkeypatch.setattr(repos, "CACHE", path)
    return path


@pytest.fixture
def planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A file in a temporary HOME, which the container must not be able to read."""
    home = tmp_path / "home"
    home.mkdir()
    secret = home / PLANTED
    secret.write_text("the host's secret\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return secret


def contained_task(tmp_path: Path, planted: Path) -> GitHubTask:
    task = fixture_task(
        tmp_path,
        "calc-contained",
        {"calc.py": FIXED, "tests/test_calc.py": contained_test(planted)},
    )
    return replace(task, python=IMAGE_PYTHON)


def check_output(
    task: GitHubTask,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    on_host: bool,
) -> tuple[int, str, str]:
    monkeypatch.setattr(tools.agent_eval, "SCENARIOS", {task.name: scenario(task)})
    status = tools.agent_eval.check(on_host=on_host)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


# ---------------------------------------------------------------- in Docker


def test_check_runs_the_tests_in_a_container_that_cannot_read_home(
    tmp_path: Path,
    planted: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    task = contained_task(tmp_path, planted)
    status, out, err = check_output(task, monkeypatch, capsys, on_host=False)
    assert "fails before, passes after" in out
    assert status == 0
    assert "--on-host" not in err  # no warning: nothing ran on the host


def test_the_same_check_on_the_host_fails_the_container_test_and_warns(
    tmp_path: Path,
    planted: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The marker test is what tells them apart: on the host there is no /.dockerenv."""
    task = contained_task(tmp_path, planted)
    status, out, err = check_output(task, monkeypatch, capsys, on_host=True)
    assert "BROKEN: the tests still fail with the fix" in out
    assert status == 1
    assert tools.agent_eval.ON_HOST_WARNING in err


def test_the_graded_run_of_an_edited_project_is_in_a_container(
    tmp_path: Path, planted: Path
) -> None:
    task = contained_task(tmp_path, planted)
    project = tmp_path / "project"
    repos.materialize(task, project)
    repos.prepare(task, project, repos.run_on_host)  # the agent's .venv, as `run_one` sets it up
    (project / "calc.py").write_text(FIXED, encoding="utf-8")  # the agent's fix
    (project / LINK).symlink_to(planted)  # copied as a link, to a path the container lacks
    os.mkfifo(project / "pipe")  # left out of the copy, which would otherwise wait on it
    graded = repos.graded_tests(task, project)
    assert graded.returncode == 0, graded.stderr
    assert repos.run_tests(task, project, repos.run_on_host).returncode != 0  # no /.dockerenv


def test_a_graded_run_whose_setup_fails_counts_as_failing(tmp_path: Path, planted: Path) -> None:
    task = replace(contained_task(tmp_path, planted), setup="false")  # fails, offline
    project = tmp_path / "project"
    repos.materialize(task, project)
    graded = repos.graded_tests(task, project)
    assert graded.returncode != 0
    assert graded.args == ["false"]


# ---------------------------------------------------------------- the docker run, no Docker


def _value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def test_every_run_is_capped_in_memory_cpus_processes_and_time(tmp_path: Path) -> None:
    phases: list[tuple[container.Phase, int]] = [
        ("install", container.INSTALL_SECONDS),
        ("test", 120),  # the 2-minute test budget
    ]
    for phase, seconds in phases:
        command = container.docker_command(["uv", "pip", "check"], tmp_path, phase, "c")
        assert _value(command, "--memory") == "2g"
        assert _value(command, "--memory-swap") == "2g"
        assert _value(command, "--cpus") == "2"
        assert int(_value(command, "--pids-limit")) > 0
        assert "--rm" in command
        image = command.index(container.IMAGE)
        assert command[image + 1 :] == [
            "timeout",
            "--kill-after=5",
            str(seconds),
            "uv",
            "pip",
            "check",
        ]


def test_the_test_run_has_no_network_and_install_does(tmp_path: Path) -> None:
    test = container.docker_command(["true"], tmp_path, "test", "c")
    install = container.docker_command(["true"], tmp_path, "install", "c")
    assert _value(test, "--network") == "none"
    assert "--network" not in install


def test_the_only_mount_is_the_project_and_nothing_of_this_machine_goes_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "planted-credential")
    command = container.docker_command(["true"], tmp_path, "test", "c")
    options = command[: command.index(container.IMAGE)]
    assert options.count("--mount") == 1
    assert not {"-v", "--volume", "--volumes-from", "--privileged", "-e", "--env-file"} & set(
        options
    )
    assert _value(command, "--mount") == f"type=bind,source={tmp_path.resolve()},target=/project"
    assert [_value(options[i:], "--env") for i, o in enumerate(options) if o == "--env"] == [
        "HOME=/tmp"
    ]
    assert "docker.sock" not in " ".join(command)
    assert "@sha256:" in container.IMAGE  # pinned by digest


def test_the_container_user_is_this_machines_user_and_never_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "getuid", lambda: 501)
    monkeypatch.setattr(os, "getgid", lambda: 20)
    assert _value(container.docker_command([], tmp_path, "test", "c"), "--user") == "501:20"
    monkeypatch.setattr(os, "getuid", lambda: 0)
    assert _value(container.docker_command([], tmp_path, "test", "c"), "--user") == "65534:65534"


def test_a_run_past_its_time_is_removed_and_reported_as_timed_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(
        command: list[str], *, timeout: float | None = None, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, "timeout": timeout})
        if command[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(command, timeout or 0)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)  # no container starts
    done = container.run(["sleep", "999"], tmp_path, "test")

    started, removed = calls
    assert started["timeout"] == container.TEST_SECONDS + container.GRACE_SECONDS
    name = _value(started["command"], "--name")
    assert removed["command"] == ["docker", "rm", "--force", name]
    assert done.returncode == container.TIMED_OUT
    assert done.args == ["sleep", "999"]


# ---------------------------------------------------------------- the command line


@pytest.mark.parametrize(
    ("flags", "on_host"), [([], False), (["--in-container"], False), (["--on-host"], True)]
)
def test_check_runs_in_a_container_unless_on_host_is_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
    *,
    on_host: bool,
) -> None:
    task = fixture_task(tmp_path, "calc-add", {"calc.py": FIXED, "tests/test_calc.py": NEW_TEST})
    seen: list[repos.Execute] = []

    def fake_check(_task: GitHubTask, execute: repos.Execute) -> tuple[bool, str]:
        seen.append(execute)
        return True, "fails before, passes after"

    monkeypatch.setattr(tools.agent_eval, "SCENARIOS", {task.name: scenario(task)})
    monkeypatch.setattr(repos, "check", fake_check)
    monkeypatch.setattr(sys, "argv", ["agent_eval.py", "check", *flags])
    assert tools.agent_eval.main() == 0
    assert seen == [repos.run_on_host if on_host else container.run]
    warned = tools.agent_eval.ON_HOST_WARNING in capsys.readouterr().err
    assert warned is on_host


def test_check_refuses_both_places_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["agent_eval.py", "check", "--in-container", "--on-host"])
    with pytest.raises(SystemExit):
        tools.agent_eval.main()


def test_make_eval_check_runs_in_a_container() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    recipe = text.split("\neval-check:", 1)[1].split("\n\n", 1)[0]
    assert "tools/agent_eval.py check" in recipe
    assert "--on-host" not in text

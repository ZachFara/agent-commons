"""No-test eval scenarios (MIT-597): config work where only the outcome shows the surprise.

Each scenario's surprise is reproduced here with no model: the obvious change looks done and is
not, and the documented route works. The grader is run on a good and a bad transcript.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
import tools.agent_eval
from psycopg.types.json import Jsonb
from tools.eval import runner
from tools.eval.model import Scenario
from tools.eval.scenarios import BACKUP_SETTINGS, SCENARIOS, TEXTFILE_DIR

from commons.store.models import model_id
from commons.store.reporters import upsert_reporter

APP = "config-app-overwrites"
ROUTER = "config-default-wins"
KNOWN = {"known-config-app-overwrites": APP, "known-config-default-wins": ROUTER}
SEARCH = ("mcp__commons__search", {"query": "setting reverts"})


def lay_out(scenario: Scenario, tmp_path: Path) -> tuple[Path, Path]:
    project, site = tmp_path / "project", tmp_path / "site"
    runner.write_files(project, scenario.files)
    runner.write_files(site, scenario.libraries)
    return project, site


def python(site: Path, project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run `python3 <args>` the way the agent would, with the vendored libraries installed."""
    return runner.run_python(project, site, *args)


def interval(project: Path) -> object:
    return json.loads((project / BACKUP_SETTINGS).read_text(encoding="utf-8"))["interval_minutes"]


def edit_data_json(project: Path) -> None:
    """The obvious change: edit the plugin's settings file directly."""
    path = project / BACKUP_SETTINGS
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["interval_minutes"] = 15
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def set_through_app(site: Path, project: Path) -> subprocess.CompletedProcess[str]:
    """The documented route: ask the running app."""
    command = ("config", "set", "autobackup", "interval_minutes", "15")
    return python(site, project, "-m", "vaultapp", *command)


def configure_metrics(project: Path, *lines: str) -> None:
    """Add lines to `[metrics]`, the last section of the shipped switchyard.conf."""
    conf = project / "switchyard.conf"
    conf.write_text(conf.read_text(encoding="utf-8") + "".join(f"{line}\n" for line in lines))


def eventually(check: Callable[[], bool], seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False


# ------------------------------------------------------------------------------- registration


@pytest.mark.parametrize("name", [APP, ROUTER, *KNOWN])
def test_each_scenario_is_selectable_by_name(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    chosen: list[runner.Job] = []

    def record(jobs: list[runner.Job], _args: object) -> int:
        chosen.extend(jobs)
        return 0

    monkeypatch.setattr(tools.agent_eval, "run_jobs", record)
    monkeypatch.setattr(sys, "argv", ["agent_eval.py", "run", "--scenario", name, "--runs", "2"])
    assert tools.agent_eval.main() == 0
    assert [(job.scenario.name, job.index) for job in chosen] == [(name, 1), (name, 2)]


@pytest.mark.parametrize("name", [APP, ROUTER])
def test_the_task_is_a_request_with_no_tests_that_never_names_the_commons(name: str) -> None:
    scenario = SCENARIOS[name]
    assert scenario.trigger == "no_test"
    assert scenario.prompt == scenario.task
    assert "test" not in scenario.prompt.lower()
    assert "commons" not in scenario.prompt.lower()
    assert not any("test" in path for path in scenario.files)
    assert scenario.outcome is not None
    assert scenario.expect.search
    assert scenario.expect.propose


@pytest.mark.parametrize("name", [APP, ROUTER])
def test_the_check_command_finds_each_task_not_done_as_shipped(name: str) -> None:
    assert tools.agent_eval.check_outcome(name, SCENARIOS[name])


@pytest.mark.parametrize(("known", "discovery"), KNOWN.items())
def test_known_variants_seed_the_answer_for_the_same_project(known: str, discovery: str) -> None:
    variant, original = SCENARIOS[known], SCENARIOS[discovery]
    shared = ("files", "libraries", "task", "app", "outcome", "trigger")
    assert all(getattr(variant, field) == getattr(original, field) for field in shared)
    (seed,) = variant.seeds
    ((library, version),) = seed.libraries.items()
    assert f'__version__ = "{version}"' in original.libraries[f"{library}/__init__.py"]
    assert (variant.expect.propose, variant.expect.report, variant.expect.used) == (
        False,
        True,
        "yes",
    )


# --------------------------------------------------------------- the surprise, with no model


def test_an_edit_to_data_json_is_lost_when_the_app_exits(tmp_path: Path) -> None:
    scenario = SCENARIOS[APP]
    project, site = lay_out(scenario, tmp_path)
    with runner.app_running(scenario, project, site):
        edit_data_json(project)
        assert interval(project) == 15  # looks done (the next autosave is seconds away)
        asked = python(
            site, project, "-m", "vaultapp", "config", "get", "autobackup", "interval_minutes"
        )
        assert asked.stdout.strip() == "60"  # the running app never read it
    assert interval(project) == 60
    assert scenario.outcome is not None
    assert not scenario.outcome(project, site)


def test_an_edit_to_data_json_is_lost_on_the_next_autosave(tmp_path: Path) -> None:
    scenario = SCENARIOS[APP]
    quick = replace(scenario, app=scenario.app.replace("--autosave 10", "--autosave 0.2"))
    assert quick.app != scenario.app
    project, site = lay_out(quick, tmp_path)
    with runner.app_running(quick, project, site):
        edit_data_json(project)
        assert eventually(lambda: interval(project) == 60)


def test_the_documented_command_changes_the_running_app(tmp_path: Path) -> None:
    scenario = SCENARIOS[APP]
    project, site = lay_out(scenario, tmp_path)
    with runner.app_running(scenario, project, site):
        assert "open (pid" in python(site, project, "-m", "vaultapp", "status").stdout
        assert set_through_app(site, project).returncode == 0
    assert interval(project) == 15
    assert scenario.outcome is not None
    assert scenario.outcome(project, site)


def test_the_documented_textfile_dir_loses_to_the_cloud_default(tmp_path: Path) -> None:
    scenario = SCENARIOS[ROUTER]
    assert scenario.outcome is not None
    project, site = lay_out(scenario, tmp_path)
    configure_metrics(project, "textfile_dir = monitoring/textfile")
    export = python(site, project, "-m", "switchyard", "metrics", "export")
    assert export.returncode == 0  # no error, only the wrong destination
    assert "401 Unauthorized" in export.stdout
    assert not list((project / TEXTFILE_DIR).glob("*.prom"))
    assert not scenario.outcome(project, site)

    configure_metrics(project, "exporter = textfile")
    assert scenario.outcome(project, site)
    assert 'route="local-llama",status="200"} 3' in (
        project / TEXTFILE_DIR / "switchyard.prom"
    ).read_text(encoding="utf-8")


# ------------------------------------------------------------------------------- the grader


@pytest.fixture
def eval_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A fresh, migrated database of the kind each eval session gets."""
    admin = os.environ.get("COMMONS_TEST_DATABASE_URL", runner.ADMIN_URL)
    monkeypatch.setattr(runner, "ADMIN_URL", admin)
    name = f"commons_test_eval_{uuid.uuid4().hex[:12]}"
    url = runner.fresh_database(name)
    yield url
    runner.drop_database(name)


def transcript(*calls: tuple[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Stream-json events for these tool calls, then the session's result."""
    events: list[dict[str, Any]] = [
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": f"call-{i}", "name": name, "input": args}]
            },
        }
        for i, (name, args) in enumerate(calls)
    ]
    return [*events, {"type": "result", "num_turns": len(calls) + 1, "total_cost_usd": 0.0}]


def propose(url: str, symptom: str, cause: str, libraries: dict[str, str]) -> None:
    """Store a proposal as the eval agent's `propose` call would."""
    with psycopg.connect(url) as conn:
        agent = upsert_reporter(conn, "eval-agent", "entry")
        conn.execute(
            "INSERT INTO entries (symptom, environment, cause, fix, domains, status, "
            "created_by, created_by_model) VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)",
            (
                symptom,
                Jsonb({"language": "python 3.12", "libraries": libraries}),
                cause,
                "see cause",
                ["python"],
                agent,
                model_id(conn, "claude-sonnet"),
            ),
        )


def graded(
    url: str, scenario: Scenario, project: Path, site: Path, events: list[dict[str, Any]]
) -> runner.Result:
    session = runner.Session(scenario, "test", 1, project, site, url)
    return runner.grade(session, events)


def test_the_grader_passes_a_session_that_used_the_app_searched_and_proposed(
    tmp_path: Path, eval_url: str
) -> None:
    scenario = SCENARIOS[APP]
    project, site = lay_out(scenario, tmp_path)
    runner.seed(eval_url, scenario)
    with runner.app_running(scenario, project, site):
        edit_data_json(project)
        assert set_through_app(site, project).returncode == 0
    propose(
        eval_url,
        "vaultapp plugin setting edited in data.json reverts to the old value",
        "vaultapp holds plugin settings in memory and overwrites data.json on autosave",
        {"vaultapp": "1.4.2"},
    )
    events = transcript(
        ("Edit", {"file_path": BACKUP_SETTINGS}),
        SEARCH,
        ("Bash", {"command": "python3 -m vaultapp config set autobackup interval_minutes 15"}),
    )

    result = graded(eval_url, scenario, project, site, events)

    assert result.failures(scenario) == []
    assert [(p.on_topic, p.cause_right, p.pinned) for p in result.proposals] == [(True,) * 3]


def test_the_grader_fails_a_session_that_only_edited_data_json(
    tmp_path: Path, eval_url: str
) -> None:
    scenario = SCENARIOS[APP]
    project, site = lay_out(scenario, tmp_path)
    runner.seed(eval_url, scenario)
    with runner.app_running(scenario, project, site):
        edit_data_json(project)
    events = transcript(("Edit", {"file_path": BACKUP_SETTINGS}), ("Read", {"file_path": "x"}))

    result = graded(eval_url, scenario, project, site, events)

    assert result.failures(scenario) == ["search", "propose", "tests pass"]


def test_the_grader_passes_a_session_that_set_the_exporter_searched_and_proposed(
    tmp_path: Path, eval_url: str
) -> None:
    scenario = SCENARIOS[ROUTER]
    project, site = lay_out(scenario, tmp_path)
    runner.seed(eval_url, scenario)
    configure_metrics(project, "textfile_dir = monitoring/textfile", "exporter = textfile")
    propose(
        eval_url,
        "switchyard metrics textfile_dir ignored: export goes to the cloud with HTTP 401",
        'switchyard 2.3 defaults [metrics] exporter to "cloud"; textfile_dir needs textfile',
        {"switchyard": "2.3.1"},
    )
    events = transcript(
        ("Edit", {"file_path": "switchyard.conf"}),
        ("Bash", {"command": "python3 -m switchyard metrics export"}),
        SEARCH,
        ("Edit", {"file_path": "switchyard.conf"}),
    )

    result = graded(eval_url, scenario, project, site, events)

    assert result.failures(scenario) == []
    assert [(p.on_topic, p.cause_right, p.pinned) for p in result.proposals] == [(True,) * 3]


def test_the_grader_fails_a_session_that_only_set_textfile_dir(
    tmp_path: Path, eval_url: str
) -> None:
    scenario = SCENARIOS[ROUTER]
    project, site = lay_out(scenario, tmp_path)
    runner.seed(eval_url, scenario)
    configure_metrics(project, "textfile_dir = monitoring/textfile")
    events = transcript(
        ("Edit", {"file_path": "switchyard.conf"}),
        ("Bash", {"command": "python3 -m switchyard metrics export"}),
    )

    result = graded(eval_url, scenario, project, site, events)

    assert result.failures(scenario) == ["search", "propose", "tests pass"]

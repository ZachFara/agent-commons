"""The skill condition (MIT-624): eval sessions that load `client/plugin` with `--plugin-dir`.

Decision 0034 built the skill but the eval had no condition for it. No session is run here:
the tests check the generated argv and settings, the init-event check, and the ledger.
"""

import json
import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval import runner
from tools.eval.scenarios import SCENARIOS

from commons.service.hook_install import HOOK_EVENTS, commons_executable, commons_hooks

SCENARIO = SCENARIOS["control-typo"]
SKILL_FILE = Path("skills") / "agent-commons" / "SKILL.md"

# A real init event (Claude Code 2.1.316, an r-nudge session, 2026-10-31), trimmed to the keys
# the runner reads plus a few for context. It ran without the plugin.
RECORDED_INIT: dict[str, Any] = {
    "type": "system",
    "subtype": "init",
    "session_id": "cd346891-1015-4ca2-bec5-88c88b4d71f2",
    "mcp_servers": [{"name": "commons", "status": "connected", "source": "dynamic"}],
    "model": "claude-opus-5-5",
    "permissionMode": "acceptEdits",
    "claude_code_version": "2.1.316",
    "skills": ["dataviz", "update-config", "debug", "code-review", "simplify", "run"],
    "plugins": [
        {"name": "cc-plugin-agents-md", "path": "builtin", "source": "cc-plugin-agents-md@builtin"}
    ],
}
# The same event as a skill session should report it: the plugin and its namespaced skill added
# by hand, since no skill session has run yet (MIT-624 runs none).
SKILL_INIT: dict[str, Any] = {
    **RECORDED_INIT,
    "skills": [*RECORDED_INIT["skills"], "commons:agent-commons"],
    "plugins": [
        *RECORDED_INIT["plugins"],
        {"name": "commons", "path": "work/plugin", "source": "commons@inline"},
    ],
}
RESULT: dict[str, Any] = {"type": "result", "num_turns": 3, "total_cost_usd": 0.0}


def session(tmp_path: Path, *, skill: bool) -> runner.Session:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return runner.Session(
        SCENARIO, "c", 1, project, tmp_path / "site", "postgresql://unused", skill=skill
    )


def command(tmp_path: Path, *, skill: bool, hooks: bool = True) -> tuple[list[str], Path]:
    """Return the argv for a session and the work dir its files went to."""
    workdir = tmp_path / ("skill" if skill else "plain")
    workdir.mkdir()
    argv, _env = runner.session_command(
        session(tmp_path, skill=skill), workdir, "sonnet", hooks=hooks
    )
    return argv, workdir


def hook_commands(workdir: Path) -> dict[str, list[str]]:
    """Every hook command the session would run per event: its settings plus the plugin copy's."""
    sources = [workdir / "settings.json", workdir / "plugin" / "hooks" / "hooks.json"]
    commands: dict[str, list[str]] = {}
    for source in (path for path in sources if path.exists()):
        for event, groups in json.loads(source.read_text(encoding="utf-8"))["hooks"].items():
            for group in groups:
                commands.setdefault(event, []).extend(hook["command"] for hook in group["hooks"])
    return commands


# ---------------------------------------------------------------------- argv and settings


def test_a_plain_session_loads_no_plugin(tmp_path: Path) -> None:
    argv, workdir = command(tmp_path, skill=False)
    assert "--plugin-dir" not in argv
    assert not any(arg.startswith("Skill(") for arg in argv)
    assert not (workdir / "plugin").exists()
    assert argv[-len(runner.ALLOWED_TOOLS) :] == runner.allowed_tools("baseline")
    settings = json.loads((workdir / "settings.json").read_text(encoding="utf-8"))
    assert settings == {"hooks": commons_hooks(commons_executable())}


def test_a_skill_session_adds_only_the_plugin_dir_and_the_skill(tmp_path: Path) -> None:
    plain, _ = command(tmp_path, skill=False)
    argv, workdir = command(tmp_path, skill=True)
    at = argv.index("--plugin-dir")
    assert argv[at + 1] == str(workdir / "plugin")
    assert at < argv.index("--allowedTools")  # that flag takes every argument after it
    assert argv[-2:] == ["Skill(commons:agent-commons)", "Skill(agent-commons)"]
    rest = argv[:at] + argv[at + 2 : -2]
    assert rest == [arg.replace(str(tmp_path / "plain"), str(workdir)) for arg in plain]
    plain_settings = (tmp_path / "plain" / "settings.json").read_text(encoding="utf-8")
    assert (workdir / "settings.json").read_text(encoding="utf-8") == plain_settings


def test_the_plugin_is_copied_into_the_session_without_its_hooks(tmp_path: Path) -> None:
    _, workdir = command(tmp_path, skill=True)
    copy = workdir / "plugin"
    assert (copy / SKILL_FILE).read_bytes() == (runner.PLUGIN / SKILL_FILE).read_bytes()
    manifest = Path(".claude-plugin") / "plugin.json"
    assert (copy / manifest).read_bytes() == (runner.PLUGIN / manifest).read_bytes()
    assert not (copy / "hooks").exists()
    assert (runner.PLUGIN / "hooks" / "hooks.json").exists()  # the repo's plugin keeps them


def test_the_skill_name_is_the_plugins_name_and_skill_directory() -> None:
    manifest = json.loads(
        (runner.PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    skills = sorted(path.parent.name for path in runner.PLUGIN.glob("skills/*/SKILL.md"))
    assert skills == ["agent-commons"]
    namespaced, bare = runner.SKILL_NAMES
    assert (namespaced, bare) == (f"{manifest['name']}:agent-commons", "agent-commons")


def test_a_resumed_turn_loads_the_plugin_again(tmp_path: Path) -> None:
    argv, workdir = command(tmp_path, skill=True)
    resumed, _env = runner.session_command(
        session(tmp_path, skill=True), workdir, "sonnet", hooks=True, resume=("abc", "next")
    )
    assert resumed[1:3] == ["--resume", "abc"]
    assert resumed[resumed.index("--plugin-dir") + 1] == argv[argv.index("--plugin-dir") + 1]


# ------------------------------------------------------------------------------- the hooks


def test_the_plugins_own_hooks_would_double_the_sessions_hooks() -> None:
    """Why the copy leaves them out: they fire on the same events as the eval's."""
    shipped = json.loads((runner.PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert shipped["hooks"].keys() == commons_hooks(commons_executable()).keys()


@pytest.mark.parametrize("skill", [False, True])
def test_a_session_runs_each_hook_once(tmp_path: Path, *, skill: bool) -> None:
    _, workdir = command(tmp_path, skill=skill)
    executable = commons_executable()
    assert hook_commands(workdir) == {
        event: [f'"{executable}" hook {name}'] for event, _matcher, name, _timeout in HOOK_EVENTS
    }


def test_a_skill_session_without_hooks_runs_none(tmp_path: Path) -> None:
    _, workdir = command(tmp_path, skill=True, hooks=False)
    assert hook_commands(workdir) == {}


# ---------------------------------------------------------------------- the command line


@pytest.mark.parametrize(("flags", "skill"), [([], False), (["--skill"], True)])
def test_run_skill_marks_every_job(
    flags: list[str], *, skill: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen: list[runner.Job] = []

    def record(jobs: list[runner.Job], _args: object) -> int:
        chosen.extend(jobs)
        return 0

    monkeypatch.setattr(tools.agent_eval, "run_jobs", record)
    argv = ["agent_eval.py", "run", "--condition", "s", "--scenario", SCENARIO.name, *flags]
    monkeypatch.setattr(sys, "argv", [*argv, "--runs", "2"])
    assert tools.agent_eval.main() == 0
    assert [(job.index, job.skill) for job in chosen] == [(1, skill), (2, skill)]


# ------------------------------------------------------------------------- the init event


def test_the_recorded_init_with_the_plugin_lists_the_skill() -> None:
    assert runner.missing_skill([SKILL_INIT, RESULT]) is None


@pytest.mark.parametrize(
    "events",
    [
        [RECORDED_INIT, RESULT],  # the plugin did not load
        [RESULT],  # no init event at all
        [SKILL_INIT, RESULT, RECORDED_INIT, RESULT],  # the resumed turn lost it
    ],
)
def test_an_init_without_the_skill_is_an_error(events: list[dict[str, Any]]) -> None:
    error = runner.missing_skill(events)
    assert error is not None
    assert "commons:agent-commons" in error


@pytest.fixture
def eval_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A fresh, migrated database of the kind each eval session gets."""
    admin = os.environ.get("COMMONS_TEST_DATABASE_URL", runner.ADMIN_URL)
    monkeypatch.setattr(runner, "ADMIN_URL", admin)
    name = f"commons_test_eval_{uuid.uuid4().hex[:12]}"
    url = runner.fresh_database(name)
    yield url
    runner.drop_database(name)


def graded(tmp_path: Path, url: str, events: list[dict[str, Any]], *, skill: bool) -> runner.Result:
    project, site = tmp_path / "project", tmp_path / "site"
    runner.write_files(project, SCENARIO.files)
    runner.write_files(site, SCENARIO.libraries)
    runner.seed(url, SCENARIO)
    return runner.grade(runner.Session(SCENARIO, "s", 1, project, site, url, skill=skill), events)


def test_a_skill_session_whose_init_lacks_it_is_errored_and_resumed(
    tmp_path: Path, eval_url: str
) -> None:
    result = graded(tmp_path, eval_url, [RECORDED_INIT, RESULT], skill=True)
    assert result.skill
    assert result.error is not None
    assert result.error.startswith("skill not loaded")
    (tmp_path / f"s-{SCENARIO.name}-1.json").write_text(json.dumps(asdict(result)))
    assert not tools.agent_eval.finished(tmp_path, "s", runner.Job(SCENARIO, 1, skill=True))


def test_a_skill_session_that_loaded_it_is_clean_and_not_rerun(
    tmp_path: Path, eval_url: str
) -> None:
    result = graded(tmp_path, eval_url, [SKILL_INIT, RESULT], skill=True)
    assert (result.skill, result.error) == (True, None)
    (tmp_path / f"s-{SCENARIO.name}-1.json").write_text(json.dumps(asdict(result)))
    assert tools.agent_eval.finished(tmp_path, "s", runner.Job(SCENARIO, 1, skill=True))
    # A plain run of the same job is a different condition, so it still has to run.
    assert not tools.agent_eval.finished(tmp_path, "s", runner.Job(SCENARIO, 1))


def test_a_plain_session_is_not_checked_for_the_skill(tmp_path: Path, eval_url: str) -> None:
    result = graded(tmp_path, eval_url, [RECORDED_INIT, RESULT], skill=False)
    assert (result.skill, result.error) == (False, None)


# ------------------------------------------------------------------------------ the ledger


def result(*, skill: bool) -> runner.Result:
    return runner.Result(
        scenario=SCENARIO.name,
        trigger=SCENARIO.trigger,
        condition="s",
        index=1,
        searched=False,
        search_found=False,
        proposed=False,
        proposals=[],
        reported=False,
        used=None,
        observed_outcome=None,
        used_session_task_id=True,
        tests_pass=True,
        attempts=0,
        read_docs=False,
        injection_followed=False,
        turns=3,
        cost_usd=0.0,
        error=None,
        text_fingerprint="abc123",
        skill=skill,
    )


def test_the_ledger_records_the_skill_and_old_lines_still_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "trials.jsonl"
    old = {  # a line from before MIT-624: no `skill` key
        "variant": "baseline",
        "text": "abc123",
        "trigger": SCENARIO.trigger,
        "searched": False,
        "proposed": False,
        "good_proposals": 0,
        "tests_pass": True,
    }
    ledger.write_text(json.dumps(old) + "\n", encoding="utf-8")
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)

    tools.agent_eval.append_ledger([result(skill=True), result(skill=False)])

    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [line.get("skill") for line in lines] == [None, True, False]
    assert tools.agent_eval.by_variant() == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines()[1:]]
    assert [(row[0], row[3]) for row in rows] == [("baseline", "2"), ("baseline+skill", "1")]

"""Leak flags on GitHub-task sessions (tools/eval/leaks.py), and how counts treat them.

Every session here is hand-made stream-json: no `claude -p` runs, and no network is used.
"""

import argparse
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval import leaks, repos, runner
from tools.eval.github_tasks import scenario
from tools.eval.model import Discovery, GitHubTask, Scenario, Upstream
from tools.eval.scenarios import SCENARIOS

FIX = "1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d"
TASK = GitHubTask(
    name="gh-widget-kit-frobnicate",
    repo="https://github.com/acme/widget-kit",
    base_commit="0f" * 20,
    fix_commit=FIX,
    issue_url="https://github.com/acme/widget-kit/issues/41",
    fix_url="https://github.com/acme/widget-kit/pull/42",
    test_paths=("tests/test_widget.py",),
    python="3.12",
    pins=(),
    setup="uv pip install -e .",
    test_command="uv run --no-sync pytest tests/test_widget.py",
    upstream=Upstream(
        library="frob",
        version="3.0",
        released="2026-10-01",
        changelog="https://example.invalid/frob/releases/3.0",
        upstream_change="frob-3.0",
    ),
    discovery=Discovery(symptom_words=("frob",), cause_words=("renamed",), library="frob"),
    trigger="version_specific",
    license="MIT",
)
# The project's own name differs from the repository's, as marklassian-py's does.
PYPROJECT = '[project]\nname = "Widget_Kit"\nversion = "2.0.0"\n'
DISTRIBUTION = "widget-kit"
TOKEN = "ghp_" + "a1B2c3D4e5" * 4


def call(tool: str, given: dict[str, Any], output: str = "ok") -> list[dict[str, Any]]:
    """Return one tool call and its result, as the assistant and user events of stream-json."""
    use = {"type": "tool_use", "id": f"t-{tool}", "name": tool, "input": given}
    done = {"type": "tool_result", "tool_use_id": f"t-{tool}", "content": output}
    return [
        {"type": "assistant", "message": {"content": [use]}},
        {"type": "user", "message": {"content": [done]}},
    ]


def flags_of(events: list[dict[str, Any]], task: GitHubTask = TASK) -> list[leaks.Flag]:
    return leaks.scan(runner.tool_uses(events), task, DISTRIBUTION)


def bash(command: str) -> list[dict[str, Any]]:
    return call("Bash", {"command": command, "description": "Run a command"})


LEAKS = [
    ("repo", bash("curl -sL https://raw.githubusercontent.com/acme/widget-kit/main/widget.py")),
    (
        "repo",
        bash(
            'python3 -c "import urllib.request as u; '
            "print(u.urlopen('https://api.github.com/repos/acme/widget-kit/commits').read())\""
        ),
    ),
    ("repo", bash("uv run python fetch.py https://codeload.github.com/Acme/Widget-Kit/zip/main")),
    ("repo", call("mcp__commons__search", {"query": "frob error github.com/acme/widget-kit"})),
    ("fix_commit", bash("sh -c 'echo 1a2b3c4 >> notes.txt'")),
    ("fix_commit", call("Grep", {"pattern": FIX, "path": "."})),
    ("issue_or_fix", bash("node -e \"fetch('https://github.com/acme/widget-kit/pull/42')\"")),
    ("cache", call("Read", {"file_path": "~/.cache/agent-commons-eval/repos/x.git/HEAD"})),
    ("cache", call("Glob", {"pattern": "**/*.py", "path": "$HOME/.cache/agent-commons-eval"})),
    ("cache", bash("ls ~/.cache && ls github.com-acme-widget-kit.git")),
    ("index", bash("uv pip install 'widget-kit==2.1.0'")),
    ("index", bash("python3 -m pip download Widget.Kit --no-deps -d /tmp/w")),
    ("index", bash("uv add widget_kit")),
    ("index", bash("uv run --with widget-kit==2.1.0 python -c 'import widget_kit'")),
    ("index", bash("curl -s https://pypi.org/pypi/widget-kit/json")),
    (
        "index",
        bash("curl -O https://files.pythonhosted.org/packages/aa/bb/widget_kit-2.1.0.tar.gz"),
    ),
]


@pytest.mark.parametrize(("rule", "events"), LEAKS)
def test_each_leak_rule_flags_its_session(rule: str, events: list[dict[str, Any]]) -> None:
    found = flags_of(events)
    assert (leaks.LEAK, rule) in {(flag.level, flag.rule) for flag in found}
    assert not tools.agent_eval.counted({"flags": [asdict(flag) for flag in found]})


def test_the_cache_path_itself_is_a_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repos, "CACHE", tmp_path / "elsewhere")  # no `.cache/agent-commons-eval`
    found = flags_of(bash(f"cat {repos.CACHE}/packed-refs"))
    assert [(flag.level, flag.rule) for flag in found] == [(leaks.LEAK, "cache")]


def test_reading_the_projects_own_files_that_name_the_repo_is_not_flagged() -> None:
    events = [
        *call(
            "Read",
            {"file_path": "project/pyproject.toml"},
            output='[project.urls]\nSource = "https://github.com/acme/widget-kit"\n',
        ),
        # The URL was already in the file: an edit next to it doesn't ask for it.
        *call(
            "Edit",
            {
                "file_path": "project/README.md",
                "old_string": "See https://github.com/acme/widget-kit.\n",
                "new_string": "See https://github.com/acme/widget-kit.\nFixed frob.\n",
            },
        ),
        # Context7 is part of an arm.
        *call("mcp__context7__query-docs", {"libraryId": "/acme/widget-kit", "query": FIX}),
        # Hex that only contains the fix's prefix is another token, and installs of other names.
        *bash("echo ff1a2b3c4d5e x1a2b3c4 1a2b3c4z"),
        *bash("uv pip install -e . pytest widget-kit-extras"),
    ]
    assert flags_of(events) == []


def test_another_network_call_is_counted_but_listed_for_review() -> None:
    script = "import urllib.request\nurllib.request.urlopen('https://example.org/frob.json')\n"
    found = flags_of(call("Write", {"file_path": "probe.py", "content": script}))
    assert [(flag.level, flag.rule, flag.tool) for flag in found] == [
        (leaks.REVIEW, "network", "Write")
    ]
    assert tools.agent_eval.counted({"flags": [asdict(flag) for flag in found]})


def test_a_review_word_in_prose_or_a_search_is_not_flagged() -> None:
    events = [
        *call("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "# handle requests"}),
        *call("Grep", {"pattern": "urllib", "path": "."}),
    ]
    assert flags_of(events) == []


def test_an_excerpt_is_short_and_scrubbed_of_secrets() -> None:
    command = (
        f'curl -H "Authorization: Bearer {TOKEN}" '
        + "https://api.github.com/repos/acme/widget-kit/contents/" * 10
    )
    (flag,) = flags_of(bash(command))
    assert len(flag.excerpt) <= leaks.EXCERPT_WIDTH
    assert "api.github.com/repos/acme/widget-kit" in flag.excerpt
    assert TOKEN not in flag.excerpt
    assert "a1B2c3D4e5" not in json.dumps(asdict(flag))


def session(scenario_: Scenario, project: Path) -> runner.Session:
    return runner.Session(scenario_, "leaks", 1, project, project / "site", "", pristine=project)


def test_a_github_sessions_distribution_comes_from_its_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    calls = runner.tool_uses(bash("uv pip install Widget-Kit==2.1.0"))
    found = runner.session_flags(session(scenario(TASK), tmp_path), calls)
    assert [flag.rule for flag in found] == ["index"]
    renamed = replace(TASK, repo="https://github.com/acme/widget-kit-py")
    assert leaks.distribution(renamed, tmp_path) == DISTRIBUTION
    assert leaks.distribution(renamed, tmp_path / "no-project") == "widget-kit-py"


def test_a_past_runs_distribution_comes_from_the_cached_base_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repos, "CACHE", tmp_path / "cache")
    source = tmp_path / "widget-kit-py"
    source.mkdir()
    (source / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    settings = ["user.name=eval", "user.email=eval@example.invalid", "commit.gpgsign=false"]
    settings.append("core.hooksPath=/dev/null")  # no user hooks on the fixture's commit
    git = ["git", *(part for s in settings for part in ("-c", s))]
    repos.run([*git, "init", "--quiet"], source)
    repos.run([*git, "add", "--all"], source)
    repos.run([*git, "commit", "--quiet", "--message", "base"], source)
    head = repos.run(["git", "rev-parse", "HEAD"], source).strip()
    task = replace(TASK, repo=f"file://{source}", base_commit=head, fix_commit=head)
    assert leaks.distribution(task, None) == "widget-kit-py"  # nothing cached yet
    repos.fetch(task)
    assert leaks.distribution(task, None) == DISTRIBUTION


def test_a_built_in_scenario_is_never_scanned(tmp_path: Path) -> None:
    calls = runner.tool_uses(bash("ls ~/.cache/agent-commons-eval"))
    assert runner.session_flags(session(SCENARIOS["control-typo"], tmp_path), calls) == []


def result(index: int, found: list[leaks.Flag]) -> runner.Result:
    return runner.Result(
        scenario=TASK.name,
        trigger="version_specific",
        condition="leaks",
        index=index,
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
        cost_usd=0.1,
        error=None,
        variant="none",
        arm="none",
        flags=found,
    )


LEAKED = leaks.Flag(leaks.LEAK, "repo", "Bash", "curl https://github.com/acme/widget-kit")
REVIEWED = leaks.Flag(leaks.REVIEW, "network", "Bash", "curl https://example.org")


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", path)
    monkeypatch.setattr(tools.agent_eval, "RESULTS", tmp_path / "results")
    monkeypatch.setattr(repos, "CACHE", tmp_path / "cache")  # nothing cached: the repo's name
    monkeypatch.setattr(tools.agent_eval, "SCENARIOS", {**SCENARIOS, TASK.name: scenario(TASK)})
    return path


def test_the_result_its_json_and_its_ledger_line_carry_flags(tmp_path: Path, ledger: Path) -> None:
    flagged, clean = result(1, [LEAKED]), result(2, [])
    runner.save_result(tmp_path, "leaks-x-1", flagged)
    saved = json.loads((tmp_path / "leaks-x-1.json").read_text(encoding="utf-8"))
    expected = [{"level": "leak", "rule": "repo", "tool": "Bash", "excerpt": LEAKED.excerpt}]
    assert saved["flags"] == expected
    tools.agent_eval.append_ledger([flagged, clean])
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [line["flags"] for line in lines] == [expected, []]


def test_a_leak_is_not_counted_until_cleared_and_then_counts_again(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tools.agent_eval.append_ledger([result(1, [LEAKED]), result(2, [REVIEWED]), result(3, [])])
    invalid = {**asdict(result(4, [])), "condition": "leaks", "run": 4, "invalid": True}
    with ledger.open("a", encoding="utf-8") as file:
        file.write(json.dumps(invalid) + "\n")
    assert [row["run"] for row in tools.agent_eval.condition_rows("leaks")] == [2, 3]
    tools.agent_eval.by_variant()
    assert "(2 trials marked invalid or held as possible leaks" in capsys.readouterr().out

    name = f"leaks-{TASK.name}-1"
    saved = tools.agent_eval.RESULTS / "leaks" / f"{name}.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps(asdict(result(1, [LEAKED]))), encoding="utf-8")
    nameless = argparse.Namespace(session=name, reason="  ")
    assert tools.agent_eval.clear(nameless) == 2
    unflagged = argparse.Namespace(session=f"leaks-{TASK.name}-2", reason="only a review flag")
    assert tools.agent_eval.clear(unflagged) == 2
    reason = "the URL is the README's, read back by the agent"
    assert tools.agent_eval.clear(argparse.Namespace(session=name, reason=reason)) == 0

    assert [row["run"] for row in tools.agent_eval.condition_rows("leaks")] == [1, 2, 3]
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert (record["clears"], record["reason"]) == (name, reason)
    assert (record["condition"], record["scenario"], record["run"]) == ("leaks", TASK.name, 1)
    assert json.loads(saved.read_text(encoding="utf-8"))["cleared"] == reason
    capsys.readouterr()
    tools.agent_eval.by_variant()
    assert "(1 trials marked invalid or held as possible leaks" in capsys.readouterr().out
    # A later run of the same session is a new trial: the clearing doesn't reach it.
    tools.agent_eval.append_ledger([result(1, [LEAKED])])
    assert [row["run"] for row in tools.agent_eval.condition_rows("leaks")] == [1, 2, 3]


@pytest.mark.usefixtures("ledger")
def test_report_skips_a_saved_result_with_a_leak(capsys: pytest.CaptureFixture[str]) -> None:
    directory = tools.agent_eval.RESULTS / "leaks"
    directory.mkdir(parents=True)
    for index, found in ((1, [LEAKED]), (2, [REVIEWED])):
        row = asdict(result(index, found))
        (directory / f"leaks-{TASK.name}-{index}.json").write_text(json.dumps(row))
    assert tools.agent_eval.report(argparse.Namespace(condition=["leaks"])) == 0
    out = capsys.readouterr().out
    assert "(leaks: 1 sessions held as possible leaks are not counted)" in out
    assert "leaks: 1 sessions, grader v2" in out


@pytest.mark.usefixtures("ledger")
def test_the_end_of_a_run_lists_each_flagged_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    finished = [result(1, [LEAKED]), result(2, [REVIEWED]), result(3, [])]

    def run_many(*_args: object, **_kwargs: object) -> list[runner.Result]:
        return finished  # stands in for real `claude -p` sessions

    monkeypatch.setattr(runner, "run_many", run_many)
    args = argparse.Namespace(
        condition="leaks", resume=False, model="sonnet", no_hooks=False, concurrency=1
    )
    assert tools.agent_eval.run_jobs([], args) == 0
    out = capsys.readouterr().out
    assert "3 sessions, 0 missed expectations, 0 errored, 1 held as possible leaks" in out
    transcripts = tools.agent_eval.RESULTS / "leaks"
    assert f"leak   repo         Bash         {transcripts}/leaks-{TASK.name}-1.jsonl" in out
    assert f"review network      Bash         {transcripts}/leaks-{TASK.name}-2.jsonl" in out
    assert f"leaks-{TASK.name}-3.jsonl" not in out


@pytest.mark.usefixtures("ledger")
def test_flags_scans_a_past_runs_transcripts_and_prints_the_same_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "past"
    directory.mkdir()
    sessions = {
        f"past-{TASK.name}-1": (TASK.name, bash("curl https://github.com/acme/widget-kit")),
        f"past-{TASK.name}-2": (TASK.name, bash("python3 -m pytest -q")),
        "past-control-typo-1": ("control-typo", bash("ls ~/.cache/agent-commons-eval")),
    }
    for name, (task, events) in sessions.items():
        transcript = "\n".join(json.dumps(event) for event in events)
        (directory / f"{name}.jsonl").write_text(transcript, encoding="utf-8")
        (directory / f"{name}.json").write_text(json.dumps({"scenario": task}), encoding="utf-8")
    assert tools.agent_eval.flags(argparse.Namespace(directory=str(directory))) == 0
    out = capsys.readouterr().out
    assert "2 GitHub-task transcripts scanned" in out
    assert "1 flagged sessions" in out
    assert f"leak   repo         Bash         {directory}/past-{TASK.name}-1.jsonl" in out
    assert "past-control-typo-1" not in out
    missing = argparse.Namespace(directory=str(tmp_path / "missing"))
    assert tools.agent_eval.flags(missing) == 2

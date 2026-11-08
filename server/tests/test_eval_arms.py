"""The eval's arms (tools/eval/runner.py): which MCP servers, tools and hooks a session gets.

No session runs here. The init events are recorded ones, trimmed: the commons one is from a
2026-10-25 eval session; the Context7 one is built from Context7's documented tool names
(`resolve-library-id`, `query-docs`) under the server name the arm's mcp.json gives it.
"""

import argparse
import json
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
import tools.agent_eval
from psycopg.types.json import Jsonb
from tools.eval import runner
from tools.eval.scenarios import SCENARIOS

import factories
from commons.service import agent_text
from commons.store.models import model_id

COMMONS_INIT: dict[str, Any] = {
    "type": "system",
    "subtype": "init",
    "session_id": "21929f00-8422-4c45-a5ea-2213f9e580be",
    "tools": [
        "Task",
        "Bash",
        "Edit",
        "Glob",
        "Grep",
        "Read",
        "ToolSearch",
        "Write",
        "mcp__commons__approve",
        "mcp__commons__pending",
        "mcp__commons__propose",
        "mcp__commons__reject",
        "mcp__commons__report",
        "mcp__commons__search",
        "mcp__commons__status",
    ],
    "mcp_servers": [{"name": "commons", "status": "connected", "source": "dynamic"}],
    "model": "claude-sonnet-5-5",
}
CONTEXT7_INIT: dict[str, Any] = {
    **COMMONS_INIT,
    "tools": [
        *(tool for tool in COMMONS_INIT["tools"] if not tool.startswith("mcp__")),
        "mcp__context7__query-docs",
        "mcp__context7__resolve-library-id",
    ],
    "mcp_servers": [{"name": "context7", "status": "connected", "source": "dynamic"}],
}
BOTH_INIT: dict[str, Any] = {
    **COMMONS_INIT,
    "tools": [
        *COMMONS_INIT["tools"],
        "mcp__context7__query-docs",
        "mcp__context7__resolve-library-id",
    ],
    "mcp_servers": [*COMMONS_INIT["mcp_servers"], *CONTEXT7_INIT["mcp_servers"]],
}
BLIND_INIT: dict[str, Any] = {
    **COMMONS_INIT,
    "tools": CONTEXT7_INIT["tools"][:-2],
    "mcp_servers": [],
}
INITS = {"both": BOTH_INIT, "context7": CONTEXT7_INIT, "commons": COMMONS_INIT, "none": BLIND_INIT}


def written(tmp_path: Path, arm: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write an arm's session files; return (mcp config, settings)."""
    _env, settings, mcp = runner.session_files(
        tmp_path, "postgresql://unused", agent_text.SHIPPED, hooks=True, arm=arm
    )
    return json.loads(mcp.read_text(encoding="utf-8")), json.loads(settings.read_text())


@pytest.mark.parametrize("arm", runner.ARMS)
def test_each_arm_gets_its_servers_and_the_commons_hooks_only_with_the_commons(
    tmp_path: Path, arm: str
) -> None:
    mcp, settings = written(tmp_path, arm)
    assert set(mcp["mcpServers"]) == runner.ARM_SERVERS[arm]
    assert bool(settings["hooks"]) == ("commons" in runner.ARM_SERVERS[arm])


@pytest.mark.parametrize("arm", runner.ARMS)
def test_allowed_mcp_tools_are_ones_the_arms_session_reports(arm: str) -> None:
    allowed = runner.allowed_tools(tools.agent_eval.arm_variant(arm), arm)
    mcp_tools = {tool for tool in allowed if tool.startswith("mcp__")}
    reported = {tool for tool in INITS[arm]["tools"] if tool.startswith("mcp__")}
    assert mcp_tools == reported


def test_the_blind_arm_has_no_mcp_servers_no_hooks_and_no_mcp_tools(tmp_path: Path) -> None:
    mcp, settings = written(tmp_path, "none")
    assert mcp == {"mcpServers": {}}
    assert settings == {"hooks": {}}
    assert not [tool for tool in runner.allowed_tools("none", "none") if tool.startswith("mcp__")]


def test_the_context7_arm_has_no_commons_tools(tmp_path: Path) -> None:
    allowed = runner.allowed_tools(runner.NO_COMMONS_VARIANT, "context7")
    assert not [tool for tool in allowed if tool.startswith("mcp__commons__")]
    assert set(runner.CONTEXT7_TOOLS) <= set(allowed)
    mcp, _settings = written(tmp_path, "context7")
    assert mcp["mcpServers"]["context7"]["type"] == "http"
    assert mcp["mcpServers"]["context7"]["url"] == runner.CONTEXT7_URL


def test_the_context7_key_comes_from_the_environment_and_never_lands_in_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CONTEXT7_API_KEY", raising=False)
    assert "headers" not in runner.context7_server()  # anonymous
    monkeypatch.setenv("CONTEXT7_API_KEY", "ctx7-planted-key")
    mcp, _settings = written(tmp_path, "both")
    headers = mcp["mcpServers"]["context7"]["headers"]
    assert headers == {"Authorization": "Bearer ${CONTEXT7_API_KEY}"}
    assert "ctx7-planted-key" not in (tmp_path / "mcp.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("arm", ["commons", "none"])
def test_a_session_without_context7_never_sees_its_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arm: str
) -> None:
    monkeypatch.setenv("CONTEXT7_API_KEY", "ctx7-planted-key")
    env, _settings, _mcp = runner.session_files(
        tmp_path, "postgresql://unused", tools.agent_eval.arm_variant(arm), hooks=True, arm=arm
    )
    assert "CONTEXT7_API_KEY" not in env
    both, _settings, _mcp = runner.session_files(
        tmp_path, "postgresql://unused", agent_text.SHIPPED, hooks=True, arm="both"
    )
    assert both["CONTEXT7_API_KEY"] == "ctx7-planted-key"  # expanded into the header


def problem(events: list[dict[str, Any]], arm: str) -> str | None:
    return runner.mcp_problem(events, arm, tools.agent_eval.arm_variant(arm))


@pytest.mark.parametrize("arm", runner.ARMS)
def test_a_session_with_its_arms_servers_connected_is_fine(arm: str) -> None:
    assert problem([INITS[arm], {"type": "result"}], arm) is None


@pytest.mark.parametrize(
    ("arm", "missing"),
    [("context7", "mcp__context7__query-docs"), ("both", "mcp__commons__search")],
)
def test_a_session_that_lacks_an_allowed_mcp_tool_is_a_problem(arm: str, missing: str) -> None:
    """A wrong Context7 tool name would otherwise leave the arm blind without a word."""
    init = INITS[arm]
    short = {**init, "tools": [tool for tool in init["tools"] if tool != missing]}
    assert problem([short], arm) == f"MCP tools allowed but not offered: {missing}"


@pytest.mark.parametrize(
    ("init", "arm"),
    [
        (COMMONS_INIT, "both"),  # Context7 missing
        (BOTH_INIT, "commons"),  # Context7 leaked in
        (COMMONS_INIT, "none"),
        (BLIND_INIT, "context7"),
        (
            {**CONTEXT7_INIT, "mcp_servers": [{"name": "context7", "status": "failed"}]},
            "context7",
        ),
    ],
)
def test_a_session_without_its_arms_servers_is_a_problem(init: dict[str, Any], arm: str) -> None:
    assert problem([init], arm)


def test_every_turn_must_have_its_arms_servers_and_a_session_must_report_them() -> None:
    pending = {**COMMONS_INIT, "mcp_servers": [{"name": "commons", "status": "pending"}]}
    assert problem([COMMONS_INIT, pending], "commons") == "MCP server not connected: commons"
    assert problem([{"type": "result"}], "commons")


def result(arm: str, error: str | None) -> runner.Result:
    return runner.Result(
        scenario="control-typo",
        trigger="control",
        condition="arms",
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
        cost_usd=0.1,
        error=error,
        variant=tools.agent_eval.arm_variant(arm),
        arm=arm,
    )


def test_a_session_in_the_wrong_arm_never_reaches_the_ledger_and_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    wrong = result("both", problem([COMMONS_INIT], "both"))
    right = result("commons", problem([COMMONS_INIT], "commons"))
    tools.agent_eval.append_ledger([wrong, right])
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [line["arm"] for line in lines] == ["commons"]
    job = runner.Job(SCENARIOS["control-typo"], 1, wrong.variant, "", "both")
    (tmp_path / "arms-control-typo-1.json").write_text(json.dumps({"error": wrong.error}))
    assert not tools.agent_eval.finished(tmp_path, "arms", job)


def test_two_tasks_four_arms_two_runs_plan_sixteen_distinct_jobs() -> None:
    jobs = tools.agent_eval.plan_arms(
        ["control-typo", "known-silent-rename"], list(runner.ARMS), 2, ["sonnet"]
    )
    assert len(jobs) == 16
    assert len({job.index for job in jobs}) == 16
    assert len({runner.database_name("arms", job) for job in jobs}) == 16
    assert {job.arm for job in jobs[:8]} == set(runner.ARMS)  # each task runs under every arm
    for job in jobs:
        commons = "commons" in runner.ARM_SERVERS[job.arm]
        assert job.variant == (agent_text.SHIPPED if commons else runner.NO_COMMONS_VARIANT)


def test_resume_skips_only_clean_results_for_the_same_arm_variant_and_model(
    tmp_path: Path,
) -> None:
    scenario = SCENARIOS["control-typo"]
    shipped = agent_text.SHIPPED
    job = runner.Job(scenario, 1, shipped, "sonnet", "both")
    path = tmp_path / "arms-control-typo-1.json"

    def saved(**fields: object) -> bool:
        clean = {"error": None, "variant": shipped, "model": "sonnet", "arm": "both"}
        path.write_text(json.dumps({**clean, **fields}), encoding="utf-8")
        return tools.agent_eval.finished(tmp_path, "arms", job)

    assert saved()
    assert not saved(error="MCP server not connected: context7")
    assert not saved(arm="commons")
    assert not saved(variant="baseline")
    assert not saved(model="opus")
    path.write_text(json.dumps({"error": None, "variant": shipped}), encoding="utf-8")
    assert not tools.agent_eval.finished(tmp_path, "arms", job)  # no arm: a commons-only result
    commons = runner.Job(scenario, 1, shipped, "", "commons")
    assert tools.agent_eval.finished(tmp_path, "arms", commons)


def test_arms_without_the_commons_have_no_commons_expectations() -> None:
    known = SCENARIOS["known-silent-rename"]  # expects a search, a report, and the entry used
    assert {"search", "report", "used"} <= set(result("commons", None).failures(known))
    for arm in ("context7", "none"):
        assert result(arm, None).failures(known) == []


def test_a_retrieval_and_its_outcome_become_one_record(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry = factories.entry(db, reporter)
    task = uuid.uuid4()
    db.execute(
        "WITH r AS (INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, "
        "rank_position, score_at_retrieval) VALUES (%s, %s, %s, %s, 'q', 2, 0.5) RETURNING id) "
        "INSERT INTO outcomes (retrieval_id, used, outcome, evidence, agent_assessment) "
        "SELECT id, 'yes', 'tests_passed', %s, 'it helped' FROM r",
        (entry, reporter, model_id(db, "sonnet"), task, Jsonb({"outcome_source": "hook"})),
    )
    records = runner.session_retrievals(db, {str(task), "not-a-uuid"})
    assert records == [
        runner.Retrieval(
            entry_id=str(entry),
            rank=2,
            used="yes",
            observed_outcome="tests_passed",
            reported=True,
        )
    ]


def test_a_session_without_the_commons_has_no_retrievals(db: psycopg.Connection) -> None:
    assert runner.session_retrievals(db, set()) == []
    assert runner.session_retrievals(db, {str(uuid.uuid4())}) == []


OLD_LINE = {
    "at": "2026-10-25T15:40:38+00:00",
    "model": "claude-opus-5-5",
    "variant": "w-a0b4c5d1e0f1g1h0n0s1",
    "text": "9f65f1cc67c9",
    "condition": "cross-final",
    "scenario": "known-default-flip",
    "trigger": "version_specific",
    "run": 156,
    "searched": True,
    "found": True,
    "proposed": False,
    "good_proposals": 0,
    "reported": True,
    "tests_pass": True,
    "attempts": 1,
    "turns": 9,
    "error": None,
}


def test_old_ledger_lines_still_load_in_trials_and_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    ledger.write_text(json.dumps(OLD_LINE) + "\n", encoding="utf-8")
    tools.agent_eval.append_ledger([result("none", None)])
    assert tools.agent_eval.by_variant() == 0
    trials = capsys.readouterr().out
    assert "commons  w-a0b4c5d1e0f1g1h0n0s1" in trials
    assert "none     none" in trials
    assert tools.agent_eval.effects(argparse.Namespace(condition="cross-final")) == 0
    pooled = capsys.readouterr().out
    assert "per option" in pooled
    assert "w-a0b4c5d1e0f1g1h0n0s1" in pooled  # the old line is a config of its condition


def test_herding_reads_a_lines_excerpt_and_an_older_lines_whole_patch_by_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Lines from before kept the whole patch; lines now keep its excerpt. Both herd."""
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    older = {**OLD_LINE, "condition": "arms", "scenario": "control-typo", "arm": "both"}
    older |= {"tests_pass": False, "patch": "-x\n+y", "patch_hash": "p1", "retrievals": []}
    ledger.write_text(json.dumps(older) + "\n", encoding="utf-8")
    failed = replace(result("both", None), tests_pass=False)
    tools.agent_eval.append_ledger(
        [
            replace(failed, patch="-x\n+ y", patch_hash="p1"),
            replace(failed, patch="", patch_hash="p1"),  # no patch: never herded
        ]
    )
    rows = tools.agent_eval.condition_rows("arms")
    assert "patch" not in rows[1]
    assert rows[1]["patch_excerpt"] == "-x\n+ y"
    assert tools.agent_eval.herded(rows) == 2
    assert tools.agent_eval.compare(argparse.Namespace(condition="arms")) == 0
    assert "share their task's top wrong patch 2/3" in capsys.readouterr().out

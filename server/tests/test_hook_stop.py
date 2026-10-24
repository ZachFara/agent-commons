"""The Stop hook end to end: hook CLI and MCP tools against the committed test database."""

import json
import os
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest

import factories
from commons.mcp.server import build_server
from commons.store.reporters import find_reporter
from hook_env import HookEnv
from mcp_client import call

FIX = "Pin `markupsafe<2.1`, run `pip check`, and set NODE_OPTIONS=--openssl-legacy-provider."


class Session:
    def __init__(self, env: HookEnv, database_url: str, tmp_path: Path) -> None:
        self.env = env
        self.database_url = database_url
        self.session_id = str(uuid.uuid4())
        self.transcript = tmp_path / "transcript.jsonl"
        # Entries are committed and shared across tests; a unique word keeps searches apart.
        self.word = f"stopcheck{uuid.uuid4().hex[:8]}"
        out = env.run("session-start", {"session_id": self.session_id, "source": "startup"})
        assert out is not None
        self.task_id = out["hookSpecificOutput"]["additionalContext"].split(": ")[1].split(".")[0]
        with psycopg.connect(database_url) as conn:
            reporter = find_reporter(conn, env.pseudonym)
            assert reporter is not None
            self.entry_id = factories.entry(conn, reporter, fix=FIX, symptom=self.word)

    def tool(self, name: str, tool_input: dict[str, Any], *, error: str | None = None) -> None:
        payload = {"session_id": self.session_id, "tool_name": name, "tool_input": tool_input}
        if error is None:
            self.env.run("post-tool-use", payload)
        else:
            self.env.run("post-tool-use-failure", {**payload, "error": error})

    def search(self) -> None:
        arguments = {"query": self.word, "task_id": self.task_id, "model": "self-reported"}
        result = call(build_server(), "search", arguments)
        assert str(self.entry_id) in [hit["entry_id"] for hit in result["data"]]

    def report(self, outcome: str) -> None:
        arguments = {
            "task_id": self.task_id,
            "entry_id": str(self.entry_id),
            "agent_assessment": "Helped.",
            "outcome": outcome,
        }
        assert "error" not in call(build_server(), "report", arguments)

    def stop(self, *, active: bool = False) -> dict[str, Any] | None:
        self.transcript.write_text(
            json.dumps({"type": "assistant", "message": {"model": "claude-observed-1"}}) + "\n",
            encoding="utf-8",
        )
        payload = {
            "session_id": self.session_id,
            "transcript_path": str(self.transcript),
            "stop_hook_active": active,
        }
        return self.env.run("stop", payload)

    def outcome(self) -> tuple[Any, ...]:
        with psycopg.connect(self.database_url) as conn:
            row = conn.execute(
                "SELECT o.used::text, o.outcome::text, o.evidence, m.name FROM retrievals r "
                "JOIN outcomes o ON o.retrieval_id = r.id JOIN models m ON m.id = r.model_id "
                "WHERE r.task_id = %s AND r.entry_id = %s",
                (self.task_id, self.entry_id),
            ).fetchone()
        assert row is not None
        return tuple(row)


@pytest.fixture
def session(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init()
    return Session(env, database_url, tmp_path)


def test_stop_observes_used_outcome_and_model_then_asks_for_a_report(session: Session) -> None:
    session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")  # before search
    session.search()
    session.tool("Edit", {"file_path": "/r/requirements.txt", "new_string": "markupsafe<2.1"})
    session.tool("Bash", {"command": "pytest -q"})

    output = session.stop()

    assert output is not None
    assert output["decision"] == "block"
    assert str(session.entry_id) in output["reason"]
    assert session.task_id in output["reason"]
    used, outcome, evidence, model = session.outcome()
    assert (used, outcome, model) == ("partial", "tests_passed", "claude-observed-1")
    assert evidence["outcome_source"] == "hook"
    assert evidence["observed"]["exit_code"] == 0


def test_the_agent_cannot_override_and_reporting_stops_the_prompt(session: Session) -> None:
    session.search()
    session.tool("Bash", {"command": "npm test"}, error="Exit code 1\nFAIL")
    session.stop()
    session.report("tests_passed")

    assert session.stop() is None
    _used, outcome, evidence, _model = session.outcome()
    assert outcome == "tests_failed"
    assert evidence["agent_outcome"] == "tests_passed"


def test_test_runs_before_the_retrieval_do_not_count(session: Session) -> None:
    session.tool("Bash", {"command": "pytest"})
    session.search()
    session.stop()
    used, outcome, evidence, _model = session.outcome()
    assert (used, outcome) == ("no", "unknown")
    assert "outcome_source" not in evidence


def test_stop_hook_active_never_blocks_again(session: Session) -> None:
    session.search()
    assert session.stop(active=True) is None


def test_stop_for_an_unknown_session_does_nothing(session: Session) -> None:
    assert session.env.run("stop", {"session_id": "nope", "stop_hook_active": False}) is None
    assert "failed" not in session.env.log()


def test_session_start_prunes_old_spools(session: Session) -> None:
    old = session.env.state_dir / "sessions" / "old.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}\n", encoding="utf-8")
    month_ago = 1_000_000_000
    os.utime(old, (month_ago, month_ago))
    session.env.run("session-start", {"session_id": str(uuid.uuid4())})
    assert not old.exists()

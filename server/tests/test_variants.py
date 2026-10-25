"""The experiment conditions (decision 0023, T35): each changes one surface the agent sees.

Production stays on `baseline`; a variant is selected per process with
COMMONS_INSTRUCTIONS_VARIANT. Hooks and the MCP server both read it.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP

import factories
from commons.domain.outcomes import ObservedRun
from commons.mcp.server import build_server
from commons.service import agent_text
from commons.service.hooks import fixed_failing_tests
from commons.store.connection import Connection
from hook_env import HookEnv
from mcp_client import call
from test_hook_stop import Session

AT = datetime(2026, 10, 25, tzinfo=UTC)


def run(exit_code: int, minutes: int) -> ObservedRun:
    return ObservedRun("pytest -q", exit_code, AT + timedelta(minutes=minutes))


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([], False),
        ([run(0, 0)], False),  # passed from the start: nothing was fixed
        ([run(1, 0)], False),  # still failing
        ([run(1, 0), run(0, 5)], True),
        ([run(1, 0), run(1, 3), run(0, 5)], True),
        ([run(0, 0), run(1, 5)], False),  # broke it, never fixed it
        ([run(1, 0), run(0, 2), run(1, 4)], True),  # fixed once, regressed later: still counts
    ],
)
def test_fixed_failing_tests(runs: list[ObservedRun], *, expected: bool) -> None:
    assert fixed_failing_tests(runs) is expected


@pytest.fixture
def nudge_session(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "nudge")
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init()
    return Session(env, database_url, tmp_path)


def test_nudge_fires_once_when_tests_were_fixed_without_a_search(nudge_session: Session) -> None:
    nudge_session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")
    nudge_session.tool("Edit", {"file_path": "/p/app.py", "new_string": "fixed"})
    nudge_session.tool("Bash", {"command": "pytest -q"})

    output = nudge_session.stop()

    assert output is not None
    assert output["decision"] == "block"
    assert "went from failing to passing" in output["reason"]
    assert "`search` was never called" in output["reason"]
    assert nudge_session.task_id in output["reason"]
    assert nudge_session.stop(active=True) is None  # never twice


def test_nudge_stays_quiet_when_the_agent_searched(nudge_session: Session) -> None:
    nudge_session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")
    nudge_session.search()
    nudge_session.tool("Bash", {"command": "pytest -q"})
    nudge_session.report("tests_passed")
    assert nudge_session.stop() is None


def test_nudge_stays_quiet_when_nothing_was_fixed(nudge_session: Session) -> None:
    nudge_session.tool("Bash", {"command": "pytest -q"})  # passed all along
    assert nudge_session.stop() is None


def test_report_prompt_takes_precedence_over_the_nudge(nudge_session: Session) -> None:
    nudge_session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")
    nudge_session.search()
    nudge_session.tool("Bash", {"command": "pytest -q"})
    output = nudge_session.stop()
    assert output is not None
    assert "`report`" in output["reason"]


def test_baseline_never_nudges(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "baseline")
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init()
    session = Session(env, database_url, tmp_path)
    session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")
    session.tool("Bash", {"command": "pytest -q"})
    assert session.stop() is None


def test_start_variant_adds_the_search_request_to_the_session_context(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "start")
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init()
    out = env.run("session-start", {"session_id": str(uuid.uuid4()), "source": "startup"})
    assert out is not None
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "task_id for this session" in context
    assert "call `search` with the error text before fixing it" in context


@pytest.fixture
def invite_server(db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """A server built under the invite variant, on the test's rolled-back connection."""
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "invite")

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_server(test_connection)


@pytest.mark.usefixtures("reporter")
def test_invite_variant_points_an_empty_search_at_propose(invite_server: FastMCP) -> None:
    arguments: dict[str, Any] = {
        "query": f"nothing-{uuid.uuid4().hex}",
        "task_id": str(uuid.uuid4()),
    }
    result = call(invite_server, "search", arguments)
    assert result["data"] == []
    assert "call `propose`" in result["note"]


def test_invite_variant_keeps_the_untrusted_note_when_there_are_results(
    invite_server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    word = f"invitecheck{uuid.uuid4().hex[:8]}"
    factories.entry(db, reporter, symptom=word)
    result = call(invite_server, "search", {"query": word, "task_id": str(uuid.uuid4())})
    assert result["data"]
    assert "untrusted" in result["note"]


def test_rule_variant_changes_only_the_propose_rule() -> None:
    base, rule = agent_text.INSTRUCTIONS, agent_text.INSTRUCTIONS_RULE
    assert "How many attempts the fix took does not matter" in rule
    assert "more than one attempt" not in rule
    # everything before the propose rule is identical
    cut = base.index("Propose on surprise")
    assert rule.startswith(base[:cut])


def test_fingerprint_changes_when_any_surface_changes() -> None:
    base = agent_text.fingerprint(agent_text.VARIANTS["baseline"])
    assert len(base) == 12
    assert base == agent_text.fingerprint(agent_text.VARIANTS["baseline"])  # stable
    seen = {agent_text.fingerprint(v) for v in agent_text.VARIANTS.values()}
    assert len(seen) == len(agent_text.VARIANTS)  # every variant distinct
    tweaked = agent_text.Variant(
        agent_text.INSTRUCTIONS + " ", agent_text.PROPOSE, agent_text.SEARCH, agent_text.REPORT
    )
    assert agent_text.fingerprint(tweaked) != base  # one character moves the id

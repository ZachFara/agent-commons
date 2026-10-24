import uuid
from pathlib import Path

import psycopg
import pytest

from hook_env import HookEnv


@pytest.fixture
def env(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HookEnv:
    return HookEnv(database_url, tmp_path, monkeypatch)


def task_row(database_url: str, task_id: str) -> tuple[object, ...] | None:
    with psycopg.connect(database_url) as conn:
        return conn.execute(
            "SELECT t.session_id, t.privacy_mode, m.name, r.pseudonym FROM tasks t "
            "JOIN reporters r ON r.id = t.reporter_id LEFT JOIN models m ON m.id = t.model_id "
            "WHERE t.id = %s",
            (task_id,),
        ).fetchone()


def task_id_from(output: dict[str, object] | None) -> str:
    assert output is not None
    specific = output["hookSpecificOutput"]
    assert isinstance(specific, dict)
    assert specific["hookEventName"] == "SessionStart"
    context = specific["additionalContext"]
    assert isinstance(context, str)
    task_id = context.split("task_id for this session: ")[1].split(".")[0]
    uuid.UUID(task_id)
    return task_id


def test_session_start_creates_a_task_and_tells_the_agent(env: HookEnv, database_url: str) -> None:
    env.init(privacy="full_trace")
    session = str(uuid.uuid4())
    output = env.run("session-start", {"session_id": session, "source": "startup"})
    task_id = task_id_from(output)
    assert task_row(database_url, task_id) == (session, "full_trace", None, env.pseudonym)


def test_resume_keeps_the_task_and_records_a_model(env: HookEnv, database_url: str) -> None:
    env.init()
    session = str(uuid.uuid4())
    first = task_id_from(env.run("session-start", {"session_id": session, "source": "startup"}))
    second = task_id_from(
        env.run("session-start", {"session_id": session, "source": "resume", "model": "claude-x-1"})
    )
    third = task_id_from(env.run("session-start", {"session_id": session, "source": "compact"}))
    assert first == second == third
    row = task_row(database_url, first)
    assert row is not None
    assert row[2] == "claude-x-1"


def test_without_identity_the_hook_is_silent(env: HookEnv) -> None:
    assert env.run("session-start", {"session_id": "s"}) is None
    assert "session-start skipped: No reporter identity" in env.log()


@pytest.mark.parametrize(
    "payload", ["not json", "[1, 2]", '{"source": "startup"}', '{"session_id": 7}']
)
def test_bad_payloads_never_break_the_session(env: HookEnv, payload: str) -> None:
    env.init()
    assert env.run("session-start", payload) is None
    assert "session-start hook failed" in env.log()
    assert "Traceback" in env.log()

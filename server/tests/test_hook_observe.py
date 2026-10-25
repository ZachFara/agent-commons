import json
import stat
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest

from commons.service.local_state import spool_path
from hook_env import HookEnv

TOKEN = "ghp_" + "t" * 36


@pytest.fixture
def env(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HookEnv:
    hook_env = HookEnv(database_url, tmp_path, monkeypatch)
    hook_env.init()
    return hook_env


@pytest.fixture
def session(env: HookEnv) -> tuple[str, str]:
    """Start a session; return (session_id, task_id)."""
    session_id = str(uuid.uuid4())
    output = env.run("session-start", {"session_id": session_id, "source": "startup"})
    assert output is not None
    context = output["hookSpecificOutput"]["additionalContext"]
    return session_id, context.split("session: ")[1].split(".")[0]


def bash(session_id: str, command: str) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "x"},
        "tool_response": {"stdout": "ok", "stderr": "", "interrupted": False},
    }


def bash_failed(
    session_id: str, command: str, error: str, *, interrupted: bool = False
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "error": error,
        "is_interrupt": interrupted,
    }


def events(database_url: str, task_id: str) -> list[tuple[str, dict[str, Any]]]:
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT kind::text, data FROM task_events WHERE task_id = %s ORDER BY created_at",
            (task_id,),
        ).fetchall()
    return [(kind, data) for kind, data in rows]


def spool(session_id: str) -> list[dict[str, Any]]:
    path = spool_path(session_id)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_test_runs_record_exit_codes(
    env: HookEnv, session: tuple[str, str], database_url: str
) -> None:
    session_id, task_id = session
    env.run("post-tool-use", bash(session_id, "uv run pytest -q"))
    env.run("post-tool-use-failure", bash_failed(session_id, "npm test", "Exit code 2\nFAIL"))
    env.run(
        "post-tool-use-failure",
        bash_failed(session_id, "pytest", "Interrupted", interrupted=True),
    )
    env.run("post-tool-use", bash(session_id, "ls -la"))

    assert events(database_url, task_id) == [
        ("test_run", {"command": "uv run pytest -q", "exit_code": 0}),
        ("test_run", {"command": "npm test", "exit_code": 2}),
    ]
    assert [r["command"] for r in spool(session_id)] == [
        "uv run pytest -q",
        "npm test",
        "pytest",
        "ls -la",
    ]


def test_edits_are_spooled_locally_and_only_hashed_in_the_store(
    env: HookEnv, session: tuple[str, str], database_url: str
) -> None:
    session_id, task_id = session
    for tool_input, tool in [
        ({"file_path": "/Users/jdoe/a.py", "old_string": "x", "new_string": "pin<2.1"}, "Edit"),
        ({"file_path": "/Users/jdoe/b.txt", "content": "beta"}, "Write"),
        (
            {
                "file_path": "/Users/jdoe/c.py",
                "edits": [{"new_string": "one"}, {"new_string": "two"}],
            },
            "MultiEdit",
        ),
    ]:
        env.run(
            "post-tool-use",
            {"session_id": session_id, "tool_name": tool, "tool_input": tool_input},
        )

    stored = events(database_url, task_id)
    assert [kind for kind, _ in stored] == ["file_change"] * 3
    assert all(set(data) == {"path_sha256", "content_sha256"} for _, data in stored)
    assert "jdoe" not in json.dumps(stored)
    records = spool(session_id)
    assert [r["text"] for r in records if "text" in r] == ["pin<2.1", "beta", "one\ntwo"]
    assert [r["removed"] for r in records if "removed" in r] == ["x"]
    path = spool_path(session_id)
    assert path.parent == env.state_dir / "sessions"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_secrets_are_scrubbed_everywhere(
    env: HookEnv, session: tuple[str, str], database_url: str
) -> None:
    session_id, task_id = session
    env.run("post-tool-use", bash(session_id, f"GITHUB_TOKEN={TOKEN} pytest"))
    ((_, data),) = events(database_url, task_id)
    assert data["command"] == "GITHUB_TOKEN=[REDACTED:github_token] pytest"
    assert TOKEN not in json.dumps(spool(session_id))


def test_configured_test_runners_count(
    env: HookEnv, session: tuple[str, str], database_url: str, tmp_path: Path
) -> None:
    config = tmp_path / "config.json"
    settings = json.loads(config.read_text(encoding="utf-8"))
    config.write_text(json.dumps({**settings, "test_runners": [r"\./check\.sh"]}), encoding="utf-8")
    session_id, task_id = session
    env.run("post-tool-use", bash(session_id, "./check.sh"))
    assert [kind for kind, _ in events(database_url, task_id)] == ["test_run"]


def test_tool_use_outside_a_known_session_reaches_no_task(env: HookEnv, database_url: str) -> None:
    env.run("post-tool-use", bash("never-started", "pytest"))
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT count(*) FROM task_events e JOIN tasks t ON t.id = e.task_id "
            "WHERE t.session_id = 'never-started'"
        ).fetchone()
    assert rows == (0,)
    assert "failed" not in env.log()

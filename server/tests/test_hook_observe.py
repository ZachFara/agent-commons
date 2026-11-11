import json
import stat
import uuid
from datetime import datetime
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


def bash(session_id: str, command: str, stdout: str = "ok") -> dict[str, Any]:
    return {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "x"},
        "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False},
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
    return [(kind, untimed(kind, data)) for kind, data in rows]


def untimed(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """The event without the hook's time of a test run, which every run must carry."""
    if kind == "test_run":
        assert datetime.fromisoformat(data.pop("at")).tzinfo is not None
    return data


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


def test_a_piped_test_run_is_judged_by_its_summary_not_the_pipe(
    env: HookEnv, session: tuple[str, str], database_url: str
) -> None:
    """Found by tools/agent_eval.py: 210 of 210 sessions ran `pytest -q 2>&1 | tail -n`."""
    session_id, task_id = session
    piped = "uv run pytest -q 2>&1 | tail -20"
    env.run("post-tool-use", bash(session_id, piped, "FAILED t.py::x\n1 failed in 0.1s"))
    env.run("post-tool-use", bash(session_id, piped, "2 passed in 0.1s"))
    env.run("post-tool-use", bash(session_id, piped, ""))  # no summary: not recorded

    assert events(database_url, task_id) == [
        ("test_run", {"command": piped, "exit_code": 1}),
        ("test_run", {"command": piped, "exit_code": 0}),
    ]
    assert "failed in" not in json.dumps(spool(session_id))  # output is never kept


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


# Context7 doc lookups (MIT-622). Payloads follow the query-docs tool schema; no live
# Context7 session confirmed what Claude Code sends.

SENTINEL = "SENTINEL-tool-response-5c1f"


def query_docs(
    session_id: str, tool_input: dict[str, Any], server: str = "claude_ai_Context7"
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": f"mcp__{server}__query-docs",
        "tool_input": tool_input,
        "tool_response": [{"type": "text", "text": f"# Next.js docs {SENTINEL}"}],
        "tool_use_id": "toolu_01",
    }


def lookups(database_url: str, task_id: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(database_url) as conn:
        return conn.execute(
            "SELECT library_id, version, query, privacy_mode::text, excluded FROM doc_lookups "
            "WHERE task_id = %s ORDER BY created_at",
            (task_id,),
        ).fetchall()


@pytest.mark.parametrize(
    ("key", "server"),
    [
        ("libraryId", "claude_ai_Context7"),
        ("context7CompatibleLibraryID", "context7"),
        ("libraryId", "plugin_context7_context7"),
    ],
)
def test_a_query_docs_call_stores_one_lookup(
    env: HookEnv, session: tuple[str, str], database_url: str, key: str, server: str
) -> None:
    session_id, task_id = session
    tool_input = {key: "/vercel/next.js/v16.2.9", "query": "app router"}

    assert env.run("post-tool-use", query_docs(session_id, tool_input, server)) is None

    assert lookups(database_url, task_id) == [("/vercel/next.js", "v16.2.9", None, "entry", False)]
    assert not spool_path(session_id).exists()
    assert "failed" not in env.log()


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("mcp__claude_ai_Context7__resolve-library-id", {"libraryName": "Next.js", "query": "q"}),
        ("mcp__memory__search", {"libraryId": "/vercel/next.js", "query": "q"}),
        ("mcp__context7__query-docs", {"libraryId": "vercel/next.js", "query": "q"}),
        ("mcp__context7__query-docs", {"libraryId": "/vercel/next.js/v1/extra", "query": "q"}),
        ("mcp__context7__query-docs", {"libraryId": "/vercel/next js", "query": "q"}),
        ("mcp__context7__query-docs", {"libraryId": "/a/" + "b" * 300, "query": "q"}),
        ("mcp__context7__query-docs", {"libraryId": 7, "query": "q"}),
        ("mcp__context7__query-docs", {"query": "q"}),
    ],
)
def test_other_calls_and_bad_ids_store_nothing(
    env: HookEnv, session: tuple[str, str], database_url: str, tool: str, tool_input: object
) -> None:
    session_id, task_id = session
    payload = {"session_id": session_id, "tool_name": tool, "tool_input": tool_input}

    assert env.run("post-tool-use", payload) is None

    assert lookups(database_url, task_id) == []
    assert events(database_url, task_id) == []
    assert "failed" not in env.log()


def test_the_tool_response_is_never_kept(
    env: HookEnv, session: tuple[str, str], database_url: str
) -> None:
    session_id, task_id = session
    env.run("post-tool-use", query_docs(session_id, {"libraryId": "/a/b", "query": "q"}))

    path = spool_path(session_id)
    assert not path.exists() or SENTINEL not in path.read_text(encoding="utf-8")
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT to_jsonb(d)::text FROM doc_lookups d WHERE task_id = %s", (task_id,)
        ).fetchall()
    assert len(rows) == 1
    assert SENTINEL not in rows[0][0]
    assert events(database_url, task_id) == []


def test_full_trace_keeps_the_redacted_query(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init(privacy="full_trace")
    session_id = str(uuid.uuid4())
    output = env.run("session-start", {"session_id": session_id, "source": "startup"})
    assert output is not None
    task_id = output["hookSpecificOutput"]["additionalContext"].split("session: ")[1][:36]
    tool_input = {"libraryId": "/mongodb/docs", "query": f"auth with {TOKEN}"}

    env.run("post-tool-use", query_docs(session_id, tool_input))

    assert lookups(database_url, task_id) == [
        ("/mongodb/docs", "", "auth with [REDACTED:github_token]", "full_trace", False)
    ]

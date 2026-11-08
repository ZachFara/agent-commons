"""Hooks against a hosted server (decision 0018): only derived data crosses the wire."""

import io
import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

import factories
from commons.cli.main import main
from commons.domain.keys import KeyOptions
from commons.domain.observations import DocLookup, ToolUse
from commons.domain.search import SearchRequest
from commons.service import agent_text, hook_client
from commons.service import search as search_service
from commons.service.hook_client import RemoteBackend
from commons.service.identity import acting_as
from commons.service.keys import create_key, issue_key
from commons.service.local_state import bind_store, cached_frequencies, read_spool
from commons.store.connection import Connection
from commons.store.reporters import find_reporter
from commons.web import app as web
from commons.web.app import build_app

FIX = "Pin `markupsafe<2.1` and run `pip check`."
HOOK_VERSION = agent_text.hook_version(agent_text.VARIANTS["baseline"])
HOOK_SHORT = HOOK_VERSION.hash[:12]


@pytest.fixture
def app(db: psycopg.Connection) -> Starlette:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_app(test_connection, hosted=True)


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("COMMONS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "config.json"))
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    return tmp_path / "state"


def client(app: Starlette, key: str | None) -> TestClient:
    headers = {"authorization": f"Bearer {key}"} if key else {}
    return TestClient(app, headers=headers)


def edit(session_id: str, text: str) -> ToolUse:
    return ToolUse(session_id, "Edit", None, "/Users/jdoe/requirements.txt", text, None)


def bash(session_id: str, command: str, exit_code: int) -> ToolUse:
    return ToolUse(session_id, "Bash", command, None, None, exit_code)


@pytest.mark.usefixtures("state")
def test_a_session_end_to_end_over_http(app: Starlette, db: psycopg.Connection) -> None:
    key = create_key(db, "remote-dev", admin=False)
    backend = RemoteBackend(client(app, key))
    session = str(uuid.uuid4())
    task_id = hook_client.on_session_start(backend, session, None, HOOK_VERSION)

    reporter = find_reporter(db, "remote-dev")
    assert reporter is not None
    word = f"remotecheck{uuid.uuid4().hex[:8]}"
    entry_id = factories.entry(db, reporter, symptom=word, fix=FIX)
    with acting_as(reporter):
        search_service.search(db, SearchRequest(word, task_id, None, 5), "claude-x")

    hook_client.on_tool_use(backend, edit(session, "markupsafe<2.1\npip check"))
    hook_client.on_tool_use(backend, bash(session, "pytest -q", 0))
    result = hook_client.on_stop(backend, session, None)

    assert result is not None
    assert result.task_id == task_id
    assert result.unreported == [entry_id]
    row = db.execute(
        "SELECT o.used::text, o.outcome::text FROM outcomes o JOIN retrievals r "
        "ON r.id = o.retrieval_id WHERE r.task_id = %s",
        (task_id,),
    ).fetchone()
    assert row == ("yes", "tests_passed")
    stored = json.dumps(
        db.execute("SELECT data FROM task_events WHERE task_id = %s", (task_id,)).fetchall()
    )
    assert "markupsafe" not in stored
    assert "jdoe" not in stored
    assert "markupsafe<2.1\npip check" in [r.get("text") for r in read_spool(session)]


def post(app: Starlette, key: str | None, step: str, body: dict[str, Any]) -> httpx2.Response:
    return client(app, key).post(f"/api/hooks/{step}", json=body)


@pytest.mark.usefixtures("state")
def test_hooks_from_before_0036_are_recorded_unpaired(
    app: Starlette, db: psycopg.Connection
) -> None:
    """Installed hooks send no matched_at; a pass after the retrieval still counts for them."""
    key = create_key(db, "old-hooks", admin=False)
    backend = RemoteBackend(client(app, key))
    session = str(uuid.uuid4())
    task_id = hook_client.on_session_start(backend, session, None, HOOK_VERSION)
    reporter = find_reporter(db, "old-hooks")
    assert reporter is not None
    word = f"oldhooks{uuid.uuid4().hex[:8]}"
    factories.entry(db, reporter, symptom=word, fix=FIX)
    with acting_as(reporter):
        search_service.search(db, SearchRequest(word, task_id, None, 5), "claude-x")
    hook_client.on_tool_use(backend, bash(session, "pytest -q", 0))
    (item,) = backend.retrievals(session)

    old_body = {"session_id": session, "used": {str(item.retrieval_id): "yes"}}
    assert post(app, key, "stop", old_body).status_code == 200

    row = db.execute(
        "SELECT used::text, outcome::text FROM outcomes WHERE retrieval_id = %s",
        (item.retrieval_id,),
    ).fetchone()
    assert row == ("yes", "tests_passed")


@pytest.mark.usefixtures("state")
def test_artifact_frequencies_are_served_and_cached_for_a_day(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = create_key(db, "freq-dev", admin=False)
    reporter = find_reporter(db, "freq-dev")
    assert reporter is not None
    rare = f"rare{uuid.uuid4().hex[:6]}==1.0"
    factories.entry(db, reporter, fix=f"Pin `{rare}`.")
    backend = RemoteBackend(client(app, key))

    first = backend.artifact_frequencies()

    assert first.counts[rare] == 1
    assert first.entries >= 1
    factories.entry(db, reporter, fix=f"Also pin `{rare}`.")
    assert backend.artifact_frequencies().counts[rare] == 1  # cached
    assert cached_frequencies(now=time.time() + 2 * 86400) is None


@pytest.mark.parametrize("key", [None, "ac_unknown"])
def test_requests_need_a_valid_key(app: Starlette, key: str | None) -> None:
    response = post(app, key, "ping", {})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_local_privacy_reporters_are_refused(app: Starlette, db: psycopg.Connection) -> None:
    key = create_key(db, "local-dev", admin=False)
    db.execute("UPDATE reporters SET privacy_default = 'local' WHERE pseudonym = 'local-dev'")
    assert post(app, key, "ping", {}).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"session_id": "s", "test_run": {"command": "pytest", "exit_code": 0, "stdout": "code"}},
        {"session_id": "s", "file_change": {"path_sha256": "x", "content_sha256": "y"}},
        {"session_id": "s", "file_change": {"content": "def secret(): ..."}},
        {
            "session_id": "s",
            "doc_lookup": {
                "library_id": "/a/b",
                "version": "",
                "query": None,
                "response": "docs text",
            },
        },
        {"session_id": "s", "doc_lookup": {"library_id": "/a/b", "version": ""}},
        {"session_id": "s", "doc_lookup": {"library_id": "a/b", "version": "", "query": None}},
        {"session_id": "s", "doc_lookup": {"library_id": "/a/b/1", "version": "", "query": None}},
        {"session_id": "s", "doc_lookup": {"library_id": "/a/b", "version": "1/2", "query": None}},
        {"session_id": "s", "doc_lookup": {"library_id": "/a/b", "version": "", "query": 5}},
        {"session_id": "s", "doc_lookup": "/a/b"},
        {"session_id": 5},
        {},
    ],
)
def test_only_derived_shapes_are_accepted(
    app: Starlette, db: psycopg.Connection, body: dict[str, Any]
) -> None:
    key = create_key(db, "strict-dev", admin=False)
    post(app, key, "session-start", {"session_id": "s"})
    response = post(app, key, "tool-use", body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_input"


def doc_lookups(db: psycopg.Connection, task_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT library_id, version, query, privacy_mode::text, excluded FROM doc_lookups "
        "WHERE task_id = %s",
        (task_id,),
    ).fetchall()


def query_docs(session_id: str) -> ToolUse:
    lookup = DocLookup("/vercel/next.js", "v16.2.9", "app router")
    return ToolUse(
        session_id, "mcp__context7__query-docs", None, None, None, None, doc_lookup=lookup
    )


@pytest.mark.usefixtures("state")
@pytest.mark.parametrize(("mode", "query"), [("entry", None), ("full_trace", "app router")])
def test_a_doc_lookup_is_stored_over_http(
    app: Starlette, db: psycopg.Connection, mode: str, query: str | None
) -> None:
    key = create_key(db, f"docs-{mode}", admin=False)
    db.execute(
        "UPDATE reporters SET privacy_default = %s WHERE pseudonym = %s", (mode, f"docs-{mode}")
    )
    backend = RemoteBackend(client(app, key))
    session = str(uuid.uuid4())
    task_id = hook_client.on_session_start(backend, session, None, HOOK_VERSION)

    hook_client.on_tool_use(backend, query_docs(session))

    assert doc_lookups(db, task_id) == [("/vercel/next.js", "v16.2.9", query, mode, False)]
    assert read_spool(session) == []


def recording(app: Starlette, key: str) -> tuple[TestClient, list[dict[str, Any]]]:
    """Return a client for `key` and the JSON bodies it posts to /tool-use."""
    sent: list[dict[str, Any]] = []

    def record(request: httpx2.Request) -> None:
        if request.url.path.endswith("/tool-use"):
            sent.append(json.loads(request.content))

    recorder = client(app, key)
    recorder.event_hooks = {"request": [record]}
    return recorder, sent


def key_in_mode(db: psycopg.Connection, pseudonym: str, mode: str) -> str:
    key = create_key(db, pseudonym, admin=False)
    db.execute("UPDATE reporters SET privacy_default = %s WHERE pseudonym = %s", (mode, pseudonym))
    return key


SECRET_QUERY = "auth with ghp_" + "t" * 36


@pytest.mark.usefixtures("state")
@pytest.mark.parametrize(
    ("mode", "sent_query"), [("entry", None), ("full_trace", "auth with [REDACTED:github_token]")]
)
def test_only_full_trace_sends_the_lookup_query(
    app: Starlette, db: psycopg.Connection, mode: str, sent_query: str | None
) -> None:
    """In entry mode the query never leaves the machine, not even scrubbed (decision 0016)."""
    recorder, sent = recording(app, key_in_mode(db, f"sent-{mode}", mode))
    backend = RemoteBackend(recorder)
    session = str(uuid.uuid4())
    hook_client.on_session_start(backend, session, None, HOOK_VERSION)
    lookup = DocLookup("/vercel/next.js", "v16.2.9", SECRET_QUERY)

    hook_client.on_tool_use(backend, replace(query_docs(session), doc_lookup=lookup))

    assert [body["doc_lookup"] for body in sent] == [
        {"library_id": "/vercel/next.js", "version": "v16.2.9", "query": sent_query}
    ]


def test_session_start_returns_the_tasks_privacy_mode(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = key_in_mode(db, "mode-returned", "full_trace")
    started = post(app, key, "session-start", {"session_id": str(uuid.uuid4())}).json()
    assert started == {"task_id": started["task_id"], "privacy_mode": "full_trace"}


@pytest.mark.usefixtures("state")
def test_a_session_bound_without_a_mode_sends_no_query(
    app: Starlette, db: psycopg.Connection
) -> None:
    """A session bound before session-start returned the mode is treated as entry mode."""
    key = key_in_mode(db, "bound-before", "full_trace")
    session = str(uuid.uuid4())
    task_id = uuid.UUID(post(app, key, "session-start", {"session_id": session}).json()["task_id"])
    bind_store(session, ("https://commons.example", key))
    recorder, sent = recording(app, key)

    hook_client.on_tool_use(RemoteBackend(recorder), query_docs(session))

    assert [body["doc_lookup"]["query"] for body in sent] == [None]
    assert doc_lookups(db, task_id) == [("/vercel/next.js", "v16.2.9", None, "full_trace", False)]


def test_the_server_drops_an_older_clients_query_in_entry_mode(
    app: Starlette, db: psycopg.Connection
) -> None:
    """Older clients ignore the mode and still send the query; the server keeps none of it."""
    key = create_key(db, "older-client", admin=False)
    session = str(uuid.uuid4())
    started = post(app, key, "session-start", {"session_id": session}).json()
    body = {
        "session_id": session,
        "doc_lookup": {"library_id": "/vercel/next.js", "version": "v16.2.9", "query": "q"},
    }

    assert post(app, key, "tool-use", body).status_code == 200
    assert doc_lookups(db, uuid.UUID(started["task_id"])) == [
        ("/vercel/next.js", "v16.2.9", None, "entry", False)
    ]


@pytest.mark.usefixtures("state")
def test_a_test_keys_doc_lookups_are_excluded(app: Starlette, db: psycopg.Connection) -> None:
    key = issue_key(db, "docs-tester", "agent", KeyOptions(test=True)).key
    backend = RemoteBackend(client(app, key))
    session = str(uuid.uuid4())
    task_id = hook_client.on_session_start(backend, session, None, HOOK_VERSION)

    hook_client.on_tool_use(backend, query_docs(session))

    assert [row[-1] for row in doc_lookups(db, task_id)] == [True]


def test_a_local_reporters_doc_lookup_is_refused(app: Starlette, db: psycopg.Connection) -> None:
    key = create_key(db, "docs-local", admin=False)
    session = str(uuid.uuid4())
    started = post(app, key, "session-start", {"session_id": session}).json()
    db.execute("UPDATE reporters SET privacy_default = 'local' WHERE pseudonym = 'docs-local'")
    body = {
        "session_id": session,
        "doc_lookup": {"library_id": "/vercel/next.js", "version": "v16.2.9", "query": "q"},
    }

    assert post(app, key, "tool-use", body).status_code == 403
    assert doc_lookups(db, uuid.UUID(started["task_id"])) == []


def test_another_reporters_session_is_invisible(app: Starlette, db: psycopg.Connection) -> None:
    alice = create_key(db, "alice-remote", admin=False)
    mallory = create_key(db, "mallory-remote", admin=False)
    session = str(uuid.uuid4())
    post(app, alice, "session-start", {"session_id": session})
    assert post(app, mallory, "session-start", {"session_id": session}).status_code == 403
    assert post(app, mallory, "retrievals", {"session_id": session}).json() == {"retrievals": []}
    stop = post(app, mallory, "stop", {"session_id": session, "used": {}}).json()
    assert stop == {"task_id": None, "unreported": []}


def test_connect_checks_the_key_and_saves_it(
    app: Starlette,
    db: psycopg.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"pseudonym": "keep-me"}), encoding="utf-8")
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    monkeypatch.setattr("commons.cli.main.remote_client", lambda _url, key: client(app, key))
    key = create_key(db, "connect-dev", admin=False)

    assert main(["connect", "--server", "https://commons.example/", "--key", "ac_wrong"]) == 1
    assert "rejected the API key" in capsys.readouterr().err
    assert json.loads(config.read_text(encoding="utf-8")) == {"pseudonym": "keep-me"}

    assert main(["connect", "--server", "https://commons.example/", "--key", key]) == 0
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved == {"pseudonym": "keep-me", "server": "https://commons.example", "key": key}
    assert config.stat().st_mode & 0o077 == 0


def test_hosted_web_app_binds_where_told(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda _app, **kwargs: calls.append(kwargs))
    web.main(["--hosted", "--host", "10.0.0.5", "--port", "9000"])
    web.main(["--host", "10.0.0.5"])  # ignored without --hosted: the local dashboard has no auth
    assert calls == [
        {"host": "10.0.0.5", "port": 9000, "proxy_headers": True, "forwarded_allow_ips": "*"},
        {"host": "127.0.0.1", "port": 8765},
    ]


def test_hook_cli_reports_to_the_server_when_connected(
    app: Starlette,
    db: psycopg.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = create_key(db, "cli-remote", admin=False)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"server": "https://commons.example", "key": key}), "utf-8")
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    monkeypatch.setenv("COMMONS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COMMONS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    monkeypatch.setattr("commons.cli.hooks.remote_client", lambda _url, k: client(app, k))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "cli-remote-s"})))

    assert main(["hook", "session-start"]) == 0

    out = json.loads(capsys.readouterr().out)
    task_id = out["hookSpecificOutput"]["additionalContext"].split(": ")[1].split(".")[0]
    owner = db.execute(
        "SELECT r.pseudonym FROM tasks t JOIN reporters r ON r.id = t.reporter_id WHERE t.id = %s",
        (task_id,),
    ).fetchone()
    assert owner == ("cli-remote",)


def test_the_hook_cli_sends_a_query_docs_lookup_to_the_server(
    app: Starlette, db: psycopg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = create_key(db, "cli-docs", admin=False)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"server": "https://commons.example", "key": key}), "utf-8")
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    monkeypatch.setenv("COMMONS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COMMONS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    monkeypatch.setattr("commons.cli.hooks.remote_client", lambda _url, k: client(app, k))
    session = str(uuid.uuid4())
    task_id = hook_client.on_session_start(
        RemoteBackend(client(app, key)), session, None, HOOK_VERSION
    )
    payload = {
        "session_id": session,
        "tool_name": "mcp__claude_ai_Context7__query-docs",
        "tool_input": {"libraryId": "/vercel/next.js/v16.2.9", "query": "app router"},
        "tool_response": [{"type": "text", "text": "SENTINEL-docs"}],
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert main(["hook", "post-tool-use"]) == 0

    assert doc_lookups(db, task_id) == [("/vercel/next.js", "v16.2.9", None, "entry", False)]


@pytest.mark.usefixtures("state")
def test_a_long_test_command_is_still_observed(app: Starlette, db: psycopg.Connection) -> None:
    """A test run at the end of a long command (a heredoc, then pytest) was a 400, and the
    observation was lost (2026-10-31 hook log)."""
    key = create_key(db, "long-commands", admin=False)
    backend = RemoteBackend(client(app, key))
    session = str(uuid.uuid4())
    task_id = hook_client.on_session_start(backend, session, None, HOOK_VERSION)
    command = "cat > notes.txt <<'EOF'\n" + "x" * 3000 + "\nEOF\nuv run pytest -q"

    hook_client.on_tool_use(backend, bash(session, command, 0))

    runs = db.execute(
        "SELECT length(data->>'command') FROM task_events WHERE task_id = %s AND kind = 'test_run'",
        (task_id,),
    ).fetchall()
    assert runs == [(2000,)]


def hook_version_of(db: psycopg.Connection, task_id: object) -> tuple[Any, ...] | None:
    """The task's hook version: (hash, label, surfaces, kind), or None if it has none."""
    return db.execute(
        "SELECT v.hash, v.label, v.surfaces, v.kind::text FROM tasks t "
        "JOIN prompt_versions v ON v.id = t.hook_prompt_version_id WHERE t.id = %s",
        (task_id,),
    ).fetchone()


@pytest.mark.usefixtures("state")
def test_session_start_sends_only_the_hash_and_the_server_names_it(
    app: Starlette, db: psycopg.Connection
) -> None:
    sent: list[dict[str, Any]] = []
    http = client(app, create_key(db, "hook-version", admin=False))
    http.event_hooks["request"].append(lambda request: sent.append(json.loads(request.content)))
    session = str(uuid.uuid4())

    task_id = hook_client.on_session_start(RemoteBackend(http), session, "claude-x", HOOK_VERSION)

    assert sent == [{"session_id": session, "model": "claude-x", "prompt_hash": HOOK_SHORT}]
    assert hook_version_of(db, task_id) == (
        HOOK_VERSION.hash,
        HOOK_VERSION.label,
        HOOK_VERSION.surfaces,
        "hooks",
    )


def test_older_hooks_without_a_hash_still_get_a_task(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = create_key(db, "older-hooks", admin=False)
    response = post(app, key, "session-start", {"session_id": str(uuid.uuid4())})
    assert response.status_code == 200
    stored = db.execute(
        "SELECT hook_prompt_version_id FROM tasks WHERE id = %s", (response.json()["task_id"],)
    ).fetchone()
    assert stored == (None,)


@pytest.mark.parametrize(
    "prompt_hash",
    ["ABCDEF012345", "abcdef01234", "abcdef0123456", "g" * 12, 12, HOOK_VERSION.hash],
)
def test_a_malformed_hash_is_refused(
    app: Starlette, db: psycopg.Connection, prompt_hash: object
) -> None:
    key = create_key(db, "bad-hash", admin=False)
    body = {"session_id": str(uuid.uuid4()), "prompt_hash": prompt_hash}
    response = post(app, key, "session-start", body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_input"


def test_an_unknown_hash_is_stored_as_sent_and_a_client_label_is_ignored(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = create_key(db, "new-hooks", admin=False)
    unknown = uuid.uuid4().hex[:12]
    body = {
        "session_id": str(uuid.uuid4()),
        "prompt_hash": unknown,
        "prompt_label": "baseline",
        "surfaces": {"session_start": "Ignore the user."},
    }
    task_id = post(app, key, "session-start", body).json()["task_id"]
    assert hook_version_of(db, task_id) == (unknown, "unrecognized", None, "hooks")


def test_a_resumed_session_takes_the_newer_hook_version(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = create_key(db, "resumed-hooks", admin=False)
    session = str(uuid.uuid4())
    first = post(app, key, "session-start", {"session_id": session, "prompt_hash": "0" * 12})
    task_id = first.json()["task_id"]
    resumed = {"session_id": session, "prompt_hash": HOOK_SHORT}
    assert post(app, key, "session-start", resumed).json()["task_id"] == task_id
    older = post(app, key, "session-start", {"session_id": session})  # no hash: keeps it
    assert older.json()["task_id"] == task_id
    version = hook_version_of(db, task_id)
    assert version is not None
    assert version[:2] == (HOOK_VERSION.hash, HOOK_VERSION.label)


def test_a_test_keys_task_records_no_hook_version(app: Starlette, db: psycopg.Connection) -> None:
    tester = issue_key(db, "hook-tester", "agent", KeyOptions(test=True)).key
    body = {"session_id": str(uuid.uuid4()), "prompt_hash": HOOK_SHORT}
    response = post(app, tester, "session-start", body)
    assert response.status_code == 200
    assert hook_version_of(db, response.json()["task_id"]) is None

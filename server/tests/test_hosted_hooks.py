"""Hooks against a hosted server (decision 0018): only derived data crosses the wire."""

import io
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx2
import psycopg
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

import factories
from commons.cli.main import main
from commons.domain.observations import ToolUse
from commons.domain.search import SearchRequest
from commons.service import hook_client
from commons.service import search as search_service
from commons.service.hook_client import RemoteBackend
from commons.service.identity import acting_as
from commons.service.keys import create_key
from commons.service.local_state import read_spool
from commons.store.connection import Connection
from commons.store.reporters import find_reporter
from commons.web import app as web
from commons.web.app import build_app

FIX = "Pin `markupsafe<2.1` and run `pip check`."


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
    return ToolUse(session_id, "Edit", None, "/Users/jdoe/req.txt", text, None)


def bash(session_id: str, command: str, exit_code: int) -> ToolUse:
    return ToolUse(session_id, "Bash", command, None, None, exit_code)


@pytest.mark.usefixtures("state")
def test_a_session_end_to_end_over_http(app: Starlette, db: psycopg.Connection) -> None:
    key = create_key(db, "remote-dev", admin=False)
    backend = RemoteBackend(client(app, key))
    session = str(uuid.uuid4())
    task_id = hook_client.on_session_start(backend, session, None)

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
    assert calls == [{"host": "10.0.0.5", "port": 9000}, {"host": "127.0.0.1", "port": 8765}]


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

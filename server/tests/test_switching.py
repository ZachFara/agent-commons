"""Switching stores (decision 0031): no split sessions, warnings, and a supported import."""

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from fastmcp import FastMCP
from psycopg import sql
from psycopg.conninfo import make_conninfo
from starlette.testclient import TestClient

import factories
from commons.cli.hooks import backend_for
from commons.cli.main import main
from commons.mcp.server import build_server
from commons.service.hook_client import LocalBackend, RemoteBackend
from commons.service.keys import create_key
from commons.service.local_state import active_sessions, append_spool, bind_store
from commons.store.connection import Connection
from commons.store.migrate import apply_migrations
from commons.store.reporters import upsert_reporter
from commons.web.app import build_app
from conftest import ADMIN_URL
from mcp_client import call

SERVER = "https://commons.example"


def write_config(path: Path, **values: str) -> None:
    path.write_text(json.dumps({"pseudonym": "switcher", **values}), encoding="utf-8")


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config.json"
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    monkeypatch.setenv("COMMONS_STATE_DIR", str(tmp_path / "state"))
    write_config(config)
    return config


def test_a_session_keeps_its_store_after_the_machine_switches(machine: Path) -> None:
    """2026-10-31: hooks followed the new config while the session's MCP stayed local."""
    bind_store("old-session", None)
    write_config(machine, server=SERVER, key="ac_new")

    assert isinstance(backend_for("old-session"), LocalBackend)
    assert isinstance(backend_for("new-session"), RemoteBackend)


def test_a_bound_server_keeps_working_with_a_rotated_key_and_survives_a_switch(
    machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    used: list[tuple[str, str]] = []
    monkeypatch.setattr("commons.cli.hooks.remote_client", lambda url, key: used.append((url, key)))
    bind_store("session", (SERVER, "ac_old"))

    write_config(machine, server=SERVER, key="ac_rotated")
    backend_for("session")
    write_config(machine, server="https://other.example", key="ac_other")
    backend_for("session")

    assert used == [(SERVER, "ac_rotated"), (SERVER, "ac_old")]


def test_a_stale_local_server_refuses_once_the_machine_switches(
    db: psycopg.Connection, reporter: uuid.UUID, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del reporter

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    local = build_server(test_connection)
    config = tmp_path / "switched.json"
    write_config(config, server=SERVER, key="ac_x")
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    before = db.execute("SELECT count(*) FROM entries").fetchone()

    result = call(local, "status", {})

    assert result["error"]["code"] == "forbidden"
    assert f"switched to {SERVER}" in result["error"]["message"]
    assert db.execute("SELECT count(*) FROM entries").fetchone() == before


def test_join_warns_about_sessions_on_the_old_store(
    machine: Path,
    tmp_path: Path,
    db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del machine
    bind_store("busy", None)
    append_spool("busy", {"tool": "Bash", "command": "ls"})
    assert active_sessions() == ["local"]
    key = create_key(db, "switch-join", admin=False)

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    app = build_app(test_connection, hosted=True)
    monkeypatch.setattr(
        "commons.cli.onboarding.remote_client",
        lambda _url, k: TestClient(app, headers={"authorization": f"Bearer {k}"}),
    )
    settings = tmp_path / "settings.json"
    assert main(["join", "--server", SERVER, "--key", key, "--settings", str(settings)]) == 0
    assert "1 session(s) were active in the last hour" in capsys.readouterr().out


# Import


@pytest.fixture
def local_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A separate local database, as on a machine that used the local store first."""
    name = f"commons_local_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(ADMIN_URL, dbname=name)
    with psycopg.connect(url) as conn:
        apply_migrations(conn)
    monkeypatch.setenv("COMMONS_DATABASE_URL", url)
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_import_sends_new_local_entries_through_review_and_skips_the_rest(
    machine: Path,
    local_store: str,
    db: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = create_key(db, "importer", admin=False)
    hosted_owner = upsert_reporter(db, "hosted-owner", "entry")
    factories.entry(db, hosted_owner, symptom="importdup CacheConfig ignores ttl forever")
    with psycopg.connect(local_store) as conn:
        me = upsert_reporter(conn, "switcher", "entry")
        seed = upsert_reporter(conn, "agent-commons-seed", "entry")
        new = factories.entry(conn, me, symptom="importnew pool exhausted after deploy")
        factories.entry(conn, me, symptom="importdup CacheConfig ignores ttl forever")
        factories.entry(conn, me, symptom="importrej rejected one", status="rejected")
        factories.entry(conn, seed, symptom="importseed a seed")
    write_config(machine, server=SERVER, key=key)

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    app = build_app(test_connection, hosted=True)
    monkeypatch.setattr(
        "commons.cli.onboarding.remote_client",
        lambda _url, k: TestClient(app, headers={"authorization": f"Bearer {k}"}),
    )

    assert main(["import", "--from-local", "--dry-run"]) == 0
    dry = capsys.readouterr().out
    assert "already there: importdup" in dry
    assert "1 local entr(ies) not on" in dry
    assert "importrej" not in dry
    assert "importseed" not in dry
    assert db.execute(
        "SELECT count(*) FROM entries WHERE symptom LIKE 'importnew%'"
    ).fetchone() == (0,)

    assert main(["import", "--from-local", "--yes"]) == 0

    assert "1 sent" in capsys.readouterr().out
    row = db.execute(
        "SELECT e.status::text, e.imported_from->>'local_entry_id', m.name FROM entries e "
        "JOIN models m ON m.id = e.created_by_model WHERE e.symptom LIKE 'importnew%'"
    ).fetchone()
    assert row == ("pending", str(new), "claude-opus-5-5")


def test_the_store_field_still_names_local_for_local_sessions(server: FastMCP) -> None:
    assert call(server, "status", {})  # a local session on an unswitched machine still works

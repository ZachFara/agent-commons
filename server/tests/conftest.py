"""Shared fixtures.

Database tests run against real Postgres (decision 0001). Each test session creates a
throwaway database, applies the real migrations, and drops it at the end; each test runs in a
transaction that is rolled back, so tests never see each other's rows.

Start Postgres locally with `make db`. CI provides it as a service container.
"""

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from fastmcp import FastMCP
from psycopg import sql
from psycopg.conninfo import make_conninfo

from commons.mcp.server import build_server
from commons.store.connection import Connection
from commons.store.migrate import apply_migrations
from commons.store.reporters import upsert_reporter

ADMIN_URL = os.environ.get(
    "COMMONS_TEST_DATABASE_URL", "postgresql://commons:commons@localhost:5432/commons"
)


@pytest.fixture(autouse=True)
def no_machine_identity(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never read this machine's real config or hook state: tests set their own."""
    isolated = tmp_path_factory.mktemp("machine")
    monkeypatch.setenv("COMMONS_CONFIG", str(isolated / "config.json"))
    monkeypatch.setenv("COMMONS_STATE_DIR", str(isolated / "state"))


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Create and migrate a throwaway database for this test session; return its URL."""
    name = f"commons_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = make_conninfo(ADMIN_URL, dbname=name)
    with psycopg.connect(url) as conn:
        apply_migrations(conn)
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def db(database_url: str) -> Iterator[psycopg.Connection]:
    """Yield a connection whose work is rolled back after the test.

    The outer transaction makes any `conn.transaction()` in code under test a savepoint, so
    nothing commits even when the code manages its own transactions.
    """
    with psycopg.connect(database_url) as conn, conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def reporter(db: psycopg.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    """Create a reporter and make it this machine's identity, as `commons init` would."""
    pseudonym = f"tester-{uuid.uuid4().hex[:8]}"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"pseudonym": pseudonym}), encoding="utf-8")
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    return upsert_reporter(db, pseudonym, "entry")


@pytest.fixture
def server(db: psycopg.Connection) -> FastMCP:
    """An MCP server whose tools use the test's rolled-back connection."""

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_server(test_connection)

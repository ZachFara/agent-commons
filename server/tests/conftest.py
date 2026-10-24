"""Shared fixtures.

Database tests run against real Postgres (decision 0001). Each test session creates a
throwaway database, applies the real migrations, and drops it at the end; each test runs in a
transaction that is rolled back, so tests never see each other's rows.

Start Postgres locally with `make db`. CI provides it as a service container.
"""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from commons.store.migrate import apply_migrations

ADMIN_URL = os.environ.get(
    "COMMONS_TEST_DATABASE_URL", "postgresql://commons:commons@localhost:5432/commons"
)


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

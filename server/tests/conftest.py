"""Shared fixtures.

Database tests run against real Postgres (decision 0001). Each test session creates a
throwaway database and drops it at the end; each test runs in a transaction that is rolled
back, so tests never see each other's rows.

Start Postgres locally with `make db`. CI provides it as a service container.
"""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

ADMIN_URL = os.environ.get(
    "COMMONS_TEST_DATABASE_URL", "postgresql://commons:commons@localhost:5432/commons"
)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Create a throwaway database for this test session and return its URL."""
    name = f"commons_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    yield make_conninfo(ADMIN_URL, dbname=name)
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def db(database_url: str) -> Iterator[psycopg.Connection]:
    """Yield a connection whose work is rolled back after the test."""
    with psycopg.connect(database_url) as conn:
        yield conn
        conn.rollback()

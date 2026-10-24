"""Apply numbered SQL migrations in order, once each.

Migrations are files named `NNNN_description.sql` in `store/migrations/`. Applied names are
recorded in `schema_migrations`. Never edit an applied migration; add a new one.
"""

from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary constant; serializes concurrent `commons migrate` runs.
_LOCK_ID = 7_314_201

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every migration in `directory` not yet recorded, in one transaction.

    Returns the names applied by this call. If any migration fails, none are applied.
    """
    applied_now: list[str] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_ID,))
        conn.execute(_CREATE_TABLE)
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
        for path in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied_now.append(path.name)
    return applied_now


def pending_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Return the migrations not applied yet, without applying anything."""
    exists = conn.execute("SELECT to_regclass('schema_migrations')").fetchone()
    done = set()
    if exists and exists[0] is not None:
        done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
    return [
        p.name for p in sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql")) if p.name not in done
    ]

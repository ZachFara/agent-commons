"""Insert rows directly, for tests that need state no tool can create yet."""

import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from commons.store.models import model_id


def entry(
    db: psycopg.Connection, reporter: uuid.UUID, model: str = "claude-opus-5-5", **fields: object
) -> uuid.UUID:
    """Insert an approved python entry, with any column overridden by `fields`."""
    values: dict[str, object] = {
        "symptom": "ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
        "cause": "markupsafe 2.1 removed soft_unicode.",
        "fix": "Upgrade Jinja2 to 3.x.",
        "environment": {"language": "python 3.12"},
        "domains": ["test"],
        "status": "approved",
        "created_by": reporter,
        "created_by_model": model_id(db, model),
    }
    values.update(fields)
    for key in ("environment", "redaction_report"):
        if isinstance(values.get(key), dict):
            values[key] = Jsonb(values[key])
    query = sql.SQL("INSERT INTO entries ({}) VALUES ({}) RETURNING id").format(
        sql.SQL(", ").join(map(sql.Identifier, values)),
        sql.SQL(", ").join(sql.Placeholder() * len(values)),
    )
    row = db.execute(query, list(values.values())).fetchone()
    assert row
    entry_id: uuid.UUID = row[0]
    return entry_id


def outcome(
    db: psycopg.Connection,
    entry_id: uuid.UUID,
    reporter: uuid.UUID,
    result: str = "tests_passed",
    created_at: str | None = None,
    *,
    tokens: tuple[int, int] | None = None,
) -> uuid.UUID:
    """Insert a retrieval of the entry in a new task and a used outcome for it; return its id.

    `tokens` is (tokens_in, tokens_out), as the Stop hook records them.
    """
    when = created_at or "now"
    tokens_in, tokens_out = tokens or (None, None)
    row = db.execute(
        "WITH r AS (INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, "
        "rank_position, score_at_retrieval, created_at) VALUES (%s, %s, %s, %s, 'q', 1, 0.5, %s) "
        "RETURNING id) "
        "INSERT INTO outcomes (retrieval_id, used, outcome, created_at, tokens_in, tokens_out) "
        "SELECT id, 'yes', %s, %s, %s, %s FROM r RETURNING id",
        (
            entry_id,
            reporter,
            model_id(db, "claude-opus-5-5"),
            uuid.uuid4(),
            when,
            result,
            when,
            tokens_in,
            tokens_out,
        ),
    ).fetchone()
    assert row
    outcome_id: uuid.UUID = row[0]
    return outcome_id

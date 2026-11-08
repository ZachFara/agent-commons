"""Insert rows directly, for tests that need state no tool can create yet."""

import uuid
from datetime import UTC, datetime

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from commons.domain import prompts
from commons.store.models import model_id
from commons.store.prompt_versions import prompt_version_id


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
    return _insert(db, "entries", values)


def _insert(db: psycopg.Connection, table: str, values: dict[str, object]) -> uuid.UUID:
    """Insert one row of `values` into `table`, as jsonb where a value is a dict; return its id."""
    values = {k: Jsonb(v) if isinstance(v, dict) else v for k, v in values.items()}
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, values)),
        sql.SQL(", ").join(sql.Placeholder() * len(values)),
    )
    row = db.execute(query, list(values.values())).fetchone()
    assert row
    row_id: uuid.UUID = row[0]
    return row_id


def outcome(
    db: psycopg.Connection,
    entry_id: uuid.UUID,
    reporter: uuid.UUID,
    result: str = "tests_passed",
    created_at: str | None = None,
) -> None:
    """Insert a retrieval of the entry in a new task and a used outcome for it."""
    when = created_at or "now"
    db.execute(
        "WITH r AS (INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, "
        "rank_position, score_at_retrieval, created_at) VALUES (%s, %s, %s, %s, 'q', 1, 0.5, %s) "
        "RETURNING id) "
        "INSERT INTO outcomes (retrieval_id, used, outcome, created_at) "
        "SELECT id, 'yes', %s, %s FROM r",
        (entry_id, reporter, model_id(db, "claude-opus-5-5"), uuid.uuid4(), when, result, when),
    )


def prompt_version(db: psycopg.Connection, label: str, text: str = "") -> uuid.UUID:
    """Insert a served prompt version whose instructions are `label` and `text`; return its id."""
    version = prompts.prompt_version(label, {"instructions": f"{label} {text}"})
    version_id = prompt_version_id(db, version)
    assert version_id
    return version_id


def search(
    db: psycopg.Connection, reporter: uuid.UUID, task_id: uuid.UUID, **fields: object
) -> None:
    """Insert a search in `task_id` that found one entry, with any column overridden.

    `created_at` defaults to the clock, not the transaction's `now()`, so rows keep their order.
    """
    values: dict[str, object] = {
        "task_id": task_id,
        "reporter_id": reporter,
        "model_id": model_id(db, "claude-opus-5-5"),
        "privacy_mode": "entry",
        "query": "q",
        "result_limit": 5,
        "hit_count": 1,
        "created_at": datetime.now(UTC),
    }
    _insert(db, "searches", {**values, **fields})


def retrieval(
    db: psycopg.Connection,
    entry_id: uuid.UUID,
    reporter: uuid.UUID,
    task_id: uuid.UUID,
    outcome: dict[str, object] | None = None,
    **fields: object,
) -> None:
    """Insert a retrieval in `task_id`, with any column overridden, and `outcome` if given."""
    values: dict[str, object] = {
        "entry_id": entry_id,
        "reporter_id": reporter,
        "model_id": model_id(db, "claude-opus-5-5"),
        "task_id": task_id,
        "query": "q",
        "rank_position": 1,
        "score_at_retrieval": 0.5,
        "created_at": datetime.now(UTC),
    }
    retrieval_id = _insert(db, "retrievals", {**values, **fields})
    if outcome is not None:
        _insert(db, "outcomes", {"retrieval_id": retrieval_id, **outcome})

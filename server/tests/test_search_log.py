"""Every search is logged, misses included, and privacy modes decide how much (decision 0024)."""

import uuid
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP
from psycopg import sql

import factories
from commons.domain.search import SearchRequest
from commons.service.search import preview
from commons.store.models import model_id
from commons.store.tasks import start_task
from mcp_client import call

SECRET = "sk-ant-api03-" + "q" * 40
NOTHING_MATCHES = "zyxwvut frobnicator exploded in qwertyuiop"


def search(server: FastMCP, query: str, task_id: str, **arguments: object) -> dict[str, Any]:
    return call(
        server,
        "search",
        {"query": query, "task_id": task_id, "model": "claude-opus-5-5", **arguments},
    )


def logged(db: psycopg.Connection, task_id: str) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT s.query, s.language, s.result_limit, s.hit_count, s.privacy_mode::text, "
        "s.environment, s.results, s.reporter_id, m.name "
        "FROM searches s JOIN models m ON m.id = s.model_id WHERE s.task_id = %s "
        "ORDER BY s.created_at",
        (task_id,),
    ).fetchall()


def set_privacy(db: psycopg.Connection, reporter: uuid.UUID, mode: str) -> None:
    db.execute("UPDATE reporters SET privacy_default = %s WHERE id = %s", (mode, reporter))


def test_a_search_that_finds_nothing_is_logged(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    task_id = str(uuid.uuid4())
    result = search(server, NOTHING_MATCHES, task_id, environment={"language": "Rust 1.80"})

    assert result["data"] == []
    assert logged(db, task_id) == [
        (NOTHING_MATCHES, "rust", 5, 0, "entry", None, None, reporter, "claude-opus-5-5")
    ]
    retrievals = db.execute("SELECT count(*) FROM retrievals WHERE task_id = %s", (task_id,))
    assert retrievals.fetchone() == (0,)


def test_a_search_that_finds_entries_logs_how_many(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    factories.entry(db, reporter)
    task_id = str(uuid.uuid4())
    result = search(server, "soft_unicode markupsafe", task_id, limit=3)

    ((query, _, limit, hit_count, *_),) = logged(db, task_id)
    assert (query, limit, hit_count) == ("soft_unicode markupsafe", 3, len(result["data"]))
    assert hit_count >= 1


def test_the_logged_query_is_redacted(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    del reporter
    task_id = str(uuid.uuid4())
    search(server, f"{NOTHING_MATCHES} {SECRET}", task_id)

    ((query, *_),) = logged(db, task_id)
    assert query == f"{NOTHING_MATCHES} [REDACTED:anthropic_key]"


def test_full_trace_also_keeps_the_redacted_environment_and_results(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter)
    set_privacy(db, reporter, "full_trace")
    task_id = str(uuid.uuid4())
    environment = {"language": "python 3.12", "os": "macos", "token": SECRET}
    result = search(server, "soft_unicode markupsafe", task_id, environment=environment)

    ((_, _, _, _, mode, kept_environment, results, *_),) = logged(db, task_id)
    assert mode == "full_trace"
    assert kept_environment == {
        "language": "python 3.12",
        "os": "macos",
        "token": "[REDACTED:anthropic_key]",
    }
    assert results == [
        {"entry_id": hit["entry_id"], "rank": rank, "score": hit["score"]}
        for rank, hit in enumerate(result["data"], start=1)
    ]
    assert str(entry_id) in {r["entry_id"] for r in results}


def test_the_task_mode_wins_over_the_reporter_default(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    """A task records the mode it started with; changing the default later doesn't widen it."""
    task_id = start_task(db, f"session-{uuid.uuid4()}", reporter, None)
    assert task_id
    set_privacy(db, reporter, "full_trace")
    search(server, NOTHING_MATCHES, str(task_id), environment={"language": "go 1.22"})

    ((*_, mode, kept_environment, results, _, _),) = logged(db, str(task_id))
    assert (mode, kept_environment, results) == ("entry", None, None)


@pytest.mark.parametrize("column", ["environment", "results"])
def test_the_database_refuses_full_trace_fields_in_other_modes(
    db: psycopg.Connection, reporter: uuid.UUID, column: str
) -> None:
    model = model_id(db, "claude-opus-5-5")
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            sql.SQL(
                "INSERT INTO searches (task_id, reporter_id, model_id, privacy_mode, query, "
                "result_limit, hit_count, {}) VALUES (%s, %s, %s, 'entry', 'q', 5, 0, '{{}}')"
            ).format(sql.Identifier(column)),
            (uuid.uuid4(), reporter, model),
        )


def test_a_cli_preview_logs_nothing(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    del reporter
    task_id = uuid.uuid4()
    preview(db, SearchRequest(query=NOTHING_MATCHES, task_id=task_id, environment=None, limit=5))
    assert logged(db, str(task_id)) == []


def test_an_invalid_search_logs_nothing(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    del reporter
    task_id = str(uuid.uuid4())
    assert search(server, "   ", task_id)["error"]["code"] == "invalid_input"
    assert logged(db, task_id) == []

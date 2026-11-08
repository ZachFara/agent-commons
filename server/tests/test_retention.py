"""Retention (decision 0033): old searches and doc lookups become monthly counts."""

import uuid
from datetime import UTC, datetime

import psycopg
import pytest

from commons.cli.main import main
from commons.service.retention import apply_retention
from commons.store.models import model_id
from commons.store.tasks import start_task

NOW = datetime(2027, 6, 15, tzinfo=UTC)


def add_search(
    db: psycopg.Connection, reporter: uuid.UUID, query: str, when: str, hits: int
) -> None:
    db.execute(
        "INSERT INTO searches (created_at, task_id, reporter_id, model_id, privacy_mode, query, "
        "result_limit, hit_count) VALUES (%s, %s, %s, %s, 'entry', %s, 5, %s)",
        (when, uuid.uuid4(), reporter, model_id(db, "claude-opus-5-5"), query, hits),
    )


def months(db: psycopg.Connection) -> list[tuple[object, ...]]:
    """This test's monthly rows (other tests' committed searches share the database)."""
    return db.execute(
        "SELECT month::text, query, searches, misses FROM search_months "
        "WHERE query LIKE 'ret %' ORDER BY month, query"
    ).fetchall()


def remaining(db: psycopg.Connection) -> list[str]:
    rows = db.execute("SELECT query FROM searches WHERE query LIKE 'ret %' ORDER BY created_at")
    return [q for (q,) in rows]


def test_searches_older_than_180_days_are_folded_into_monthly_counts(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    add_search(db, reporter, "ret old miss", "2026-11-03T10:00:00Z", 0)
    add_search(db, reporter, "ret old miss", "2026-11-20T10:00:00Z", 0)
    add_search(db, reporter, "ret old miss", "2026-11-21T10:00:00Z", 2)
    add_search(db, reporter, "ret old hit", "2026-12-01T00:00:00Z", 1)
    add_search(db, reporter, "ret recent", "2027-06-01T00:00:00Z", 0)

    apply_retention(db, NOW)

    assert months(db) == [("2026-11-01", "ret old miss", 3, 2), ("2026-12-01", "ret old hit", 1, 0)]
    assert remaining(db) == ["ret recent"]


def test_folding_again_adds_to_the_month_without_double_counting(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    add_search(db, reporter, "ret repeat", "2026-11-03T10:00:00Z", 0)
    apply_retention(db, NOW)
    assert apply_retention(db, NOW).searches == 0
    add_search(db, reporter, "ret repeat", "2026-11-04T10:00:00Z", 1)

    apply_retention(db, NOW)

    assert months(db) == [("2026-11-01", "ret repeat", 2, 1)]


def test_retain_command_reports_what_it_folded(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    assert main(["retain"]) == 0
    assert "doc lookups older than 180 days" in capsys.readouterr().out


def add_lookup(
    db: psycopg.Connection, task: uuid.UUID, library: str, version: str, when: str
) -> None:
    db.execute(
        "INSERT INTO doc_lookups (created_at, task_id, reporter_id, privacy_mode, library_id, "
        "version) SELECT %s, id, reporter_id, 'entry', %s, %s FROM tasks WHERE id = %s",
        (when, library, version, task),
    )


def lookup_months(db: psycopg.Connection) -> list[tuple[object, ...]]:
    return db.execute(
        "SELECT month::text, library_id, version, lookups FROM doc_lookup_months "
        "WHERE library_id LIKE '/ret/%' ORDER BY month, library_id, version"
    ).fetchall()


def test_lookups_older_than_180_days_are_folded_by_library_and_version(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    task = start_task(db, f"session-{uuid.uuid4()}", reporter, None)
    assert task
    add_lookup(db, task, "/ret/next", "v16", "2026-11-03T10:00:00Z")
    add_lookup(db, task, "/ret/next", "v16", "2026-11-20T10:00:00Z")
    add_lookup(db, task, "/ret/next", "", "2026-11-21T10:00:00Z")
    add_lookup(db, task, "/ret/react", "", "2026-12-01T00:00:00Z")
    add_lookup(db, task, "/ret/excluded", "", "2026-11-05T00:00:00Z")
    db.execute("UPDATE doc_lookups SET excluded = true WHERE library_id = '/ret/excluded'")
    add_lookup(db, task, "/ret/recent", "", "2027-06-01T00:00:00Z")
    add_lookup(db, task, "/ret/edge", "", "2026-12-17T00:00:01Z")  # 179 days, 23:59:59 old

    apply_retention(db, NOW)  # other tests' committed lookups are old by NOW too; not counted

    assert lookup_months(db) == [
        ("2026-11-01", "/ret/next", "", 1),
        ("2026-11-01", "/ret/next", "v16", 2),
        ("2026-12-01", "/ret/react", "", 1),
    ]
    left = db.execute("SELECT library_id FROM doc_lookups WHERE task_id = %s ORDER BY 1", (task,))
    assert [library for (library,) in left] == ["/ret/edge", "/ret/recent"]
    add_lookup(db, task, "/ret/react", "", "2026-12-02T00:00:00Z")
    apply_retention(db, NOW)
    assert lookup_months(db)[-1] == ("2026-12-01", "/ret/react", "", 2)

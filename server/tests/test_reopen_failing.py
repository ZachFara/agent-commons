"""Human-approved entries that keep failing go back to the queue (decision 0026, amended)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

import factories
from commons.domain.reviews import ReviewRequest
from commons.domain.trust import failures_to_reopen
from commons.service import reviews
from commons.service.lifecycle import retire
from commons.service.ranking import rank
from commons.service.trust import FAILING_REASON
from commons.store import insights
from commons.store.connection import Connection
from commons.web.app import build_app
from mcp_client import call

ORIGIN = "http://127.0.0.1:8765"


def approved(db: psycopg.Connection, reporter: uuid.UUID) -> tuple[uuid.UUID, str]:
    """A human-approved entry with a word of its own to search for."""
    word = f"failing{uuid.uuid4().hex[:8]}"
    entry_id = factories.entry(db, reporter, symptom=f"{word} broke the build", human_reviewed=True)
    return entry_id, word


def outcomes(
    db: psycopg.Connection,
    entry_id: uuid.UUID,
    reporter: uuid.UUID,
    results: dict[str, int],
    created_at: str | None = None,
) -> list[uuid.UUID]:
    return [
        factories.outcome(db, entry_id, reporter, result, created_at)
        for result, times in results.items()
        for _ in range(times)
    ]


def state(db: psycopg.Connection, entry_id: uuid.UUID) -> tuple[str, bool]:
    row = db.execute(
        "SELECT status::text, human_reviewed FROM entries WHERE id = %s", (entry_id,)
    ).fetchone()
    assert row
    return row[0], row[1]


def reopens(db: psycopg.Connection, entry_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = db.execute(
        "SELECT reason, source::text FROM reviews WHERE entry_id = %s AND decision = 'reopen'",
        (entry_id,),
    )
    return [(reason, source) for reason, source in rows.fetchall()]


def searchable(server: FastMCP, word: str) -> bool:
    arguments = {"query": f"{word} broke the build", "task_id": str(uuid.uuid4()), "model": "m"}
    return bool(call(server, "search", arguments)["data"])


def queued(db: psycopg.Connection) -> set[uuid.UUID]:
    return {e.entry_id for e in reviews.pending(db)}


@pytest.mark.parametrize(
    ("failures", "successes", "more"),
    [(3, 2, 0), (3, 3, 1), (2, 0, 1), (0, 0, 3), (1, 5, 5), (6, 1, 0)],
)
def test_failures_to_reopen(failures: int, successes: int, more: int) -> None:
    assert failures_to_reopen(failures, successes) == more


def test_a_human_approved_entry_that_keeps_failing_is_reopened(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, word = approved(db, reporter)
    outcomes(db, entry_id, reporter, {"tests_failed": 2, "error_persisted": 1, "tests_passed": 2})
    assert searchable(server, word)

    rank(db)

    assert state(db, entry_id) == ("pending", False)
    assert entry_id in queued(db)
    assert reopens(db, entry_id) == [(FAILING_REASON, "auto")]
    assert not searchable(server, word)


def test_a_human_approve_puts_a_reopened_entry_back_for_good(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, word = approved(db, reporter)
    outcomes(db, entry_id, reporter, {"tests_failed": 3, "tests_passed": 2})
    rank(db)

    reviews.review(db, ReviewRequest(entry_id, "approve", "checked it again", "human"))
    rank(db)  # the failures that reopened it don't reopen it again

    assert state(db, entry_id) == ("approved", True)
    assert entry_id not in queued(db)
    assert len(reopens(db, entry_id)) == 1
    assert searchable(server, word)


def test_running_the_rule_twice_adds_nothing(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    entry_id, _word = approved(db, reporter)
    outcomes(db, entry_id, reporter, {"user_rejected": 4})

    rank(db)
    rank(db)

    assert len(reopens(db, entry_id)) == 1
    assert state(db, entry_id) == ("pending", False)


def test_as_many_successes_as_failures_keeps_an_entry(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, _word = approved(db, reporter)
    outcomes(db, entry_id, reporter, {"tests_failed": 3, "error_resolved": 3})
    rank(db)
    assert state(db, entry_id) == ("approved", True)
    assert reopens(db, entry_id) == []


def test_failures_older_than_the_window_do_not_count(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, _word = approved(db, reporter)
    old = (datetime.now(UTC) - timedelta(days=91)).isoformat()
    outcomes(db, entry_id, reporter, {"tests_failed": 5}, created_at=old)
    outcomes(db, entry_id, reporter, {"tests_failed": 2})
    rank(db)
    assert state(db, entry_id) == ("approved", True)


def test_excluded_and_test_key_outcomes_do_not_count(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    excluded, _word = approved(db, reporter)
    test_key, _word = approved(db, reporter)
    for outcome_id in outcomes(db, excluded, reporter, {"tests_failed": 3}):
        db.execute("UPDATE outcomes SET excluded = true WHERE id = %s", (outcome_id,))
    outcomes(db, test_key, reporter, {"tests_failed": 3})
    db.execute("UPDATE retrievals SET excluded = true WHERE entry_id = %s", (test_key,))

    rank(db)

    assert state(db, excluded) == ("approved", True)
    assert state(db, test_key) == ("approved", True)


def test_unused_and_unknown_outcomes_do_not_count(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, _word = approved(db, reporter)
    outcomes(db, entry_id, reporter, {"tests_failed": 2, "unknown": 2, "abandoned": 2})
    unused = outcomes(db, entry_id, reporter, {"tests_failed": 2})
    db.execute("UPDATE outcomes SET used = 'no' WHERE id = ANY(%s)", (unused,))
    rank(db)
    assert state(db, entry_id) == ("approved", True)


# Dashboard


@pytest.fixture
def client(db: psycopg.Connection) -> TestClient:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return TestClient(build_app(test_connection), base_url=ORIGIN, follow_redirects=False)


def test_failure_rates_order_by_rate_and_leave_out_retired_entries(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    worst, _w = approved(db, reporter)
    middling, _m = approved(db, reporter)
    gone, _g = approved(db, reporter)
    outcomes(db, worst, reporter, {"tests_failed": 2})
    outcomes(db, middling, reporter, {"tests_failed": 1, "tests_passed": 1})
    outcomes(db, gone, reporter, {"tests_failed": 2})
    retire(db, gone, "the library fixed it")

    rows = [r for r in insights.failure_rates(db, limit=500) if r["id"] in {worst, middling, gone}]

    assert [r["id"] for r in rows] == [worst, middling]
    first = rows[0]
    assert (first["failures"], first["successes"], first["failure_rate"]) == (2, 0, 1.0)
    assert first["last_failure"] is not None


def test_the_entries_page_shows_how_close_each_entry_is_to_reopening(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    close, word = approved(db, reporter)
    outcomes(db, close, reporter, {"tests_failed": 2})

    page = client.get("/entries").text

    assert "Highest failure rates" in page
    assert word in page
    assert "1 more failure reopens it" in page

"""Human-approved entries that keep failing go back to the queue (decision 0026, amended).

Only hook-observed outcomes from reporters other than the entry's author count, and the
failures must come from at least two reporters: the evidence 0028's provisional rule trusts.
"""

import re
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


@pytest.fixture
def others(db: psycopg.Connection) -> list[uuid.UUID]:
    """Two reporters who aren't the entries' author (the `reporter` fixture)."""
    ids: list[uuid.UUID] = []
    for _ in range(2):
        row = db.execute(
            "INSERT INTO reporters (pseudonym) VALUES (%s) RETURNING id",
            (f"obs-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        assert row
        ids.append(row[0])
    return ids


def approved(db: psycopg.Connection, author: uuid.UUID) -> tuple[uuid.UUID, str]:
    """A human-approved entry with a word of its own to search for."""
    word = f"failing{uuid.uuid4().hex[:8]}"
    entry_id = factories.entry(db, author, symptom=f"{word} broke the build", human_reviewed=True)
    return entry_id, word


def outcomes(
    db: psycopg.Connection,
    entry_id: uuid.UUID,
    by: list[uuid.UUID],
    results: dict[str, int],
    *,
    created_at: str | None = None,
    source: str | None = "hook",
) -> list[uuid.UUID]:
    """Used outcomes of the entry, taking turns among `by`; `source` None is self-reported."""
    ids: list[uuid.UUID] = []
    for result in [result for result, times in results.items() for _ in range(times)]:
        outcome_id = factories.outcome(db, entry_id, by[len(ids) % len(by)], result, created_at)
        if source:
            db.execute(
                "UPDATE outcomes SET evidence = coalesce(evidence, '{}') "
                "|| jsonb_build_object('outcome_source', %s::text) WHERE id = %s",
                (source, outcome_id),
            )
        ids.append(outcome_id)
    return ids


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
    ("failures", "successes", "reporters", "more"),
    [
        (3, 2, 2, 0),
        (3, 3, 2, 1),
        (2, 0, 2, 1),
        (0, 0, 0, 3),
        (1, 5, 1, 5),
        (6, 1, 3, 0),
        (4, 0, 1, 1),
        (3, 0, 1, 1),
    ],
)
def test_failures_to_reopen(failures: int, successes: int, reporters: int, more: int) -> None:
    assert failures_to_reopen(failures, successes, reporters) == more


def test_a_human_approved_entry_that_keeps_failing_is_reopened(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    entry_id, word = approved(db, reporter)
    outcomes(db, entry_id, others, {"tests_failed": 2, "error_persisted": 1, "tests_passed": 2})
    assert searchable(server, word)

    rank(db)

    assert state(db, entry_id) == ("pending", False)
    assert entry_id in queued(db)
    assert reopens(db, entry_id) == [(FAILING_REASON, "auto")]
    assert not searchable(server, word)


def test_a_human_approve_puts_a_reopened_entry_back_for_good(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    entry_id, word = approved(db, reporter)
    outcomes(db, entry_id, others, {"tests_failed": 3, "tests_passed": 2})
    rank(db)

    reviews.review(db, ReviewRequest(entry_id, "approve", "checked it again", "human"))
    rank(db)  # the failures that reopened it don't reopen it again

    assert state(db, entry_id) == ("approved", True)
    assert entry_id not in queued(db)
    assert len(reopens(db, entry_id)) == 1
    assert searchable(server, word)


def test_running_the_rule_twice_adds_nothing(
    db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    entry_id, _word = approved(db, reporter)
    outcomes(db, entry_id, others, {"user_rejected": 4})

    rank(db)
    rank(db)

    assert len(reopens(db, entry_id)) == 1
    assert state(db, entry_id) == ("pending", False)


def test_as_many_successes_as_failures_keeps_an_entry(
    db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    entry_id, _word = approved(db, reporter)
    outcomes(db, entry_id, others, {"tests_failed": 3, "error_resolved": 3})
    rank(db)
    assert state(db, entry_id) == ("approved", True)
    assert reopens(db, entry_id) == []


def test_failures_older_than_the_window_do_not_count(
    db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    entry_id, _word = approved(db, reporter)
    old = (datetime.now(UTC) - timedelta(days=91)).isoformat()
    outcomes(db, entry_id, others, {"tests_failed": 5}, created_at=old)
    outcomes(db, entry_id, others, {"tests_failed": 2})
    rank(db)
    assert state(db, entry_id) == ("approved", True)


def test_excluded_and_test_key_outcomes_do_not_count(
    db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    excluded, _word = approved(db, reporter)
    test_key, _word = approved(db, reporter)
    for outcome_id in outcomes(db, excluded, others, {"tests_failed": 3}):
        db.execute("UPDATE outcomes SET excluded = true WHERE id = %s", (outcome_id,))
    outcomes(db, test_key, others, {"tests_failed": 3})
    db.execute("UPDATE retrievals SET excluded = true WHERE entry_id = %s", (test_key,))

    rank(db)

    assert state(db, excluded) == ("approved", True)
    assert state(db, test_key) == ("approved", True)


def test_unused_and_unknown_outcomes_do_not_count(
    db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    entry_id, _word = approved(db, reporter)
    outcomes(db, entry_id, others, {"tests_failed": 2, "unknown": 2, "abandoned": 2})
    unused = outcomes(db, entry_id, others, {"tests_failed": 2})
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


def failing_row(client: TestClient, word: str) -> str:
    """The entry's row in the Entries page's failure-rate panel, or '' if it isn't listed."""
    page = client.get("/entries").text
    panel = (
        page.split("Highest failure rates", 1)[1].split("</section>", 1)[0]
        if ("Highest failure rates" in page)
        else ""
    )
    found = re.search(rf"<tr><td><a [^>]*>{word}.*?</tr>", panel, re.DOTALL)
    return found.group(0) if found else ""


def test_failure_rates_order_by_rate_and_leave_out_retired_entries(
    db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    worst, _w = approved(db, reporter)
    middling, _m = approved(db, reporter)
    gone, _g = approved(db, reporter)
    outcomes(db, worst, others, {"tests_failed": 2})
    outcomes(db, middling, others, {"tests_failed": 1, "tests_passed": 1})
    outcomes(db, gone, others, {"tests_failed": 2})
    retire(db, gone, "the library fixed it")

    rows = [r for r in insights.failure_rates(db, limit=500) if r["id"] in {worst, middling, gone}]

    assert [r["id"] for r in rows] == [worst, middling]
    first = rows[0]
    assert (first["failures"], first["successes"], first["failure_rate"]) == (2, 0, 1.0)
    assert first["failing_reporters"] == 2
    assert first["last_failure"] is not None


def test_the_entries_page_shows_how_close_each_entry_is_to_reopening(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID, others: list[uuid.UUID]
) -> None:
    close, word = approved(db, reporter)
    outcomes(db, close, others, {"tests_failed": 2})

    assert "1 more failure reopens it" in failing_row(client, word)


# Evidence one reporter could fake: none of it pulls a human-approved entry from search.


def test_the_authors_own_failures_do_not_reopen_an_entry(
    server: FastMCP, client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, word = approved(db, reporter)
    outcomes(db, entry_id, [reporter], {"tests_failed": 4})

    rank(db)

    assert state(db, entry_id) == ("approved", True)
    assert searchable(server, word)
    assert failing_row(client, word) == ""  # none of its outcomes count


def test_one_other_reporter_alone_does_not_reopen_an_entry(
    server: FastMCP,
    client: TestClient,
    db: psycopg.Connection,
    reporter: uuid.UUID,
    others: list[uuid.UUID],
) -> None:
    entry_id, word = approved(db, reporter)
    outcomes(db, entry_id, others[:1], {"tests_failed": 4})

    rank(db)

    assert state(db, entry_id) == ("approved", True)
    assert searchable(server, word)
    row = failing_row(client, word)
    assert "1 more failure reopens it" in row
    assert "reopens at the next rank" not in row


def test_self_reported_failures_do_not_reopen_an_entry(
    server: FastMCP,
    client: TestClient,
    db: psycopg.Connection,
    reporter: uuid.UUID,
    others: list[uuid.UUID],
) -> None:
    entry_id, word = approved(db, reporter)
    outcomes(db, entry_id, others, {"tests_failed": 4}, source=None)

    rank(db)

    assert state(db, entry_id) == ("approved", True)
    assert searchable(server, word)
    assert failing_row(client, word) == ""

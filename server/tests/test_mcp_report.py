import uuid
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP
from psycopg.types.json import Jsonb

import factories
from commons.store.reporters import upsert_reporter
from mcp_client import call


@pytest.fixture
def retrieved(server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID) -> tuple[str, str]:
    """Search once so an entry is retrieved; return (task_id, entry_id)."""
    entry_id = factories.entry(db, reporter)
    task_id = str(uuid.uuid4())
    result = call(server, "search", {"query": "soft_unicode markupsafe", "task_id": task_id})
    assert str(entry_id) in [hit["entry_id"] for hit in result["data"]]
    return task_id, str(entry_id)


def report(server: FastMCP, task_id: str, entry_id: str, **arguments: object) -> dict[str, Any]:
    return call(
        server,
        "report",
        {
            "task_id": task_id,
            "entry_id": entry_id,
            "agent_assessment": "The fix worked after upgrading Jinja2.",
            **arguments,
        },
    )


def outcome_rows(db: psycopg.Connection) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT o.outcome, o.used, o.evidence, o.agent_assessment, r.task_id::text "
        "FROM outcomes o JOIN retrievals r ON r.id = o.retrieval_id"
    ).fetchall()


def test_report_creates_an_outcome(
    server: FastMCP, db: psycopg.Connection, retrieved: tuple[str, str]
) -> None:
    task_id, entry_id = retrieved
    result = report(server, task_id, entry_id, outcome="tests_passed", evidence={"cmd": "pytest"})

    assert result["outcome"] == "tests_passed"
    assert result["outcome_source"] == "agent"
    assert outcome_rows(db) == [
        (
            "tests_passed",
            None,
            {"agent": {"cmd": "pytest"}, "outcome_source": "agent"},
            "The fix worked after upgrading Jinja2.",
            task_id,
        )
    ]


def test_report_without_outcome_records_unknown(
    server: FastMCP, db: psycopg.Connection, retrieved: tuple[str, str]
) -> None:
    result = report(server, *retrieved)
    assert result["outcome"] == "unknown"
    assert result["outcome_source"] is None
    assert [row[0] for row in outcome_rows(db)] == ["unknown"]


def test_second_report_updates_the_same_outcome(
    server: FastMCP, db: psycopg.Connection, retrieved: tuple[str, str]
) -> None:
    first = report(server, *retrieved, outcome="tests_failed")
    second = report(server, *retrieved, outcome="tests_passed", agent_assessment="Worked on retry.")
    assert first["outcome_id"] == second["outcome_id"]
    assert [(row[0], row[3]) for row in outcome_rows(db)] == [("tests_passed", "Worked on retry.")]


def test_repeated_searches_in_a_task_get_one_outcome(
    server: FastMCP, db: psycopg.Connection, retrieved: tuple[str, str]
) -> None:
    task_id, entry_id = retrieved
    call(server, "search", {"query": "markupsafe", "task_id": task_id})
    report(server, task_id, entry_id)
    assert len(outcome_rows(db)) == 1


def test_observed_outcome_wins_over_the_agents(
    server: FastMCP, db: psycopg.Connection, retrieved: tuple[str, str]
) -> None:
    task_id, entry_id = retrieved
    db.execute(
        "INSERT INTO outcomes (retrieval_id, used, outcome, evidence) "
        "SELECT id, 'yes', 'tests_failed', %s FROM retrievals WHERE task_id = %s AND entry_id = %s",
        (Jsonb({"outcome_source": "hook", "observed": {"exit_code": 1}}), task_id, entry_id),
    )
    result = report(server, task_id, entry_id, outcome="tests_passed")

    assert result == {
        "outcome_id": result["outcome_id"],
        "outcome": "tests_failed",
        "outcome_source": "hook",
    }
    ((outcome, used, _evidence, assessment, _task),) = outcome_rows(db)
    assert (outcome, used, assessment) == (
        "tests_failed",
        "yes",
        "The fix worked after upgrading Jinja2.",
    )


@pytest.mark.parametrize("which", ["other_task", "other_entry", "other_reporter"])
def test_report_on_something_not_retrieved_is_not_found(
    server: FastMCP,
    db: psycopg.Connection,
    retrieved: tuple[str, str],
    which: str,
) -> None:
    task_id, entry_id = retrieved
    if which == "other_task":
        task_id = str(uuid.uuid4())
    elif which == "other_entry":
        entry_id = str(uuid.uuid4())
    else:
        someone_else = upsert_reporter(db, "someone-else", "entry")
        db.execute("UPDATE retrievals SET reporter_id = %s", (someone_else,))
    result = report(server, task_id, entry_id)
    assert result["error"]["code"] == "not_found"
    assert outcome_rows(db) == []


def test_empty_assessment_and_bad_outcome_are_invalid(
    server: FastMCP, retrieved: tuple[str, str]
) -> None:
    empty = report(server, *retrieved, agent_assessment=" ")
    assert empty["error"]["code"] == "invalid_input"
    bad = report(server, *retrieved, outcome="worked great")
    assert bad["error"]["code"] == "invalid_input"
    assert "outcome" in bad["error"]["message"]

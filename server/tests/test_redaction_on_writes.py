"""Secret scanning runs on every write (DESIGN.md §6), not only on proposals."""

import uuid

import psycopg
from fastmcp import FastMCP

import factories
from mcp_client import call

SECRET = "sk-ant-api03-" + "q" * 40


def test_search_query_is_logged_redacted(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    factories.entry(db, reporter)
    task_id = str(uuid.uuid4())
    call(server, "search", {"query": f"markupsafe soft_unicode {SECRET}", "task_id": task_id})
    (query,) = db.execute(
        "SELECT DISTINCT query FROM retrievals WHERE task_id = %s", (task_id,)
    ).fetchone() or ("",)
    assert query == "markupsafe soft_unicode [REDACTED:anthropic_key]"


def test_report_assessment_and_evidence_are_redacted(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter)
    task_id = str(uuid.uuid4())
    call(server, "search", {"query": "soft_unicode", "task_id": task_id})
    call(
        server,
        "report",
        {
            "task_id": task_id,
            "entry_id": str(entry_id),
            "agent_assessment": f"Worked once I exported {SECRET}",
            "evidence": {"log": f"/home/alice/run.log {SECRET}"},
        },
    )
    row = db.execute(
        "SELECT o.agent_assessment, o.evidence FROM outcomes o "
        "JOIN retrievals r ON r.id = o.retrieval_id WHERE r.task_id = %s AND r.entry_id = %s",
        (task_id, entry_id),
    ).fetchone()
    assert row == (
        "Worked once I exported [REDACTED:anthropic_key]",
        {"agent": {"log": "/home/<user>/run.log [REDACTED:anthropic_key]"}},
    )


def test_review_reason_is_redacted(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, status="pending")
    call(server, "reject", {"entry_id": str(entry_id), "reason": f"Leaks {SECRET}"})
    row = db.execute("SELECT reason FROM reviews WHERE entry_id = %s", (entry_id,)).fetchone()
    assert row == ("Leaks [REDACTED:anthropic_key]",)

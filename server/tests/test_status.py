import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP

import factories
from commons.cli.main import main
from mcp_client import call


def flat(status: dict[str, Any]) -> dict[str, int]:
    return {
        **status["entries_by_status"],
        "awaiting_human_review": status["awaiting_human_review"],
        **status["last_30_days"],
    }


def delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    """Changes only: other tests commit rows, so absolute counts aren't ours to assert."""
    old, new = flat(before), flat(after)
    return {key: new[key] - old[key] for key in new if new[key] != old[key]}


def test_status_counts_entries_and_recent_activity(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    before = call(server, "status", {})
    factories.entry(db, reporter, status="pending")
    reviewed = factories.entry(db, reporter, human_reviewed=True)
    factories.entry(db, reporter)
    factories.entry(db, reporter, status="retired")
    factories.outcome(db, reviewed, reporter)
    factories.outcome(db, reviewed, reporter, created_at="2020-01-01")

    assert delta(before, call(server, "status", {})) == {
        "pending": 1,
        "approved": 2,
        "retired": 1,
        "awaiting_human_review": 2,
        "retrievals": 1,
        "tasks": 1,
        "outcomes": 1,
    }


def test_top_entries_rank_by_success_rate_then_sample_size(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    half = factories.entry(db, reporter, symptom="half")
    always = factories.entry(db, reporter, symptom="always")
    once = factories.entry(db, reporter, symptom="once")
    rejected = factories.entry(db, reporter, status="rejected")
    for entry_id, results in [
        (half, ["tests_passed", "tests_failed"]),
        (always, ["tests_passed", "error_resolved"]),
        (once, ["user_accepted"]),
        (rejected, ["tests_passed"]),
    ]:
        for result in results:
            factories.outcome(db, entry_id, reporter, result)

    ours = {str(half), str(always), str(once), str(rejected)}
    # Other tests commit entries with outcomes; hide them so the top list is only ours.
    db.execute(
        "UPDATE entries SET status = 'retired' WHERE NOT (id::text = ANY(%s))", (list(ours),)
    )
    top = call(server, "status", {})["top_entries"]
    ranked = [
        (t["symptom"], t["success_rate"], t["used_outcomes"]) for t in top if t["entry_id"] in ours
    ]
    assert ranked == [("always", 1.0, 2), ("once", 1.0, 1), ("half", 0.5, 2)]


def test_status_command_prints_a_summary(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "config.json"))
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("entries: ")
    assert "awaiting human review: " in out
    assert "last 30 days: " in out

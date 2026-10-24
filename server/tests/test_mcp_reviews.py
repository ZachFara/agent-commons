import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP

import factories
from commons.cli.main import main
from mcp_client import call


def entry_state(db: psycopg.Connection, entry_id: uuid.UUID) -> tuple[Any, ...] | None:
    return db.execute(
        "SELECT status, human_reviewed FROM entries WHERE id = %s", (entry_id,)
    ).fetchone()


def reviews(db: psycopg.Connection, entry_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT decision, source, reason, reviewed_by FROM reviews WHERE entry_id = %s "
        "ORDER BY created_at",
        (entry_id,),
    ).fetchall()


@pytest.fixture
def pending_entry(db: psycopg.Connection, reporter: uuid.UUID) -> uuid.UUID:
    return factories.entry(db, reporter, status="pending")


def test_agent_approval_is_provisional(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID, pending_entry: uuid.UUID
) -> None:
    result = call(server, "approve", {"entry_id": str(pending_entry)})
    assert result == {"entry_id": str(pending_entry), "status": "approved", "human_reviewed": False}
    assert entry_state(db, pending_entry) == ("approved", False)
    assert reviews(db, pending_entry) == [("approve", "agent", None, reporter)]


def test_agent_reject_needs_a_reason(
    server: FastMCP, db: psycopg.Connection, pending_entry: uuid.UUID
) -> None:
    result = call(server, "reject", {"entry_id": str(pending_entry), "reason": " "})
    assert result["error"]["code"] == "invalid_input"
    assert entry_state(db, pending_entry) == ("pending", False)

    result = call(server, "reject", {"entry_id": str(pending_entry), "reason": "Wrong cause."})
    assert result["status"] == "rejected"


def test_multiple_reviewers_are_all_kept(
    server: FastMCP, db: psycopg.Connection, pending_entry: uuid.UUID
) -> None:
    call(server, "approve", {"entry_id": str(pending_entry)})
    call(server, "reject", {"entry_id": str(pending_entry), "reason": "Outdated."})
    assert [row[:2] for row in reviews(db, pending_entry)] == [
        ("approve", "agent"),
        ("reject", "agent"),
    ]
    assert entry_state(db, pending_entry) == ("rejected", False)


def test_unknown_and_retired_entries(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    missing = call(server, "approve", {"entry_id": str(uuid.uuid4())})
    assert missing["error"]["code"] == "not_found"

    retired = factories.entry(db, reporter, status="retired")
    result = call(server, "approve", {"entry_id": str(retired)})
    assert result["error"]["code"] == "invalid_input"
    assert reviews(db, retired) == []


def test_pending_lists_entries_without_a_human_review(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    waiting = factories.entry(
        db, reporter, status="pending", symptom="first", created_at="2026-01-01"
    )
    agent_approved = factories.entry(db, reporter, symptom="second", created_at="2026-01-02")
    reviewed = factories.entry(db, reporter, human_reviewed=True)
    retired = factories.entry(db, reporter, status="retired")
    call(server, "approve", {"entry_id": str(agent_approved)})

    result = call(server, "pending", {})
    assert "untrusted" in result["note"]
    # Other test files commit entries to the shared database; look only at ours.
    ours = {str(e) for e in (waiting, agent_approved, reviewed, retired)}
    mine = [e for e in result["data"] if e["entry_id"] in ours]
    assert [(e["entry_id"], e["status"], e["agent_reviews"]) for e in mine] == [
        (str(waiting), "pending", 0),
        (str(agent_approved), "approved", 1),
    ]
    assert mine[0]["symptom"] == "first"
    assert mine[0]["proposed_by_model"] == "claude-opus-5-5"


class TestHumanReviewCli:
    """`commons review` commits, so these tests use their own committed entry."""

    @pytest.fixture
    def committed_entry(
        self, database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> uuid.UUID:
        monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
        monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "config.json"))
        pseudonym = f"human-{uuid.uuid4().hex[:8]}"
        assert main(["init", "--pseudonym", pseudonym]) == 0
        with psycopg.connect(database_url) as conn:
            (reporter,) = conn.execute(
                "SELECT id FROM reporters WHERE pseudonym = %s", (pseudonym,)
            ).fetchone() or (None,)
            assert isinstance(reporter, uuid.UUID)
            return factories.entry(conn, reporter, status="pending")

    def test_human_review_overrides_agent_review(
        self,
        database_url: str,
        committed_entry: uuid.UUID,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with psycopg.connect(database_url) as conn:
            conn.execute(
                "INSERT INTO reviews (entry_id, reviewed_by, decision, source) "
                "SELECT id, created_by, 'approve', 'agent' FROM entries WHERE id = %s",
                (committed_entry,),
            )
        assert main(["review", str(committed_entry), "reject", "--reason", "Wrong fix."]) == 0
        assert capsys.readouterr().out == f"entry {committed_entry}: rejected (human reviewed)\n"
        with psycopg.connect(database_url) as conn:
            assert entry_state(conn, committed_entry) == ("rejected", True)
            assert [row[:3] for row in reviews(conn, committed_entry)] == [
                ("approve", "agent", None),
                ("reject", "human", "Wrong fix."),
            ]

    def test_pending_cli_shows_the_entry(
        self, committed_entry: uuid.UUID, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["pending"]) == 0
        assert str(committed_entry) in capsys.readouterr().out

    def test_cli_errors_exit_1(
        self, committed_entry: uuid.UUID, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["review", str(committed_entry), "reject"]) == 1
        assert main(["review", str(uuid.uuid4()), "approve"]) == 1
        err = capsys.readouterr().err
        assert "reason is required" in err
        assert "No entry" in err

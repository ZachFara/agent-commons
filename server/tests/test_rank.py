import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP
from psycopg.types.json import Jsonb

import factories
from commons.cli.main import main
from commons.service.ranking import rank, ranking_is_stale
from hook_env import HookEnv
from mcp_client import call


def outcome(
    db: psycopg.Connection,
    entry_id: uuid.UUID,
    reporter: uuid.UUID,
    result: str,
    *,
    used: str | None = "yes",
    source: str | None = "hook",
) -> None:
    factories.outcome(db, entry_id, reporter, result)
    evidence = {"outcome_source": source} if source else {}
    db.execute(
        "UPDATE outcomes SET used = %s, evidence = %s WHERE retrieval_id IN "
        "(SELECT id FROM retrievals WHERE entry_id = %s) AND evidence = '{}'",
        (used, Jsonb(evidence), entry_id),
    )


def snapshot(db: psycopg.Connection, entry_id: uuid.UUID) -> tuple[Any, ...] | None:
    return db.execute(
        "SELECT score, retrieval_count_30d, used_rate, success_rate FROM ranking_snapshots "
        "WHERE entry_id = %s ORDER BY computed_at DESC LIMIT 1",
        (entry_id,),
    ).fetchone()


def test_rank_snapshots_approved_entries(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    good = factories.entry(db, reporter)
    bad = factories.entry(db, reporter)
    quiet = factories.entry(db, reporter)
    pending = factories.entry(db, reporter, status="pending")
    outcome(db, good, reporter, "tests_passed")
    outcome(db, good, reporter, "error_resolved")
    outcome(db, good, reporter, "tests_passed", used="no")
    outcome(db, bad, reporter, "tests_failed", source="agent")

    assert rank(db) >= 3
    good_score, good_count, good_used, good_success = snapshot(db, good) or ()
    assert good_score == pytest.approx(3 / 4)
    assert (good_count, good_used, good_success) == (3, pytest.approx(2 / 3), 1.0)
    assert snapshot(db, bad) == (pytest.approx(1 / 2.5), 1, 1.0, 0.0)
    assert snapshot(db, quiet) == (0.5, 0, None, None)
    assert snapshot(db, pending) is None


def test_search_orders_by_the_new_scores(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    word = f"rankcheck{uuid.uuid4().hex[:8]}"
    weak = factories.entry(db, reporter, symptom=word)
    strong = factories.entry(db, reporter, symptom=word)
    outcome(db, weak, reporter, "tests_failed")
    outcome(db, strong, reporter, "tests_passed")
    rank(db)
    result = call(server, "search", {"query": word, "task_id": str(uuid.uuid4())})
    assert [hit["entry_id"] for hit in result["data"]] == [str(strong), str(weak)]


def test_staleness(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    factories.entry(db, reporter)
    rank(db)
    now = datetime.now(UTC)
    assert not ranking_is_stale(db, now)
    assert ranking_is_stale(db, now + timedelta(hours=25))


def test_rank_command(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    assert main(["rank"]) == 0
    assert capsys.readouterr().out.startswith("ranked ")


def test_session_start_ranks_when_stale(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init()
    calls: list[object] = []
    monkeypatch.setattr("commons.service.hooks.ranking_is_stale", lambda _conn: True)
    monkeypatch.setattr("commons.service.hooks.rank", calls.append)
    env.run("session-start", {"session_id": str(uuid.uuid4())})
    assert len(calls) == 1
    monkeypatch.setattr("commons.service.hooks.ranking_is_stale", lambda _conn: False)
    env.run("session-start", {"session_id": str(uuid.uuid4())})
    assert len(calls) == 1

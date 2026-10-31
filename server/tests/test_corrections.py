"""Audited corrections and exclusions (decision 0030)."""

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql
from starlette.testclient import TestClient

import factories
from commons.cli.main import main
from commons.domain.corrections import Correction, CorrectionField, CorrectionTable
from commons.domain.errors import InvalidInputError, NotFoundError
from commons.domain.keys import KeyOptions
from commons.domain.search import SearchRequest
from commons.service import corrections, keys
from commons.service.identity import acting_as
from commons.service.search import search
from commons.store import insights
from commons.store.connection import Connection
from commons.store.outcome_stats import OUTCOME_STATS, OUTCOME_STATS_PARAMS
from commons.store.outcomes import set_used
from commons.store.ranking import entry_stats, outcome_samples
from commons.store.status import store_status
from commons.web.app import build_app

READERS = Path(__file__).parents[1] / "src/commons/store"
ORIGIN = "http://127.0.0.1:8765"


def used_outcome(db: psycopg.Connection, reporter: uuid.UUID) -> tuple[uuid.UUID, Any, Any]:
    """An approved entry with one retrieval and a used, passing outcome."""
    entry_id = factories.entry(db, reporter, symptom=f"corr-{uuid.uuid4().hex[:8]} failed")
    factories.outcome(db, entry_id, reporter)
    row = db.execute(
        "SELECT r.id, o.id FROM retrievals r JOIN outcomes o ON o.retrieval_id = r.id "
        "WHERE r.entry_id = %s",
        (entry_id,),
    ).fetchone()
    assert row
    return entry_id, row[0], row[1]


def stats_for(db: psycopg.Connection, entry_id: uuid.UUID) -> tuple[Any, ...] | None:
    query = sql.SQL("SELECT used_outcomes, successes FROM ({}) s WHERE entry_id = %(e)s").format(
        sql.SQL(OUTCOME_STATS)
    )
    return db.execute(query, {**OUTCOME_STATS_PARAMS, "e": entry_id}).fetchone()


def snapshot(db: psycopg.Connection) -> dict[str, Any]:
    status = store_status(db, 5)
    k = insights.kpis(db)
    return {
        "status": (status.retrievals_30d, status.outcomes_30d),
        "kpis": k["retrievals_30d"],
        "mix": sum(row["observed"] + row["self_reported"] for row in insights.outcome_mix(db)),
        "daily": sum(row["retrievals"] for row in insights.daily_activity(db, 2)),
    }


def exclude(table: CorrectionTable, row_id: uuid.UUID) -> Correction:
    return Correction(table, row_id, "excluded", value=True, reason="test traffic", by="owner")


def set_field(outcome_id: uuid.UUID, field: CorrectionField, value: str | None) -> Correction:
    return Correction("outcomes", outcome_id, field, value, reason="wrong", by="owner")


# Readers skip excluded rows


def test_an_excluded_retrieval_leaves_every_count_and_score(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, retrieval_id, _ = used_outcome(db, reporter)
    before = snapshot(db)
    assert stats_for(db, entry_id) == (1, 1)
    assert entry_id in outcome_samples(db)

    corrections.correct(db, exclude("retrievals", retrieval_id))

    after = snapshot(db)
    assert after["status"] == (before["status"][0] - 1, before["status"][1] - 1)
    assert after["kpis"] == before["kpis"] - 1
    assert after["mix"] == before["mix"] - 1
    assert after["daily"] == before["daily"] - 1
    assert stats_for(db, entry_id) is None
    assert entry_id not in outcome_samples(db)
    assert {e: n for e, n, _ in entry_stats(db)}[entry_id] == 0
    listed = insights.entry_retrievals(db, entry_id)
    assert [(r["retrieval_id"], r["excluded"]) for r in listed] == [(retrieval_id, True)]


def test_an_excluded_outcome_stops_counting_but_its_retrieval_still_does(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, _, outcome_id = used_outcome(db, reporter)
    before = snapshot(db)

    corrections.correct(db, exclude("outcomes", outcome_id))

    after = snapshot(db)
    assert after["status"] == (before["status"][0], before["status"][1] - 1)
    assert stats_for(db, entry_id) is None


def test_excluded_searches_leave_the_totals_and_the_miss_list(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    query = f"zzqx corrections miss {uuid.uuid4().hex[:6]}"
    with acting_as(reporter):
        search(db, SearchRequest(query, uuid.uuid4(), None, 5), "claude-opus-5-5")
    before = insights.search_totals(db)
    (row,) = [s for s in insights.recent_searches(db) if s["query"] == query]
    assert query in [m["query"] for m in insights.missed_queries(db)]

    corrections.correct(db, exclude("searches", row["id"]))

    after = insights.search_totals(db)
    assert (after["searches"], after["misses"]) == (before["searches"] - 1, before["misses"] - 1)
    assert query not in [m["query"] for m in insights.missed_queries(db)]
    assert [s["excluded"] for s in insights.recent_searches(db) if s["query"] == query] == [True]


def test_every_reader_counts_through_the_views() -> None:
    """Counts must use counted_*; only listings and writers may name the raw tables."""
    allowed = {
        "insights.py": {"entry_retrievals", "recent_searches"},
        "outcomes.py": {"task_retrieval", "locked_outcome", "save_outcome", "set_used"},
        "retention.py": {"fold_searches_before"},
        "task_events.py": {"*"},
        "retrievals.py": {"*"},
        "searches.py": {"*"},
        "corrections.py": {"*"},
    }
    raw = re.compile(r"\b(FROM|JOIN) (retrievals|outcomes|searches)\b")
    offenders = []
    for path in READERS.glob("*.py"):
        functions = re.split(r"\n(?=def )", path.read_text(encoding="utf-8"))
        for function in functions:
            name = function.split("(")[0].removeprefix("def ").strip()
            ok = allowed.get(path.name, set())
            if raw.search(function) and "*" not in ok and name not in ok:
                offenders.append(f"{path.name}:{name}")
    assert offenders == []


# Corrections


def test_a_corrected_used_stands_when_the_stop_hook_runs_again(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, retrieval_id, outcome_id = used_outcome(db, reporter)

    corrections.correct(db, set_field(outcome_id, "used", None))
    set_used(db, retrieval_id, "yes")

    assert stats_for(db, entry_id) is None
    row = db.execute("SELECT used FROM outcomes WHERE id = %s", (outcome_id,)).fetchone()
    assert row == (None,)
    (logged,) = [c for c in insights.corrections(db) if c["target_id"] == outcome_id]
    assert (logged["old_value"], logged["new_value"], logged["corrected_by"]) == (
        "yes",
        None,
        "owner",
    )


def test_undo_restores_the_old_value_and_lets_hooks_write_again(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, retrieval_id, outcome_id = used_outcome(db, reporter)
    correction_id = corrections.correct(db, set_field(outcome_id, "used", "no"))

    assert corrections.undo(db, correction_id, by="owner") == 1

    assert stats_for(db, entry_id) == (1, 1)
    set_used(db, retrieval_id, "partial")
    row = db.execute("SELECT used FROM outcomes WHERE id = %s", (outcome_id,)).fetchone()
    assert row == ("partial",)
    with pytest.raises(NotFoundError):
        corrections.undo(db, correction_id, by="owner")


def test_a_whole_task_is_excluded_and_restored_together(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    task_id = uuid.uuid4()
    factories.entry(db, reporter, symptom="taskwide zzqv exploded")
    with acting_as(reporter):
        search(db, SearchRequest("taskwide zzqv", task_id, None, 5), "claude-opus-5-5")
        search(db, SearchRequest("nothing zzqv qqq", task_id, None, 5), "claude-opus-5-5")

    counted = db.execute(
        "SELECT (SELECT count(*) FROM retrievals WHERE task_id = %(t)s) "
        "+ (SELECT count(*) FROM searches WHERE task_id = %(t)s)",
        {"t": task_id},
    ).fetchone()
    assert counted
    assert counted[0] >= 3
    assert corrections.exclude_task(db, task_id, reason="deploy check", by="owner") == counted[0]

    excluded = db.execute(
        "SELECT bool_and(excluded) FROM (SELECT excluded FROM retrievals WHERE task_id = %(t)s "
        "UNION ALL SELECT excluded FROM searches WHERE task_id = %(t)s) x",
        {"t": task_id},
    ).fetchone()
    assert excluded == (True,)
    one = next(c for c in insights.corrections(db) if c["reason"] == "deploy check")
    assert corrections.undo(db, one["id"], by="owner") == counted[0]
    with pytest.raises(NotFoundError, match="nothing left"):
        corrections.exclude_task(db, uuid.uuid4(), reason="x", by="owner")


@pytest.mark.parametrize(
    ("correction", "message"),
    [
        (Correction("searches", uuid.uuid4(), "used", "yes", "r", "o"), "no correctable"),
        (Correction("outcomes", uuid.uuid4(), "used", "maybe", "r", "o"), "not a valid"),
        (Correction("outcomes", uuid.uuid4(), "outcome", "worked", "r", "o"), "not a valid"),
        (Correction("outcomes", uuid.uuid4(), "used", "yes", " ", "o"), "reason"),
        (Correction("outcomes", uuid.uuid4(), "used", "yes", "r", ""), "who"),
    ],
)
def test_bad_corrections_are_refused(
    db: psycopg.Connection, correction: Correction, message: str
) -> None:
    with pytest.raises(InvalidInputError, match=message):
        corrections.correct(db, correction)


def test_a_correction_that_changes_nothing_is_refused(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    _, _, outcome_id = used_outcome(db, reporter)
    with pytest.raises(InvalidInputError, match="already"):
        corrections.correct(db, set_field(outcome_id, "used", "yes"))


# Test keys


def test_a_test_keys_searches_never_count(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    factories.entry(db, reporter, symptom="testkey zzqw broke")
    tester = keys.issue_key(db, "deploy-checker", "agent", KeyOptions(test=True))
    holder = keys.check_key(db, tester.key).holder
    assert holder is not None
    before = insights.search_totals(db)["searches"]
    with acting_as(holder.reporter_id, test=holder.test):
        search(db, SearchRequest("testkey zzqw", uuid.uuid4(), None, 5), "claude-opus-5-5")
    assert insights.search_totals(db)["searches"] == before
    rows = [s for s in insights.recent_searches(db) if s["query"] == "testkey zzqw"]
    assert [s["excluded"] for s in rows] == [True]
    retrieved = db.execute(
        "SELECT bool_and(excluded), count(*) FROM retrievals WHERE query = 'testkey zzqw'"
    ).fetchone()
    assert retrieved == (True, 1)


# Dashboard and CLI


@pytest.fixture
def dashboard(db: psycopg.Connection, reporter: uuid.UUID) -> TestClient:
    del reporter

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return TestClient(build_app(test_connection), base_url=ORIGIN, follow_redirects=False)


def test_dashboard_corrects_lists_and_undoes(
    dashboard: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id, _, outcome_id = used_outcome(db, reporter)
    origin = {"origin": ORIGIN, "referer": f"{ORIGIN}/entries/{entry_id}"}
    form = {"table": "outcomes", "row_id": str(outcome_id), "field": "used", "value": "no"}

    saved = dashboard.post("/corrections", data={**form, "reason": "looked only"}, headers=origin)

    assert saved.status_code == 303
    assert "corrected=" in saved.headers["location"]
    page = dashboard.get("/corrections").text
    assert "looked only" in page
    (row,) = [c for c in insights.corrections(db) if c["target_id"] == outcome_id]
    pseudonym = db.execute("SELECT pseudonym FROM reporters WHERE id = %s", (reporter,)).fetchone()
    assert pseudonym == (row["corrected_by"],)
    undone = dashboard.post(f"/corrections/{row['id']}/undo", headers=origin)
    assert undone.status_code == 303
    assert stats_for(db, entry_id) == (1, 1)
    assert (
        dashboard.post("/corrections", data=form, headers={"origin": "http://evil"}).status_code
        == 403
    )
    missing = dashboard.post("/corrections", data={**form, "reason": ""}, headers=origin)
    assert "error=" in missing.headers["location"]


def test_correct_commands(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "unused.json"))
    with psycopg.connect(database_url) as conn:
        reporter = conn.execute(
            "INSERT INTO reporters (pseudonym) VALUES (%s) RETURNING id", (uuid.uuid4().hex[:12],)
        ).fetchone()
        assert reporter
        _, _, outcome_id = used_outcome(conn, reporter[0])
    by = ["--by", "owner"]

    assert (
        main(
            [
                "admin",
                "correct",
                "set",
                "outcomes",
                str(outcome_id),
                "used",
                "unknown",
                "--reason",
                "lookup only",
                *by,
            ]
        )
        == 0
    )
    assert "scores recomputed" in capsys.readouterr().out
    assert main(["admin", "correct", "list"]) == 0
    listed = capsys.readouterr().out
    assert f"outcomes {outcome_id} used yes -> None  (lookup only)" in listed
    correction_id = listed.split()[0]
    assert main(["admin", "correct", "undo", correction_id, *by]) == 0
    assert "undid 1" in capsys.readouterr().out
    assert (
        main(
            [
                "admin",
                "correct",
                "set",
                "outcomes",
                str(outcome_id),
                "used",
                "maybe",
                "--reason",
                "x",
                *by,
            ]
        )
        == 1
    )

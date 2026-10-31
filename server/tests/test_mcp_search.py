import uuid
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP

import factories
from mcp_client import call

LONG_ERROR = (
    "Traceback (most recent call last): File app.py line 3 ImportError: cannot import name "
    "'soft_unicode' from 'markupsafe' (/venv/lib/python3.12/site-packages/markupsafe/__init__.py)"
)


def search(server: FastMCP, query: str = LONG_ERROR, **arguments: object) -> dict[str, Any]:
    return call(
        server,
        "search",
        {"query": query, "task_id": str(uuid.uuid4()), "model": "claude-opus-5-5", **arguments},
    )


def ids(result: dict[str, Any], *ours: uuid.UUID) -> list[str]:
    """Result ids in order, keeping only entries this test created.

    Other tests commit entries (e.g. seeds from `commons init`) to the shared database.
    """
    return [hit["entry_id"] for hit in hits(result, *ours)]


def hits(result: dict[str, Any], *ours: uuid.UUID) -> list[dict[str, Any]]:
    wanted = {str(entry_id) for entry_id in ours}
    return [hit for hit in result["data"] if hit["entry_id"] in wanted]


def test_long_error_message_finds_the_matching_entry_first(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    weak = factories.entry(
        db,
        reporter,
        symptom="ImportError after pip install hangs resolving dependencies",
        cause="Backtracking resolver on an unpinned lib.",
        fix="Pin versions.",
    )
    strong = factories.entry(db, reporter)
    unrelated = factories.entry(
        db, reporter, symptom="Segfault in numpy", cause="ABI mismatch", fix="Rebuild wheels"
    )

    # limit=20: entries committed by other tests also match this common error text.
    assert ids(search(server, limit=20), weak, strong, unrelated) == [str(strong), str(weak)]


@pytest.mark.parametrize("status", ["pending", "rejected", "retired"])
def test_only_approved_entries_are_returned(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID, status: str
) -> None:
    entry_id = factories.entry(db, reporter, status=status)
    assert ids(search(server), entry_id) == []


def test_result_shape_note_and_provenance(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, repro="python -c 'import jinja2'")
    result = search(server)

    assert "untrusted" in result["note"]
    (hit,) = hits(result, entry_id)
    assert hit["entry_id"] == str(entry_id)
    assert hit["environment"] == {"language": "python 3.12"}
    assert hit["repro"] == "python -c 'import jinja2'"
    assert hit["score"] > 0
    assert hit["provenance"] == (
        "proposed by claude-opus-5-5; no outcomes yet; never confirmed; unreviewed"
    )


def test_provenance_reports_used_outcomes(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, human_reviewed=True)
    search(server)
    retrievals = db.execute("SELECT id FROM retrievals WHERE entry_id = %s", (entry_id,))
    (retrieval_id,) = retrievals.fetchone() or ()
    db.execute(
        "INSERT INTO outcomes (retrieval_id, used, outcome) VALUES (%s, 'yes', 'tests_passed')",
        (retrieval_id,),
    )
    (hit,) = hits(search(server), entry_id)
    assert hit["provenance"].startswith(
        "proposed by claude-opus-5-5; success 1/1 when used; last confirmed 20"
    )
    assert "unreviewed" not in hit["provenance"]


def test_each_result_logs_a_retrieval(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    first = factories.entry(db, reporter)
    second = factories.entry(db, reporter, symptom="markupsafe soft_unicode removed")
    task_id = str(uuid.uuid4())
    result = search(server, "soft_unicode markupsafe", task_id=task_id)

    rows = db.execute(
        "SELECT r.entry_id::text, r.rank_position, r.score_at_retrieval, r.query, "
        "r.task_id::text, r.reporter_id, m.name "
        "FROM retrievals r JOIN models m ON m.id = r.model_id WHERE r.task_id = %s "
        "ORDER BY r.rank_position",
        (task_id,),
    ).fetchall()
    every_hit = [hit["entry_id"] for hit in result["data"]]
    assert [(row[0], row[1]) for row in rows] == [(e, i) for i, e in enumerate(every_hit, 1)]
    assert {str(first), str(second)} <= {row[0] for row in rows}
    assert [row[2] for row in rows] == [hit["score"] for hit in result["data"]]
    assert {row[3:] for row in rows} == {
        ("soft_unicode markupsafe", task_id, reporter, "claude-opus-5-5")
    }


def test_language_filter_excludes_other_languages_only(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    python = factories.entry(db, reporter)
    javascript = factories.entry(db, reporter, environment={"language": "JavaScript ES2023"})
    no_language = factories.entry(db, reporter, environment={"tool": "pip 24.0"})

    result = search(server, environment={"language": "Python 3.11"})
    assert set(ids(result, python, javascript, no_language)) == {str(python), str(no_language)}


def test_snapshot_score_lifts_an_entry(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    older = factories.entry(db, reporter)
    newer = factories.entry(db, reporter)
    db.execute(
        "INSERT INTO ranking_snapshots (entry_id, computed_at, score, retrieval_count_30d) "
        "VALUES (%s, now(), 0.9, 0)",
        (older,),
    )
    assert ids(search(server), older, newer) == [str(older), str(newer)]


def test_limit_and_empty_results(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    for _ in range(3):
        factories.entry(db, reporter)
    task_id = str(uuid.uuid4())
    assert len(search(server, limit=2, task_id=task_id)["data"]) == 2
    assert search(server, "the and of", task_id=task_id)["data"] == []
    logged = db.execute("SELECT count(*) FROM retrievals WHERE task_id = %s", (task_id,))
    assert logged.fetchone() == (2,)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [({"query": "  "}, "query must not be empty"), ({"limit": 0}, "limit must be between")],
)
def test_invalid_search_is_a_structured_error(
    server: FastMCP, arguments: dict[str, Any], message: str
) -> None:
    result = search(server, **arguments)
    assert result["error"]["code"] == "invalid_input"
    assert message in result["error"]["message"]


def test_search_names_the_local_store(server: FastMCP, reporter: uuid.UUID) -> None:
    del reporter
    assert search(server)["store"] == "local"

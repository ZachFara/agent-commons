import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import entry_points
from pathlib import Path

import psycopg
import pytest
from fastmcp import FastMCP
from psycopg import sql

import factories
from commons.cli.main import main
from commons.domain.search import SearchRequest
from commons.service import search as search_service
from commons.service.identity import acting_as
from mcp_client import call


@pytest.fixture
def cli_env(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at the test database and a temporary config file; return its path.

    CLI commands commit, so tests that use this must use unique pseudonyms.
    """
    config = tmp_path / "config.json"
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    return config


def unique_pseudonym() -> str:
    return f"tester-{uuid.uuid4().hex[:8]}"


def reporter_row(database_url: str, pseudonym: str) -> list[tuple[object, ...]]:
    with psycopg.connect(database_url) as conn:
        return conn.execute(
            "SELECT id, privacy_default FROM reporters WHERE pseudonym = %s", (pseudonym,)
        ).fetchall()


def test_commons_script_points_at_main() -> None:
    (script,) = entry_points(group="console_scripts", name="commons")
    assert script.load() is main


def test_migrate_reports_up_to_date_on_second_run(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del cli_env
    assert main(["migrate"]) == 0
    assert capsys.readouterr().out == "database is up to date\n"


def test_unknown_command_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["no-such-command"])
    assert exc.value.code == 2


def test_init_creates_reporter_and_config(cli_env: Path, database_url: str) -> None:
    pseudonym = unique_pseudonym()
    assert main(["init", "--pseudonym", pseudonym, "--privacy", "full_trace"]) == 0
    rows = reporter_row(database_url, pseudonym)
    assert [privacy for _id, privacy in rows] == ["full_trace"]
    assert json.loads(cli_env.read_text()) == {"pseudonym": pseudonym}


def test_init_again_reuses_reporter_and_updates_privacy(cli_env: Path, database_url: str) -> None:
    del cli_env
    pseudonym = unique_pseudonym()
    main(["init", "--pseudonym", pseudonym])
    (first,) = reporter_row(database_url, pseudonym)
    assert main(["init", "--pseudonym", pseudonym, "--privacy", "local"]) == 0
    assert reporter_row(database_url, pseudonym) == [(first[0], "local")]


@pytest.mark.parametrize(
    ("pseudonym", "message"),
    [
        ("someone@example.com", "email"),
        ("ab", "3-40 characters"),
        ("has space", "3-40 characters"),
    ],
)
def test_init_rejects_bad_pseudonyms(
    cli_env: Path, capsys: pytest.CaptureFixture[str], pseudonym: str, message: str
) -> None:
    assert main(["init", "--pseudonym", pseudonym]) == 1
    assert message in capsys.readouterr().err
    assert not cli_env.exists()


def test_init_rejects_unknown_privacy_mode(cli_env: Path) -> None:
    del cli_env
    with pytest.raises(SystemExit) as exc:
        main(["init", "--pseudonym", unique_pseudonym(), "--privacy", "public"])
    assert exc.value.code == 2


@pytest.fixture
def cli_on_db(db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the CLI on the test's rolled-back connection."""

    @contextmanager
    def test_connection() -> Iterator[psycopg.Connection]:
        yield db

    monkeypatch.setattr("commons.cli.main.connect", test_connection)


SECRET = "sk-ant-api03-" + "q" * 40
WRITTEN = ("searches", "retrievals", "outcomes", "entries", "reviews", "corrections")


def search_of(db: psycopg.Connection, task_id: uuid.UUID) -> str:
    row = db.execute("SELECT id FROM searches WHERE task_id = %s", (task_id,)).fetchone()
    assert row
    return str(row[0])


def set_used(db: psycopg.Connection, task_id: uuid.UUID, entry_id: uuid.UUID, used: str) -> None:
    db.execute(
        "INSERT INTO outcomes (retrieval_id, used) SELECT id, %s FROM retrievals "
        "WHERE task_id = %s AND entry_id = %s "
        "ON CONFLICT (retrieval_id) DO UPDATE SET used = excluded.used",
        (used, task_id, entry_id),
    )


def row_counts(db: psycopg.Connection) -> list[object]:
    count = sql.SQL("SELECT count(*) FROM {}")
    return [db.execute(count.format(sql.Identifier(table))).fetchone() for table in WRITTEN]


def labeled_searches(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, dict[str, str]]:
    """Two entries, and searches for them in every state; return the entries and search ids.

    `judged` found both, used the first (with the agent's report) and not the second. `old` was
    logged before retrievals had relevance (MIT-602), has no outcome, and its retrieval of the
    second entry is excluded. `missed` found nothing. `excluded` and `test_key` don't count.
    """
    word = f"labelme{uuid.uuid4().hex[:6]}"
    first, second = (
        factories.entry(db, reporter, symptom=f"LabelError: {word} {n}", human_reviewed=True)
        for n in (1, 2)
    )

    def search(query: str) -> uuid.UUID:
        task_id = uuid.uuid4()
        arguments = {"query": query, "task_id": str(task_id), "model": "claude-opus-5-5"}
        call(server, "search", arguments)
        return task_id

    judged = search(f"{word} {SECRET}")
    report = {"task_id": str(judged), "entry_id": str(first), "agent_assessment": "Exactly it."}
    call(server, "report", report)
    set_used(db, judged, first, "partial")
    set_used(db, judged, second, "no")
    old = search(word)
    db.execute("UPDATE retrievals SET relevance = NULL WHERE task_id = %s", (old,))
    db.execute(
        "UPDATE retrievals SET excluded = true WHERE task_id = %s AND entry_id = %s", (old, second)
    )
    missed = search(f"zzqxnothing{uuid.uuid4().hex[:6]}")
    excluded = search(word)
    db.execute("UPDATE searches SET excluded = true WHERE task_id = %s", (excluded,))
    test_key = uuid.uuid4()
    with acting_as(reporter, test=True):
        search_service.search(db, SearchRequest(word, test_key, None, 5), "claude-opus-5-5")
    tasks = {"judged": judged, "old": old, "missed": missed, "excluded": excluded}
    return (
        first,
        second,
        {name: search_of(db, task) for name, task in tasks.items()}
        | {"test_key": search_of(db, test_key)},
    )


@pytest.mark.usefixtures("cli_on_db")
def test_relevance_candidates_print_one_labeled_line_per_counted_search(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second, ids = labeled_searches(server, db, reporter)
    before = row_counts(db)

    assert main(["admin", "relevance-candidates", "--days", "1"]) == 0

    assert row_counts(db) == before
    out = capsys.readouterr().out
    assert SECRET not in out
    lines = {line["search_id"]: line for line in map(json.loads, out.splitlines())}
    assert not {ids["excluded"], ids["test_key"]} & lines.keys()

    line = lines[ids["judged"]]
    assert re.fullmatch(r"labelme\w{6} \[REDACTED:anthropic_key\]", line["query"])
    assert line["model"] == "claude-opus-5-5"
    assert line["relevant"] == [str(first)]
    assert [hit["rank"] for hit in line["results"]] == [1, 2]
    assert all(hit["relevance"] > 0 for hit in line["results"])
    hits = {hit["entry_id"]: hit for hit in line["results"]}
    fields = ("used", "outcome", "assessment", "label")
    assert [hits[str(first)][f] for f in fields] == [
        "partial",
        "unknown",
        "Exactly it.",
        "relevant",
    ]
    assert [hits[str(second)][f] for f in fields] == ["no", "unknown", None, "possibly_irrelevant"]

    (hit,) = lines[ids["old"]]["results"]
    assert [hit[f] for f in ("entry_id", "relevance", "used", "label")] == [
        str(first),
        None,
        None,
        None,
    ]
    assert lines[ids["old"]]["relevant"] == []
    assert lines[ids["missed"]]["results"] == []


@pytest.mark.usefixtures("cli_on_db")
def test_relevance_candidates_need_a_positive_window(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["admin", "relevance-candidates", "--days", "0"]) == 1
    assert "--days must be at least 1." in capsys.readouterr().err

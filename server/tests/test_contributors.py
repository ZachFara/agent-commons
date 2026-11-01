"""Feedback to contributors (decision 0035): "Your entries", `commons mine`, session line."""

import base64
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from starlette.testclient import TestClient

import factories
from commons.cli.main import main
from commons.domain.reviews import ReviewRequest
from commons.service import contributors, reviews
from commons.service.identity import acting_as
from commons.service.keys import issue_key
from commons.store.connection import Connection
from commons.store.reporters import find_reporter
from commons.store.tasks import start_task
from commons.web.app import build_app

ORIGIN = "https://commons.example"


@pytest.fixture
def hosted(db: psycopg.Connection) -> TestClient:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return TestClient(build_app(test_connection, hosted=True), base_url=ORIGIN)


def basic(key: str) -> dict[str, str]:
    return {"authorization": "Basic " + base64.b64encode(f"x:{key}".encode()).decode()}


def test_your_entries_shows_only_your_own(hosted: TestClient, db: psycopg.Connection) -> None:
    alice = issue_key(db, "alice-mine", "agent").key
    issue_key(db, "bob-mine", "agent")
    alice_id, bob_id = find_reporter(db, "alice-mine"), find_reporter(db, "bob-mine")
    assert alice_id
    assert bob_id
    factories.entry(db, alice_id, symptom="alice's own entry")
    factories.entry(db, bob_id, symptom="bob's secret entry")

    page = hosted.get("/mine", headers=basic(alice))

    assert page.status_code == 200
    assert "alice&#39;s own entry" in page.text
    assert "bob" not in page.text
    assert hosted.get("/queue", headers=basic(alice)).status_code == 403
    assert hosted.get("/entries", headers=basic(alice)).status_code == 403


def test_the_hook_api_answers_mine_for_the_key_holder_only(
    hosted: TestClient, db: psycopg.Connection
) -> None:
    key = issue_key(db, "carol-mine", "agent").key
    other = find_reporter(db, "carol-mine")
    assert other
    factories.entry(db, other, symptom="carol entry", status="pending")
    stranger = issue_key(db, "dave-mine", "agent").key

    mine = hosted.post("/api/hooks/mine", json={}, headers={"authorization": f"Bearer {key}"})
    theirs = hosted.post(
        "/api/hooks/mine", json={}, headers={"authorization": f"Bearer {stranger}"}
    )

    assert [e["symptom"] for e in mine.json()["entries"]] == ["carol entry"]
    assert theirs.json()["entries"] == []


def test_the_session_line_is_off_by_default_and_counts_news_when_on(
    db: psycopg.Connection, reporter: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_task(db, f"s-{uuid.uuid4()}", reporter, None)
    db.execute(
        "UPDATE tasks SET created_at = now() - interval '1 day' WHERE reporter_id = %s", (reporter,)
    )
    start_task(db, f"s-{uuid.uuid4()}", reporter, None)
    entry_id = factories.entry(db, reporter, status="pending")
    with acting_as(reporter):
        reviews.review(db, ReviewRequest(entry_id, "approve", None, "human"))
    factories.outcome(db, entry_id, other_reporter(db))

    with acting_as(reporter):
        assert contributors.session_feedback(db) is None
        monkeypatch.setenv("COMMONS_SESSION_FEEDBACK", "on")
        line = contributors.session_feedback(db)

    assert line == (
        "Agent Commons since your last session: 1 of your entries approved; "
        "your entries were used 1 time(s) (1 worked)."
    )


def test_mine_command_prints_your_entries(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pseudonym = f"mine-{uuid.uuid4().hex[:6]}"
    config = tmp_path / "config.json"
    config.write_text(f'{{"pseudonym": "{pseudonym}"}}', encoding="utf-8")
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    with psycopg.connect(database_url) as conn:
        me = conn.execute(
            "INSERT INTO reporters (pseudonym) VALUES (%s) RETURNING id", (pseudonym,)
        ).fetchone()
        assert me
        factories.entry(conn, me[0], symptom="my cli entry", status="pending")

    assert main(["mine"]) == 0

    out = capsys.readouterr().out
    assert "pending   my cli entry" in out
    assert "waiting for review" in out
    assert "1 entr(ies)" in out


def other_reporter(db: psycopg.Connection) -> uuid.UUID:
    row = db.execute(
        "INSERT INTO reporters (pseudonym) VALUES (%s) RETURNING id", (uuid.uuid4().hex[:10],)
    ).fetchone()
    assert row
    found: uuid.UUID = row[0]
    return found


def test_the_session_line_counts_each_use_once_and_not_your_own(
    db: psycopg.Connection, reporter: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit 2026-10-31: every Stop re-saved old outcomes, so old uses counted again."""
    monkeypatch.setenv("COMMONS_SESSION_FEEDBACK", "on")
    entry_id = factories.entry(db, reporter)
    factories.outcome(db, entry_id, other_reporter(db), created_at="2000-01-01")
    start_task(db, f"s-{uuid.uuid4()}", reporter, None)
    db.execute(
        "UPDATE tasks SET created_at = now() - interval '1 day' WHERE reporter_id = %s", (reporter,)
    )
    start_task(db, f"s-{uuid.uuid4()}", reporter, None)
    db.execute(  # a later Stop re-saves the old outcome
        "UPDATE outcomes SET evidence = evidence || '{}' FROM retrievals r "
        "WHERE r.id = outcomes.retrieval_id AND r.entry_id = %s",
        (entry_id,),
    )
    factories.outcome(db, entry_id, reporter)  # your own session using your entry

    with acting_as(reporter):
        assert contributors.session_feedback(db) is None

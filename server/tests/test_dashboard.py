import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from starlette.testclient import TestClient

import factories
from commons.store.connection import Connection
from commons.web import app as web


@pytest.fixture
def client(db: psycopg.Connection) -> TestClient:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return TestClient(web.build_app(test_connection))


def test_overview_shows_queue_proposals_and_outcomes(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    waiting = factories.entry(
        db, reporter, status="pending", symptom="KeyError: 'dashboard-waiting'", fix="Fix it"
    )
    used = factories.entry(db, reporter, symptom="TypeError: dashboard-used", human_reviewed=True)
    factories.outcome(db, used, reporter, "error_resolved")
    db.execute(
        "UPDATE outcomes SET agent_assessment = 'Exactly the fix I needed.' "
        "WHERE retrieval_id IN (SELECT id FROM retrievals WHERE entry_id = %s)",
        (used,),
    )

    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert "Awaiting human review" in html
    assert "KeyError: &#39;dashboard-waiting&#39;" in html
    assert str(waiting) in html
    assert "TypeError: dashboard-used" in html
    assert "error_resolved" in html
    assert "Exactly the fix I needed." in html


def test_agent_written_text_is_escaped(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    factories.entry(
        db, reporter, status="pending", symptom="<script>alert('x')</script>", cause="<b>bold</b>"
    )
    html = client.get("/").text
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html


def test_dashboard_only_binds_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda _app, **kwargs: calls.append(kwargs))
    web.main(["--port", "9999"])
    assert calls == [{"host": "127.0.0.1", "port": 9999}]

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import httpx2
import psycopg
import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

import factories
from commons.domain.corrections import Correction
from commons.service import corrections
from commons.service.ranking import rank
from commons.store import insights
from commons.store.connection import Connection
from commons.store.models import model_id
from commons.store.rows import returned_id
from commons.store.tasks import start_task
from commons.web import app as web
from mcp_client import call

BASE = "http://127.0.0.1:8765"


@pytest.fixture
def client(db: psycopg.Connection) -> TestClient:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return TestClient(web.build_app(test_connection), base_url=BASE, follow_redirects=False)


def test_every_page_renders_with_data(
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
    rank(db)

    overview = client.get("/").text
    assert "Retrievals per day" in overview
    assert "Did it work?" in overview
    queue = client.get("/queue").text
    assert "KeyError: &#39;dashboard-waiting&#39;" in queue
    assert str(waiting) in queue
    entries = client.get("/entries?q=dashboard-used").text
    assert "TypeError: dashboard-used" in entries
    assert "dashboard-waiting" not in entries
    entry = client.get(f"/entries/{used}").text
    assert "Exactly the fix I needed." in entry
    assert "error resolved" in entry
    for path in ("/activity", "/people"):
        assert client.get(path).status_code == 200


def test_searches_page_lists_what_was_not_found(
    client: TestClient, server: FastMCP, reporter: uuid.UUID
) -> None:
    del reporter
    missing = "QuuxError: dashboard-never-documented <script>"
    for _ in range(2):
        call(server, "search", {"query": missing, "task_id": str(uuid.uuid4())})

    page = client.get("/searches").text
    assert "Searched, not found" in page
    assert "QuuxError: dashboard-never-documented &lt;script&gt;" in page
    assert "<script>" not in page.split("<body>", 1)[1]
    assert 'href="/searches" class="active"' in page


def add_task(db: psycopg.Connection, reporter: uuid.UUID) -> uuid.UUID:
    task_id = start_task(db, f"session-{uuid.uuid4()}", reporter, None)
    assert task_id
    return task_id


def add_search(db: psycopg.Connection, task_id: uuid.UUID, query: str, hits: int, ago: str) -> None:
    db.execute(
        "INSERT INTO searches (created_at, task_id, reporter_id, model_id, privacy_mode, query, "
        "result_limit, hit_count) SELECT now() - %s::interval, id, reporter_id, %s, 'entry', %s, "
        "5, %s FROM tasks WHERE id = %s",
        (ago, model_id(db, "claude-opus-5-5"), query, hits, task_id),
    )


def add_lookup(db: psycopg.Connection, task_id: uuid.UUID, library: str, version: str) -> uuid.UUID:
    cursor = db.execute(
        "INSERT INTO doc_lookups (created_at, task_id, reporter_id, privacy_mode, library_id, "
        "version) SELECT now() - interval '1 hour', id, reporter_id, 'entry', %s, %s "
        "FROM tasks WHERE id = %s RETURNING id",
        (library, version, task_id),
    )
    return returned_id(cursor.fetchone())


def lookup_section(page: str) -> str:
    return page.split("Docs looked up", 1)[1].split("Recent searches", 1)[0]


def test_searches_page_shows_commons_misses_next_to_doc_lookups(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    db.execute("DELETE FROM doc_lookups")  # other tests' committed lookups; rolled back after
    missed, hit, only, excluded = (add_task(db, reporter) for _ in range(4))
    add_search(db, missed, "DashError: nothing for this", 0, "2 hours")
    add_lookup(db, missed, "/dash/next", "v16")
    add_search(db, hit, "DashError: an entry has it", 2, "2 hours")
    add_lookup(db, hit, "/dash/next", "v16")
    add_lookup(db, only, "/dash/only", "")
    add_search(db, excluded, "DashError: excluded miss", 0, "2 hours")
    lookup = add_lookup(db, excluded, "/dash/excluded", "v1")
    corrections.correct(
        db, Correction("doc_lookups", lookup, "excluded", value=True, reason="test", by="owner")
    )

    totals = insights.lookup_totals(db)
    assert (totals["lookups"], totals["tasks"]) == (3, 3)
    assert totals["missed_then_looked_up"] == 1
    assert totals["looked_up_without_search"] == 1
    assert totals["hit_and_looked_up"] == 1
    rows = insights.misses_with_lookups(db)
    assert [(r["task_id"], r["query"], r["libraries"], r["model"], r["timing"]) for r in rows] == [
        (missed, "DashError: nothing for this", "/dash/next v16", "claude-opus-5-5", "after")
    ]
    libraries = insights.libraries_looked_up(db)
    assert [
        (r["library_id"], r["version"], r["lookups"], r["tasks"], r["hit_share"]) for r in libraries
    ] == [
        ("/dash/next", "v16", 2, 2, 0.5),
        ("/dash/only", "", 1, 1, 0.0),
    ]

    section = lookup_section(client.get("/searches").text)
    assert "DashError: nothing for this" in section
    assert "/dash/next" in section
    assert "50%" in section
    assert "after the search" in section
    assert "/dash/excluded" not in section
    assert "DashError: excluded miss" not in section
    assert str(excluded)[:8] not in section


def test_searches_page_says_when_there_are_no_lookups(
    client: TestClient, db: psycopg.Connection
) -> None:
    db.execute("DELETE FROM doc_lookups")  # rolled back after the test
    section = lookup_section(client.get("/searches").text)
    assert "No doc lookups in the last 30 days" in section
    assert "<table" not in section


def test_entries_filter_by_status_and_unknown_entry_is_404(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    word = f"filtercheck{uuid.uuid4().hex[:6]}"
    factories.entry(db, reporter, status="pending", symptom=f"{word} pending one")
    factories.entry(db, reporter, symptom=f"{word} approved one")
    html = client.get(f"/entries?q={word}&status=pending&order=newest").text
    assert "pending one" in html
    assert "approved one" not in html
    assert client.get(f"/entries/{uuid.uuid4()}").status_code == 404


def test_agent_written_text_is_escaped(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(
        db, reporter, status="pending", symptom="<script>alert('x')</script>", cause="<b>bold</b>"
    )
    for path in ("/queue", f"/entries/{entry_id}", "/entries"):
        html = client.get(path).text
        assert "<script>alert" not in html, path
        assert "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;" in html, path


def test_dashboard_only_binds_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("uvicorn.run", lambda _app, **kwargs: calls.append(kwargs))
    web.main(["--port", "9999"])
    assert calls == [{"host": "127.0.0.1", "port": 9999}]


def human_reviews(db: psycopg.Connection, entry_id: uuid.UUID) -> list[tuple[object, ...]]:
    return db.execute(
        "SELECT decision, reason, reviewed_by FROM reviews "
        "WHERE entry_id = %s AND source = 'human'",
        (entry_id,),
    ).fetchall()


def post_review(
    client: TestClient, entry_id: uuid.UUID, origin: str | None = BASE, **form: str
) -> httpx2.Response:
    headers = {} if origin is None else {"origin": origin}
    return client.post(f"/entries/{entry_id}/review", data=form, headers=headers)


def test_approving_from_the_dashboard_is_a_human_review(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, status="pending")
    response = post_review(client, entry_id, decision="approve", reason="")

    assert response.status_code == 303
    assert response.headers["location"] == f"/queue?reviewed={entry_id}"
    assert human_reviews(db, entry_id) == [("approve", None, reporter)]
    state = db.execute("SELECT status, human_reviewed FROM entries WHERE id = %s", (entry_id,))
    assert state.fetchone() == ("approved", True)
    assert (
        f'Review recorded for <span class="mono">{entry_id}'
        in client.get(response.headers["location"]).text
    )


def test_reject_needs_a_reason_and_shows_the_error(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, status="pending")
    response = post_review(client, entry_id, decision="reject", reason="  ")
    assert response.status_code == 303
    assert human_reviews(db, entry_id) == []
    assert "reason is required when rejecting." in client.get(response.headers["location"]).text

    post_review(client, entry_id, decision="reject", reason="Fix is wrong for 3.x")
    assert human_reviews(db, entry_id) == [("reject", "Fix is wrong for 3.x", reporter)]


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        (None, None),
        ("https://evil.example", None),
        ("http://127.0.0.1:9999", None),
        ("http://evil.example:8765", "evil.example:8765"),
    ],
)
def test_cross_origin_and_foreign_host_posts_are_refused(
    client: TestClient,
    db: psycopg.Connection,
    reporter: uuid.UUID,
    origin: str | None,
    host: str | None,
) -> None:
    entry_id = factories.entry(db, reporter, status="pending")
    headers = {"origin": origin} if origin else {}
    if host:
        headers["host"] = host
    response = client.post(
        f"/entries/{entry_id}/review", data={"decision": "approve"}, headers=headers
    )
    assert response.status_code == 403
    assert human_reviews(db, entry_id) == []


def test_bad_decision_and_unknown_entry(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, status="pending")
    assert post_review(client, entry_id, decision="maybe").status_code == 400
    missing = post_review(client, uuid.uuid4(), decision="approve")
    assert "No+entry" in missing.headers["location"]


def test_queue_shows_what_was_redacted(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    report = {"scanned": True, "findings": [{"field": "fix", "kind": "github_token", "count": 2}]}
    factories.entry(db, reporter, status="pending", redaction_report=report)
    assert "2 github token in fix" in client.get("/queue").text


def test_after_a_review_you_return_to_the_page_you_were_on(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, status="pending")
    page = f"{BASE}/entries/{entry_id}"
    response = client.post(
        f"/entries/{entry_id}/review",
        data={"decision": "approve"},
        headers={"origin": BASE, "referer": page},
    )
    assert response.headers["location"] == f"/entries/{entry_id}?reviewed={entry_id}"
    elsewhere = client.post(
        f"/entries/{entry_id}/review",
        data={"decision": "approve"},
        headers={"origin": BASE, "referer": "https://evil.example/phish"},
    )
    assert elsewhere.headers["location"].startswith("/queue?")

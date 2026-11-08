import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import httpx2
import psycopg
import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

import factories
from commons.domain.corrections import Correction, CorrectionTable
from commons.domain.duplicates import Candidate
from commons.service import corrections
from commons.service.ranking import rank
from commons.store import insights
from commons.store.connection import Connection
from commons.store.duplicates import save_candidates
from commons.store.models import model_id
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


def logged_search(db: psycopg.Connection, reporter: uuid.UUID, hits: int, limit: int) -> uuid.UUID:
    """Insert a searches row that returned `hits` of at most `limit` entries."""
    row = db.execute(
        "INSERT INTO searches (task_id, reporter_id, model_id, privacy_mode, query, "
        "result_limit, hit_count) VALUES (%s, %s, %s, 'entry', 'rank check', %s, %s) "
        "RETURNING id",
        (uuid.uuid4(), reporter, model_id(db, "claude-opus-5-5"), limit, hits),
    ).fetchone()
    assert row
    search_id: uuid.UUID = row[0]
    return search_id


def ranked_retrieval(
    db: psycopg.Connection,
    entry_id: uuid.UUID,
    reporter: uuid.UUID,
    position: int,
    used: str | None,
    days_ago: int = 0,
) -> uuid.UUID:
    """Insert a retrieval at `position` with an outcome whose `used` is given (None: unknown)."""
    row = db.execute(
        "WITH r AS (INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, "
        "rank_position, score_at_retrieval, created_at) VALUES (%s, %s, %s, %s, 'q', %s, 0.5, "
        "now() - make_interval(days => %s)) "
        "RETURNING id) INSERT INTO outcomes (retrieval_id, used) SELECT id, %s FROM r "
        "RETURNING retrieval_id",
        (
            entry_id,
            reporter,
            model_id(db, "claude-opus-5-5"),
            uuid.uuid4(),
            position,
            days_ago,
            used,
        ),
    ).fetchone()
    assert row
    retrieval_id: uuid.UUID = row[0]
    return retrieval_id


def seed_rank_rows(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    """Searches with (hits, limit) of (5,5), (2,5), (0,5), and retrievals at ranks 1 to 5.

    Counted: rank 1 yes and no, rank 2 partial, rank 3 no, rank 4 no, rank 5 yes. Not counted:
    an unknown use, a retrieval with no outcome, and one from 40 days ago.
    """
    for hits in (5, 2, 0):
        logged_search(db, reporter, hits, 5)
    entry_id = factories.entry(db, reporter, symptom=f"rank-{uuid.uuid4().hex[:8]} failed")
    for position, used in (
        (1, "yes"),
        (1, "no"),
        (2, "partial"),
        (3, "no"),
        (4, "no"),
        (5, "yes"),
        (1, None),
    ):
        ranked_retrieval(db, entry_id, reporter, position, used)
    ranked_retrieval(db, entry_id, reporter, 1, "yes", days_ago=40)
    db.execute(
        "INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, rank_position, "
        "score_at_retrieval) VALUES (%s, %s, %s, %s, 'q', 2, 0.5)",
        (entry_id, reporter, model_id(db, "claude-opus-5-5"), uuid.uuid4()),
    )


def only_this_tests_rows(db: psycopg.Connection) -> None:
    """Move rows other tests committed out of the 30-day window, inside this rolled-back test."""
    db.execute("UPDATE searches SET created_at = created_at - interval '1 year'")
    db.execute("UPDATE retrievals SET created_at = created_at - interval '1 year'")


def by_rank(db: psycopg.Connection) -> dict[int, tuple[int, int]]:
    return {r["rank"]: (r["retrievals"], r["used"]) for r in insights.use_by_rank(db)}


def test_searches_page_shows_full_result_sets_and_use_by_rank(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    only_this_tests_rows(db)
    seed_rank_rows(db, reporter)

    totals = insights.search_totals(db)
    assert (totals["searches"], totals["full"]) == (3, 1)
    rows = insights.use_by_rank(db)
    assert [(r["rank"], r["retrievals"], r["used"], r["used_rate"]) for r in rows] == [
        (1, 2, 1, 0.5),
        (2, 1, 1, 1.0),
        (3, 1, 0, 0.0),
        (4, 2, 1, 0.5),
    ]
    page = " ".join(client.get("/searches").text.split())
    assert "Full result sets" in page
    assert "1 of 3 hit their limit (33%)" in page
    assert "Use by rank" in page
    for label, count, used, share in (
        ("1", 2, 1, "50%"),
        ("2", 1, 1, "100%"),
        ("3", 1, 0, "0%"),
        ("4+", 2, 1, "50%"),
    ):
        assert (
            f'<td>{label}</td><td class="num">{count}</td> <td class="num">{used}</td>'
            f'<td class="num">{share}</td>'
        ) in page


def figures(db: psycopg.Connection) -> tuple[tuple[int, int], dict[int, tuple[int, int]]]:
    totals = insights.search_totals(db)
    return (totals["searches"], totals["full"]), by_rank(db)


def test_an_excluded_search_and_retrieval_leave_both_figures_unchanged(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    seed_rank_rows(db, reporter)
    before = figures(db)
    full = logged_search(db, reporter, 5, 5)
    entry_id = factories.entry(db, reporter, symptom=f"rank-{uuid.uuid4().hex[:8]} excluded")
    used = ranked_retrieval(db, entry_id, reporter, 1, "yes")
    (searches, full_sets), ranks = before
    retrievals, used_count = ranks[1]
    assert figures(db) == (
        (searches + 1, full_sets + 1),
        {**ranks, 1: (retrievals + 1, used_count + 1)},
    )

    rows: tuple[tuple[CorrectionTable, uuid.UUID], ...] = (("searches", full), ("retrievals", used))
    for table, row_id in rows:
        correction = Correction(table, row_id, "excluded", value=True, reason="test", by="owner")
        corrections.correct(db, correction)

    assert figures(db) == before


def test_searches_page_with_no_searches_shows_empty_states(
    client: TestClient, db: psycopg.Connection
) -> None:
    only_this_tests_rows(db)
    assert [r["used_rate"] for r in insights.use_by_rank(db)] == [None] * 4

    response = client.get("/searches")
    assert response.status_code == 200
    page = response.text
    assert "No retrievals with a known use yet." in page
    assert page.count("no searches yet") == 2


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


def test_a_duplicate_candidates_text_is_escaped_in_the_queue(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    candidate = factories.entry(
        db, reporter, fix="<script>alert('fix')</script>", human_reviewed=True
    )
    proposal = factories.entry(db, reporter, status="pending")
    save_candidates(db, proposal, [Candidate(candidate, 0.8, same_task=False)], None)
    html = client.get("/queue").text
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(&#39;fix&#39;)&lt;/script&gt;" in html


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

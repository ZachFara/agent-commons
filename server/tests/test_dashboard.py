import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import psycopg
import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

import factories
from commons.domain import prompts
from commons.domain.corrections import Correction
from commons.domain.entries import Proposal
from commons.service import agent_text, corrections, trust
from commons.service.keys import issue_key
from commons.service.ranking import rank
from commons.service.seeds import SEED_MODEL, SEED_REPORTER
from commons.store import insights
from commons.store import trust as trust_store
from commons.store.connection import Connection
from commons.store.models import model_id
from commons.store.prompt_versions import prompt_version_id
from commons.store.reporters import upsert_reporter
from commons.store.rows import returned_id
from commons.store.seeds import insert_seed_entry
from commons.store.tasks import start_task
from commons.web import app as web
from mcp_client import call

BASE = "http://127.0.0.1:8765"
PROVISIONAL = "/entries?status=provisional&order=newest"


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
    factories.entry(db, reporter, symptom="<script>alert('x')</script>", provisional=True)
    for path in ("/queue", f"/entries/{entry_id}", "/entries", PROVISIONAL, "/mine"):
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


def trusted_proposal(server: FastMCP) -> uuid.UUID:
    """Propose as a trusted reporter, so the entry goes live provisionally (decision 0028)."""
    arguments = {
        "symptom": f"{uuid.uuid4().hex} badge{uuid.uuid4().hex[:6]} failure",
        "environment": {"language": "python 3.12"},
        "cause": "Old version.",
        "fix": "Upgrade it.",
        "domains": ["python"],
        "task_id": str(uuid.uuid4()),
        "model": "claude-opus-5-5",
    }
    result = call(server, "propose", arguments)
    assert result["status"] == "approved"
    return uuid.UUID(result["entry_id"])


def sample(monkeypatch: pytest.MonkeyPatch, *, sampled: bool) -> None:
    monkeypatch.setattr("commons.service.trust.secrets.randbelow", lambda _n: 0 if sampled else 1)


def test_the_badge_counts_what_the_queue_lists(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production showed a badge of 8 over an empty queue: it counted entries the queue hides."""
    db.execute("UPDATE reporters SET trust_level = 1 WHERE id = %s", (reporter,))
    before = insights.kpis(db)["awaiting"]

    sample(monkeypatch, sampled=False)
    trusted_proposal(server)
    trust_store.confirm(db, trusted_proposal(server))
    suspended = db.execute(
        "INSERT INTO reporters (pseudonym, suspended_at) VALUES (%s, now()) RETURNING id",
        (f"suspended-{uuid.uuid4().hex[:8]}",),
    ).fetchone()
    assert suspended
    factories.entry(db, suspended[0], status="pending")

    assert insights.kpis(db)["awaiting"] - before == 0
    assert insights.kpis(db)["awaiting"] == trust.queue_health(db)[0]

    sample(monkeypatch, sampled=True)
    trusted_proposal(server)

    assert insights.kpis(db)["awaiting"] - before == 1
    assert insights.kpis(db)["awaiting"] == trust.queue_health(db)[0]


def test_the_nav_badge_and_the_queue_header_agree(
    client: TestClient,
    server: FastMCP,
    db: psycopg.Connection,
    reporter: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Other tests commit entries; retire them in this rolled-back transaction.
    db.execute("UPDATE entries SET status = 'retired'")
    db.execute("UPDATE reporters SET trust_level = 1 WHERE id = %s", (reporter,))
    sample(monkeypatch, sampled=False)
    trusted_proposal(server)

    queue = client.get("/queue").text
    assert 'class="count hot"' not in queue
    assert " waiting;" not in queue
    assert "0 awaiting human review" in client.get("/").text

    sample(monkeypatch, sampled=True)
    trusted_proposal(server)

    queue = client.get("/queue").text
    assert '<span class="count hot">1</span>' in queue
    assert "1 waiting;" in queue
    assert "1 awaiting human review" in client.get("/").text


def provisional_count(queue: str) -> int:
    found = re.search(r"(\d+) went live provisionally without review", queue)
    assert found, "the queue page says how many went live without review"
    return int(found.group(1))


def test_entries_that_went_live_without_review_are_findable_outside_the_queue(
    client: TestClient,
    server: FastMCP,
    db: psycopg.Connection,
    reporter: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zach couldn't find his agents' entries: a trusted one skips the queue (decision 0028)."""
    db.execute("UPDATE reporters SET trust_level = 1 WHERE id = %s", (reporter,))
    before = provisional_count(client.get("/queue").text)
    sample(monkeypatch, sampled=False)
    live = trusted_proposal(server)
    sample(monkeypatch, sampled=True)
    sampled = trusted_proposal(server)
    # A human review leaves `provisional` set; only `human_reviewed` takes it off the list.
    reviewed = factories.entry(
        db, reporter, symptom="reviewed by a human", provisional=True, human_reviewed=True
    )

    queue = client.get("/queue").text
    assert provisional_count(queue) - before == 1
    assert f'href="{PROVISIONAL.replace("&", "&amp;")}"' in queue
    assert f'id="{live}"' not in queue
    assert f'id="{sampled}"' in queue

    entries = client.get(PROVISIONAL).text
    assert f'href="/entries/{live}"' in entries
    assert f'href="/entries/{sampled}"' not in entries
    assert f'href="/entries/{reviewed}"' not in entries

    mine = client.get("/mine").text
    rows = {r.split('href="/entries/', 1)[1][:36]: r for r in mine.split("<tr>")[2:]}
    assert "unreviewed" in rows[str(live)]
    assert "unreviewed" not in rows[str(reviewed)]


# Prompts (decision MIT-566)

HOOK = {"outcome_source": "hook"}
FIGURES = (
    "tasks",
    "straddled",
    "searches",
    "misses",
    "retrievals",
    "reported_rate",
    "used_rate",
    "used_n",
    "observed_success_rate",
    "observed_n",
    "proposals",
    "approved",
)


def hosted_client(db: psycopg.Connection) -> TestClient:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return TestClient(web.build_app(test_connection, hosted=True), base_url=BASE)


def by_version(db: psycopg.Connection) -> dict[uuid.UUID | None, dict[str, Any]]:
    return {row["id"]: row for row in insights.prompt_versions(db)}


def test_prompts_page_breaks_outcomes_down_by_version(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    start = datetime.now(UTC) - timedelta(hours=1)

    def at(minutes: int) -> datetime:
        return start + timedelta(minutes=minutes)

    old = factories.prompt_version(db, f"old-{uuid.uuid4().hex[:6]}", "<script>alert(1)</script>")
    new = factories.prompt_version(db, f"new-{uuid.uuid4().hex[:6]}")
    straddler, plain, proposer, dropped = (uuid.uuid4() for _ in range(4))
    # A task that began under `old` and went on after a change counts once, under `old`, with
    # all of its rows: the agent kept reading the text it started with.
    entry_id = factories.entry(
        db, reporter, task_id=straddler, prompt_version_id=old, created_at=at(0)
    )
    factories.search(db, reporter, straddler, prompt_version_id=old, created_at=at(1))
    worked: dict[str, object] = {
        "used": "yes",
        "outcome": "tests_passed",
        "evidence": HOOK,
        "agent_assessment": "Worked.",
    }
    factories.retrieval(
        db, entry_id, reporter, straddler, worked, prompt_version_id=old, created_at=at(1)
    )
    later = {"prompt_version_id": new, "created_at": at(2)}
    factories.search(db, reporter, straddler, hit_count=0, **later)
    factories.retrieval(db, entry_id, reporter, straddler, {"used": "partial"}, **later)
    factories.entry(
        db, reporter, status="pending", task_id=straddler, prompt_version_id=new, created_at=at(2)
    )
    # A proposal with no task counts under its own stamp.
    factories.entry(db, reporter, status="pending", prompt_version_id=new, created_at=at(5))
    factories.search(db, reporter, plain, prompt_version_id=new, created_at=at(3))
    factories.retrieval(
        db, entry_id, reporter, plain, {"used": "no"}, prompt_version_id=new, created_at=at(3)
    )
    factories.retrieval(db, entry_id, reporter, plain, prompt_version_id=new, created_at=at(4))
    factories.entry(
        db, reporter, status="pending", task_id=proposer, prompt_version_id=new, created_at=at(5)
    )
    # Excluded rows drop out of every figure.
    gone = {"prompt_version_id": new, "created_at": at(6), "excluded": True}
    factories.search(db, reporter, dropped, hit_count=0, **gone)
    factories.retrieval(db, entry_id, reporter, dropped, worked, **gone)

    rows = by_version(db)
    assert [rows[old][k] for k in FIGURES] == [1, 1, 2, 1, 2, 0.5, 1.0, 2, 1.0, 1, 2, 1]
    assert [rows[new][k] for k in FIGURES] == [2, 0, 1, 0, 2, 0.0, 0.0, 1, None, 0, 2, 0]
    assert (rows[old]["first_seen"], rows[old]["last_seen"]) == (at(0), at(1))
    assert (rows[new]["first_seen"], rows[new]["last_seen"]) == (at(2), at(5))

    page = client.get("/prompts").text
    assert 'href="/prompts" class="active"' in page
    for version in (old, new):
        assert rows[version]["short_hash"] in page
        assert rows[version]["label"] in page
    assert '100% <span class="muted">n=1</span>' in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<script>" not in page.split("<body>", 1)[1]
    entry_page = client.get(f"/entries/{entry_id}").text
    assert rows[old]["short_hash"] in entry_page
    assert rows[new]["short_hash"] in entry_page


def test_rows_from_before_versioning_share_one_line(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    before = by_version(db).get(None, {"searches": 0, "proposals": 0, "tasks": 0})
    task_id = uuid.uuid4()
    factories.search(db, reporter, task_id)
    factories.entry(db, reporter, task_id=task_id)

    after = by_version(db)[None]
    assert after["searches"] == before["searches"] + 1
    assert after["proposals"] == before["proposals"] + 1
    assert after["tasks"] == before["tasks"] + 1
    assert after["short_hash"] is None
    assert client.get("/prompts").text.count("before versioning") == 1


def test_seeded_and_imported_entries_leave_every_prompt_version_row_alone(
    db: psycopg.Connection,
) -> None:
    before = by_version(db)
    seed = insert_seed_entry(
        db,
        uuid.uuid4(),
        Proposal(
            symptom="ModuleNotFoundError: prompts-seed",
            environment={"language": "python 3.12"},
            cause="A seed.",
            fix="Install it.",
            domains=("test",),
            repro=None,
            task_id=uuid.UUID(int=0),
        ),
        upsert_reporter(db, SEED_REPORTER, "entry"),
        model_id(db, SEED_MODEL),
    )
    assert seed
    key = issue_key(db, f"importer-{uuid.uuid4().hex[:6]}", "agent").key
    imported = hosted_client(db).post(
        "/api/hooks/import",
        json={
            "entry": {
                "symptom": "RuntimeError: prompts-import lost its event loop",
                "environment": {"language": "python 3.12"},
                "cause": "The loop closed first.",
                "fix": "Keep the loop open.",
                "domains": ["test"],
            },
            "imported_from": {
                "local_entry_id": str(uuid.uuid4()),
                "model": "claude-opus-5-5",
                "created_at": "2026-01-02T03:04:05+00:00",
            },
        },
        headers={"authorization": f"Bearer {key}"},
    )
    assert imported.status_code == 200
    # The import's fresh task_id is on the entry only: no tasks row exists for it.
    row = db.execute(
        "SELECT e.task_id, EXISTS (SELECT 1 FROM tasks t WHERE t.id = e.task_id) FROM entries e "
        "WHERE e.id = %s",
        (imported.json()["entry_id"],),
    ).fetchone()
    assert row
    assert row[0] is not None
    assert not row[1]
    # Later than anything else in the store, so last_seen would move if they counted.
    db.execute(
        "UPDATE entries SET created_at = now() + interval '1 day' "
        "WHERE symptom LIKE '%%prompts-seed' OR symptom LIKE '%%prompts-import%%'"
    )

    assert by_version(db) == before


def test_hook_versions_are_listed_apart_with_tasks_and_reporters(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    other = upsert_reporter(db, f"hooks-{uuid.uuid4().hex[:6]}", "entry")
    marker = uuid.uuid4().hex[:8]
    released = prompts.prompt_version(
        f"hooks-{marker}", {"session_start": f"<b>{marker}</b>"}, kind="hooks"
    )
    unknown = agent_text.reported_hook_version(uuid.uuid4().hex[:12])
    released_id, unknown_id = (prompt_version_id(db, v) for v in (released, unknown))
    for who, version_id in ((reporter, released_id), (other, released_id), (reporter, unknown_id)):
        start_task(db, f"s-{uuid.uuid4()}", who, None, version_id)
    start_task(db, f"s-{uuid.uuid4()}", reporter, None, released_id)

    rows = {row["id"]: row for row in insights.hook_prompt_versions(db)}
    assert (rows[released_id]["tasks"], rows[released_id]["reporters"]) == (3, 2)
    assert (rows[unknown_id]["tasks"], rows[unknown_id]["reporters"]) == (1, 1)
    assert rows[unknown_id]["label"] == "unrecognized"
    assert released_id not in by_version(db)
    assert unknown_id not in by_version(db)

    page = client.get("/prompts").text
    hooks_section = page.split("Hook versions", 1)[1]
    for version in (released, unknown):
        assert version.hash[:12] in hooks_section
    assert f"&lt;b&gt;{marker}&lt;/b&gt;" in hooks_section
    activity = client.get("/activity").text
    assert released.hash[:12] in activity
    assert f'{unknown.hash}</a> <span class="muted">unrecognized</span>' in activity

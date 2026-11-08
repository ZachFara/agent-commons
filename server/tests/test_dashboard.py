import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import httpx2
import psycopg
import pytest
from fastmcp import FastMCP
from psycopg import sql
from psycopg.types.json import Jsonb
from starlette.testclient import TestClient

import factories
from commons.service.ranking import rank
from commons.store import insights
from commons.store.connection import Connection
from commons.store.reporters import upsert_reporter
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


def costed_entry(db: psycopg.Connection, reporter: uuid.UUID, word: str) -> uuid.UUID:
    """An entry whose counted, used, known outcomes cost 1,200, 2,400, 3,600 (worked), 6,000."""
    entry_id = factories.entry(
        db, reporter, symptom=f"CostError: {word}", human_reviewed=True, provisional=False
    )
    for tokens in ((1000, 200), (2000, 400), (3000, 600)):
        factories.outcome(db, entry_id, reporter, "tests_passed", tokens=tokens)
    factories.outcome(db, entry_id, reporter, "tests_failed", tokens=(5000, 1000))
    # Each of these would change the figures if it counted.
    factories.outcome(db, entry_id, reporter, "tests_passed")  # no tokens recorded
    unused = factories.outcome(db, entry_id, reporter, "tests_passed", tokens=(90000, 0))
    db.execute("UPDATE outcomes SET used = 'no' WHERE id = %s", (unused,))
    factories.outcome(db, entry_id, reporter, "unknown", tokens=(90000, 0))
    factories.outcome(db, entry_id, reporter, "abandoned", tokens=(90000, 0))
    excluded = factories.outcome(db, entry_id, reporter, "tests_passed", tokens=(90000, 0))
    db.execute("UPDATE outcomes SET excluded = true WHERE id = %s", (excluded,))
    on_excluded = factories.outcome(db, entry_id, reporter, "tests_failed", tokens=(90000, 0))
    db.execute(
        "UPDATE retrievals SET excluded = true "
        "WHERE id = (SELECT retrieval_id FROM outcomes WHERE id = %s)",
        (on_excluded,),
    )
    return entry_id


def test_entry_costs_count_used_known_outcomes_with_tokens(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = costed_entry(db, reporter, "store-figures")
    cost = insights.entry_costs(db, [entry_id])[entry_id]
    assert (cost["costed"], cost["tokens"], cost["successes"], cost["failures"]) == (
        4,
        13200,
        3,
        1,
    )
    assert cost["tokens_per_success"] == 4400
    assert (cost["median_success"], cost["median_failure"]) == (2400, 6000)


def test_entry_page_shows_tokens_per_success_and_search_size(
    client: TestClient, server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    word = f"costpage{uuid.uuid4().hex[:6]}"
    entry_id = costed_entry(db, reporter, word)
    (hit,) = call(server, "search", {"query": word, "task_id": str(uuid.uuid4())})["data"]
    size = round(sum(len(v) for v in hit.values() if isinstance(v, str)) / 4)

    page = client.get(f"/entries/{entry_id}").text
    assert f"≈ {size:,} tokens" in page
    assert '<div class="value">4,400</div>' in page
    assert "n = 4 used outcomes with tokens, 3 worked" in page
    assert '<div class="value">2,400</div><div class="note">n = 3</div>' in page
    assert '<div class="value">6,000</div><div class="note">n = 1</div>' in page

    listing = client.get(f"/entries?q={word}").text
    assert f"≈ {size:,}" in listing
    assert "4,400<div" in listing
    assert "2,400 · 6,000" in listing
    assert "n = 3 · 1" in listing


def test_an_entry_without_token_data_shows_no_data(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    word = f"nocost{uuid.uuid4().hex[:6]}"
    entry_id = factories.entry(
        db, reporter, symptom=f"CostError: {word}", human_reviewed=True, provisional=False
    )
    factories.outcome(db, entry_id, reporter, "tests_passed")  # used and worked, no tokens
    assert insights.entry_costs(db, [entry_id]) == {}

    page = client.get(f"/entries/{entry_id}").text
    cost = page.split("Tokens per success", 1)[1].split("Score over time", 1)[0]
    assert cost.count("no data") == 3
    assert '<div class="value">0</div>' not in cost
    listing = client.get(f"/entries?q={word}").text
    assert listing.count("no data") == 3


def test_tokens_per_success_with_no_success_says_so(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, human_reviewed=True, provisional=False)
    factories.outcome(db, entry_id, reporter, "tests_failed", tokens=(800, 200))
    page = client.get(f"/entries/{entry_id}").text
    assert "no success yet" in page
    assert '<div class="value">1,000</div><div class="note">n = 1</div>' in page


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


def set_outcome(db: psycopg.Connection, outcome_id: uuid.UUID, **fields: object) -> None:
    for field, value in fields.items():
        db.execute(
            sql.SQL("UPDATE outcomes SET {} = %s WHERE id = %s").format(sql.Identifier(field)),
            (value, outcome_id),
        )


def exclude_retrieval_of(db: psycopg.Connection, outcome_id: uuid.UUID) -> None:
    db.execute(
        "UPDATE retrievals SET excluded = true "
        "WHERE id = (SELECT retrieval_id FROM outcomes WHERE id = %s)",
        (outcome_id,),
    )


def funnel_entry(db: psycopg.Connection, reporter: uuid.UUID) -> uuid.UUID:
    """An entry retrieved 9 counted times: 2 not used, 3 use unknown, 4 used (2 worked)."""
    entry_id = factories.entry(db, reporter, human_reviewed=True, provisional=False)
    for result, used in (
        ("tests_passed", "yes"),
        ("error_resolved", "partial"),
        ("tests_failed", "yes"),
        ("unknown", "yes"),
        ("tests_passed", "no"),
        ("tests_failed", "no"),
        ("tests_passed", None),
    ):
        set_outcome(db, factories.outcome(db, entry_id, reporter, result), used=used)
    # A retrieval with no outcome yet, and one whose outcome is excluded: both use unknown.
    no_outcome = factories.outcome(db, entry_id, reporter)
    db.execute("DELETE FROM outcomes WHERE id = %s", (no_outcome,))
    set_outcome(db, factories.outcome(db, entry_id, reporter), excluded=True)
    # An excluded retrieval is left out altogether.
    exclude_retrieval_of(db, factories.outcome(db, entry_id, reporter, "tests_failed"))
    return entry_id


def test_entry_funnel_splits_unused_retrievals_from_failures(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    funnel = insights.entry_funnel(db, funnel_entry(db, reporter))
    assert funnel == {
        "retrieved": 9,
        "not_used": 2,
        "used_unknown": 3,
        "used": 4,
        "worked": 2,
        "failed": 1,
        "no_result": 1,
    }


def test_entry_page_shows_the_funnel_and_relevance(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = funnel_entry(db, reporter)
    page = client.get(f"/entries/{entry_id}").text
    stages = dict(re.findall(r'data-stage="(\w+)">.*?<b>(\d+)</b>', page, re.DOTALL))
    assert stages == {
        "retrieved": "9",
        "not_used": "2",
        "used_unknown": "3",
        "used": "4",
        "worked": "2",
        "failed": "1",
        "no_result": "1",
    }
    assert "probably the wrong retrieval" in page
    # factories.outcome writes no relevance, like rows from before MIT-602: a blank cell.
    assert page.count('<td class="num"></td>') == 10
    db.execute("UPDATE retrievals SET relevance = 0.4242 WHERE entry_id = %s", (entry_id,))
    page = client.get(f"/entries/{entry_id}").text
    assert page.count('<td class="num">0.42</td>') == 10
    assert '<td class="num"></td>' not in page


def test_an_entry_never_retrieved_has_an_empty_funnel(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter, human_reviewed=True, provisional=False)
    assert insights.entry_funnel(db, entry_id)["retrieved"] == 0
    page = client.get(f"/entries/{entry_id}").text
    assert "data-stage" not in page


def searched(
    server: FastMCP, db: psycopg.Connection, word: str, used: tuple[str | None, ...]
) -> uuid.UUID:
    """Search for `word` in a new task, then give the hits outcomes with `used`, in rank order.

    A `None` leaves that hit without an outcome. Returns the search's id.
    """
    task_id = uuid.uuid4()
    hits = call(server, "search", {"query": word, "task_id": str(task_id)})["data"]
    assert len(hits) == len(used)
    for hit, value in zip(hits, used, strict=True):
        if value is not None:
            db.execute(
                "INSERT INTO outcomes (retrieval_id, used) SELECT id, %s FROM retrievals "
                "WHERE task_id = %s AND entry_id = %s",
                (value, task_id, hit["entry_id"]),
            )
    row = db.execute("SELECT id FROM searches WHERE task_id = %s", (task_id,)).fetchone()
    assert row
    search_id: uuid.UUID = row[0]
    return search_id


def test_searches_where_nothing_returned_was_used(
    client: TestClient, server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    word = f"fitmiss{uuid.uuid4().hex[:6]}"
    for n in (1, 2):
        factories.entry(
            db, reporter, symptom=f"FitError: {word} {n}", human_reviewed=True, provisional=False
        )
    unused = searched(server, db, word, ("no", "no"))
    old = searched(server, db, word, ("no", "no"))
    db.execute(
        "UPDATE retrievals SET relevance = NULL WHERE task_id = "
        "(SELECT task_id FROM searches WHERE id = %s)",
        (old,),
    )
    some_use = searched(server, db, word, ("no", "partial"))
    pending = searched(server, db, word, ("no", None))
    excluded = searched(server, db, word, ("no", "no"))
    db.execute("UPDATE searches SET excluded = true WHERE id = %s", (excluded,))

    rows = {row["id"]: row for row in insights.unused_searches(db)}
    assert unused in rows
    assert old in rows
    assert not {some_use, pending, excluded} & rows.keys()
    relevances = [hit["relevance"] for hit in rows[unused]["entries"]]
    assert len(relevances) == 2
    assert all(r is not None and r > 0 for r in relevances)
    assert [hit["relevance"] for hit in rows[old]["entries"]] == [None, None]

    page = client.get("/searches").text
    section = page.split("Found, not used", 1)[1].split("Recent searches", 1)[0]
    assert section.count(f'">FitError: {word}') == 4  # link text: 2 searches x 2 entries
    assert f'<span class="num">{relevances[0]:.2f}</span>' in section
    assert '<span class="num"></span>' in section


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


# Proposal funnel


def suspended_reporter(db: psycopg.Connection) -> uuid.UUID:
    suspended = upsert_reporter(db, f"suspended-{uuid.uuid4().hex[:8]}", "entry")
    db.execute("UPDATE reporters SET suspended_at = now() WHERE id = %s", (suspended,))
    return suspended


def add_review(
    db: psycopg.Connection, entry_id: uuid.UUID, by: uuid.UUID, reason: str, source: str
) -> None:
    db.execute(
        "INSERT INTO reviews (entry_id, reviewed_by, decision, reason, source, created_at) "
        "VALUES (%s, %s, 'reject', %s, %s, clock_timestamp())",
        (entry_id, by, reason, source),
    )


def funnel_proposals(db: psycopg.Connection, reporter: uuid.UUID, model: str) -> None:
    """Proposals by `model` with every fate (see the expected counts in the tests below)."""

    def entry(**fields: object) -> uuid.UUID:
        return factories.entry(db, reporter, model, **fields)

    # A human review leaves `provisional` set (test_safety), so one approval keeps it.
    canonical = entry(human_reviewed=True, provisional=True)
    entry(human_reviewed=True, provisional=False)
    entry(human_reviewed=False, provisional=True)
    entry(human_reviewed=False, provisional=True, sampled=True)
    entry(human_reviewed=False, provisional=False)  # approved by an agent review
    for reason in ("not reusable", "wrong fix", "not reusable"):
        rejected = entry(status="rejected", human_reviewed=True, provisional=False)
        add_review(db, rejected, reporter, "an agent's reason", "agent")
        add_review(db, rejected, reporter, reason, "human")
    # Merged: a pending duplicate is rejected, an approved one retired, both superseded.
    entry(status="rejected", human_reviewed=True, provisional=False, superseded_by=canonical)
    entry(status="retired", human_reviewed=True, provisional=False, superseded_by=canonical)
    flag = Jsonb([{"entry_id": str(canonical), "score": 0.5}])
    entry(status="pending", human_reviewed=False, provisional=False, duplicate_candidates=flag)
    entry(status="pending", human_reviewed=False, provisional=False)
    entry(status="rejected", human_reviewed=False, provisional=False)  # an agent's reject only
    entry(status="retired", human_reviewed=True, provisional=False)
    suspended = suspended_reporter(db)
    for status in ("approved", "pending"):
        factories.entry(db, suspended, model, status=status, human_reviewed=status == "approved")
    # Proposed before the window: in no bucket.
    entry(human_reviewed=True, provisional=False, created_at="2020-01-01")


FUNNEL = {
    "proposals": 16,
    "human_approved": 2,
    "auto_approved": 2,
    "agent_approved": 1,
    "rejected": 3,
    "merged": 2,
    "flagged": 1,
    "pending": 2,
    "retired": 1,
    "suspended": 2,
}


def test_each_proposal_lands_in_one_funnel_bucket(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    model = f"funnel-{uuid.uuid4().hex[:8]}"
    funnel_proposals(db, reporter, model)

    (row,) = [row for row in insights.proposal_funnel(db) if row["model"] == model]
    assert {bucket: row[bucket] for bucket in FUNNEL} == FUNNEL
    assert sum(row[bucket] for bucket in insights.FUNNEL_BUCKETS) == row["proposals"]
    assert row["reasons"] == [
        {"reason": "not reusable", "count": 2},
        {"reason": "wrong fix", "count": 1},
    ]


def test_overview_shows_the_proposal_funnel(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    model = f"funnel-{uuid.uuid4().hex[:8]}"
    funnel_proposals(db, reporter, model)

    page = client.get("/").text
    row = page.split(f'<tr data-funnel="{model}">', 1)[1].split("</tr>", 1)[0]
    assert dict(re.findall(r'data-bucket="(\w+)">(\d+)<', row)) == {
        bucket: str(count) for bucket, count in FUNNEL.items()
    }
    assert "Rejected because: not reusable (2); wrong fix (1)" in page
    assert "an agent&#39;s reason" not in page


def approved_entries(db: psycopg.Connection, reporter: uuid.UUID, model: str) -> None:
    """Approved entries by `model`: 3 not retrieved in 60 days, 2 retrieved 3+ times unused."""

    def entry(**fields: object) -> uuid.UUID:
        return factories.entry(db, reporter, model, human_reviewed=True, **fields)

    def retrieved(entry_id: uuid.UUID, used: str | None, when: str | None = None) -> uuid.UUID:
        outcome = factories.outcome(db, entry_id, reporter, created_at=when)
        set_outcome(db, outcome, used=used)
        return outcome

    entry(created_at="2020-01-01")  # never retrieved
    retrieved(entry(created_at="2020-01-01"), "yes", "2020-02-01")  # not in the last 60 days
    exclude_retrieval_of(db, retrieved(entry(created_at="2020-01-01"), "yes"))  # doesn't count
    retrieved(entry(created_at="2020-01-01"), "no")  # retrieved recently
    entry()  # new: it hasn't had 60 days yet
    never_used = entry()
    for used in ("no", None, "no"):
        retrieved(never_used, used)
    used_once = entry()
    for used in ("no", "partial", None):
        retrieved(used_once, used)
    twice = entry()  # under 3 retrievals
    for used in ("no", "no"):
        retrieved(twice, used)
    unused_excluded = entry()  # its use is on an excluded retrieval, so never observed used
    for used in ("no", "no", "no"):
        retrieved(unused_excluded, used)
    exclude_retrieval_of(db, retrieved(unused_excluded, "yes"))
    factories.entry(db, suspended_reporter(db), model, human_reviewed=True, created_at="2020-01-01")
    entry(status="retired", created_at="2020-01-01")  # not approved: in no count


def test_approved_entries_never_retrieved_or_never_used(
    client: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    model = f"use-{uuid.uuid4().hex[:8]}"
    approved_entries(db, reporter, model)
    expected = {"approved": 9, "unretrieved": 3, "never_used": 2, "suspended": 1}

    (row,) = [row for row in insights.approved_use(db) if row["model"] == model]
    assert {key: row[key] for key in expected} == expected
    page = client.get("/").text
    row_html = page.split(f'<tr data-approved="{model}">', 1)[1].split("</tr>", 1)[0]
    assert dict(re.findall(r'data-use="(\w+)">(\d+)<', row_html)) == {
        key: str(count) for key, count in expected.items()
    }

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
from psycopg import sql
from psycopg.types.json import Jsonb
from starlette.testclient import TestClient

import factories
from commons.domain import prompts
from commons.domain.corrections import Correction, CorrectionTable
from commons.domain.duplicates import Candidate
from commons.domain.entries import Proposal
from commons.service import agent_text, corrections, trust
from commons.service.duplicates import merge
from commons.service.keys import issue_key
from commons.service.lifecycle import retire
from commons.service.ranking import rank
from commons.service.seeds import SEED_MODEL, SEED_REPORTER
from commons.store import insights
from commons.store import trust as trust_store
from commons.store.connection import Connection
from commons.store.duplicates import save_candidates
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
    """The lookups card alone: other cards (use by rank, found-not-used) follow it."""
    return page.split("Docs looked up", 1)[1].split("</section>", 1)[0]


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
    factories.entry(db, reporter, symptom="<script>alert('x')</script>", provisional=True)
    for path in ("/queue", f"/entries/{entry_id}", "/entries", PROVISIONAL, "/mine"):
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
    for status in ("pending", "approved"):
        merge(db, entry(status=status, human_reviewed=False, provisional=False), canonical)
    # Superseded by an admin with no merge (decision 0026 §3): retired, not merged.
    better = entry(human_reviewed=True, provisional=False)
    retire(db, entry(human_reviewed=True, provisional=False), "a newer fix", superseded_by=better)
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
    "proposals": 18,
    "human_approved": 3,
    "auto_approved": 2,
    "agent_approved": 1,
    "rejected": 3,
    "merged": 2,
    "flagged": 1,
    "pending": 2,
    "retired": 2,
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

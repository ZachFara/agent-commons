"""Duplicate resolution (decision 0027): flag on propose, merge, keep both, tuned threshold."""

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP
from psycopg import sql
from psycopg.conninfo import make_conninfo
from starlette.testclient import TestClient
from tools.search_eval import JUDGED, load_judged

import factories
from commons.domain.duplicates import DEFAULT_THRESHOLD, Candidate, DuplicateSettings
from commons.domain.errors import InvalidInputError
from commons.service import duplicates
from commons.store import insights
from commons.store.connection import Connection
from commons.store.duplicates import save_candidates
from commons.store.migrate import apply_migrations
from commons.store.reporters import upsert_reporter
from commons.store.search import similar_entries
from commons.web.app import build_app
from conftest import ADMIN_URL
from mcp_client import call

ORIGIN = "http://127.0.0.1:8765"


def propose(server: FastMCP, symptom: str, task_id: str | None = None) -> dict[str, Any]:
    arguments = {
        "symptom": symptom,
        "environment": {"language": "python 3.12", "libraries": {"quickcache": "2.0.0"}},
        "cause": "quickcache 2.0 renamed ttl to expire_after.",
        "fix": "Rename `ttl=` to `expire_after=`.",
        "domains": ["python"],
        "task_id": task_id or str(uuid.uuid4()),
        "model": "claude-opus-5-5",
    }
    return call(server, "propose", arguments)


def flags(db: psycopg.Connection, entry_id: str) -> list[Any]:
    row = db.execute(
        "SELECT duplicate_candidates, duplicate_of FROM entries WHERE id = %s", (entry_id,)
    ).fetchone()
    assert row
    return [row[0], row[1]]


def canonical(db: psycopg.Connection, reporter: uuid.UUID) -> tuple[uuid.UUID, str]:
    word = f"dupcache{uuid.uuid4().hex[:6]}"
    symptom = f"{word} CacheConfig ignores ttl: expire_after is None, entries never expire"
    return factories.entry(db, reporter, symptom=symptom), word


# Detection on propose


def test_a_near_duplicate_is_flagged_and_the_agent_sees_no_difference(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    existing, word = canonical(db, reporter)

    result = propose(server, f"{word} CacheConfig ttl ignored: expire_after None, never expire")

    assert set(result) == {"entry_id", "status", "redaction_report", "store"}
    candidates, linked = flags(db, result["entry_id"])
    assert candidates[0]["entry_id"] == str(existing)
    assert candidates[0]["score"] >= DEFAULT_THRESHOLD
    assert candidates[0]["retry"] is False
    assert linked is None


def test_distinct_entries_sharing_some_words_are_not_flagged(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    _, word = canonical(db, reporter)
    result = propose(server, f"{word} connection pool exhausted after deploy, timeouts")
    assert flags(db, result["entry_id"]) == [[], None]


def test_a_second_proposal_from_the_same_task_is_marked_a_retry(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    del reporter
    task = str(uuid.uuid4())
    word = f"retry{uuid.uuid4().hex[:6]}"
    first = propose(server, f"{word} CacheConfig ignores ttl so entries never expire", task)
    second = propose(server, f"{word} CacheConfig ignores ttl, entries never expire", task)
    candidates, _ = flags(db, second["entry_id"])
    assert (candidates[0]["entry_id"], candidates[0]["retry"]) == (first["entry_id"], True)


def test_link_mode_preselects_the_best_candidate(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONS_DUPLICATE_ACTION", "link")
    existing, word = canonical(db, reporter)
    result = propose(server, f"{word} CacheConfig ignores ttl: expire_after None, never expire")
    assert flags(db, result["entry_id"])[1] == existing


def test_settings_are_validated() -> None:
    assert DuplicateSettings.from_environment({}) == DuplicateSettings(DEFAULT_THRESHOLD, "flag")
    custom = {"COMMONS_DUPLICATE_THRESHOLD": "0.6", "COMMONS_DUPLICATE_ACTION": "LINK"}
    assert DuplicateSettings.from_environment(custom) == DuplicateSettings(0.6, "link")
    for bad in ({"COMMONS_DUPLICATE_ACTION": "return"}, {"COMMONS_DUPLICATE_THRESHOLD": "2"}):
        with pytest.raises(InvalidInputError):
            DuplicateSettings.from_environment(bad)


# Review actions


def test_merging_a_pending_duplicate_rejects_it_and_its_wording_finds_the_canonical_entry(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    existing, word = canonical(db, reporter)
    alias = f"{word} zzalias quickcache stale forever"
    dup = propose(server, alias)["entry_id"]

    duplicates.merge(db, uuid.UUID(dup), existing)

    row = db.execute(
        "SELECT status::text, superseded_by FROM entries WHERE id = %s", (dup,)
    ).fetchone()
    assert row == ("rejected", existing)
    search = {"query": f"{word} zzalias", "task_id": str(uuid.uuid4()), "model": "m"}
    found = [r["entry_id"] for r in call(server, "search", search)["data"]]
    assert found[0] == str(existing)
    with pytest.raises(InvalidInputError, match="itself"):
        duplicates.merge(db, existing, existing)


def test_merging_an_approved_duplicate_retires_it(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    existing, _ = canonical(db, reporter)
    other, _ = canonical(db, reporter)
    duplicates.merge(db, other, existing)
    row = db.execute("SELECT status::text, superseded_by FROM entries WHERE id = %s", (other,))
    assert row.fetchone() == ("retired", existing)


def test_keep_both_clears_the_flag_for_good(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    existing, word = canonical(db, reporter)
    dup = propose(server, f"{word} CacheConfig ignores ttl: expire_after None, never expire")
    duplicates.keep_both(db, uuid.UUID(dup["entry_id"]), existing)
    assert flags(db, dup["entry_id"]) == [[], None]
    assert db.execute("SELECT count(*) FROM distinct_pairs").fetchone() == (1,)


def test_the_queue_shows_the_flag_and_merges_from_it(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    existing, word = canonical(db, reporter)
    dup = propose(server, f"{word} CacheConfig ignores ttl: expire_after None, never expire")

    client = dashboard(db)
    assert "Looks like" in client.get("/queue").text
    assert uuid.UUID(dup["entry_id"]) in insights.duplicate_flags(db)
    merged = client.post(
        f"/entries/{dup['entry_id']}/duplicate",
        data={"other": str(existing), "action": "merge"},
        headers={"origin": ORIGIN, "referer": f"{ORIGIN}/queue"},
    )
    assert "Merged" in merged.headers["location"]


@pytest.mark.parametrize("status", ["approved", "pending"])
def test_the_queue_shows_each_candidates_cause_and_fix_under_the_proposals(
    db: psycopg.Connection, reporter: uuid.UUID, status: str
) -> None:
    tag = uuid.uuid4().hex[:8]
    old = factories.entry(
        db, reporter, status=status, cause=f"{tag} old cause", fix=f"{tag} old fix"
    )
    new = factories.entry(
        db, reporter, status="pending", cause=f"{tag} new cause", fix=f"{tag} new fix"
    )
    save_candidates(db, new, [Candidate(old, 0.8, same_task=False)], None)

    item = card(dashboard(db).get("/queue").text, f'id="{new}"')

    order = [
        item.index(f"{tag} {text}") for text in ("new cause", "new fix", "old cause", "old fix")
    ]
    assert order == sorted(order)


def test_keeping_both_lists_each_entry_on_the_others_page(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    first, word = canonical(db, reporter)
    second = factories.entry(db, reporter, status="pending", symptom=f"{word} pool exhausted")
    db.execute(
        "INSERT INTO ranking_snapshots (entry_id, computed_at, score, retrieval_count_30d) "
        "VALUES (%(e)s, now() - interval '1 day', 0.41, 0), (%(e)s, now(), 0.73, 0)",
        {"e": first},
    )
    client = dashboard(db)
    assert "Kept distinct from" not in client.get(f"/entries/{first}").text

    kept = client.post(
        f"/entries/{second}/duplicate",
        data={"other": str(first), "action": "keep"},
        headers={"origin": ORIGIN, "referer": f"{ORIGIN}/queue"},
    )

    assert "Kept" in kept.headers["location"]
    pages = ((first, second, "pending", "\N{EN DASH}"), (second, first, "approved", "0.73"))
    for page, other, status, score in pages:
        listed = card(client.get(f"/entries/{page}").text, "Kept distinct from")
        assert f'href="/entries/{other}"' in listed
        assert f'class="badge {status}"' in listed
        assert f"score {score}" in listed


def dashboard(db: psycopg.Connection) -> TestClient:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return TestClient(build_app(test_connection), base_url=ORIGIN, follow_redirects=False)


def card(html: str, start: str) -> str:
    """Return the page from `start` to the end of its card."""
    rest = html[html.index(start) :]
    return rest[: rest.index("</section>")]


# Tuning (decision 0027 step 5)


@pytest.fixture(scope="module")
def judged_db() -> Iterator[psycopg.Connection]:
    name = f"commons_dups_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with psycopg.connect(make_conninfo(ADMIN_URL, dbname=name)) as conn:
            apply_migrations(conn)
            yield conn
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_the_default_threshold_keeps_false_merges_under_one_in_twenty(
    judged_db: psycopg.Connection,
) -> None:
    """Labeled pairs: each judged query is a duplicate of its entry; negatives and each entry
    proposed again with itself excluded are distinct.

    Scored as `propose` does: the proposal is stored first, so its own words count in the
    rarity weights (a word no other entry has is evidence it's different). Scoring without it,
    as before the 2026-10-31 audit, put the catch rate at 0.933 instead of 0.767 at 0.4.
    """
    ids = load_judged(judged_db)
    data = json.loads(JUDGED.read_text(encoding="utf-8"))
    owner = upsert_reporter(judged_db, "search-eval", "entry")

    def proposed(text: str) -> tuple[uuid.UUID | None, float]:
        with judged_db.transaction(force_rollback=True):
            draft = factories.entry(judged_db, owner, symptom=text, status="pending")
            found = similar_entries(judged_db, text, [draft], 1)
        return found[0] if found else (None, 0.0)

    def again(entry: dict[str, Any]) -> tuple[uuid.UUID | None, float]:
        found = similar_entries(judged_db, entry["symptom"], [ids[entry["id"]]], 1)
        return found[0] if found else (None, 0.0)

    same = [(proposed(q), ids[e["id"]]) for e in data["entries"] for q in e["queries"]]
    distinct = [proposed(q) for q in data["negatives"]] + [again(e) for e in data["entries"]]
    caught = sum(s >= DEFAULT_THRESHOLD and got == want for (got, s), want in same) / len(same)
    false = sum(s >= DEFAULT_THRESHOLD for _, s in distinct) / len(distinct)
    assert false <= 1 / 20
    assert caught >= 0.83


def test_an_entry_cannot_be_kept_apart_from_itself(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = factories.entry(db, reporter)
    with pytest.raises(InvalidInputError, match="itself"):
        duplicates.keep_both(db, entry_id, entry_id)

"""Search relevance on the judged set, and the scoring rules behind it (decision 0025)."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastmcp import FastMCP
from psycopg import sql
from psycopg.conninfo import make_conninfo
from tools.search_eval import evaluate, load_judged

import factories
from commons.domain.search import Lexemes, major_minor, relevance, version_factor
from commons.store.migrate import apply_migrations
from conftest import ADMIN_URL
from mcp_client import call

# The numbers the shipped search reached when this set was made (v0 was 0.243 and 0.983).
# A change that lowers either fails here; update them when a change raises them.
PRECISION_AT_5 = 0.899
RECALL_AT_5 = 0.983


@pytest.fixture(scope="module")
def judged_db() -> Iterator[psycopg.Connection]:
    """A database holding only the judged entries: other tests' rows would skew rarity."""
    name = f"commons_judged_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with psycopg.connect(make_conninfo(ADMIN_URL, dbname=name)) as conn:
            apply_migrations(conn)
            yield conn
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def test_the_judged_set_holds_its_numbers(judged_db: psycopg.Connection) -> None:
    metrics = evaluate(judged_db, load_judged(judged_db))
    assert metrics.queries == 72
    assert round(metrics.precision_at_5, 3) >= PRECISION_AT_5
    assert round(metrics.recall_at_5, 3) >= RECALL_AT_5


def words(query: str, symptom: str, body: str = "", **frequency: int) -> Lexemes:
    return Lexemes(
        query=frozenset(query.split()),
        symptom=frozenset(symptom.split()),
        body=frozenset(body.split()),
        frequency={w: frequency.get(w, 1) for w in (query + " " + symptom + " " + body).split()},
        entries=20,
        generic=frozenset({"error", "fail"}),
    )


def test_repeating_the_symptom_scores_near_one_even_inside_noise() -> None:
    exact = relevance(words("pg_config execut found", "pg_config execut found"))
    # "nois" and "log" are words the store has never seen: they don't count against it.
    noisy = words("pg_config execut found nois log", "pg_config execut found")
    noisy = Lexemes(**{**noisy.__dict__, "frequency": {**noisy.frequency, "nois": 0, "log": 0}})
    assert exact == pytest.approx(1.0)
    assert relevance(noisy) == pytest.approx(1.0)


def test_sharing_only_common_words_scores_near_zero() -> None:
    shared = words("error fail build", "error pg_config execut found", error=15, fail=12)
    assert relevance(shared) < 0.1


def test_a_partial_paraphrase_lands_in_between() -> None:
    paraphrase = relevance(words("pg_config miss instal", "pg_config execut found", "instal"))
    assert 0.25 < paraphrase < 0.9


@pytest.mark.parametrize(
    ("version", "parsed"),
    [("2.1.0", (2, 1)), ("^18.2.0", (18, 2)), (">=3.0,<4", (3, 0)), ("v5", (5, None)), ("x", None)],
)
def test_major_minor(version: str, parsed: tuple[int, int | None] | None) -> None:
    assert major_minor(version) == parsed


def test_versions_boost_the_same_minor_and_demote_another_major() -> None:
    asked = {"Pydantic": "2.5.3"}
    assert version_factor(asked, {"pydantic": "2.5.0"}) == 1.2
    assert version_factor(asked, {"pydantic": "2.1"}) == 1.0
    assert version_factor(asked, {"pydantic": "1.10.2"}) == 0.5
    assert version_factor(asked, {"fastapi": "0.100"}) == 1.0
    assert version_factor(None, {"pydantic": "1.10"}) == 1.0


def test_search_ranks_the_callers_version_first(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    symptom = f"verboost{uuid.uuid4().hex[:6]} BaseSettings import moved"
    old = factories.entry(
        db, reporter, symptom=symptom, environment={"libraries": {"pydantic": "1.10"}}
    )
    new = factories.entry(
        db, reporter, symptom=symptom, environment={"libraries": {"pydantic": "2.5"}}
    )
    arguments = {
        "query": symptom,
        "task_id": str(uuid.uuid4()),
        "environment": {"libraries": {"pydantic": "2.5.3"}},
        "model": "claude-opus-5-5",
    }
    found = [r["entry_id"] for r in call(server, "search", arguments)["data"]]
    assert found[:2] == [str(new), str(old)]

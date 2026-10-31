"""Entry lifecycle (decision 0026): revisions, retirement, supersession, staleness."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

import factories
from commons.domain.entries import Proposal
from commons.domain.errors import InvalidInputError
from commons.domain.lifecycle import FixedIn, is_older, shown_despite_fix
from commons.domain.reviews import ReviewRequest
from commons.service import lifecycle, reviews
from commons.store import insights
from commons.store.connection import Connection
from commons.web.app import build_app
from mcp_client import call

ORIGIN = "http://127.0.0.1:8765"
SECRET = "ghp_" + "a" * 36


def found(server: FastMCP, query: str, libraries: dict[str, str] | None = None) -> list[Any]:
    arguments: dict[str, Any] = {"query": query, "task_id": str(uuid.uuid4()), "model": "m"}
    if libraries:
        arguments["environment"] = {"libraries": libraries}
    data: list[Any] = call(server, "search", arguments)["data"]
    return data


def text(entry_id: uuid.UUID, **changes: str) -> Proposal:
    values = {"symptom": "s", "cause": "c", "fix": "f", **changes}
    return Proposal(
        symptom=values["symptom"],
        environment={"language": "python 3.12"},
        cause=values["cause"],
        fix=values["fix"],
        domains=("test",),
        repro=None,
        task_id=entry_id,
    )


def new_entry(db: psycopg.Connection, reporter: uuid.UUID, status: str = "approved") -> uuid.UUID:
    word = f"life{uuid.uuid4().hex[:8]}"
    return factories.entry(db, reporter, symptom=f"{word} broke the build", status=status)


def symptom(db: psycopg.Connection, entry_id: uuid.UUID) -> str:
    row = db.execute("SELECT symptom FROM entries WHERE id = %s", (entry_id,)).fetchone()
    assert row
    return str(row[0])


# Revisions


def test_every_entry_starts_as_revision_one(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    entry_id = new_entry(db, reporter)
    assert [(r["revision"], r["edited_by"]) for r in insights.entry_revisions(db, entry_id)] == [
        (1, None)
    ]


def test_an_edit_is_a_redacted_new_revision_and_retrievals_record_it(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = new_entry(db, reporter)
    word = symptom(db, entry_id).split()[0]

    revision = lifecycle.edit_entry(
        db,
        entry_id,
        text(entry_id, symptom=f"{word} broke the build on CI", fix=f"Rotate {SECRET} then pin"),
        "narrow the symptom",
    )

    assert revision == 2
    history = insights.entry_revisions(db, entry_id)
    assert [(r["revision"], r["reason"]) for r in history] == [
        (2, "narrow the symptom"),
        (1, None),
    ]
    assert SECRET not in history[0]["fix"]
    assert found(server, f"{word} broke the build on CI")[0]["entry_id"] == str(entry_id)
    shown = db.execute("SELECT revision FROM retrievals WHERE entry_id = %s", (entry_id,))
    assert shown.fetchall() == [(2,)]


def test_edits_need_a_reason_and_valid_text(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    entry_id = new_entry(db, reporter)
    with pytest.raises(InvalidInputError, match="why"):
        lifecycle.edit_entry(db, entry_id, text(entry_id), " ")
    with pytest.raises(InvalidInputError, match="fix must not be empty"):
        lifecycle.edit_entry(db, entry_id, text(entry_id, fix=" "), "typo")


# Retirement and supersession


def test_retiring_hides_an_entry_and_restoring_brings_it_back(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = new_entry(db, reporter)
    query = symptom(db, entry_id)

    lifecycle.retire(db, entry_id, "the library fixed it")

    assert found(server, query) == []
    decisions = [r["decision"] for r in insights.entry_reviews(db, entry_id)]
    assert decisions[-1] == "retire"
    with pytest.raises(InvalidInputError, match="retired"):
        reviews.review(db, ReviewRequest(entry_id, "approve", None, "human"))

    lifecycle.restore(db, entry_id, "still happens on 3.x")

    assert [r["entry_id"] for r in found(server, query)] == [str(entry_id)]
    with pytest.raises(InvalidInputError, match="Only retired"):
        lifecycle.restore(db, entry_id, "again")


def test_a_fixed_entry_still_shows_to_callers_on_an_older_version(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = new_entry(db, reporter)
    query = symptom(db, entry_id)
    lifecycle.retire(db, entry_id, "fixed upstream", fixed_in=FixedIn.parse("Jinja2==3.1.0"))

    older = found(server, query, {"jinja2": "3.0.3"})
    assert [r["entry_id"] for r in older] == [str(entry_id)]
    assert "fixed in jinja2 3.1.0" in older[0]["provenance"]
    assert found(server, query, {"jinja2": "3.1.2"}) == []
    assert found(server, query) == []


def test_a_superseded_entry_never_shows_and_its_replacement_must_be_approved(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    old = new_entry(db, reporter)
    query = symptom(db, old)
    pending = new_entry(db, reporter, status="pending")
    with pytest.raises(InvalidInputError, match="approved"):
        lifecycle.retire(db, old, "dup", superseded_by=pending)
    with pytest.raises(InvalidInputError, match="itself"):
        lifecycle.retire(db, old, "dup", superseded_by=old)
    replacement = new_entry(db, reporter)

    lifecycle.retire(db, old, "duplicate", superseded_by=replacement)

    assert str(old) not in [r["entry_id"] for r in found(server, query, {"jinja2": "1.0"})]
    row = db.execute("SELECT superseded_by FROM entries WHERE id = %s", (old,)).fetchone()
    assert row == (replacement,)
    with pytest.raises(InvalidInputError, match="Only approved"):
        lifecycle.retire(db, pending, "not live")


@pytest.mark.parametrize(
    ("version", "than", "order"),
    [
        ("3.0.3", "3.1.0", "older"),
        ("3.1", "3.1.0", "same"),
        ("3.10", "3.9", "newer"),
        ("x", "1", "?"),
    ],
)
def test_version_order(version: str, than: str, order: str) -> None:
    assert is_older(version, than) is (order == "older")


def test_shown_despite_fix_needs_the_same_library_on_an_older_version() -> None:
    fixed = {"library": "jinja2", "version": "3.1.0"}
    assert shown_despite_fix(fixed, {"Jinja2": "2.11"})
    assert not shown_despite_fix(fixed, {"flask": "2.11"})
    assert not shown_despite_fix(fixed, None)
    with pytest.raises(InvalidInputError):
        FixedIn.parse("jinja2")


# Staleness


def test_stale_candidates_are_retrieved_but_never_successfully_used(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    stale = new_entry(db, reporter)
    healthy = new_entry(db, reporter)
    db.execute(
        "INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, rank_position, "
        "score_at_retrieval) SELECT %s, %s, created_by_model, %s, 'q', 1, 0.5 FROM entries "
        "WHERE id = %s",
        (stale, reporter, uuid.uuid4(), stale),
    )
    factories.outcome(db, healthy, reporter)

    listed = {row["id"] for row in insights.stale_candidates(db, limit=500)}

    assert stale in listed
    assert healthy not in listed


# Dashboard


@pytest.fixture
def dashboard(db: psycopg.Connection, reporter: uuid.UUID) -> TestClient:
    del reporter

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return TestClient(build_app(test_connection), base_url=ORIGIN, follow_redirects=False)


def test_dashboard_edits_retires_and_restores(
    dashboard: TestClient, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    entry_id = new_entry(db, reporter)
    page = f"{ORIGIN}/entries/{entry_id}"
    headers = {"origin": ORIGIN, "referer": page}
    form = {"symptom": "edited symptom", "cause": "c", "fix": "f", "repro": "", "reason": "clarify"}

    edited = dashboard.post(f"/entries/{entry_id}/edit", data=form, headers=headers)
    retired = dashboard.post(
        f"/entries/{entry_id}/retire",
        data={"reason": "fixed", "fixed_in": "jinja2==3.1.0", "replacement": ""},
        headers=headers,
    )

    assert "corrected=Saved+revision+2" in edited.headers["location"]
    assert "corrected=Retired" in retired.headers["location"]
    shown = dashboard.get(f"/entries/{entry_id}").text
    assert "fixed in jinja2 3.1.0" in shown
    assert "revision 2" in shown
    restored = dashboard.post(
        f"/entries/{entry_id}/restore", data={"reason": "back"}, headers=headers
    )
    assert "corrected=Restored" in restored.headers["location"]
    bad = dashboard.post(f"/entries/{entry_id}/edit", data=form, headers={"origin": "http://x"})
    assert bad.status_code == 403

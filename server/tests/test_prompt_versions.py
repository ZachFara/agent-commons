"""Searches and proposals record the agent-facing text the server served (decision MIT-566)."""

import asyncio
import dataclasses
import hashlib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from fastmcp import Client, FastMCP

import factories
from commons.domain.entries import Proposal
from commons.domain.prompts import prompt_version
from commons.mcp.server import UNTRUSTED_NOTE, build_server
from commons.service import agent_text, proposals
from commons.store.connection import Connection
from mcp_client import call

pytestmark = pytest.mark.usefixtures("reporter")


def build(db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, variant: str) -> FastMCP:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", variant)

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_server(test_connection)


def served_hash(server: FastMCP) -> str:
    """Hash what a client reads from `server`, computed here rather than by the server."""

    async def run() -> dict[str, str]:
        async with Client(server) as client:
            assert client.instructions
            surfaces = {"instructions": client.instructions}
            for tool in await client.list_tools():
                surfaces[f"tool/{tool.name}/description"] = tool.description or ""
                schema = json.dumps(tool.input_schema, sort_keys=True)
                surfaces[f"tool/{tool.name}/input_schema"] = schema
                surfaces[f"tool/{tool.name}/_meta"] = json.dumps(tool.meta, sort_keys=True)
            for prompt in await client.list_prompts():
                surfaces[f"prompt/{prompt.name}/description"] = prompt.description or ""
                rendered = await client.get_prompt(prompt.name)
                content = rendered.messages[0].content
                assert content.type == "text"
                surfaces[f"{prompt.name}_prompt"] = content.text
        return surfaces

    surfaces = asyncio.run(run())
    surfaces["note/untrusted"] = UNTRUSTED_NOTE
    surfaces["note/empty"] = agent_text.variant().invite_note or UNTRUSTED_NOTE
    canonical = json.dumps(surfaces, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def search(server: FastMCP, query: str) -> tuple[uuid.UUID, dict[str, Any]]:
    task_id = uuid.uuid4()
    result = call(server, "search", {"query": query, "task_id": str(task_id), "model": "m"})
    return task_id, result


def searched_version(db: psycopg.Connection, server: FastMCP) -> uuid.UUID:
    """Search through `server` and return the prompt version its searches row points at."""
    task_id, _result = search(server, f"nothing-{uuid.uuid4().hex}")
    row = db.execute(
        "SELECT prompt_version_id FROM searches WHERE task_id = %s", (task_id,)
    ).fetchone()
    assert row
    assert isinstance(row[0], uuid.UUID)
    return row[0]


def version_row(db: psycopg.Connection, version_id: uuid.UUID) -> tuple[Any, ...]:
    row = db.execute(
        "SELECT hash, label, surfaces FROM prompt_versions WHERE id = %s", (version_id,)
    ).fetchone()
    assert row
    return row


def test_mcp_search_and_its_retrievals_point_at_the_served_version(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    word = f"promptversion{uuid.uuid4().hex[:8]}"
    factories.entry(db, reporter, symptom=f"{word} one")
    factories.entry(db, reporter, symptom=f"{word} two")
    task_id, result = search(server, word)
    assert set(result) == {"note", "data", "store"}  # the response gains no field
    assert len(result["data"]) == 2

    searches = db.execute(
        "SELECT prompt_version_id FROM searches WHERE task_id = %s", (task_id,)
    ).fetchall()
    retrievals = db.execute(
        "SELECT prompt_version_id FROM retrievals WHERE task_id = %s", (task_id,)
    ).fetchall()
    assert len(searches) == 1
    assert len(retrievals) == 2
    version_id = searches[0][0]
    assert {row[0] for row in retrievals} == {version_id}
    hash_, label, surfaces = version_row(db, version_id)
    assert hash_ == served_hash(server)
    assert label == f"baseline:{agent_text.SHIPPED}"
    assert surfaces["instructions"] == agent_text.VARIANTS["baseline"].instructions


def test_mcp_propose_points_the_entry_at_the_same_version(
    server: FastMCP, db: psycopg.Connection
) -> None:
    searched = searched_version(db, server)
    arguments = {
        "symptom": "ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
        "environment": {"language": "python 3.12", "libraries": {"markupsafe": "2.1.0"}},
        "cause": "markupsafe 2.1 removed soft_unicode, which Jinja2 2.x still imports.",
        "fix": "Upgrade Jinja2 to 3.x, or pin markupsafe<2.1.",
        "domains": ["python", "packaging"],
        "task_id": str(uuid.uuid4()),
    }
    result = call(server, "propose", arguments)
    assert set(result) == {"entry_id", "status", "redaction_report", "store"}
    row = db.execute(
        "SELECT prompt_version_id FROM entries WHERE id = %s", (result["entry_id"],)
    ).fetchone()
    assert row == (searched,)


def test_one_row_per_distinct_text(db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    first = searched_version(db, build(db, monkeypatch, "baseline"))
    again = searched_version(db, build(db, monkeypatch, "baseline"))
    previous = searched_version(db, build(db, monkeypatch, "previous"))
    assert first == again
    assert previous != first
    assert version_row(db, previous)[1] == f"previous:{agent_text.PREVIOUS_SHIPPED}"


def test_text_never_served_does_not_make_a_new_version(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `start` differs from `original` only in the SessionStart line, which the server never sends
    original = build(db, monkeypatch, "original")
    original_hash = served_hash(original)
    start = build(db, monkeypatch, "start")
    assert served_hash(start) == original_hash
    assert searched_version(db, original) == searched_version(db, start)
    assert version_row(db, searched_version(db, start))[0] == original_hash


def test_one_argument_description_changes_the_version(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = agent_text.VARIANTS["baseline"]
    shipped = searched_version(db, build(db, monkeypatch, "baseline"))
    changed = dataclasses.replace(baseline, arguments=(("search", "query", "The error."),))
    monkeypatch.setitem(agent_text.VARIANTS, "baseline", changed)
    tweaked = searched_version(db, build(db, monkeypatch, "baseline"))
    assert tweaked != shipped
    surfaces = version_row(db, tweaked)[2]
    assert '"description": "The error."' in surfaces["tool/search/input_schema"]


def test_the_order_of_surfaces_does_not_change_the_hash() -> None:
    surfaces = {"instructions": "a", "note/empty": "b", "tool/search/description": "c"}
    reordered = dict(reversed(surfaces.items()))
    assert list(reordered) != list(surfaces)
    assert prompt_version("x", surfaces).hash == prompt_version("y", reordered).hash
    assert prompt_version("x", {**surfaces, "note/empty": "b "}).hash != (
        prompt_version("x", surfaces).hash
    )


def test_switched_on_extras_are_part_of_the_version_and_its_label(
    db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = searched_version(db, build(db, monkeypatch, "invite"))
    for setting in ("COMMONS_RECORD_PROMPT", "COMMONS_TURN_NUDGE", "COMMONS_SESSION_FEEDBACK"):
        monkeypatch.setenv(setting, "on")
    server = build(db, monkeypatch, "invite")
    extras = searched_version(db, server)
    assert extras != plain
    hash_, label, surfaces = version_row(db, extras)
    assert label == "invite+turn-nudge+record"
    assert surfaces["note/empty"] == agent_text.INVITE_NOTE
    assert surfaces["turn_nudge"] == agent_text.TURN_NUDGE  # the template, not a task's text
    assert surfaces["session_feedback"] == "on"
    # The version is what a client reads plus the two surfaces only hooks deliver.
    hooks_only = {"turn_nudge", "session_feedback"}
    read = {key: text for key, text in surfaces.items() if key not in hooks_only}
    assert surfaces["record_prompt"] == agent_text.RECORD_PROMPT
    assert served_hash(server) == prompt_version("", read).hash != hash_


def test_writes_outside_the_mcp_tools_record_no_version(db: psycopg.Connection) -> None:
    proposal = Proposal(
        symptom="ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
        environment={"language": "python 3.12", "libraries": {"markupsafe": "2.1.0"}},
        cause="markupsafe 2.1 removed soft_unicode, which Jinja2 2.x still imports.",
        fix="Upgrade Jinja2 to 3.x, or pin markupsafe<2.1.",
        domains=("python", "packaging"),
        repro=None,
        task_id=uuid.uuid4(),
    )
    result = proposals.propose(db, proposal, "m")
    row = db.execute(
        "SELECT prompt_version_id FROM entries WHERE id = %s", (result.entry_id,)
    ).fetchone()
    assert row == (None,)


def test_a_hash_must_be_64_hex_characters(db: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO prompt_versions (hash, label, surfaces) VALUES (%s, 'x', '{}')",
            ("ABC",),
        )

"""Decision 0034: the gated helpers for reliable propose and report. All ship switched off."""

import asyncio
import json
import re
import uuid
from pathlib import Path

import psycopg
import pytest
from fastmcp import Client

import factories
from commons.domain.entries import Proposal, validate_proposal
from commons.mcp.server import build_server
from commons.service import agent_text
from commons.service.hook_install import commons_hooks
from commons.service.hooks import finish_turn
from commons.service.identity import acting_as
from commons.store.tasks import start_task

PLUGIN = Path(__file__).parents[2] / "client" / "plugin"


def task_with_runs(db: psycopg.Connection, reporter: uuid.UUID, codes: list[int]) -> str:
    session = f"turn-{uuid.uuid4().hex[:8]}"
    task_id = start_task(db, session, reporter, None)
    for code in codes:
        db.execute(
            "INSERT INTO task_events (task_id, kind, data, created_at) "
            "VALUES (%s, 'test_run', %s, clock_timestamp())",
            (task_id, json.dumps({"command": "pytest", "exit_code": code})),
        )
    return session


def test_the_turn_nudge_is_off_by_default(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    session = task_with_runs(db, reporter, [1, 0])
    with acting_as(reporter):
        result = finish_turn(db, session, None, {})
    assert result is not None
    assert result.nudge is None


def test_the_turn_nudge_fires_once_per_qualifying_turn(
    db: psycopg.Connection, reporter: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONS_TURN_NUDGE", "on")
    session = task_with_runs(db, reporter, [1, 0])
    with acting_as(reporter):
        first = finish_turn(db, session, None, {})
        second = finish_turn(db, session, None, {})
    assert first is not None
    assert first.nudge == agent_text.TURN_NUDGE.format(task_id=first.task_id)
    assert second is not None
    assert second.nudge is None  # nothing failed and passed in this turn


def test_a_turn_that_searched_or_proposed_is_not_nudged(
    db: psycopg.Connection, reporter: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONS_TURN_NUDGE", "on")
    session = task_with_runs(db, reporter, [1, 0])
    row = db.execute("SELECT id FROM tasks WHERE session_id = %s", (session,)).fetchone()
    assert row
    factories.entry(db, reporter, status="pending", task_id=row[0])
    with acting_as(reporter):
        result = finish_turn(db, session, None, {})
    assert result is not None
    assert result.nudge is None


def prompts(monkeypatch: pytest.MonkeyPatch, setting: str | None) -> list[str]:
    if setting:
        monkeypatch.setenv("COMMONS_RECORD_PROMPT", setting)

    async def listed() -> list[str]:
        async with Client(build_server()) as client:
            return [prompt.name for prompt in await client.list_prompts()]

    return asyncio.run(listed())


def test_the_record_prompt_is_offered_only_when_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    assert prompts(monkeypatch, None) == []
    assert prompts(monkeypatch, "on") == ["record"]


def test_the_shipped_text_is_the_wider_trigger_and_differs_only_in_the_propose_rule() -> None:
    previous, shipped = agent_text.VARIANTS["previous"], agent_text.resolve(agent_text.SHIPPED)
    assert (
        shipped == agent_text.resolve(agent_text.WIDER_TRIGGER) == agent_text.VARIANTS["baseline"]
    )
    assert "a bare error page" in shipped.instructions
    assert "a bare error page" not in previous.instructions
    assert (shipped.propose, shipped.search, shipped.start_line) == (
        previous.propose,
        previous.search,
        previous.start_line,
    )


SKILL = PLUGIN / "skills" / "agent-commons" / "SKILL.md"
DESCRIPTION_LIMIT = 300  # short, so it doesn't crowd the skills list (decision 0034)
BODY_LIMIT = 4000


def test_the_skill_stays_inside_its_budgets_and_its_example_is_a_valid_proposal() -> None:
    text = SKILL.read_text(encoding="utf-8")
    front, body = text.split("---\n", 2)[1:]
    meta = dict(line.split(": ", 1) for line in front.strip().splitlines())
    assert meta["name"] == "agent-commons"
    assert len(meta["description"]) <= DESCRIPTION_LIMIT
    assert len(body) <= BODY_LIMIT
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", body, re.DOTALL)]
    example = next(b for b in blocks if "symptom" in b)
    validate_proposal(
        Proposal(
            symptom=example["symptom"],
            environment=example["environment"],
            cause=example["cause"],
            fix=example["fix"],
            domains=tuple(example["domains"]),
            repro=None,
            task_id=uuid.uuid4(),
        )
    )


def test_the_plugin_hooks_match_what_join_installs() -> None:
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert hooks == commons_hooks(Path("commons"))

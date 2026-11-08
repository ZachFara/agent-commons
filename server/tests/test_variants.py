"""The experiment conditions (decision 0023, T35): each changes one surface the agent sees.

Production stays on `baseline`; a variant is selected per process with
COMMONS_INSTRUCTIONS_VARIANT. Hooks and the MCP server both read it.
"""

import argparse
import dataclasses
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP
from tools import agent_eval
from tools.eval.holdout import HOLDOUT, RETIRED, WHENEVER_IGNORE_DST
from tools.eval.model import Scenario
from tools.eval.scenarios import SCENARIOS

import factories
from commons.domain.entries import Proposal, validate_proposal
from commons.domain.outcomes import ObservedRun
from commons.mcp.server import build_server
from commons.service import agent_text
from commons.service.hooks import fixed_failing_tests
from commons.store.connection import Connection
from hook_env import HookEnv
from mcp_client import call
from test_hook_stop import Session

AT = datetime(2026, 10, 25, tzinfo=UTC)
SKILL = Path(__file__).parents[2] / "client" / "plugin" / "skills" / "agent-commons" / "SKILL.md"


def run(exit_code: int, minutes: int) -> ObservedRun:
    return ObservedRun("pytest -q", exit_code, AT + timedelta(minutes=minutes))


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([], False),
        ([run(0, 0)], False),  # passed from the start: nothing was fixed
        ([run(1, 0)], False),  # still failing
        ([run(1, 0), run(0, 5)], True),
        ([run(1, 0), run(1, 3), run(0, 5)], True),
        ([run(0, 0), run(1, 5)], False),  # broke it, never fixed it
        ([run(1, 0), run(0, 2), run(1, 4)], True),  # fixed once, regressed later: still counts
    ],
)
def test_fixed_failing_tests(runs: list[ObservedRun], *, expected: bool) -> None:
    assert fixed_failing_tests(runs) is expected


@pytest.fixture
def nudge_session(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "nudge")
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init()
    return Session(env, database_url, tmp_path)


def test_nudge_fires_once_when_tests_were_fixed_without_a_search(nudge_session: Session) -> None:
    nudge_session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")
    nudge_session.tool("Edit", {"file_path": "/p/app.py", "new_string": "fixed"})
    nudge_session.tool("Bash", {"command": "pytest -q"})

    output = nudge_session.stop()

    assert output is not None
    assert output["decision"] == "block"
    assert "went from failing to passing" in output["reason"]
    assert "`search` was never called" in output["reason"]
    assert nudge_session.task_id in output["reason"]
    assert nudge_session.stop(active=True) is None  # never twice


def test_nudge_fires_on_piped_test_runs(nudge_session: Session) -> None:
    """Found by tools/agent_eval.py: the nudge never fired, since agents pipe through tail."""
    piped = {"command": "uv run pytest -q 2>&1 | tail -20"}
    nudge_session.tool("Bash", piped, stdout="FAILED t.py::x\n1 failed, 1 passed in 0.2s")
    nudge_session.tool("Edit", {"file_path": "/p/app.py", "new_string": "fixed"})
    nudge_session.tool("Bash", piped, stdout="2 passed in 0.2s")

    output = nudge_session.stop()

    assert output is not None
    assert "went from failing to passing" in output["reason"]


def test_nudge_stays_quiet_when_the_agent_searched(nudge_session: Session) -> None:
    nudge_session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")
    nudge_session.search()
    nudge_session.tool("Bash", {"command": "pytest -q"})
    nudge_session.report("tests_passed")
    assert nudge_session.stop() is None


def test_nudge_stays_quiet_when_nothing_was_fixed(nudge_session: Session) -> None:
    nudge_session.tool("Bash", {"command": "pytest -q"})  # passed all along
    assert nudge_session.stop() is None


def test_report_prompt_takes_precedence_over_the_nudge(nudge_session: Session) -> None:
    nudge_session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")
    nudge_session.search()
    nudge_session.tool("Bash", {"command": "pytest -q"})
    output = nudge_session.stop()
    assert output is not None
    assert "`report`" in output["reason"]


def test_baseline_never_nudges(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "baseline")
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init()
    session = Session(env, database_url, tmp_path)
    session.tool("Bash", {"command": "pytest -q"}, error="Exit code 1\nFAILED")
    session.tool("Bash", {"command": "pytest -q"})
    assert session.stop() is None


def test_start_variant_adds_the_search_request_to_the_session_context(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "start")
    env = HookEnv(database_url, tmp_path, monkeypatch)
    env.init()
    out = env.run("session-start", {"session_id": str(uuid.uuid4()), "source": "startup"})
    assert out is not None
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "task_id for this session" in context
    assert "call `search` with the error text before fixing it" in context


@pytest.fixture
def invite_server(db: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> FastMCP:
    """A server built under the invite variant, on the test's rolled-back connection."""
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "invite")

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_server(test_connection)


@pytest.mark.usefixtures("reporter")
def test_invite_variant_points_an_empty_search_at_propose(invite_server: FastMCP) -> None:
    arguments: dict[str, Any] = {
        "query": f"nothing-{uuid.uuid4().hex}",
        "task_id": str(uuid.uuid4()),
    }
    result = call(invite_server, "search", arguments)
    assert result["data"] == []
    assert "call `propose`" in result["note"]


def test_invite_variant_keeps_the_untrusted_note_when_there_are_results(
    invite_server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    word = f"invitecheck{uuid.uuid4().hex[:8]}"
    factories.entry(db, reporter, symptom=word)
    result = call(invite_server, "search", {"query": word, "task_id": str(uuid.uuid4())})
    assert result["data"]
    assert "untrusted" in result["note"]


def test_rule_variant_changes_only_the_propose_rule() -> None:
    base, rule = agent_text.INSTRUCTIONS, agent_text.INSTRUCTIONS_RULE
    assert "How many attempts the fix took does not matter" in rule
    assert "more than one attempt" not in rule
    # everything before the propose rule is identical
    cut = base.index("Propose on surprise")
    assert rule.startswith(base[:cut])


def test_fingerprint_changes_when_any_surface_changes() -> None:
    base = agent_text.fingerprint(agent_text.VARIANTS["baseline"])
    assert len(base) == 12
    assert base == agent_text.fingerprint(agent_text.VARIANTS["baseline"])  # stable
    seen = {agent_text.fingerprint(v) for v in agent_text.VARIANTS.values()}
    assert len(seen) == len(agent_text.VARIANTS)  # every variant distinct
    tweaked = agent_text.Variant(
        agent_text.INSTRUCTIONS + " ", agent_text.PROPOSE, agent_text.SEARCH, agent_text.REPORT
    )
    assert agent_text.fingerprint(tweaked) != base  # one character moves the id


@pytest.mark.parametrize(
    ("name", "parts"),
    [
        ("start-invite", {"start", "invite"}),
        ("start-rule", {"start", "rule"}),
        ("start-all", {"start", "invite", "rule", "examples"}),
    ],
)
def test_combined_variants_take_each_surface_from_their_parts(name: str, parts: set[str]) -> None:
    combined = agent_text.VARIANTS[name]
    baseline = agent_text.VARIANTS["original"]  # the combinations were built on the original
    for field in dataclasses.fields(agent_text.Variant):
        owners = {
            part
            for part in parts
            if getattr(agent_text.VARIANTS[part], field.name) != getattr(baseline, field.name)
        }
        expected = (
            getattr(agent_text.VARIANTS[owners.pop()], field.name)
            if owners
            else getattr(baseline, field.name)
        )
        assert getattr(combined, field.name) == expected, field.name


# Unshipped drafts (MIT-625): each changes one surface of the shipped text; `combined` has all.
DRAFTS = {
    "search-surprise": "instructions",
    "own-mistake": "instructions",
    "lesson-also-propose": "instructions",
    "propose-example": "propose",
}


def differing(name: str) -> set[str]:
    shipped, draft = agent_text.VARIANTS["baseline"], agent_text.VARIANTS[name]
    return {
        field.name
        for field in dataclasses.fields(agent_text.Variant)
        if getattr(draft, field.name) != getattr(shipped, field.name)
    }


@pytest.mark.parametrize("name", [*DRAFTS, "combined"])
def test_each_draft_is_selected_by_its_name(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", name)
    assert agent_text.variant() == agent_text.VARIANTS[name]


@pytest.mark.parametrize(("name", "surface"), DRAFTS.items())
def test_each_draft_changes_one_surface_of_the_shipped_text(name: str, surface: str) -> None:
    assert differing(name) == {surface}


def without_drafts(instructions: str) -> str:
    """Undo every draft edit, which must give back the shipped instructions exactly."""
    return (
        instructions.replace(agent_text.SEARCH_RULE_SURPRISE, agent_text.SEARCH_RULES[4])
        .replace(f" {agent_text.OWN_MISTAKE_LINE}", "")
        .replace(f" {agent_text.LESSON_ALSO_PROPOSE_LINE}", "")
    )


@pytest.mark.parametrize("name", ["search-surprise", "own-mistake", "lesson-also-propose"])
def test_an_instructions_draft_only_adds_its_line_or_swaps_the_search_rule(name: str) -> None:
    draft, shipped = agent_text.VARIANTS[name], agent_text.VARIANTS["baseline"]
    assert without_drafts(draft.instructions) == shipped.instructions


def test_the_combined_draft_has_all_four_changes() -> None:
    combined, shipped = agent_text.VARIANTS["combined"], agent_text.VARIANTS["baseline"]
    assert differing("combined") == {"instructions", "propose"}
    for line in (
        agent_text.SEARCH_RULE_SURPRISE,
        agent_text.OWN_MISTAKE_LINE,
        agent_text.LESSON_ALSO_PROPOSE_LINE,
    ):
        assert line in combined.instructions
    assert without_drafts(combined.instructions) == shipped.instructions
    assert combined.propose == agent_text.VARIANTS["propose-example"].propose


def test_the_search_draft_covers_surprises_with_no_error_text() -> None:
    rule = agent_text.SEARCH_RULE_SURPRISE
    for trigger in ("tool", "a default", "a bare error page", "no error text", "what you saw"):
        assert trigger in rule
    assert "Skip" not in rule  # an exemption clause becomes the default reading (round one)


def test_the_example_draft_extends_the_shipped_example_with_a_versioned_app() -> None:
    shipped = agent_text.VARIANTS["baseline"].propose
    draft = agent_text.VARIANTS["propose-example"].propose
    assert draft == shipped + agent_text.APP_EXAMPLE
    environments = re.findall(r"^  environment: (\{.*\})$", draft, re.MULTILINE)
    assert len(environments) == 2
    for environment in map(json.loads, environments):
        validate_proposal(Proposal("s", environment, "c", "f", ("d",), None, uuid.uuid4()))
    assert json.loads(environments[1]) == {"tool": "cronie 1.7.2"}


def every_cause_word() -> set[str]:
    """Every word the eval grader takes as naming a scenario's cause, on each turn of each."""
    words: set[str] = set()
    for scenario in SCENARIOS.values():
        for turn in (scenario, scenario.then):  # a multi-turn scenario has one more turn
            if turn is not None and turn.discovery is not None:
                words.update(word.lower() for word in turn.discovery.cause_words)
    return words


def examples() -> dict[str, str]:
    """The draft's added worked example and the skill's `search`, `propose` and `report` examples.

    The `search` and three `propose` blocks are checked whole. Of the `report` block only its
    free text, `agent_assessment`, is checked: its field name `outcome` contains the cause word
    "utc", and neither that name nor the enum value `tests_passed` can hint at a cause.

    Exempt: the shipped worked example (the whenever `ignore_dst` case in `PROPOSE_EXAMPLES_TOOL`
    and the texts built on it). The hold-out scenario whose cause it named is retired (Question
    `MIT-640`), and the hold-out that replaced it is checked against the shipped text below. The
    whole shipped text can't be held to this test yet: the grader's substring matching finds
    generic cause words of other hold-outs in it ("removed", "naive", "view" in "reviews", "utc"
    in "outcomes"). Changing their `cause_words` changes how they grade, a follow-up decision.
    """
    blocks = re.findall(r"```json\n(.*?)```", SKILL.read_text(encoding="utf-8"), re.DOTALL)
    calls = [block for block in blocks if '"symptom"' in block or '"query"' in block]
    assert len(calls) == 4
    reports = [json.loads(block) for block in blocks if '"agent_assessment"' in block]
    assert len(reports) == 1
    skill = {f"skill example {n}": block for n, block in enumerate(calls, start=1)}
    return {
        "propose-example": agent_text.APP_EXAMPLE,
        **skill,
        "skill report example": reports[0]["agent_assessment"],
    }


@pytest.mark.parametrize(("where", "example"), examples().items())
def test_no_example_names_a_scenarios_cause(where: str, example: str) -> None:
    # An example that named a scenario's cause would fit the eval to it (decision 0023).
    assert every_cause_word() >= {"memory", "on exit", "default", "exporter"}
    named = {word for word in every_cause_word() if word in example.lower()}
    assert not named, where


def shipped_text() -> str:
    """Every surface of the shipped text, lowercased, as the fingerprint reads them."""
    shipped = agent_text.VARIANTS["baseline"]
    surfaces = (str(getattr(shipped, field.name) or "") for field in dataclasses.fields(shipped))
    return "\n".join(surfaces).lower()


def cause_words_in_shipped_text(scenario: Scenario) -> set[str]:
    """The scenario's cause words the shipped text contains, matched the way the grader does."""
    assert scenario.discovery is not None
    return {word for word in scenario.discovery.cause_words if word.lower() in shipped_text()}


def test_the_replacement_holdout_shares_no_cause_word_with_the_shipped_text() -> None:
    assert cause_words_in_shipped_text(HOLDOUT["real-polars-explode-empty"]) == set()


def test_the_retired_holdout_is_out_of_the_measured_set_because_the_shipped_text_names_it() -> None:
    assert "real-whenever-ignore-dst" in RETIRED
    assert "real-whenever-ignore-dst" not in HOLDOUT
    assert "real-whenever-ignore-dst" not in SCENARIOS
    assert "real-polars-explode-empty" in HOLDOUT
    assert len(HOLDOUT) == 8  # the retired scenario was replaced, not dropped
    # The check above, run on the retired scenario, finds its cause in the shipped example.
    assert cause_words_in_shipped_text(WHENEVER_IGNORE_DST) == {
        "0.11",
        "removed",
        "naive_arithmetic_ok",
    }


def test_the_retired_holdouts_old_results_still_load_and_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = agent_eval.LEDGER.read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["scenario"] in RETIRED for line in lines if line)
    assert agent_eval.by_variant() == 0
    monkeypatch.setattr(agent_eval, "RESULTS", agent_eval.REPO / "docs" / "eval")
    assert agent_eval.report(argparse.Namespace(condition=["baseline-holdout"])) == 0
    out = capsys.readouterr().out
    assert "real-whenever-ignore-dst    5" in out
    assert "real-whenever-ignore-dst is retired, not measured: the shipped `propose`" in out


def test_no_draft_ships() -> None:
    shipped = agent_text.VARIANTS["baseline"]
    assert shipped == agent_text.resolve(agent_text.SHIPPED)
    assert agent_text.variant() == shipped
    for text in (
        agent_text.SEARCH_RULE_SURPRISE,
        agent_text.OWN_MISTAKE_LINE,
        agent_text.LESSON_ALSO_PROPOSE_LINE,
    ):
        assert text not in shipped.instructions
    assert shipped.propose != agent_text.PROPOSE_EXAMPLES_APP

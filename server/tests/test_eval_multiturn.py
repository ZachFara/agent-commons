"""Multi-turn eval scenarios (decision 0034): the second task arrives through --resume."""

from pathlib import Path
from typing import Any

import pytest
from tools.eval import runner
from tools.eval.scenarios import SCENARIOS


def test_multi_turn_scenarios_expect_two_proposals_and_add_files_later() -> None:
    scenario = SCENARIOS["multi-turn-rename-then-default"]
    assert scenario.then is not None
    assert scenario.expect.proposals == 2
    assert set(scenario.then.files) - set(scenario.files)
    assert "Next task" in scenario.follow_up


def test_the_second_turn_resumes_the_same_session_after_writing_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = SCENARIOS["multi-turn-retry-then-timezone"]
    assert scenario.then is not None
    project, site = tmp_path / "project", tmp_path / "site"
    project.mkdir()
    session = runner.Session(scenario, "c", 0, project, site, "postgresql://unused")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "seed", lambda _url, _scenario: None)

    def fake_run(*_args: object, **kwargs: object) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [{"type": "result", "num_turns": 3, "total_cost_usd": 0.5}]

    monkeypatch.setattr(runner, "run_session", fake_run)
    first: list[dict[str, Any]] = [
        {"type": "system", "session_id": "abc"},
        {"type": "result", "num_turns": 4},
    ]

    later = runner.next_turn(session, tmp_path, "m", first, hooks=True)

    assert calls[0]["resume"] == ("abc", scenario.follow_up)
    assert all((project / path).exists() for path in scenario.then.files)
    assert later == [{"type": "result", "num_turns": 3, "total_cost_usd": 0.5}]


def test_a_run_short_of_the_expected_proposals_fails() -> None:
    scenario = SCENARIOS["multi-turn-rename-then-default"]
    proposal = runner.Proposal(symptom="s", on_topic=True, cause_right=True, pinned=True)
    one = runner.Result(
        scenario=scenario.name,
        trigger=scenario.trigger,
        condition="c",
        index=0,
        searched=True,
        search_found=False,
        proposed=True,
        proposals=[proposal],
        reported=False,
        used=None,
        observed_outcome=None,
        used_session_task_id=True,
        tests_pass=True,
        attempts=1,
        read_docs=False,
        injection_followed=False,
        turns=8,
        cost_usd=1.0,
        error=None,
    )
    assert one.failures(scenario) == ["proposals"]

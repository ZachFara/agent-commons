from datetime import UTC, datetime

import pytest

from commons.domain.outcomes import ObservedRun, OutcomeRecord, merge_observation
from commons.domain.usage import compute_used, fix_artifacts

FIX = "Pin `markupsafe<2.1`, run `pip check`, and set NODE_OPTIONS=--openssl-legacy-provider."


def test_artifacts_are_concrete_pieces_of_the_fix() -> None:
    assert fix_artifacts(FIX) == {
        "markupsafe<2.1",
        "pip check",
        "node_options=--openssl-legacy-provider",
    }
    assert fix_artifacts("Rotate [REDACTED:github_token] and retry the build.") == set()


@pytest.mark.parametrize(
    ("session_text", "used"),
    [
        ("pip check\nmarkupsafe<2.1\nNODE_OPTIONS=--openssl-legacy-provider npm run build", "yes"),
        ("markupsafe<2.1\npip check", "yes"),
        ("-markupsafe==2.1.0\n+markupsafe<2.1", "partial"),
        ("ls -la\npytest", "no"),
        ("", "no"),
    ],
)
def test_used_is_the_share_of_artifacts_present(session_text: str, used: str) -> None:
    assert compute_used(FIX, session_text) == used


def test_used_is_unknown_when_the_fix_has_nothing_concrete() -> None:
    assert compute_used("Restart your editor and try again.", "anything") is None


AT = datetime(2026, 10, 25, tzinfo=UTC)


def test_observation_without_a_test_run_changes_nothing() -> None:
    existing = OutcomeRecord("tests_failed", {"outcome_source": "agent"}, "meh")
    assert merge_observation(existing, None) == existing
    assert merge_observation(None, None) == OutcomeRecord("unknown", {}, None)


@pytest.mark.parametrize(("exit_code", "outcome"), [(0, "tests_passed"), (1, "tests_failed")])
def test_a_test_run_sets_the_observed_outcome_and_keeps_the_agents_words(
    exit_code: int, outcome: str
) -> None:
    existing = OutcomeRecord(
        "tests_passed",
        {"outcome_source": "agent", "agent_outcome": "tests_passed", "agent": {"x": 1}},
        "It worked.",
    )
    merged = merge_observation(existing, ObservedRun("pytest -q", exit_code, AT))
    assert merged.outcome == outcome
    assert merged.agent_assessment == "It worked."
    assert merged.evidence == {
        "outcome_source": "hook",
        "agent_outcome": "tests_passed",
        "agent": {"x": 1},
        "observed": {"test_command": "pytest -q", "exit_code": exit_code, "at": AT.isoformat()},
    }

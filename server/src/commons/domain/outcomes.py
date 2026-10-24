from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from commons.domain.errors import InvalidInputError

# Mirrors the Postgres enum outcome_kind.
OutcomeKind = Literal[
    "tests_passed",
    "tests_failed",
    "error_resolved",
    "error_persisted",
    "user_accepted",
    "user_rejected",
    "abandoned",
    "unknown",
]

# Outcomes that count as the entry having worked, for success rates (DESIGN.md §7).
SUCCESS_OUTCOMES: tuple[OutcomeKind, ...] = ("tests_passed", "error_resolved", "user_accepted")


@dataclass(frozen=True)
class Report:
    """The agent's post-hoc report on one retrieved entry (DESIGN.md §4 report)."""

    task_id: UUID
    entry_id: UUID
    agent_assessment: str
    outcome: OutcomeKind | None
    evidence: dict[str, object] | None


@dataclass(frozen=True)
class OutcomeRecord:
    """The outcome fields that a report or a hook can write."""

    outcome: OutcomeKind
    evidence: dict[str, object]
    agent_assessment: str | None

    def outcome_source(self) -> str | None:
        """Return who set `outcome`: "hook" (observed), "agent" (self-reported), or None."""
        source = self.evidence.get("outcome_source")
        return source if isinstance(source, str) else None


def validate_report(report: Report) -> None:
    """Raise InvalidInputError if the report can't be stored."""
    if not report.agent_assessment.strip():
        msg = "agent_assessment must not be empty: say whether the entry helped and why."
        raise InvalidInputError(msg)


def merge_report(existing: OutcomeRecord | None, report: Report) -> OutcomeRecord:
    """Merge an agent report into the outcome row (DESIGN.md §4).

    The assessment is always stored. The agent's outcome is used only when no hook has
    observed one; observed outcomes are never overwritten. Agent evidence is kept under
    `evidence.agent`, apart from hook payloads, and the agent's claimed outcome under
    `evidence.agent_outcome` so self-report accuracy can be measured (decision 0017).
    """
    evidence = dict(existing.evidence) if existing else {}
    outcome: OutcomeKind = existing.outcome if existing else "unknown"
    if report.evidence is not None:
        evidence["agent"] = report.evidence
    if report.outcome is not None:
        evidence["agent_outcome"] = report.outcome
    observed = existing is not None and existing.outcome_source() == "hook"
    if report.outcome is not None and not observed:
        outcome = report.outcome
        evidence["outcome_source"] = "agent"
    return OutcomeRecord(
        outcome=outcome, evidence=evidence, agent_assessment=report.agent_assessment
    )


# Mirrors the Postgres enum usage.
Usage = Literal["yes", "partial", "no"]


@dataclass(frozen=True)
class ObservedRun:
    """A test command a hook saw, and how it exited."""

    command: str
    exit_code: int
    at: datetime


def merge_observation(existing: OutcomeRecord | None, run: ObservedRun | None) -> OutcomeRecord:
    """Merge what hooks observed into the outcome row (DESIGN.md §4: observed fields win).

    A test run after the retrieval sets the outcome; without one, the row is unchanged.
    """
    base = existing or OutcomeRecord(outcome="unknown", evidence={}, agent_assessment=None)
    if run is None:
        return base
    evidence = {
        **base.evidence,
        "outcome_source": "hook",
        "observed": {
            "test_command": run.command,
            "exit_code": run.exit_code,
            "at": run.at.isoformat(),
        },
    }
    outcome: OutcomeKind = "tests_passed" if run.exit_code == 0 else "tests_failed"
    return OutcomeRecord(outcome=outcome, evidence=evidence, agent_assessment=base.agent_assessment)


@dataclass(frozen=True)
class OutcomeSample:
    """One outcome of a used entry, with what its weight depends on."""

    success: bool
    used: Usage
    observed: bool  # set by a hook rather than the agent
    reporter_reliability: float | None
    age_days: float

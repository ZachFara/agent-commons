from dataclasses import dataclass
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
    `evidence.agent`, apart from hook payloads.
    """
    evidence = dict(existing.evidence) if existing else {}
    outcome: OutcomeKind = existing.outcome if existing else "unknown"
    if report.evidence is not None:
        evidence["agent"] = report.evidence
    observed = existing is not None and existing.outcome_source() == "hook"
    if report.outcome is not None and not observed:
        outcome = report.outcome
        evidence["outcome_source"] = "agent"
    return OutcomeRecord(
        outcome=outcome, evidence=evidence, agent_assessment=report.agent_assessment
    )

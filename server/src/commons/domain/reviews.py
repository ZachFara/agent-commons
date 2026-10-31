from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from commons.domain.entries import EntryStatus
from commons.domain.errors import InvalidInputError

# Mirror the Postgres enums review_decision and review_source.
# retire and restore are admin lifecycle actions (decision 0026), never agent reviews.
ReviewDecision = Literal["approve", "reject", "retire", "restore"]
ReviewSource = Literal["agent", "human"]
REVIEW_DECISIONS: tuple[ReviewDecision, ...] = ("approve", "reject")

_STATUS_FOR: dict[ReviewDecision, EntryStatus] = {
    "approve": "approved",
    "reject": "rejected",
    "retire": "retired",
    "restore": "approved",
}


@dataclass(frozen=True)
class ReviewRequest:
    """One reviewer's decision on an entry (decision 0005)."""

    entry_id: UUID
    decision: ReviewDecision
    reason: str | None
    source: ReviewSource


def validate_review(request: ReviewRequest) -> None:
    """Rejections need a reason, so the proposer's agent can learn from them."""
    if request.decision == "reject" and not (request.reason or "").strip():
        msg = "reason is required when rejecting."
        raise InvalidInputError(msg)


def review_state(
    reviews: Sequence[tuple[ReviewDecision, ReviewSource]],
) -> tuple[EntryStatus, bool]:
    """Return (status, human_reviewed) from reviews in the order they were made.

    Status follows the latest human review if there is one, else the latest agent review.
    """
    human = [decision for decision, source in reviews if source == "human"]
    deciding = human or [decision for decision, _source in reviews]
    status: EntryStatus = _STATUS_FOR[deciding[-1]] if deciding else "pending"
    return status, bool(human)


@dataclass(frozen=True)
class PendingEntry:
    """An entry awaiting a (human) review, with what a reviewer needs to judge it."""

    entry_id: UUID
    created_at: datetime
    status: EntryStatus
    symptom: str
    environment: dict[str, object]
    cause: str
    fix: str
    repro: str | None
    domains: list[str]
    proposed_by_model: str
    agent_reviews: int
    redaction_report: dict[str, object]

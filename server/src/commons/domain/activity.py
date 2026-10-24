"""Recent activity, as the admin dashboard shows it."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from commons.domain.entries import EntryStatus
from commons.domain.outcomes import OutcomeKind


@dataclass(frozen=True)
class RecentEntry:
    """A proposed entry, newest first."""

    entry_id: UUID
    created_at: datetime
    status: EntryStatus
    human_reviewed: bool
    symptom: str
    proposed_by: str
    proposed_by_model: str


@dataclass(frozen=True)
class RecentRetrieval:
    """A search result that was returned, with its outcome if one was reported."""

    created_at: datetime
    task_id: UUID
    query: str
    entry_id: UUID
    symptom: str
    rank_position: int
    score_at_retrieval: float
    reporter: str
    model: str
    outcome: OutcomeKind | None
    outcome_source: str | None
    agent_assessment: str | None

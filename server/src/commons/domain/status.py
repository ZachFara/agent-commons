from dataclasses import dataclass
from uuid import UUID

from commons.domain.entries import EntryStatus


@dataclass(frozen=True)
class TopEntry:
    """An entry ranked by how often it worked when used."""

    entry_id: UUID
    symptom: str
    used_outcomes: int
    successes: int

    def success_rate(self) -> float:
        """Fraction of used outcomes that succeeded."""
        return self.successes / self.used_outcomes


@dataclass(frozen=True)
class StoreStatus:
    """Store statistics (DESIGN.md §4 status). Decay flags arrive with Phase 3."""

    entries_by_status: dict[EntryStatus, int]
    awaiting_human_review: int
    retrievals_30d: int
    tasks_30d: int
    outcomes_30d: int
    top_entries: list[TopEntry]

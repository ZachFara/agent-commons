from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from commons.domain.errors import InvalidInputError
from commons.domain.reporters import PrivacyDefault

MAX_LIMIT = 20


@dataclass(frozen=True)
class SearchRequest:
    """What the agent searched for (DESIGN.md §4 search, decision 0012)."""

    query: str
    task_id: UUID
    environment: dict[str, object] | None
    limit: int

    def language(self) -> str | None:
        """Return the language name to filter on: the first word of environment.language."""
        value = (self.environment or {}).get("language")
        if not isinstance(value, str) or not value.split():
            return None
        return value.split()[0].lower()


def validate_search(request: SearchRequest) -> None:
    """Raise InvalidInputError if the request can't be served."""
    if not request.query.strip():
        msg = "query must not be empty."
        raise InvalidInputError(msg)
    if not 1 <= request.limit <= MAX_LIMIT:
        msg = f"limit must be between 1 and {MAX_LIMIT}."
        raise InvalidInputError(msg)


@dataclass(frozen=True)
class SearchRecord:
    """What the store keeps about one search, already cut down for its privacy mode."""

    task_id: UUID
    privacy_mode: PrivacyDefault
    query: str
    language: str | None
    limit: int
    hit_count: int
    environment: dict[str, object] | None
    results: list[dict[str, object]] | None


@dataclass(frozen=True)
class SearchHit:
    """One search result with the facts its provenance line is built from."""

    entry_id: UUID
    symptom: str
    environment: dict[str, object]
    cause: str
    fix: str
    repro: str | None
    score: float
    proposed_by_model: str
    human_reviewed: bool
    used_outcomes: int
    successes: int
    last_confirmed: datetime | None

    def provenance(self) -> str:
        """One line on where the entry came from and how it has held up."""
        if self.used_outcomes:
            rate = f"success {self.successes}/{self.used_outcomes} when used"
        else:
            rate = "no outcomes yet"
        confirmed = (
            f"last confirmed {self.last_confirmed:%Y-%m-%d}"
            if self.last_confirmed
            else "never confirmed"
        )
        parts = [f"proposed by {self.proposed_by_model}", rate, confirmed]
        if not self.human_reviewed:
            parts.append("unreviewed")
        return "; ".join(parts)

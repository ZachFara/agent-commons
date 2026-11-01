"""Duplicate resolution settings (decision 0027)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from commons.domain.errors import InvalidInputError

DuplicateAction = Literal["flag", "link"]
# Tuned on labeled pairs from the judged set, scored as propose scores them (decision 0027
# step 5, test_duplicates.py; retuned 2026-10-31): 0.35 catches 83% of duplicates with 1 of 32
# distinct proposals flagged; 0.4 caught 77% with none. A flag only asks the reviewer.
DEFAULT_THRESHOLD = 0.35
MAX_CANDIDATES = 3


@dataclass(frozen=True)
class DuplicateSettings:
    """How propose treats likely duplicates. Server-side, set by environment variables."""

    threshold: float = DEFAULT_THRESHOLD
    action: DuplicateAction = "flag"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "DuplicateSettings":
        """Read COMMONS_DUPLICATE_THRESHOLD and COMMONS_DUPLICATE_ACTION (flag or link).

        `return` (send the agent to `report` instead) changes what agents see, so it waits for
        an eval run (decision 0023) and is not accepted yet.
        """
        action = environ.get("COMMONS_DUPLICATE_ACTION", "flag").strip().lower()
        if action not in {"flag", "link"}:
            msg = "COMMONS_DUPLICATE_ACTION must be flag or link."
            raise InvalidInputError(msg)
        try:
            threshold = float(environ.get("COMMONS_DUPLICATE_THRESHOLD", DEFAULT_THRESHOLD))
        except ValueError:
            threshold = -1.0
        if not 0 < threshold <= 1:
            msg = "COMMONS_DUPLICATE_THRESHOLD must be a number in (0, 1]."
            raise InvalidInputError(msg)
        return cls(threshold, "link" if action == "link" else "flag")


@dataclass(frozen=True)
class Candidate:
    """An existing entry a new proposal looks like."""

    entry_id: UUID
    score: float
    same_task: bool  # a retry from the same task, not new knowledge

    def as_json(self) -> dict[str, object]:
        """Return the stored form."""
        return {
            "entry_id": str(self.entry_id),
            "score": round(self.score, 3),
            "retry": self.same_task,
        }

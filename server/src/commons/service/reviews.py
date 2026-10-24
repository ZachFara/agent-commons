from dataclasses import dataclass, replace

from commons.domain.entries import EntryStatus
from commons.domain.errors import InvalidInputError, NotFoundError
from commons.domain.reviews import PendingEntry, ReviewRequest, review_state, validate_review
from commons.privacy.redaction import scrub
from commons.service.identity import current_reporter
from commons.store.connection import Connection
from commons.store.reviews import (
    add_review,
    entry_reviews,
    locked_entry_status,
    pending_entries,
    set_review_state,
)

PENDING_LIMIT = 50


@dataclass(frozen=True)
class ReviewResult:
    """The entry's state after the review."""

    status: EntryStatus
    human_reviewed: bool


def review(conn: Connection, request: ReviewRequest) -> ReviewResult:
    """Record a review by this machine's reporter and update the entry (decision 0005)."""
    validate_review(request)
    if request.reason is not None:
        request = replace(request, reason=scrub(request.reason))
    with conn.transaction():
        reviewer = current_reporter(conn)
        current = locked_entry_status(conn, request.entry_id)
        if current is None:
            msg = f"No entry {request.entry_id}."
            raise NotFoundError(msg)
        if current == "retired":
            msg = "Entry is retired; it can no longer be reviewed."
            raise InvalidInputError(msg)
        add_review(conn, request, reviewed_by=reviewer)
        status, human_reviewed = review_state(entry_reviews(conn, request.entry_id))
        set_review_state(conn, request.entry_id, status, human_reviewed=human_reviewed)
    return ReviewResult(status=status, human_reviewed=human_reviewed)


def pending(conn: Connection) -> list[PendingEntry]:
    """Entries awaiting a human review, oldest first."""
    return pending_entries(conn, PENDING_LIMIT)

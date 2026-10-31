"""Entry lifecycle (decision 0026): admins edit, supersede, retire and restore entries.

Every edit is a new revision that goes through the same validation and redaction as
`propose`. Retirement and restoring are human reviews, so they are reversible and recorded.
"""

from uuid import UUID

from commons.domain.entries import Proposal, validate_proposal
from commons.domain.errors import InvalidInputError
from commons.domain.lifecycle import FixedIn, require_reason
from commons.domain.reviews import ReviewDecision, ReviewRequest, review_state
from commons.privacy.redaction import redact_proposal, scrub
from commons.privacy.risks import scan_proposal
from commons.service.identity import current_pseudonym, current_reporter
from commons.store import lifecycle as store
from commons.store.connection import Connection
from commons.store.entries import set_risk_report
from commons.store.reviews import add_review, entry_reviews, set_review_state


def edit_entry(conn: Connection, entry_id: UUID, content: Proposal, reason: str) -> int:
    """Store edited text as the entry's next revision; return its number."""
    why = scrub(require_reason(reason))
    validate_proposal(content)
    redacted, report = redact_proposal(content)
    redacted, risks = scan_proposal(redacted)
    with conn.transaction():
        entry = store.locked_entry(conn, entry_id)
        revision = int(entry["revision"]) + 1
        edit = ("human", current_pseudonym(conn), why)
        store.add_revision(conn, entry_id, revision, (redacted, report), edit)
        set_risk_report(conn, entry_id, risks)
    return revision


def retire(
    conn: Connection,
    entry_id: UUID,
    reason: str,
    *,
    fixed_in: FixedIn | None = None,
    superseded_by: UUID | None = None,
) -> None:
    """Take an entry out of search, saying why, and what fixed or replaced it.

    A superseded entry never shows again; one with `fixed_in` still shows to callers on a
    version before the fix, marked as fixed (decision 0025's version awareness).
    """
    why = scrub(require_reason(reason))
    with conn.transaction():
        entry = store.locked_entry(conn, entry_id)
        if entry["status"] != "approved":
            msg = f"Only approved entries can be retired; this one is {entry['status']}."
            raise InvalidInputError(msg)
        if superseded_by is not None:
            _check_successor(conn, entry_id, superseded_by)
        _review(conn, entry_id, "retire", why)
        store.set_retirement(conn, entry_id, fixed_in, superseded_by)


def restore(conn: Connection, entry_id: UUID, reason: str) -> None:
    """Return a retired or superseded entry to search."""
    why = scrub(require_reason(reason))
    with conn.transaction():
        entry = store.locked_entry(conn, entry_id)
        if entry["status"] != "retired":
            msg = f"Only retired entries can be restored; this one is {entry['status']}."
            raise InvalidInputError(msg)
        _review(conn, entry_id, "restore", why)
        store.set_retirement(conn, entry_id, None, None)


def mark_superseded(conn: Connection, entry_id: UUID, successor: UUID) -> None:
    """Point a rejected duplicate at the entry it duplicates (decision 0027)."""
    _check_successor(conn, entry_id, successor)
    store.set_retirement(conn, entry_id, None, successor)


def _check_successor(conn: Connection, entry_id: UUID, successor: UUID) -> None:
    if successor == entry_id:
        msg = "An entry can't supersede itself."
        raise InvalidInputError(msg)
    if store.entry_status(conn, successor) != "approved":
        msg = f"The replacement {successor} must be an approved entry."
        raise InvalidInputError(msg)


def _review(conn: Connection, entry_id: UUID, decision: ReviewDecision, reason: str) -> None:
    request = ReviewRequest(entry_id, decision, reason, "human")
    add_review(conn, request, reviewed_by=current_reporter(conn))
    status, human = review_state(entry_reviews(conn, entry_id))
    set_review_state(conn, entry_id, status, human_reviewed=human)

"""Duplicate resolution (decision 0027): find likely duplicates on propose, merge or keep both.

The default action only flags candidates in the review queue, so nothing an agent sees
changes. `link` also preselects the best candidate for the reviewer.
"""

import os
from uuid import UUID

from commons.domain.duplicates import MAX_CANDIDATES, Candidate, DuplicateSettings
from commons.domain.entries import Proposal
from commons.domain.errors import InvalidInputError, NotFoundError
from commons.domain.lifecycle import require_reason
from commons.domain.reviews import ReviewRequest
from commons.service import lifecycle, reviews
from commons.service.identity import current_pseudonym
from commons.store import duplicates as store
from commons.store.connection import Connection
from commons.store.insights import entry
from commons.store.search import similar_entries


def settings() -> DuplicateSettings:
    """Return the server's duplicate settings (environment variables; `commons doctor`)."""
    return DuplicateSettings.from_environment(os.environ)


def flag_duplicates(
    conn: Connection, entry_id: UUID, proposal: Proposal, reporter_id: UUID
) -> list[Candidate]:
    """Store the existing entries a new proposal most resembles; return them."""
    current = settings()
    retries = store.same_task_entries(conn, proposal.task_id, reporter_id)
    found = similar_entries(conn, proposal.symptom, exclude=[entry_id], limit=MAX_CANDIDATES)
    candidates = [
        Candidate(other, score, same_task=other in retries)
        for other, score in found
        if score >= current.threshold
    ]
    link = candidates[0].entry_id if candidates and current.action == "link" else None
    store.save_candidates(conn, entry_id, candidates, link)
    return candidates


def merge(conn: Connection, duplicate: UUID, into: UUID) -> None:
    """Fold a duplicate into the canonical entry: its wording becomes an alias there.

    A pending duplicate is rejected and an approved one retired, either way pointing at the
    canonical entry, so its history stays and search never shows it again.
    """
    if duplicate == into:
        msg = "An entry can't be merged into itself."
        raise InvalidInputError(msg)
    found, canonical = entry(conn, duplicate), entry(conn, into)
    if found is None or canonical is None:
        msg = "Both entries must exist."
        raise NotFoundError(msg)
    if canonical["status"] != "approved":
        msg = "Merge into an approved entry."
        raise InvalidInputError(msg)
    reason = f"duplicate of {into}"
    with conn.transaction():
        if found["status"] == "approved":
            lifecycle.retire(conn, duplicate, reason, superseded_by=into)
        else:
            reviews.review(conn, ReviewRequest(duplicate, "reject", reason, "human"))
            lifecycle.mark_superseded(conn, duplicate, into)
        store.add_alias(conn, into, found["symptom"])


def keep_both(conn: Connection, first: UUID, second: UUID, reason: str = "distinct") -> None:
    """Record that two entries are different problems, so they are never flagged again."""
    require_reason(reason)
    with conn.transaction():
        store.add_distinct_pair(conn, first, second, current_pseudonym(conn))

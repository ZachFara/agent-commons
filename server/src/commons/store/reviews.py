from uuid import UUID

from psycopg.rows import class_row

from commons.domain.entries import EntryStatus
from commons.domain.reviews import PendingEntry, ReviewDecision, ReviewRequest, ReviewSource
from commons.store.connection import Connection

IN_QUEUE = (
    "NOT e.human_reviewed AND e.status <> 'retired' AND (NOT e.provisional OR e.sampled) "
    "AND NOT EXISTS (SELECT 1 FROM reporters qr WHERE qr.id = e.created_by "
    "AND qr.suspended_at IS NOT NULL)"
)
"""The review queue, as a condition on `entries e`. Every count of waiting entries uses it.

Provisional entries wait only when sampled (decision 0028), and suspended reporters' entries
not at all (decision 0029).
"""

_PENDING = """
SELECT e.id AS entry_id, e.created_at, e.status, e.symptom, e.environment, e.cause,
       e.fix, e.repro, e.domains, m.name AS proposed_by_model,
       (SELECT count(*) FROM reviews r WHERE r.entry_id = e.id) AS agent_reviews,
       e.redaction_report, e.risk_report
FROM entries e JOIN models m ON m.id = e.created_by_model
WHERE {in_queue}
ORDER BY e.created_at, e.id LIMIT %s
""".replace("{in_queue}", IN_QUEUE)


def locked_entry_status(conn: Connection, entry_id: UUID) -> EntryStatus | None:
    """Return the entry's status, locking the row, or None if it doesn't exist."""
    row = conn.execute("SELECT status FROM entries WHERE id = %s FOR UPDATE", (entry_id,))
    found = row.fetchone()
    if found is None:
        return None
    status: EntryStatus = found[0]
    return status


def add_review(conn: Connection, request: ReviewRequest, reviewed_by: UUID) -> None:
    """Record a review. clock_timestamp() keeps reviews in one transaction ordered."""
    conn.execute(
        "INSERT INTO reviews (entry_id, reviewed_by, decision, reason, source, created_at) "
        "VALUES (%s, %s, %s, %s, %s, clock_timestamp())",
        (request.entry_id, reviewed_by, request.decision, request.reason, request.source),
    )


def entry_reviews(conn: Connection, entry_id: UUID) -> list[tuple[ReviewDecision, ReviewSource]]:
    """Return the entry's reviews, oldest first."""
    rows = conn.execute(
        "SELECT decision, source FROM reviews WHERE entry_id = %s ORDER BY created_at",
        (entry_id,),
    )
    return [(decision, source) for decision, source in rows.fetchall()]


def set_review_state(
    conn: Connection, entry_id: UUID, status: EntryStatus, *, human_reviewed: bool
) -> None:
    """Store the status and human_reviewed flag derived from the entry's reviews."""
    conn.execute(
        "UPDATE entries SET status = %s, human_reviewed = %s WHERE id = %s",
        (status, human_reviewed, entry_id),
    )


def pending_entries(conn: Connection, limit: int) -> list[PendingEntry]:
    """Return the review queue (`IN_QUEUE`), oldest first."""
    with conn.cursor(row_factory=class_row(PendingEntry)) as cursor:
        cursor.execute(_PENDING, (limit,))
        return cursor.fetchall()


def proposer_and_risk(conn: Connection, entry_id: UUID) -> tuple[UUID, bool]:
    """Return who proposed the entry, and whether rejecting it counts as rejecting harm.

    That is: the risk scanner found something, and it isn't a duplicate merged into another
    entry (decision 0028).
    """
    row = conn.execute(
        "SELECT created_by, jsonb_array_length(risk_report->'findings') > 0 "
        "AND superseded_by IS NULL FROM entries WHERE id = %s",
        (entry_id,),
    ).fetchone()
    if row is None:
        msg = f"entry {entry_id} vanished"
        raise RuntimeError(msg)
    return row[0], bool(row[1])

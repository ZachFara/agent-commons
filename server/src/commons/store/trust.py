from collections.abc import Mapping
from uuid import UUID

from commons.domain.reviews import ReviewRequest
from commons.domain.trust import ContributorHistory, Role
from commons.store.connection import Connection
from commons.store.reviews import add_review

_HISTORIES = """
SELECT rp.id,
  (SELECT count(*) FROM entries e WHERE e.created_by = rp.id AND e.status = 'approved'),
  (SELECT count(*) FROM entries e WHERE e.created_by = rp.id),
  (SELECT count(*) FROM entries e WHERE e.created_by = rp.id AND e.status = 'rejected'
     AND e.human_reviewed AND jsonb_array_length(e.risk_report->'findings') > 0)
   + (SELECT count(*) FROM purges p WHERE p.reporter_id = rp.id AND p.restored_at IS NULL),
  (SELECT count(*) FROM entries e WHERE e.created_by = rp.id AND e.status = 'rejected'
     AND e.superseded_by IS NOT NULL),
  rp.reliability
FROM reporters rp
"""


def contributor_histories(conn: Connection) -> dict[UUID, ContributorHistory]:
    """Every reporter's record, for trust levels."""
    return {
        row[0]: ContributorHistory(row[1], row[2], row[3], row[4], row[5])
        for row in conn.execute(_HISTORIES)
    }


def set_trust_levels(conn: Connection, levels: Mapping[UUID, int]) -> None:
    """Store each reporter's trust level."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "UPDATE reporters SET trust_level = %s WHERE id = %s",
            [(level, reporter) for reporter, level in levels.items()],
        )


def trust_of(conn: Connection, reporter_id: UUID) -> int:
    """Return the reporter's current trust level."""
    row = conn.execute("SELECT trust_level FROM reporters WHERE id = %s", (reporter_id,)).fetchone()
    return int(row[0]) if row else 0


def approve_provisionally(
    conn: Connection, entry_id: UUID, reporter_id: UUID, *, sampled: bool
) -> None:
    """Approve on the proposer's trust (source `auto`), marking it provisional."""
    request = ReviewRequest(entry_id, "approve", "trusted contributor (decision 0028)", "auto")
    add_review(conn, request, reviewed_by=reporter_id)
    conn.execute(
        "UPDATE entries SET status = 'approved', provisional = true, sampled = %s WHERE id = %s",
        (sampled, entry_id),
    )


def reopen(conn: Connection, entry_ids: list[UUID], reason: str) -> None:
    """Send provisional entries back to the queue as pending."""
    for entry_id in entry_ids:
        conn.execute(
            "INSERT INTO reviews (entry_id, reviewed_by, decision, reason, source, created_at) "
            "SELECT id, created_by, 'reopen', %s, 'auto', clock_timestamp() FROM entries "
            "WHERE id = %s",
            (reason, entry_id),
        )
        conn.execute(
            "UPDATE entries SET status = 'pending', provisional = false, sampled = false "
            "WHERE id = %s",
            (entry_id,),
        )


def provisional_entries_of(conn: Connection, reporter_id: UUID) -> list[UUID]:
    """Return the reporter's provisional entries no human has reviewed."""
    rows = conn.execute(
        "SELECT id FROM entries WHERE created_by = %s AND provisional AND NOT human_reviewed "
        "AND status = 'approved'",
        (reporter_id,),
    )
    return [entry_id for (entry_id,) in rows]


def provisional_evidence(conn: Connection) -> list[tuple[UUID, int, int, int]]:
    """Return per unconfirmed provisional entry: others' observed successes and failures.

    The last number is how many other reporters succeeded with it.
    """
    rows = conn.execute(
        "SELECT e.id, "
        "count(*) FILTER (WHERE o.outcome IN ('tests_passed', 'error_resolved')), "
        "count(*) FILTER (WHERE o.outcome IN ('tests_failed', 'error_persisted')), "
        "count(DISTINCT r.reporter_id) FILTER (WHERE o.outcome IN "
        "  ('tests_passed', 'error_resolved')) "
        "FROM entries e JOIN counted_retrievals r ON r.entry_id = e.id "
        "JOIN counted_outcomes o ON o.retrieval_id = r.id "
        "WHERE e.provisional AND NOT e.human_reviewed AND e.confirmed_at IS NULL "
        "AND e.status = 'approved' AND r.reporter_id <> e.created_by "
        "AND o.used IN ('yes', 'partial') AND o.evidence->>'outcome_source' = 'hook' "
        "GROUP BY e.id"
    )
    return [(row[0], row[1], row[2], row[3]) for row in rows]


def confirm(conn: Connection, entry_id: UUID) -> None:
    """Mark a provisional entry confirmed by outcomes."""
    conn.execute("UPDATE entries SET confirmed_at = now() WHERE id = %s", (entry_id,))


def set_role(conn: Connection, reporter_id: UUID, role: Role) -> None:
    """Set what the reporter may be issued keys for."""
    conn.execute("UPDATE reporters SET role = %s WHERE id = %s", (role, reporter_id))


def role_of(conn: Connection, reporter_id: UUID) -> Role:
    """Return the reporter's role."""
    row = conn.execute("SELECT role::text FROM reporters WHERE id = %s", (reporter_id,)).fetchone()
    role: Role = row[0] if row else "contributor"
    return role


def queue_age_days(conn: Connection) -> tuple[int, float | None]:
    """How many entries wait for a human, and how many days the oldest has waited."""
    row = conn.execute(
        "SELECT count(*), extract(epoch FROM now() - min(e.created_at)) / 86400 "
        "FROM entries e JOIN reporters rp ON rp.id = e.created_by "
        "WHERE NOT e.human_reviewed AND e.status <> 'retired' AND rp.suspended_at IS NULL "
        "AND (NOT e.provisional OR e.sampled)"
    ).fetchone()
    if row is None:
        return 0, None
    return int(row[0]), None if row[1] is None else float(row[1])

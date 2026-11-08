from collections.abc import Mapping
from uuid import UUID

from psycopg import sql

from commons.domain.outcomes import SUCCESS_OUTCOMES
from commons.domain.reviews import ReviewRequest
from commons.domain.trust import REOPEN_WINDOW_DAYS, ContributorHistory, Role
from commons.store.connection import Connection
from commons.store.reviews import add_review

_HISTORIES = """
SELECT rp.id,
  (SELECT count(*) FROM entries e WHERE e.created_by = rp.id AND e.status = 'approved'
     AND NOT (e.provisional AND NOT e.human_reviewed AND e.confirmed_at IS NULL)),
  (SELECT count(*) FROM entries e WHERE e.created_by = rp.id),
  (SELECT count(*) FROM entries e WHERE e.created_by = rp.id AND e.status = 'rejected'
     AND e.human_reviewed AND jsonb_array_length(e.risk_report->'findings') > 0
     AND e.superseded_by IS NULL)
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
    """Send entries back to the queue as pending, out of search until a human approves.

    A reopen starts the review over (`review_state`), so a human approval before it no
    longer counts.
    """
    for entry_id in entry_ids:
        conn.execute(
            "INSERT INTO reviews (entry_id, reviewed_by, decision, reason, source, created_at) "
            "SELECT id, created_by, 'reopen', %s, 'auto', clock_timestamp() FROM entries "
            "WHERE id = %s",
            (reason, entry_id),
        )
        conn.execute(
            "UPDATE entries SET status = 'pending', provisional = false, sampled = false, "
            "human_reviewed = false WHERE id = %s",
            (entry_id,),
        )


# What the reopen rule for human-approved entries weighs, for one entry `e` (decision 0026,
# amended): its used outcomes with a known result (the split of `_SAMPLES` in
# store/ranking.py), in the last `days` days and since its latest reopen, so the failures
# that reopened it once don't reopen it again after a human approves it.
FAILURE_EVIDENCE = sql.SQL("""
JOIN LATERAL (
    SELECT count(*) FILTER (WHERE o.outcome <> ALL(%(success)s::outcome_kind[])) AS failures,
           count(*) FILTER (WHERE o.outcome = ANY(%(success)s::outcome_kind[])) AS successes,
           max(o.created_at) FILTER (WHERE o.outcome <> ALL(%(success)s::outcome_kind[]))
               AS last_failure
    FROM counted_outcomes o JOIN counted_retrievals r ON r.id = o.retrieval_id
    WHERE r.entry_id = e.id AND o.used IN ('yes', 'partial')
      AND o.outcome NOT IN ('unknown', 'abandoned')
      AND o.created_at > now() - make_interval(days => %(days)s)
      AND o.created_at > coalesce((SELECT max(v.created_at) FROM reviews v
          WHERE v.entry_id = e.id AND v.decision = 'reopen'), '-infinity')
) evidence ON evidence.failures > 0
""")
FAILURE_PARAMS: dict[str, object] = {
    "success": list(SUCCESS_OUTCOMES),
    "days": REOPEN_WINDOW_DAYS,
}


def reviewed_evidence(conn: Connection) -> list[tuple[UUID, int, int]]:
    """Return (entry, failures, successes) per human-approved entry with a recent failure."""
    query = sql.SQL(
        "SELECT e.id, evidence.failures, evidence.successes FROM entries e {evidence} "
        "WHERE e.status = 'approved' AND e.human_reviewed"
    ).format(evidence=FAILURE_EVIDENCE)
    rows = conn.execute(query, FAILURE_PARAMS)
    return [(row[0], row[1], row[2]) for row in rows]


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
    """How many entries wait for a human, and how many days the oldest has waited.

    A reopened entry waits from its reopening, not from when it was first proposed.
    """
    row = conn.execute(
        "SELECT count(*), extract(epoch FROM now() - min(greatest(e.created_at, ("
        "  SELECT max(v.created_at) FROM reviews v WHERE v.entry_id = e.id "
        "  AND v.decision = 'reopen')))) / 86400 "
        "FROM entries e JOIN reporters rp ON rp.id = e.created_by "
        "WHERE NOT e.human_reviewed AND e.status <> 'retired' AND rp.suspended_at IS NULL "
        "AND (NOT e.provisional OR e.sampled)"
    ).fetchone()
    if row is None:
        return 0, None
    return int(row[0]), None if row[1] is None else float(row[1])

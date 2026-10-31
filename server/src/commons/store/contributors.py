from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from commons.domain.outcomes import SUCCESS_OUTCOMES
from commons.store.connection import Connection

_MINE = """
SELECT e.id, e.symptom, e.status::text AS status, e.created_at, e.human_reviewed,
       e.redaction_report, e.risk_report,
       (SELECT r.decision::text FROM reviews r WHERE r.entry_id = e.id
        ORDER BY r.created_at DESC LIMIT 1) AS last_decision,
       (SELECT r.reason FROM reviews r WHERE r.entry_id = e.id
        ORDER BY r.created_at DESC LIMIT 1) AS last_reason,
       (SELECT count(*) FROM counted_retrievals r WHERE r.entry_id = e.id
        AND r.created_at > now() - interval '30 days') AS retrievals_30d,
       (SELECT count(*) FROM counted_outcomes o JOIN counted_retrievals r
        ON r.id = o.retrieval_id WHERE r.entry_id = e.id AND o.used IN ('yes', 'partial'))
        AS used,
       (SELECT count(*) FROM counted_outcomes o JOIN counted_retrievals r
        ON r.id = o.retrieval_id WHERE r.entry_id = e.id AND o.used IN ('yes', 'partial')
        AND o.outcome = ANY(%(success)s::outcome_kind[])) AS worked
FROM entries e WHERE e.created_by = %(reporter)s
ORDER BY e.created_at DESC LIMIT %(limit)s
"""


def entries_of(conn: Connection, reporter_id: UUID, limit: int = 200) -> list[dict[str, Any]]:
    """Return the reporter's own entries with what happened to them (decision 0035)."""
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            _MINE, {"reporter": reporter_id, "limit": limit, "success": list(SUCCESS_OUTCOMES)}
        )
        return cursor.fetchall()


def previous_session_start(conn: Connection, reporter_id: UUID) -> datetime | None:
    """Return when the reporter's session before the current one began."""
    row = conn.execute(
        "SELECT created_at FROM tasks WHERE reporter_id = %s ORDER BY created_at DESC "
        "OFFSET 1 LIMIT 1",
        (reporter_id,),
    ).fetchone()
    return None if row is None else row[0]


def news_since(conn: Connection, reporter_id: UUID, since: datetime) -> tuple[int, int, int]:
    """Count, since `since`: their entries approved, uses of them, and successes."""
    row = conn.execute(
        "SELECT "
        "(SELECT count(DISTINCT r.entry_id) FROM reviews r JOIN entries e ON e.id = r.entry_id "
        " WHERE e.created_by = %(me)s AND r.decision = 'approve' AND r.created_at > %(since)s), "
        "(SELECT count(*) FROM counted_outcomes o JOIN counted_retrievals r "
        " ON r.id = o.retrieval_id JOIN entries e ON e.id = r.entry_id "
        " WHERE e.created_by = %(me)s AND o.used IN ('yes', 'partial') "
        " AND o.updated_at > %(since)s), "
        "(SELECT count(*) FROM counted_outcomes o JOIN counted_retrievals r "
        " ON r.id = o.retrieval_id JOIN entries e ON e.id = r.entry_id "
        " WHERE e.created_by = %(me)s AND o.used IN ('yes', 'partial') "
        " AND o.outcome = ANY(%(success)s::outcome_kind[]) AND o.updated_at > %(since)s)",
        {"me": reporter_id, "since": since, "success": list(SUCCESS_OUTCOMES)},
    ).fetchone()
    return (0, 0, 0) if row is None else (int(row[0]), int(row[1]), int(row[2]))

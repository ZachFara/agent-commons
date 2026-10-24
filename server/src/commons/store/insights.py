"""Read-only queries behind the dashboard pages. Rows are plain dicts: they only feed templates."""

from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from commons.domain.outcomes import SUCCESS_OUTCOMES
from commons.store.connection import Connection

Row = dict[str, Any]
_SUCCESS = {"success": list(SUCCESS_OUTCOMES)}

_DAILY = """
WITH days AS (
    SELECT generate_series(current_date - (%(days)s - 1), current_date, interval '1 day')::date
        AS day
)
SELECT d.day,
       (SELECT count(*) FROM retrievals r WHERE r.created_at::date = d.day) AS retrievals,
       (SELECT count(*) FROM outcomes o WHERE o.created_at::date = d.day
          AND o.outcome = ANY(%(success)s::outcome_kind[])) AS successes,
       (SELECT count(*) FROM outcomes o WHERE o.created_at::date = d.day
          AND o.outcome IN ('tests_failed', 'error_persisted', 'user_rejected')) AS failures,
       (SELECT count(*) FROM entries e WHERE e.created_at::date = d.day) AS proposals
FROM days d ORDER BY d.day
"""

_KPIS = """
SELECT
  (SELECT count(*) FROM entries WHERE status = 'approved') AS approved,
  (SELECT count(*) FROM entries WHERE NOT human_reviewed AND status <> 'retired') AS awaiting,
  (SELECT count(*) FROM retrievals WHERE created_at > now() - interval '30 days') AS retrievals_30d,
  (SELECT count(DISTINCT reporter_id) FROM tasks WHERE created_at > now() - interval '30 days')
      AS active_reporters,
  (SELECT count(*) FILTER (WHERE outcome = ANY(%(success)s::outcome_kind[]))::float
          / nullif(count(*) FILTER (WHERE outcome <> 'unknown' AND outcome <> 'abandoned'), 0)
     FROM outcomes WHERE evidence->>'outcome_source' = 'hook'
       AND created_at > now() - interval '30 days') AS observed_success_rate,
  (SELECT count(*) FROM outcomes WHERE evidence->>'outcome_source' = 'hook'
       AND created_at > now() - interval '30 days') AS observed_outcomes,
  (SELECT avg(reliability) FROM reporters WHERE reliability IS NOT NULL) AS mean_reliability,
  (SELECT count(*) FROM reporters WHERE reliability IS NOT NULL) AS rated_reporters
"""

_OUTCOME_MIX = """
SELECT outcome::text AS outcome,
       count(*) FILTER (WHERE evidence->>'outcome_source' = 'hook') AS observed,
       count(*) FILTER (WHERE coalesce(evidence->>'outcome_source', '') <> 'hook') AS self_reported
FROM outcomes WHERE created_at > now() - interval '30 days'
GROUP BY outcome ORDER BY count(*) DESC
"""

_LATEST_SNAPSHOT = """
LEFT JOIN LATERAL (
    SELECT score, retrieval_count_30d, used_rate, success_rate FROM ranking_snapshots s
    WHERE s.entry_id = e.id ORDER BY computed_at DESC LIMIT 1
) s ON true
"""

_ENTRIES = (
    """
SELECT e.id, e.created_at, e.status::text AS status, e.human_reviewed, e.symptom, e.domains,
       m.name AS model, rp.pseudonym AS proposed_by,
       s.score, s.retrieval_count_30d, s.used_rate, s.success_rate
FROM entries e JOIN models m ON m.id = e.created_by_model
JOIN reporters rp ON rp.id = e.created_by
"""
    + _LATEST_SNAPSHOT
    + """
WHERE (%(status)s::text IS NULL OR e.status::text = %(status)s)
  AND (%(query)s::text IS NULL OR e.symptom ILIKE %(like)s OR e.cause ILIKE %(like)s
       OR e.fix ILIKE %(like)s OR %(query)s = ANY(e.domains))
ORDER BY {order} NULLS LAST, e.created_at DESC
LIMIT %(limit)s
"""
)

_ORDERS = {
    "score": "s.score DESC",
    "newest": "e.created_at DESC",
    "used": "s.retrieval_count_30d DESC",
}


def daily_activity(conn: Connection, days: int) -> list[Row]:
    """Per day for the last `days` days: retrievals, successes, failures, proposals."""
    return _rows(conn, _DAILY, {"days": days, **_SUCCESS})


def kpis(conn: Connection) -> Row:
    """Headline numbers for the overview."""
    rows = _rows(conn, _KPIS, _SUCCESS)
    return rows[0]


def outcome_mix(conn: Connection) -> list[Row]:
    """Outcomes in the last 30 days by kind, split observed vs self-reported."""
    return _rows(conn, _OUTCOME_MIX, {})


def entries(
    conn: Connection, *, status: str | None, query: str | None, order: str, limit: int = 200
) -> list[Row]:
    """Entries with their latest ranking snapshot, filtered and ordered."""
    text = _ENTRIES.replace("{order}", _ORDERS.get(order, _ORDERS["score"]))
    params = {"status": status, "query": query, "like": f"%{query}%", "limit": limit}
    return _rows(conn, text, params)


def entry(conn: Connection, entry_id: UUID) -> Row | None:
    """One entry with everything its page shows, or None."""
    rows = _rows(
        conn,
        "SELECT e.*, e.status::text AS status, m.name AS model, rp.pseudonym AS proposed_by, "
        "s.score, s.retrieval_count_30d, s.used_rate, s.success_rate "
        "FROM entries e JOIN models m ON m.id = e.created_by_model "
        "JOIN reporters rp ON rp.id = e.created_by " + _LATEST_SNAPSHOT + "WHERE e.id = %(id)s",
        {"id": entry_id},
    )
    return rows[0] if rows else None


def entry_reviews(conn: Connection, entry_id: UUID) -> list[Row]:
    """Return the entry's reviews, oldest first."""
    return _rows(
        conn,
        "SELECT r.created_at, r.decision::text AS decision, r.source::text AS source, r.reason, "
        "rp.pseudonym AS reviewer FROM reviews r JOIN reporters rp ON rp.id = r.reviewed_by "
        "WHERE r.entry_id = %(id)s ORDER BY r.created_at",
        {"id": entry_id},
    )


def score_history(conn: Connection, entry_id: UUID) -> list[Row]:
    """Return the entry's ranking snapshots, oldest first."""
    return _rows(
        conn,
        "SELECT computed_at, score FROM ranking_snapshots WHERE entry_id = %(id)s "
        "ORDER BY computed_at",
        {"id": entry_id},
    )


def entry_retrievals(conn: Connection, entry_id: UUID, limit: int = 50) -> list[Row]:
    """Recent retrievals of the entry with their outcomes."""
    return _rows(
        conn,
        "SELECT r.created_at, r.task_id, r.query, r.rank_position, rp.pseudonym AS reporter, "
        "m.name AS model, o.used::text AS used, o.outcome::text AS outcome, "
        "o.evidence->>'outcome_source' AS source, o.agent_assessment "
        "FROM retrievals r JOIN reporters rp ON rp.id = r.reporter_id "
        "JOIN models m ON m.id = r.model_id LEFT JOIN outcomes o ON o.retrieval_id = r.id "
        "WHERE r.entry_id = %(id)s ORDER BY r.created_at DESC LIMIT %(limit)s",
        {"id": entry_id, "limit": limit},
    )


def tasks(conn: Connection, limit: int = 100) -> list[Row]:
    """Recent agent sessions with what they retrieved, reported, and tested."""
    return _rows(
        conn,
        "SELECT t.id, t.created_at, rp.pseudonym AS reporter, m.name AS model, "
        "t.privacy_mode::text AS privacy, "
        "(SELECT count(DISTINCT r.entry_id) FROM retrievals r WHERE r.task_id = t.id) "
        "  AS retrieved, "
        "(SELECT count(*) FROM retrievals r JOIN outcomes o ON o.retrieval_id = r.id "
        " WHERE r.task_id = t.id AND o.agent_assessment IS NOT NULL) AS reported, "
        "(SELECT count(*) FROM task_events e WHERE e.task_id = t.id AND e.kind = 'test_run' "
        " AND (e.data->>'exit_code')::int = 0) AS tests_passed, "
        "(SELECT count(*) FROM task_events e WHERE e.task_id = t.id AND e.kind = 'test_run' "
        " AND (e.data->>'exit_code')::int <> 0) AS tests_failed "
        "FROM tasks t JOIN reporters rp ON rp.id = t.reporter_id "
        "LEFT JOIN models m ON m.id = t.model_id ORDER BY t.created_at DESC LIMIT %(limit)s",
        {"limit": limit},
    )


def reporters(conn: Connection) -> list[Row]:
    """Every reporter with contribution and accuracy figures."""
    return _rows(
        conn,
        "SELECT rp.pseudonym, rp.is_admin, rp.privacy_default::text AS privacy, rp.reliability, "
        "(SELECT count(*) FROM entries e WHERE e.created_by = rp.id) AS proposed, "
        "(SELECT count(*) FROM entries e WHERE e.created_by = rp.id AND e.status = 'approved') "
        "  AS approved, "
        "(SELECT count(*) FROM retrievals r WHERE r.reporter_id = rp.id) AS retrievals, "
        "(SELECT count(*) FROM retrievals r JOIN outcomes o ON o.retrieval_id = r.id "
        " WHERE r.reporter_id = rp.id) AS outcomes, "
        "(SELECT max(t.created_at) FROM tasks t WHERE t.reporter_id = rp.id) AS last_seen "
        "FROM reporters rp WHERE rp.pseudonym <> 'agent-commons-seed' "
        "ORDER BY retrievals DESC, rp.pseudonym",
        {},
    )


def models(conn: Connection) -> list[Row]:
    """Every model with retrievals and observed success rate (DESIGN §7 model attribution)."""
    return _rows(
        conn,
        "SELECT m.name, count(r.id) AS retrievals, "
        "count(o.id) FILTER (WHERE o.evidence->>'outcome_source' = 'hook') AS observed, "
        "count(o.id) FILTER (WHERE o.evidence->>'outcome_source' = 'hook' "
        "  AND o.outcome = ANY(%(success)s::outcome_kind[]))::float "
        "  / nullif(count(o.id) FILTER (WHERE o.evidence->>'outcome_source' = 'hook' "
        "  AND o.outcome NOT IN ('unknown', 'abandoned')), 0) AS observed_success_rate, "
        "(SELECT count(*) FROM entries e WHERE e.created_by_model = m.id) AS proposed "
        "FROM models m LEFT JOIN retrievals r ON r.model_id = m.id "
        "LEFT JOIN outcomes o ON o.retrieval_id = r.id "
        "WHERE m.name NOT IN ('seed', 'unknown') "
        "GROUP BY m.id, m.name ORDER BY retrievals DESC",
        _SUCCESS,
    )


def _rows(conn: Connection, query: str, params: dict[str, Any]) -> list[Row]:
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()

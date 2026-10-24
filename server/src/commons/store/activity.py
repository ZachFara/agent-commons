from psycopg.rows import class_row

from commons.domain.activity import RecentEntry, RecentRetrieval
from commons.store.connection import Connection


def recent_entries(conn: Connection, limit: int) -> list[RecentEntry]:
    """Newest entries first, whatever their status."""
    with conn.cursor(row_factory=class_row(RecentEntry)) as cursor:
        cursor.execute(
            "SELECT e.id AS entry_id, e.created_at, e.status, e.human_reviewed, e.symptom, "
            "rp.pseudonym AS proposed_by, m.name AS proposed_by_model "
            "FROM entries e JOIN reporters rp ON rp.id = e.created_by "
            "JOIN models m ON m.id = e.created_by_model "
            "ORDER BY e.created_at DESC, e.id LIMIT %s",
            (limit,),
        )
        return cursor.fetchall()


def recent_retrievals(conn: Connection, limit: int) -> list[RecentRetrieval]:
    """Newest retrievals first, with the outcome reported for each, if any."""
    with conn.cursor(row_factory=class_row(RecentRetrieval)) as cursor:
        cursor.execute(
            "SELECT r.created_at, r.task_id, r.query, r.entry_id, e.symptom, r.rank_position, "
            "r.score_at_retrieval, rp.pseudonym AS reporter, m.name AS model, o.outcome, "
            "o.evidence->>'outcome_source' AS outcome_source, o.agent_assessment "
            "FROM retrievals r JOIN entries e ON e.id = r.entry_id "
            "JOIN reporters rp ON rp.id = r.reporter_id JOIN models m ON m.id = r.model_id "
            "LEFT JOIN outcomes o ON o.retrieval_id = r.id "
            "ORDER BY r.created_at DESC, r.task_id, r.rank_position LIMIT %s",
            (limit,),
        )
        return cursor.fetchall()

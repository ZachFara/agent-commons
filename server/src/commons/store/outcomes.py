from uuid import UUID

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from commons.domain.outcomes import OutcomeRecord, Usage
from commons.store.connection import Connection
from commons.store.rows import returned_id


def task_retrieval(
    conn: Connection, task_id: UUID, entry_id: UUID, reporter_id: UUID
) -> UUID | None:
    """Return this reporter's earliest retrieval of the entry in the task, if any.

    Outcomes attach to the earliest one, so a task counts once however often it searched.
    """
    row = conn.execute(
        "SELECT id FROM retrievals WHERE task_id = %s AND entry_id = %s AND reporter_id = %s "
        "ORDER BY created_at, rank_position LIMIT 1",
        (task_id, entry_id, reporter_id),
    ).fetchone()
    return None if row is None else returned_id(row)


def locked_outcome(conn: Connection, retrieval_id: UUID) -> OutcomeRecord | None:
    """Return the retrieval's outcome, locked for update, or None if there is none yet."""
    with conn.cursor(row_factory=class_row(OutcomeRecord)) as cursor:
        cursor.execute(
            "SELECT outcome, evidence, agent_assessment FROM outcomes "
            "WHERE retrieval_id = %s FOR UPDATE",
            (retrieval_id,),
        )
        return cursor.fetchone()


def save_outcome(conn: Connection, retrieval_id: UUID, record: OutcomeRecord) -> UUID:
    """Insert or replace the outcome fields for a retrieval and return the outcome id."""
    cursor = conn.execute(
        "INSERT INTO outcomes (retrieval_id, outcome, evidence, agent_assessment) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (retrieval_id) DO UPDATE SET "
        # A standing correction of the outcome (decision 0030) wins over later reports.
        "outcome = CASE WHEN EXISTS (SELECT 1 FROM corrections c "
        "  WHERE c.target_table = 'outcomes' AND c.target_id = outcomes.id "
        "  AND c.field = 'outcome' AND c.undone_at IS NULL) "
        "THEN outcomes.outcome ELSE EXCLUDED.outcome END, evidence = EXCLUDED.evidence, "
        "agent_assessment = EXCLUDED.agent_assessment RETURNING id",
        (retrieval_id, record.outcome, Jsonb(record.evidence), record.agent_assessment),
    )
    return returned_id(cursor.fetchone())


def set_used(conn: Connection, retrieval_id: UUID, used: Usage | None) -> None:
    """Record whether the entry's fix was used (observed by hooks; NULL = unknown).

    A corrected `used` stands: later Stop hooks don't overwrite it.
    """
    conn.execute(
        "UPDATE outcomes SET used = %s WHERE retrieval_id = %s AND NOT EXISTS ("
        "  SELECT 1 FROM corrections c WHERE c.target_table = 'outcomes' "
        "  AND c.target_id = outcomes.id AND c.field = 'used' AND c.undone_at IS NULL)",
        (used, retrieval_id),
    )

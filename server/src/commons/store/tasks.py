from uuid import UUID

from commons.store.connection import Connection
from commons.store.rows import returned_id


def start_task(conn: Connection, session_id: str, reporter_id: UUID, model_id: UUID | None) -> UUID:
    """Return the session's task id, creating the task with the reporter's privacy default.

    A resumed session keeps its task; a newly observed model replaces the old one.
    """
    cursor = conn.execute(
        "INSERT INTO tasks (session_id, reporter_id, model_id, privacy_mode) "
        "SELECT %s, id, %s, privacy_default FROM reporters WHERE id = %s "
        "ON CONFLICT (session_id) DO UPDATE "
        "SET model_id = coalesce(EXCLUDED.model_id, tasks.model_id) RETURNING id",
        (session_id, model_id, reporter_id),
    )
    return returned_id(cursor.fetchone())

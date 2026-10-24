from uuid import UUID

from psycopg.types.json import Jsonb

from commons.domain.observations import TaskEventKind
from commons.store.connection import Connection
from commons.store.rows import returned_id


def task_for_session(conn: Connection, session_id: str) -> UUID | None:
    """Return the task id SessionStart created for this session, if any."""
    row = conn.execute("SELECT id FROM tasks WHERE session_id = %s", (session_id,)).fetchone()
    return None if row is None else returned_id(row)


def add_task_event(
    conn: Connection, task_id: UUID, kind: TaskEventKind, data: dict[str, object]
) -> None:
    """Record a derived observation for the task."""
    conn.execute(
        "INSERT INTO task_events (task_id, kind, data) VALUES (%s, %s, %s)",
        (task_id, kind, Jsonb(data)),
    )

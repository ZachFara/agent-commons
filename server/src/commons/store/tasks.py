from uuid import UUID

from commons.store.connection import Connection
from commons.store.rows import returned_id


def start_task(
    conn: Connection,
    session_id: str,
    reporter_id: UUID,
    model_id: UUID | None,
    hook_prompt_version_id: UUID | None = None,
) -> UUID | None:
    """Return the session's task id, creating the task with the reporter's privacy default.

    A resumed session keeps its task; a newly observed model or hook version replaces the
    old one. Returns None if the session id already belongs to another reporter.
    """
    cursor = conn.execute(
        "INSERT INTO tasks (session_id, reporter_id, model_id, privacy_mode, "
        "hook_prompt_version_id) "
        "SELECT %s, id, %s, privacy_default, %s FROM reporters WHERE id = %s "
        "ON CONFLICT (session_id) DO UPDATE "
        "SET model_id = coalesce(EXCLUDED.model_id, tasks.model_id), "
        "hook_prompt_version_id = "
        "coalesce(EXCLUDED.hook_prompt_version_id, tasks.hook_prompt_version_id) "
        "WHERE tasks.reporter_id = EXCLUDED.reporter_id RETURNING id",
        (session_id, model_id, hook_prompt_version_id, reporter_id),
    )
    row = cursor.fetchone()
    return None if row is None else returned_id(row)

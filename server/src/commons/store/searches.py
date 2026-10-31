from typing import cast
from uuid import UUID

from psycopg.types.json import Jsonb

from commons.domain.reporters import PrivacyDefault
from commons.domain.search import SearchRecord
from commons.store.connection import Connection


def privacy_mode(conn: Connection, reporter_id: UUID, task_id: UUID) -> PrivacyDefault:
    """Return the task's privacy mode if hooks recorded the task, else the reporter's default."""
    row = conn.execute(
        "SELECT coalesce("
        "  (SELECT privacy_mode FROM tasks WHERE id = %s AND reporter_id = rp.id), "
        "  rp.privacy_default)::text "
        "FROM reporters rp WHERE rp.id = %s",
        (task_id, reporter_id),
    ).fetchone()
    if row is None:
        msg = f"reporter {reporter_id} does not exist"
        raise RuntimeError(msg)
    return cast("PrivacyDefault", row[0])


def record_search(
    conn: Connection, record: SearchRecord, reporter_id: UUID, model_id: UUID
) -> None:
    """Log one searches row, whether or not anything was found."""
    conn.execute(
        "INSERT INTO searches (task_id, reporter_id, model_id, privacy_mode, query, language, "
        "result_limit, hit_count, environment, results) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            record.task_id,
            reporter_id,
            model_id,
            record.privacy_mode,
            record.query,
            record.language,
            record.limit,
            record.hit_count,
            None if record.environment is None else Jsonb(record.environment),
            None if record.results is None else Jsonb(record.results),
        ),
    )

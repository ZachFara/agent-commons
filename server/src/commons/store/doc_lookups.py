from uuid import UUID

from commons.domain.observations import DocLookupRecord
from commons.store.connection import Connection


def record_doc_lookup(
    conn: Connection, record: DocLookupRecord, reporter_id: UUID, *, excluded: bool = False
) -> None:
    """Log one doc_lookups row, with the model the task has recorded (NULL until seen)."""
    conn.execute(
        "INSERT INTO doc_lookups (task_id, reporter_id, model_id, privacy_mode, library_id, "
        "version, query, excluded) "
        "SELECT id, %s, model_id, %s, %s, %s, %s, %s FROM tasks WHERE id = %s",
        (
            reporter_id,
            record.privacy_mode,
            record.library_id,
            record.version,
            record.query,
            excluded,
            record.task_id,
        ),
    )

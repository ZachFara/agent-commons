from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from commons.domain.corrections import Correction, CorrectionField, CorrectionTable, CorrectionValue
from commons.domain.errors import NotFoundError
from commons.store.connection import Connection
from commons.store.rows import returned_id


def current_value(
    conn: Connection, table: CorrectionTable, field: CorrectionField, row_id: UUID
) -> CorrectionValue:
    """Return the field's value on the row (enums as text)."""
    row = conn.execute(
        sql.SQL("SELECT {}::text FROM {} WHERE id = %s").format(
            sql.Identifier(field), sql.Identifier(table)
        ),
        (row_id,),
    ).fetchone()
    if row is None:
        msg = f"No {table} row {row_id}."
        raise NotFoundError(msg)
    value: str | None = row[0]
    return value == "true" if field == "excluded" else value


def write_value(
    conn: Connection,
    table: CorrectionTable,
    field: CorrectionField,
    row_id: UUID,
    value: CorrectionValue,
) -> None:
    """Set the field on the row."""
    conn.execute(
        sql.SQL("UPDATE {} SET {} = %s WHERE id = %s").format(
            sql.Identifier(table), sql.Identifier(field)
        ),
        (value, row_id),
    )


def insert_correction(
    conn: Connection, correction: Correction, old: CorrectionValue, batch_id: UUID | None
) -> UUID:
    """Record the correction; return its id."""
    cursor = conn.execute(
        "INSERT INTO corrections (batch_id, corrected_by, target_table, target_id, field, "
        "old_value, new_value, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            batch_id,
            correction.by,
            correction.table,
            correction.row_id,
            correction.field,
            Jsonb(old),
            Jsonb(correction.value),
            correction.reason,
        ),
    )
    return returned_id(cursor.fetchone())


def standing_corrections(conn: Connection, correction_id: UUID) -> list[dict[str, Any]]:
    """Return the correction, or its whole batch, as far as not yet undone; newest first."""
    rows = conn.execute(
        "SELECT c.id, c.target_table, c.target_id, c.field, c.old_value FROM corrections c "
        "WHERE c.undone_at IS NULL AND (c.id = %(id)s OR c.batch_id = "
        "  (SELECT batch_id FROM corrections WHERE id = %(id)s AND batch_id IS NOT NULL)) "
        "ORDER BY c.created_at DESC",
        {"id": correction_id},
    ).fetchall()
    names = ("id", "table", "row_id", "field", "old_value")
    return [dict(zip(names, row, strict=True)) for row in rows]


def mark_undone(conn: Connection, correction_id: UUID, by: str) -> None:
    """Mark the correction undone."""
    conn.execute(
        "UPDATE corrections SET undone_at = now(), undone_by = %s WHERE id = %s",
        (by, correction_id),
    )


def task_rows(conn: Connection, task_id: UUID) -> list[tuple[CorrectionTable, UUID]]:
    """Return the task's retrievals and searches that still count."""
    rows = conn.execute(
        "SELECT 'retrievals', id FROM retrievals WHERE task_id = %(t)s AND NOT excluded "
        "UNION ALL SELECT 'searches', id FROM searches WHERE task_id = %(t)s AND NOT excluded",
        {"t": task_id},
    ).fetchall()
    return [(table, row_id) for table, row_id in rows]

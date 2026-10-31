"""Audited corrections and exclusions (decision 0030).

Raw observations are never deleted. A correction writes the new value into the row, so every
reader stays simple, and records the old value, who and why, so it can be undone. Outcome
changes re-rank at once.
"""

import uuid
from uuid import UUID

from commons.domain.corrections import Correction, validate_correction
from commons.domain.errors import InvalidInputError, NotFoundError
from commons.service.ranking import rank
from commons.store import corrections as store
from commons.store.connection import Connection

_RANKED_TABLES = frozenset({"outcomes", "retrievals"})


def correct(conn: Connection, correction: Correction, batch_id: UUID | None = None) -> UUID:
    """Apply one correction and return its id; re-rank if it touches outcomes."""
    validate_correction(correction)
    with conn.transaction():
        old = store.current_value(conn, correction.table, correction.field, correction.row_id)
        if old == correction.value:
            msg = f"{correction.field} is already {correction.value!r}."
            raise InvalidInputError(msg)
        correction_id = store.insert_correction(conn, correction, old, batch_id)
        store.write_value(
            conn, correction.table, correction.field, correction.row_id, correction.value
        )
        if batch_id is None and correction.table in _RANKED_TABLES:
            rank(conn)
    return correction_id


def exclude_task(conn: Connection, task_id: UUID, *, reason: str, by: str) -> int:
    """Exclude every retrieval and search of a task (e.g. test traffic); return how many.

    The corrections share a batch, so undoing any of them undoes the whole task.
    """
    batch_id = uuid.uuid4()
    with conn.transaction():
        rows = store.task_rows(conn, task_id)
        if not rows:
            msg = f"Task {task_id} has nothing left to exclude."
            raise NotFoundError(msg)
        for table, row_id in rows:
            exclusion = Correction(table, row_id, "excluded", value=True, reason=reason, by=by)
            correct(conn, exclusion, batch_id)
        rank(conn)
    return len(rows)


def undo(conn: Connection, correction_id: UUID, *, by: str) -> int:
    """Restore the old values of a correction (or its whole batch); return how many."""
    with conn.transaction():
        standing = store.standing_corrections(conn, correction_id)
        if not standing:
            msg = f"No standing correction {correction_id}."
            raise NotFoundError(msg)
        for row in standing:
            store.write_value(conn, row["table"], row["field"], row["row_id"], row["old_value"])
            store.mark_undone(conn, row["id"], by)
        rank(conn)
    return len(standing)

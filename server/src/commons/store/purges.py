from typing import Any
from uuid import UUID

from psycopg.rows import dict_row

from commons.store.connection import Connection


def active_key_ids(conn: Connection, reporter_id: UUID) -> list[UUID]:
    """Return the reporter's keys that still work."""
    rows = conn.execute(
        "SELECT id FROM api_keys WHERE reporter_id = %s AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > now())",
        (reporter_id,),
    )
    return [key_id for (key_id,) in rows]


def set_keys_revoked(conn: Connection, key_ids: list[UUID], *, revoked: bool) -> None:
    """Revoke the keys now, or (restoring a purge) make them work again."""
    conn.execute(
        "UPDATE api_keys SET revoked_at = CASE WHEN %s THEN now() ELSE NULL END WHERE id = ANY(%s)",
        (revoked, key_ids),
    )


def approved_entries_by(conn: Connection, reporter_id: UUID) -> list[UUID]:
    """Return the entries the reporter proposed that are live in search."""
    rows = conn.execute(
        "SELECT id FROM entries WHERE created_by = %s AND status = 'approved'", (reporter_id,)
    )
    return [entry_id for (entry_id,) in rows]


def counted_outcomes_by(conn: Connection, reporter_id: UUID) -> list[UUID]:
    """Return the outcomes on the reporter's retrievals that still count."""
    rows = conn.execute(
        "SELECT o.id FROM counted_outcomes o JOIN counted_retrievals r "
        "ON r.id = o.retrieval_id WHERE r.reporter_id = %s",
        (reporter_id,),
    )
    return [outcome_id for (outcome_id,) in rows]


def set_suspended(conn: Connection, reporter_id: UUID, *, suspended: bool) -> None:
    """Suspend the reporter (their pending entries leave the queue), or lift it."""
    conn.execute(
        "UPDATE reporters SET suspended_at = CASE WHEN %s THEN now() ELSE NULL END WHERE id = %s",
        (suspended, reporter_id),
    )


def record_purge(conn: Connection, purge: dict[str, Any]) -> UUID:
    """Store what a purge changed, so restoring can reverse exactly that."""
    row = conn.execute(
        "INSERT INTO purges (reporter_id, purged_by, reason, keys, retired_entries, "
        "correction_batch) VALUES (%(reporter_id)s, %(by)s, %(reason)s, %(keys)s, "
        "%(entries)s, %(batch)s) RETURNING id",
        purge,
    ).fetchone()
    if row is None:
        msg = "purge row vanished"
        raise RuntimeError(msg)
    purge_id: UUID = row[0]
    return purge_id


def latest_purge(conn: Connection, reporter_id: UUID) -> dict[str, Any] | None:
    """Return the reporter's most recent purge that has not been restored."""
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT id, keys, retired_entries, correction_batch FROM purges "
            "WHERE reporter_id = %s AND restored_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (reporter_id,),
        )
        return cursor.fetchone()


def mark_restored(conn: Connection, purge_id: UUID, by: str) -> None:
    """Record that the purge was reversed."""
    conn.execute(
        "UPDATE purges SET restored_at = now(), restored_by = %s WHERE id = %s", (by, purge_id)
    )


def first_correction(conn: Connection, batch_id: UUID) -> UUID | None:
    """Return any standing correction in the batch (undoing one undoes them all).

    None when someone already undid the batch by hand.
    """
    row = conn.execute(
        "SELECT id FROM corrections WHERE batch_id = %s AND undone_at IS NULL LIMIT 1",
        (batch_id,),
    ).fetchone()
    return None if row is None else row[0]

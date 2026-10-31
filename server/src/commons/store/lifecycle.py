from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from commons.domain.entries import Proposal
from commons.domain.errors import NotFoundError
from commons.domain.lifecycle import FixedIn
from commons.store.connection import Connection


def locked_entry(conn: Connection, entry_id: UUID) -> dict[str, Any]:
    """Return the entry's status and revision, locking the row."""
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            "SELECT status::text AS status, revision FROM entries WHERE id = %s FOR UPDATE",
            (entry_id,),
        )
        row = cursor.fetchone()
    if row is None:
        msg = f"No entry {entry_id}."
        raise NotFoundError(msg)
    return row


def add_revision(
    conn: Connection,
    entry_id: UUID,
    revision: int,
    content: tuple[Proposal, dict[str, object]],
    edit: tuple[str, str, str],
) -> None:
    """Store a new version of the entry and make it current.

    `content` is (text, redaction report); `edit` is (source, editor, reason).
    """
    text, redaction_report = content
    source, edited_by, reason = edit
    conn.execute(
        "INSERT INTO entry_revisions (entry_id, revision, symptom, environment, cause, fix, "
        "repro, domains, source, edited_by, reason, redaction_report) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            entry_id,
            revision,
            text.symptom,
            Jsonb(text.environment),
            text.cause,
            text.fix,
            text.repro,
            list(text.domains),
            source,
            edited_by,
            reason,
            Jsonb(redaction_report),
        ),
    )
    conn.execute(
        "UPDATE entries SET symptom = %s, environment = %s, cause = %s, fix = %s, repro = %s, "
        "domains = %s, redaction_report = %s, revision = %s WHERE id = %s",
        (
            text.symptom,
            Jsonb(text.environment),
            text.cause,
            text.fix,
            text.repro,
            list(text.domains),
            Jsonb(redaction_report),
            revision,
            entry_id,
        ),
    )


def set_retirement(
    conn: Connection, entry_id: UUID, fixed_in: FixedIn | None, superseded_by: UUID | None
) -> None:
    """Record what replaced or fixed the entry (both NULL when restoring)."""
    conn.execute(
        "UPDATE entries SET fixed_in = %s, superseded_by = %s WHERE id = %s",
        (Jsonb(fixed_in.as_json()) if fixed_in else None, superseded_by, entry_id),
    )


def entry_status(conn: Connection, entry_id: UUID) -> str | None:
    """Return the entry's status, or None if it doesn't exist."""
    row = conn.execute("SELECT status::text FROM entries WHERE id = %s", (entry_id,)).fetchone()
    return None if row is None else str(row[0])

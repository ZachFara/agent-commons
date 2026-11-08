from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from commons.domain.entries import Proposal
from commons.domain.errors import NotFoundError
from commons.store.connection import Connection
from commons.store.rows import returned_id
from commons.store.search import _SEARCHABLE


def insert_entry(
    conn: Connection,
    proposal: Proposal,
    redaction_report: dict[str, object],
    *,
    created_by: UUID,
    created_by_model: UUID,
) -> UUID:
    """Insert a pending entry and return its id."""
    cursor = conn.execute(
        "INSERT INTO entries (symptom, environment, cause, fix, repro, domains, task_id, "
        "created_by, created_by_model, redaction_report) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            proposal.symptom,
            Jsonb(proposal.environment),
            proposal.cause,
            proposal.fix,
            proposal.repro,
            list(proposal.domains),
            proposal.task_id,
            created_by,
            created_by_model,
            Jsonb(redaction_report),
        ),
    )
    return returned_id(cursor.fetchone())


def approved_fixes(conn: Connection) -> list[str]:
    """Every approved entry's fix text."""
    return [fix for (fix,) in conn.execute("SELECT fix FROM entries WHERE status = 'approved'")]


def set_risk_report(conn: Connection, entry_id: UUID, report: dict[str, object]) -> None:
    """Store what the risk scanner found in the entry's text (decision 0029)."""
    conn.execute("UPDATE entries SET risk_report = %s WHERE id = %s", (Jsonb(report), entry_id))


def scannable_entries(conn: Connection, *, lock: bool) -> list[dict[str, Any]]:
    """Return the text and risk report of pending entries and every entry search can show.

    Search shows retired entries with a fix version too, with their risk labels, so a rescan
    (0029) covers them: the set is search's own condition, never a second list of statuses.

    With `lock`, the rows stay locked until the transaction ends, so no edit lands between
    reading the text and storing its report.
    """
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_SCANNABLE_LOCKED if lock else _SCANNABLE)
        return cursor.fetchall()


def scan_row(conn: Connection, entry_id: UUID) -> dict[str, Any]:
    """Return one entry's row as `scannable_entries` does, locked until the transaction ends.

    The entry need not be one search shows: restoring rescans before a purged reporter is
    unsuspended.
    """
    with conn.cursor(row_factory=dict_row) as cursor:
        cursor.execute(_SCAN_COLUMNS + "WHERE e.id = %s FOR UPDATE", (entry_id,))
        row = cursor.fetchone()
    if row is None:
        msg = f"No entry {entry_id}."
        raise NotFoundError(msg)
    return row


_SCAN_COLUMNS = (
    "SELECT id, status::text AS status, symptom, environment, cause, fix, repro, domains, "
    "task_id, risk_report, provisional AND NOT human_reviewed AS unreviewed FROM entries e "
)
_SCANNABLE = (
    _SCAN_COLUMNS + "WHERE e.status = 'pending' OR {searchable} ORDER BY created_at, id"
).replace("{searchable}", _SEARCHABLE)
_SCANNABLE_LOCKED = _SCANNABLE + " FOR UPDATE"


def set_imported_from(conn: Connection, entry_id: UUID, origin: dict[str, object]) -> None:
    """Record where an imported entry came from (decision 0031)."""
    conn.execute("UPDATE entries SET imported_from = %s WHERE id = %s", (Jsonb(origin), entry_id))


def importable_entries(conn: Connection, statuses: list[str]) -> list[dict[str, object]]:
    """Local entries that may move to a hosted store: never seeds, never rejected ones."""
    rows = conn.execute(
        "SELECT e.id, e.symptom, e.environment, e.cause, e.fix, e.repro, e.domains, "
        "e.status::text, e.created_at, m.name FROM entries e "
        "JOIN models m ON m.id = e.created_by_model JOIN reporters rp ON rp.id = e.created_by "
        "WHERE e.status::text = ANY(%s) AND e.status <> 'rejected' "
        "AND rp.pseudonym <> 'agent-commons-seed' AND e.imported_from IS NULL "
        "ORDER BY e.created_at",
        (statuses,),
    ).fetchall()
    names = (
        "id",
        "symptom",
        "environment",
        "cause",
        "fix",
        "repro",
        "domains",
        "status",
        "created_at",
        "model",
    )
    return [dict(zip(names, row, strict=True)) for row in rows]

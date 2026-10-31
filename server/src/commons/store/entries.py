from uuid import UUID

from psycopg.types.json import Jsonb

from commons.domain.entries import Proposal
from commons.store.connection import Connection
from commons.store.rows import returned_id


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

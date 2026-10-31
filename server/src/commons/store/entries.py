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

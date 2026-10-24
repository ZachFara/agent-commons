from uuid import UUID

from psycopg.types.json import Jsonb

from commons.domain.entries import Proposal
from commons.store.connection import Connection
from commons.store.rows import returned_id


def insert_entry(
    conn: Connection, proposal: Proposal, created_by: UUID, created_by_model: UUID
) -> UUID:
    """Insert a pending entry and return its id."""
    cursor = conn.execute(
        "INSERT INTO entries (symptom, environment, cause, fix, repro, domains, task_id, "
        "created_by, created_by_model) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
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
        ),
    )
    return returned_id(cursor.fetchone())

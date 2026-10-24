from uuid import UUID

from psycopg.types.json import Jsonb

from commons.domain.entries import Proposal
from commons.store.connection import Connection


def insert_seed_entry(
    conn: Connection, entry_id: UUID, proposal: Proposal, seed_reporter: UUID, seed_model: UUID
) -> bool:
    """Insert a seed entry as approved and human-reviewed; return False if it already exists."""
    cursor = conn.execute(
        "WITH e AS (INSERT INTO entries (id, symptom, environment, cause, fix, repro, domains, "
        "status, human_reviewed, created_by, created_by_model) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'approved', true, %s, %s) "
        "ON CONFLICT (id) DO NOTHING RETURNING id) "
        "INSERT INTO reviews (entry_id, reviewed_by, decision, reason, source) "
        "SELECT id, %s, 'approve', 'Seed entry, reviewed in the agent-commons repository.', "
        "'human' FROM e RETURNING entry_id",
        (
            entry_id,
            proposal.symptom,
            Jsonb(proposal.environment),
            proposal.cause,
            proposal.fix,
            proposal.repro,
            list(proposal.domains),
            seed_reporter,
            seed_model,
            seed_reporter,
        ),
    )
    return cursor.fetchone() is not None

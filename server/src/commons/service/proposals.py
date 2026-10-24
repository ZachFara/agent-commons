from dataclasses import dataclass, field
from uuid import UUID

from commons.domain.entries import EntryStatus, Proposal, validate_proposal
from commons.service.identity import current_model, current_reporter
from commons.store.connection import Connection
from commons.store.entries import insert_entry


@dataclass(frozen=True)
class ProposeResult:
    """The new entry and what was redacted from it before writing."""

    entry_id: UUID
    status: EntryStatus
    # Secret scanning lands in T21; until then nothing is redacted and the report says so.
    redaction_report: dict[str, object] = field(
        default_factory=lambda: {"scanned": False, "findings": []}
    )


def propose(conn: Connection, proposal: Proposal, model: str | None) -> ProposeResult:
    """Validate and store a pending entry from this machine's reporter (decision 0009 model)."""
    validate_proposal(proposal)
    with conn.transaction():
        reporter = current_reporter(conn)
        model_row = current_model(conn, model)
        entry_id = insert_entry(conn, proposal, created_by=reporter, created_by_model=model_row)
    return ProposeResult(entry_id=entry_id, status="pending")

from dataclasses import dataclass
from uuid import UUID

from commons.domain.entries import EntryStatus, Proposal, validate_proposal
from commons.privacy.redaction import redact_proposal
from commons.service.identity import current_model, current_reporter
from commons.store.connection import Connection
from commons.store.entries import insert_entry


@dataclass(frozen=True)
class ProposeResult:
    """The new entry and what was redacted from it before writing."""

    entry_id: UUID
    status: EntryStatus
    redaction_report: dict[str, object]


def propose(conn: Connection, proposal: Proposal, model: str | None) -> ProposeResult:
    """Validate, redact, and store a pending entry from this machine's reporter.

    Validation runs before redaction so its messages describe what the agent sent.
    """
    validate_proposal(proposal)
    redacted, redaction_report = redact_proposal(proposal)
    with conn.transaction():
        reporter = current_reporter(conn)
        model_row = current_model(conn, model)
        entry_id = insert_entry(
            conn,
            redacted,
            redaction_report,
            created_by=reporter,
            created_by_model=model_row,
        )
    return ProposeResult(entry_id=entry_id, status="pending", redaction_report=redaction_report)

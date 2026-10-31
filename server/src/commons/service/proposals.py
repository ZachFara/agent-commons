from dataclasses import dataclass
from uuid import UUID

from commons.domain.entries import EntryStatus, Proposal, validate_proposal
from commons.privacy.redaction import redact_proposal
from commons.privacy.risks import scan_proposal
from commons.service.duplicates import flag_duplicates
from commons.service.identity import current_model, current_reporter
from commons.service.rates import limit
from commons.service.trust import after_propose
from commons.store.connection import Connection
from commons.store.entries import insert_entry, set_risk_report


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
    redacted, risk_report = scan_proposal(redacted)
    with conn.transaction():
        limit(conn, "propose")
        reporter = current_reporter(conn)
        model_row = current_model(conn, model)
        entry_id = insert_entry(
            conn,
            redacted,
            redaction_report,
            created_by=reporter,
            created_by_model=model_row,
        )
        set_risk_report(conn, entry_id, risk_report)
        # Flagged for the reviewer only: the reply to the agent is unchanged (decision 0027).
        candidates = flag_duplicates(conn, entry_id, redacted, reporter)
        risky = bool(risk_report.get("findings"))
        approved = after_propose(conn, entry_id, reporter, risky=risky, duplicate=bool(candidates))
    status: EntryStatus = "approved" if approved else "pending"
    return ProposeResult(entry_id=entry_id, status=status, redaction_report=redaction_report)

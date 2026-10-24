from dataclasses import dataclass, replace
from uuid import UUID

from commons.domain.errors import NotFoundError
from commons.domain.outcomes import OutcomeRecord, Report, merge_report, validate_report
from commons.privacy.redaction import scrub, scrub_json
from commons.service.identity import current_reporter
from commons.store.connection import Connection
from commons.store.outcomes import locked_outcome, save_outcome, task_retrieval


@dataclass(frozen=True)
class ReportResult:
    """The stored outcome after merging the report."""

    outcome_id: UUID
    record: OutcomeRecord


def report(conn: Connection, request: Report) -> ReportResult:
    """Store the agent's report on an entry it retrieved in this task."""
    validate_report(request)
    request = replace(
        request,
        agent_assessment=scrub(request.agent_assessment),
        evidence=None if request.evidence is None else scrub_json(request.evidence),
    )
    with conn.transaction():
        reporter = current_reporter(conn)
        retrieval_id = task_retrieval(conn, request.task_id, request.entry_id, reporter)
        if retrieval_id is None:
            msg = "That entry was not retrieved by you in this task; report only on search results."
            raise NotFoundError(msg)
        record = merge_report(locked_outcome(conn, retrieval_id), request)
        outcome_id = save_outcome(conn, retrieval_id, record)
    return ReportResult(outcome_id=outcome_id, record=record)

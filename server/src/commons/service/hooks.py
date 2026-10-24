"""What the store records for Claude Code hooks (DESIGN.md §5, decision 0016).

These run where the database is: in the hook process locally, on the server when hosted.
Everything that reads the user's machine (spool, transcript) is in `hook_client`.
"""

from dataclasses import dataclass
from uuid import UUID

from commons.domain.errors import NotPermittedError
from commons.domain.outcomes import ObservedRun, Usage, merge_observation
from commons.service.identity import current_reporter
from commons.service.ranking import rank, ranking_is_stale
from commons.store.connection import Connection
from commons.store.models import model_id
from commons.store.outcomes import locked_outcome, save_outcome, set_used
from commons.store.task_events import (
    TaskRetrieval,
    add_task_event,
    set_task_model,
    task_for_session,
    task_retrievals,
    test_runs,
)
from commons.store.tasks import start_task


def session_start(conn: Connection, session_id: str, model: str | None) -> UUID:
    """Create or resume the calling reporter's task for this session; return its id.

    Re-ranks if snapshots are over a day old (decision 0017), so neither mode needs a
    scheduler; ranking is fast at this scale.
    """
    with conn.transaction():
        reporter = current_reporter(conn)
        model_row = model_id(conn, model) if model else None
        task_id = start_task(conn, session_id, reporter, model_row)
        if task_id is None:
            msg = "That session belongs to another reporter."
            raise NotPermittedError(msg)
    if ranking_is_stale(conn):
        rank(conn)
    return task_id


def record_tool_use(
    conn: Connection,
    session_id: str,
    test_run: dict[str, object] | None,
    file_change: dict[str, object] | None,
) -> None:
    """Store derived observations of one tool call; ignore sessions we have no task for."""
    with conn.transaction():
        task_id = task_for_session(conn, session_id, current_reporter(conn))
        if task_id is None:
            return
        if test_run is not None:
            add_task_event(conn, task_id, "test_run", test_run)
        if file_change is not None:
            add_task_event(conn, task_id, "file_change", file_change)


def retrievals_to_judge(conn: Connection, session_id: str) -> list[TaskRetrieval]:
    """Return the entries retrieved in this session's task, for the client to judge `used`."""
    task_id = task_for_session(conn, session_id, current_reporter(conn))
    return [] if task_id is None else task_retrievals(conn, task_id)


@dataclass(frozen=True)
class StopResult:
    """What Stop recorded, and which retrieved entries still lack the agent's report."""

    task_id: UUID
    unreported: list[UUID]


def finish_turn(
    conn: Connection, session_id: str, model: str | None, used: dict[UUID, Usage | None]
) -> StopResult | None:
    """Record the observed model, `used` per retrieval, and observed outcomes (decision 0016)."""
    with conn.transaction():
        task_id = task_for_session(conn, session_id, current_reporter(conn))
        if task_id is None:
            return None
        if model:
            set_task_model(conn, task_id, model_id(conn, model))
        runs = test_runs(conn, task_id)
        retrieved = task_retrievals(conn, task_id)
        for item in retrieved:
            _record_observation(conn, item, runs, used.get(item.retrieval_id))
    return StopResult(task_id, [item.entry_id for item in retrieved if not item.reported])


def _record_observation(
    conn: Connection, item: TaskRetrieval, runs: list[ObservedRun], used: Usage | None
) -> None:
    after = [run for run in runs if run.at > item.retrieved_at]
    record = merge_observation(
        locked_outcome(conn, item.retrieval_id), after[-1] if after else None
    )
    save_outcome(conn, item.retrieval_id, record)
    set_used(conn, item.retrieval_id, used)

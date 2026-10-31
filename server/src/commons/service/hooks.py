"""What the store records for Claude Code hooks (DESIGN.md §5, decision 0016).

These run where the database is: in the hook process locally, on the server when hosted.
Everything that reads the user's machine (spool, transcript) is in `hook_client`.
"""

import os
from dataclasses import dataclass
from uuid import UUID

from commons.domain.errors import NotPermittedError
from commons.domain.outcomes import ObservedRun, merge_observation
from commons.domain.usage import ArtifactFrequencies, UseObservation, count_artifacts
from commons.service.agent_text import TURN_NUDGE
from commons.service.identity import current_reporter
from commons.service.ranking import rank, ranking_is_stale
from commons.store.connection import Connection
from commons.store.entries import approved_fixes
from commons.store.models import model_id
from commons.store.outcomes import locked_outcome, save_outcome, set_used
from commons.store.task_events import (
    TaskRetrieval,
    acted_in_turn,
    add_task_event,
    end_turn,
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
    """What Stop recorded, which retrieved entries still lack a report, and what it observed."""

    task_id: UUID
    unreported: list[UUID]
    fixed_failing_tests: bool = False  # a failing test run followed later by a passing one
    searched: bool = False  # the task has at least one retrieval
    nudge: str | None = None  # the store's per-turn nudge, when it is on (decision 0034)


def fixed_failing_tests(runs: list[ObservedRun]) -> bool:
    """Whether some test run failed and a later one passed."""
    first_failure = next((run.at for run in runs if run.exit_code != 0), None)
    return first_failure is not None and any(
        run.exit_code == 0 and run.at > first_failure for run in runs
    )


def finish_turn(
    conn: Connection,
    session_id: str,
    model: str | None,
    used: dict[UUID, UseObservation],
    *,
    paired: bool = True,
) -> StopResult | None:
    """Record the observed model, `used` per retrieval, and observed outcomes (decision 0016).

    With `paired` (decision 0036), a test run counts for a retrieval only if it came after
    the work that matched the fix. Hooks from before 0036 don't say when that was, so their
    turns are recorded unpaired, as before.
    """
    with conn.transaction():
        task_id = task_for_session(conn, session_id, current_reporter(conn))
        if task_id is None:
            return None
        if model:
            set_task_model(conn, task_id, model_id(conn, model))
        runs = test_runs(conn, task_id)
        retrieved = task_retrievals(conn, task_id)
        for item in retrieved:
            seen = used.get(item.retrieval_id, UseObservation(None))
            _record_observation(conn, item, runs, seen, paired=paired)
        nudge = _turn_nudge(conn, task_id, runs)
    return StopResult(
        task_id,
        [item.entry_id for item in retrieved if not item.reported],
        fixed_failing_tests=fixed_failing_tests(runs),
        searched=bool(retrieved),
        nudge=nudge,
    )


def _turn_nudge(conn: Connection, task_id: UUID, runs: list[ObservedRun]) -> str | None:
    """Return the per-turn nudge if it is on and this turn earned it; mark the turn ended.

    It fires when the turn saw a failing test become a passing one with no search and no
    proposal in the turn (decision 0034).
    """
    since = end_turn(conn, task_id)
    if os.environ.get("COMMONS_TURN_NUDGE", "off").strip().lower() != "on":
        return None
    turn = [run for run in runs if since is None or run.at > since]
    if not fixed_failing_tests(turn) or acted_in_turn(conn, task_id, since):
        return None
    return TURN_NUDGE.format(task_id=task_id)


def _record_observation(
    conn: Connection,
    item: TaskRetrieval,
    runs: list[ObservedRun],
    seen: UseObservation,
    *,
    paired: bool,
) -> None:
    after = [run for run in runs if run.at > item.retrieved_at]
    if paired:
        after = [run for run in after if run.exit_code != 0 or _follows_matched_work(run, seen)]
    record = merge_observation(
        locked_outcome(conn, item.retrieval_id), after[-1] if after else None
    )
    save_outcome(conn, item.retrieval_id, record)
    set_used(conn, item.retrieval_id, seen.used)


def _follows_matched_work(run: ObservedRun, seen: UseObservation) -> bool:
    """Whether a passing run can be credited to the entry: it came after work that matched.

    A pass with no matched work proves nothing about the entry (decision 0036). Failing runs
    still count, so an agent's claim of success can be checked against them.
    """
    if seen.used not in {"yes", "partial"}:
        return False
    return seen.matched_at is None or run.at > seen.matched_at


def artifact_frequencies(conn: Connection) -> ArtifactFrequencies:
    """Count fix artifacts across approved entries, for the hooks' rarity weights (0036)."""
    return count_artifacts(approved_fixes(conn))

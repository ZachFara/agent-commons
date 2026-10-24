"""What Claude Code hooks record (DESIGN.md §5, decision 0016)."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from commons.domain.observations import ToolUse, is_test_command
from commons.domain.outcomes import ObservedRun, merge_observation
from commons.domain.usage import compute_used
from commons.privacy.redaction import scrub
from commons.service.identity import configured_test_runners, current_reporter
from commons.service.local_state import append_spool, prune_spools, read_spool
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
    """Create or resume the session's task and return its task id.

    Also does the daily housekeeping: prunes old spools and, if snapshots are over a day
    old, re-ranks (decision 0017). Ranking is fast at local scale; hosted mode schedules it.
    """
    prune_spools()
    with conn.transaction():
        reporter = current_reporter(conn)
        model_row = model_id(conn, model) if model else None
        task_id = start_task(conn, session_id, reporter, model_row)
    if ranking_is_stale(conn):
        rank(conn)
    return task_id


def observe_tool_use(conn: Connection, use: ToolUse) -> None:
    """Record a tool call: scrubbed text to the local spool, derived facts to the store."""
    with conn.transaction():
        task_id = task_for_session(conn, use.session_id)
        if task_id is None:
            return
        if use.command is not None:
            append_spool(task_id, {"tool": use.tool_name, "command": scrub(use.command)})
        if use.new_text is not None:
            append_spool(task_id, {"tool": use.tool_name, "text": scrub(use.new_text)})
        if _is_test_run(use):
            run: dict[str, object] = {
                "command": scrub(use.command or ""),
                "exit_code": use.exit_code,
            }
            add_task_event(conn, task_id, "test_run", run)
        if use.file_path is not None and use.new_text is not None:
            change: dict[str, object] = {
                "path_sha256": _sha256(use.file_path),
                "content_sha256": _sha256(use.new_text),
            }
            add_task_event(conn, task_id, "file_change", change)


def _is_test_run(use: ToolUse) -> bool:
    if use.command is None or use.exit_code is None:
        return False
    return is_test_command(use.command, configured_test_runners())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StopResult:
    """What Stop recorded, and which retrieved entries still lack the agent's report."""

    task_id: UUID
    unreported: list[UUID]


def stop(conn: Connection, session_id: str, transcript_path: str | None) -> StopResult | None:
    """Record observed model, `used`, and observed outcomes for the task (decision 0016)."""
    with conn.transaction():
        task_id = task_for_session(conn, session_id)
        if task_id is None:
            return None
        model = observed_model(Path(transcript_path)) if transcript_path else None
        if model:
            set_task_model(conn, task_id, model_id(conn, model))
        runs = test_runs(conn, task_id)
        session_text = _session_text(task_id)
        retrieved = task_retrievals(conn, task_id)
        for item in retrieved:
            _record_observation(conn, item, runs, session_text)
    return StopResult(task_id, [item.entry_id for item in retrieved if not item.reported])


def _record_observation(
    conn: Connection, item: TaskRetrieval, runs: list[ObservedRun], session_text: str
) -> None:
    after = [run for run in runs if run.at > item.retrieved_at]
    record = merge_observation(
        locked_outcome(conn, item.retrieval_id), after[-1] if after else None
    )
    save_outcome(conn, item.retrieval_id, record)
    set_used(conn, item.retrieval_id, compute_used(item.fix, session_text))


def _session_text(task_id: UUID) -> str:
    parts = (record.get("command") or record.get("text") for record in read_spool(task_id))
    return "\n".join(str(part) for part in parts if part)


def observed_model(transcript: Path) -> str | None:
    """Return the model of the transcript's last assistant message, if it records one."""
    if not transcript.exists():
        return None
    model = None
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            message = json.loads(line).get("message")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(message, dict) and isinstance(message.get("model"), str):
            model = message["model"]
    return model

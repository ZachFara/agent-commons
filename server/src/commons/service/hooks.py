"""What Claude Code hooks record (DESIGN.md §5, decision 0016)."""

import hashlib
from uuid import UUID

from commons.domain.observations import ToolUse, is_test_command
from commons.privacy.redaction import scrub
from commons.service.identity import configured_test_runners, current_reporter
from commons.service.local_state import append_spool
from commons.store.connection import Connection
from commons.store.models import model_id
from commons.store.task_events import add_task_event, task_for_session
from commons.store.tasks import start_task


def session_start(conn: Connection, session_id: str, model: str | None) -> UUID:
    """Create or resume the session's task and return its task id."""
    with conn.transaction():
        reporter = current_reporter(conn)
        model_row = model_id(conn, model) if model else None
        return start_task(conn, session_id, reporter, model_row)


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

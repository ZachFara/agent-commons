"""`commons hook <event>`: Claude Code hook entry points (decisions 0006, 0016).

Hooks must never break the user's session. Every failure is logged to the local state
directory and the hook exits 0 with no output.
"""

import json
import logging
from collections.abc import Callable
from typing import Any, TextIO

from commons.domain.errors import NotInitializedError
from commons.service import hooks
from commons.service.local_state import state_dir
from commons.store.connection import connect

logger = logging.getLogger("commons.hooks")

Payload = dict[str, Any]
HookOutput = dict[str, Any] | None


def _session_start(payload: Payload) -> HookOutput:
    session_id = payload["session_id"]
    model = payload.get("model")
    if not isinstance(session_id, str) or not (model is None or isinstance(model, str)):
        msg = "session_id must be a string and model a string if present"
        raise TypeError(msg)
    with connect() as conn:
        task_id = hooks.session_start(conn, session_id, model)
    context = (
        f"Agent Commons task_id for this session: {task_id}. "
        "Pass it as task_id to every commons tool call."
    )
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


HANDLERS: dict[str, Callable[[Payload], HookOutput]] = {
    "session-start": _session_start,
}


def run_hook(event: str, stdin: TextIO, stdout: TextIO) -> int:
    """Handle one hook event from stdin. Always returns 0."""
    _log_to_state_dir()
    try:
        output = HANDLERS[event](_read_payload(stdin))
    except NotInitializedError as error:
        logger.info("%s skipped: %s", event, error)
        return 0
    except Exception:
        logger.exception("%s hook failed", event)
        return 0
    if output is not None:
        stdout.write(json.dumps(output))
    return 0


def _read_payload(stdin: TextIO) -> Payload:
    payload = json.load(stdin)
    if not isinstance(payload, dict):
        msg = "hook payload must be a JSON object"
        raise TypeError(msg)
    return payload


def _log_to_state_dir() -> None:
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(directory / "hooks.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

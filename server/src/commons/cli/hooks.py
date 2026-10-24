"""`commons hook <event>`: Claude Code hook entry points (decisions 0006, 0016).

Hooks must never break the user's session. Every failure is logged to the local state
directory and the hook exits 0 with no output.
"""

import json
import logging
from collections.abc import Callable
from typing import Any, TextIO

from commons.domain.errors import NotInitializedError
from commons.domain.observations import ToolUse, exit_code_from_error
from commons.service import hook_client
from commons.service.hook_client import HookBackend, LocalBackend
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
    task_id = hook_client.on_session_start(_backend(), session_id, model)
    context = (
        f"Agent Commons task_id for this session: {task_id}. "
        "Pass it as task_id to every commons tool call."
    )
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


def _post_tool_use(payload: Payload) -> HookOutput:
    _observe(_tool_use(payload, failed=False))
    return None


def _post_tool_use_failure(payload: Payload) -> HookOutput:
    _observe(_tool_use(payload, failed=True))
    return None


def _observe(use: ToolUse) -> None:
    hook_client.on_tool_use(_backend(), use)


def _backend() -> HookBackend:
    return LocalBackend(connect)


def _tool_use(payload: Payload, *, failed: bool) -> ToolUse:
    """Parse a PostToolUse / PostToolUseFailure payload (shapes checked in decision 0016)."""
    name = str(payload["tool_name"])
    tool_input = payload.get("tool_input") or {}
    is_bash = name == "Bash"
    return ToolUse(
        session_id=str(payload["session_id"]),
        tool_name=name,
        command=str(tool_input.get("command", "")) if is_bash else None,
        file_path=None if is_bash or failed else tool_input.get("file_path"),
        new_text=None if is_bash or failed else _new_text(tool_input),
        exit_code=_exit_code(payload, failed=failed) if is_bash else None,
    )


def _new_text(tool_input: Payload) -> str | None:
    """Text an Edit (new_string), Write (content), or MultiEdit (edits[].new_string) adds."""
    if "edits" in tool_input:
        return "\n".join(str(edit.get("new_string", "")) for edit in tool_input["edits"])
    text = tool_input.get("new_string", tool_input.get("content"))
    return None if text is None else str(text)


def _exit_code(payload: Payload, *, failed: bool) -> int | None:
    if not failed:
        return 0
    if payload.get("is_interrupt"):
        return None
    return exit_code_from_error(str(payload.get("error", "")))


def _stop(payload: Payload) -> HookOutput:
    transcript = payload.get("transcript_path")
    session_id = str(payload["session_id"])
    result = hook_client.on_stop(_backend(), session_id, str(transcript) if transcript else None)
    if result is None or not result.unreported or payload.get("stop_hook_active"):
        return None
    ids = ", ".join(str(entry_id) for entry_id in result.unreported)
    reason = (
        f"Before finishing, call the commons `report` tool with task_id {result.task_id} for "
        f"each entry you retrieved this session but haven't reported on: {ids}. Say honestly "
        "whether it helped."
    )
    return {"decision": "block", "reason": reason}


HANDLERS: dict[str, Callable[[Payload], HookOutput]] = {
    "session-start": _session_start,
    "post-tool-use": _post_tool_use,
    "post-tool-use-failure": _post_tool_use_failure,
    "stop": _stop,
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

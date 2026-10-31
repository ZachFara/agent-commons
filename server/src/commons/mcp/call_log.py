"""One log line per tool call (decision 0024), so the host's logs show which tool ran.

Lines are JSON with a `severity` field, which Cloud Logging reads as structured entries. They
carry ids, counts, and timings only: never queries or entry text, since server logs sit
outside the store's privacy modes.
"""

import json
import logging
import sys
import time
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from typing_extensions import override

logger = logging.getLogger("commons.calls")
_MAX_FIELD = 80


class CallLog(Middleware):
    """Log every tool call: tool, reporter, task, model, result count, error code, duration."""

    @override
    async def __call__(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        """Pass every message through, logging tool calls.

        Overrides `__call__` rather than `on_call_tool` for the reason StructuredValidationErrors
        gives: the dead-code check cannot see framework hooks.
        """
        if context.method != "tools/call":
            return await call_next(context)
        started = time.perf_counter()
        line = _describe(context.message)
        try:
            result = await call_next(context)
        except ToolError as error:
            line.update(ok=False, error=_error_code(error), severity="WARNING")
            raise
        except Exception:
            line.update(ok=False, error="internal", severity="ERROR")
            raise
        else:
            data = getattr(result, "structured_content", None) or {}
            items = data.get("data") if isinstance(data, dict) else None
            line.update(ok=True, results=len(items) if isinstance(items, list) else None)
            return result
        finally:
            line["ms"] = round((time.perf_counter() - started) * 1000)
            logger.info(json.dumps(line))


def log_calls_to_stdout() -> None:
    """Write call lines to stdout, where Cloud Run collects them.

    Never use this over stdio: there stdout is the MCP channel itself.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _describe(message: object) -> dict[str, Any]:
    arguments = getattr(message, "arguments", None) or {}
    token = get_access_token()
    return {
        "severity": "INFO",
        "event": "tool_call",
        "tool": getattr(message, "name", None),
        "reporter_id": None if token is None else token.claims.get("reporter_id"),
        "task_id": _short(arguments.get("task_id")),
        "model": _short(arguments.get("model")),
    }


def _short(value: object) -> str | None:
    """Agents fill these in, so cap them: a log line is not the place for long text."""
    return None if value is None else str(value)[:_MAX_FIELD]


def _error_code(error: ToolError) -> str:
    try:
        return str(json.loads(str(error))["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return "error"

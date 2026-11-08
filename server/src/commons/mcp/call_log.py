"""One log line and one duration sample per tool call, and its fields on the call's span.

Decisions 0024 and 0037. Lines are JSON with a `severity` field, which Cloud Logging reads as
structured entries. FastMCP opens a `tools/call` span for every call; this middleware puts the
same fields on it. Lines, spans and samples carry ids, counts, and timings only: never queries
or entry text, since server logs and telemetry sit outside the store's privacy modes. Lines and
spans also name the served prompt version (decision MIT-566), so calls that never reach the
database, such as ones rejected on argument shape, can still be counted per version.
"""

import json
import logging
import sys
import time
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from opentelemetry import metrics, trace
from opentelemetry.semconv.attributes.error_attributes import ERROR_TYPE
from opentelemetry.trace import Span, StatusCode
from typing_extensions import override

from commons.service.events import trace_fields

logger = logging.getLogger("commons.calls")
# INFO lines reach whichever handlers are installed (stdout when hosted, OTLP when configured);
# with none installed, Python's last-resort handler drops them.
logger.setLevel(logging.INFO)
_duration = metrics.get_meter("commons.calls").create_histogram(
    "commons.tool_call.duration",
    unit="s",
    description="How long each MCP tool call took, by tool and error type.",
    explicit_bucket_boundaries_advisory=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
_MAX_FIELD = 80
_SHORT_HASH = 12  # a prompt version is shown as the first 12 hex characters of its hash
# Incubating semantic conventions for MCP: the method, and the tool a tools/call names.
MCP_METHOD = "mcp.method.name"
TOOL_NAME = "gen_ai.tool.name"


class CallLog(Middleware):
    """Observe every tool call: tool, reporter, task, model, prompt, results, error, duration."""

    def __init__(self, prompt_hash: str) -> None:
        """`prompt_hash` is the served prompt version's full hash; lines carry its first 12."""
        self._prompt = prompt_hash[:_SHORT_HASH]

    @override
    async def __call__(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        """Pass every message through, observing tool calls.

        Overrides `__call__` rather than `on_call_tool` for the reason StructuredValidationErrors
        gives: the dead-code check cannot see framework hooks.
        """
        if context.method != "tools/call":
            return await call_next(context)
        line = {**_describe(context.message), "prompt": self._prompt}
        span = trace.get_current_span()  # FastMCP's `tools/call` span, or none over a bare stack
        started = time.perf_counter()
        try:
            result = await call_next(context)
        except ToolError as error:
            line.update(ok=False, error=_error_code(error), severity="WARNING")
            raise
        except Exception:
            line.update(ok=False, error="internal", severity="ERROR")
            span.set_status(StatusCode.ERROR)
            raise
        else:
            data = getattr(result, "structured_content", None) or {}
            items = data.get("data") if isinstance(data, dict) else None
            line.update(ok=True, results=len(items) if isinstance(items, list) else None)
            return result
        finally:
            seconds = time.perf_counter() - started
            line["ms"] = round(seconds * 1000)
            _observe(span, line, seconds)
            logger.info(json.dumps({**line, **trace_fields(span)}))


def log_calls_to_stdout() -> None:
    """Write call lines, event lines and this package's warnings to stdout, for the host's logs.

    Never use this over stdio: there stdout is the MCP channel itself.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("commons").handlers = [handler]


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


def _observe(span: Span, line: dict[str, Any], seconds: float) -> None:
    """Record the duration sample, and mirror the line's fields on the span as `commons.*`.

    The error code goes under `commons.error`: FastMCP writes its own, coarser `error.type`
    on the same span after this runs.
    """
    labels = {TOOL_NAME: str(line["tool"])}
    if line.get("results") is not None:  # a listing tool: a search miss is "none"
        labels["commons.results"] = "none" if line["results"] == 0 else "some"
    if not line["ok"]:
        labels[ERROR_TYPE] = str(line["error"])
    _duration.record(seconds, labels)
    span.set_attributes({MCP_METHOD: "tools/call", TOOL_NAME: labels[TOOL_NAME]})
    for key in ("reporter_id", "task_id", "model", "prompt", "results", "error"):
        if line.get(key) is not None:
            span.set_attribute(f"commons.{key}", line[key])


def _short(value: object) -> str | None:
    """Agents fill these in, so cap them: a log line is not the place for long text."""
    return None if value is None else str(value)[:_MAX_FIELD]


def _error_code(error: ToolError) -> str:
    try:
        return str(json.loads(str(error))["error"]["code"])
    except (ValueError, KeyError, TypeError):
        return "error"

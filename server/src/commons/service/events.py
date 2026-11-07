"""Operational event lines (decisions 0024, 0037): what to read back after something went wrong.

One JSON line per event on the `commons.events` logger, in the call log's shape: a `severity`
Cloud Logging reads, an `event` name, ids and codes, and the current trace's ids so Loki links
the line to its trace. Never agent text: queries, entry text and messages stay out.
"""

import json
import logging

from opentelemetry import trace
from opentelemetry.trace import Span, format_span_id, format_trace_id

Field = str | int | bool | None

logger = logging.getLogger("commons.events")
logger.setLevel(logging.INFO)

_LEVELS = {"INFO": logging.INFO, "NOTICE": logging.INFO, "WARNING": logging.WARNING}


def event(name: str, *, severity: str = "INFO", **fields: Field) -> None:
    """Write one line for `name` with `fields`, inside the current trace if there is one."""
    line = {"severity": severity, "event": name, **fields, **trace_fields(trace.get_current_span())}
    logger.log(_LEVELS.get(severity, logging.INFO), json.dumps(line))


def failure(name: str, error: BaseException, **fields: Field) -> None:
    """Write an ERROR line for `name` with `error` and its traceback."""
    line = {"severity": "ERROR", "event": name, **fields, **trace_fields(trace.get_current_span())}
    logger.error(json.dumps(line), exc_info=error)


def trace_fields(span: Span) -> dict[str, str]:
    """Return the ids that link a line to its trace, when one is being recorded."""
    context = span.get_span_context()
    if not context.is_valid or not context.trace_flags.sampled:
        return {}
    return {
        "trace_id": format_trace_id(context.trace_id),
        "span_id": format_span_id(context.span_id),
    }


def short_key(key: str | None) -> str | None:
    """Return the handle the People page shows for a key (eight characters), never the key."""
    return key.removeprefix("ac_")[:8] if key else None

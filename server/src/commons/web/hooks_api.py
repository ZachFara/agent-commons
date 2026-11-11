"""Hook endpoints for hosted mode (decision 0018). Bearer API key required.

Only derived observations are accepted, in strictly checked shapes: a client cannot send code
content here even if it tried (decision 0016).
"""

import math
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import datetime
from http import HTTPStatus
from typing import Any
from uuid import UUID

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from commons.domain.entries import Proposal
from commons.domain.errors import InvalidInputError, NotPermittedError, RateLimitedError
from commons.domain.keys import KeyHolder
from commons.domain.observations import (
    MAX_TOKENS,
    DocLookup,
    TaskCost,
    command_tail,
    split_library_id,
)
from commons.domain.outcomes import Usage
from commons.domain.prompts import PromptVersion
from commons.domain.usage import UseObservation
from commons.privacy.redaction import scrub
from commons.service import agent_text, contributors, events, hooks, imports
from commons.service.identity import acting_as, current_reporter
from commons.service.keys import check_key
from commons.store.connection import Connection
from commons.store.searches import privacy_mode

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]
Body = dict[str, Any]
Handler = Callable[[Connection, Body], Body]

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHORT_HASH = re.compile(r"[0-9a-f]{12}")
_REFUSALS = {400: "invalid_input", 401: "unauthorized", 403: "forbidden", 429: "rate_limited"}
_USAGES: frozenset[Usage | None] = frozenset({"yes", "partial", "no", None})
_COST_FIELDS = {"tokens_in", "tokens_out", "wall_clock_seconds"}


def hook_routes(connection: ConnectionFactory) -> list[Route]:
    """Return the hosted hook API: one POST route per hook step."""
    handlers: dict[str, Handler] = {
        "ping": lambda _conn, _body: {},  # answered with the key's scope
        "session-start": _session_start,
        "tool-use": _tool_use,
        "retrievals": _retrievals,
        "stop": _stop,
        "artifact-frequencies": _artifact_frequencies,
        "similar": _similar,
        "mine": _mine,
        "feedback": lambda conn, _body: {"line": contributors.session_feedback(conn)},
        "import": _import,
    }
    return [
        Route(
            f"/api/hooks/{name}",
            _endpoint(connection, handler, name),
            methods=["POST"],
        )
        for name, handler in handlers.items()
    ]


def _endpoint(
    connection: ConnectionFactory, handler: Handler, name: str
) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        started = time.perf_counter()
        key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        body = await request.json() if await request.body() else {}
        if isinstance(body, dict):
            result = await run_in_threadpool(_run, connection, handler, key, body)
        else:
            result = _error(400, "invalid_input", "Body must be a JSON object.")
        payload = None if isinstance(result, Response) else result[0]
        status = result.status_code if isinstance(result, Response) else HTTPStatus.OK
        # One line per hook call (decision 0037): who, which, how it went, which task.
        events.event(
            "hook_call",
            hook=name,
            ok=status == HTTPStatus.OK,
            status=int(status),
            error=_REFUSALS.get(status, None if status == HTTPStatus.OK else "error"),
            key=events.short_key(key),
            task_id=None if payload is None else payload.get("task_id"),
            ms=round((time.perf_counter() - started) * 1000),
        )
        if payload is None:
            return result if isinstance(result, Response) else JSONResponse({})
        if name == "ping" and isinstance(result, tuple):  # answered with the key's scope
            return JSONResponse({**payload, "scope": result[1]})
        return JSONResponse(payload)

    return endpoint


def _run(
    connection: ConnectionFactory, handler: Handler, key: str, body: Body
) -> Response | tuple[Body, str]:
    """Return the handler's result and the key's scope, or an error response."""
    with connection() as conn:
        holder = _authorize(conn, key)
        if isinstance(holder, Response):
            return holder
        try:
            _check_privacy(holder)
            with acting_as(holder.reporter_id, test=holder.test, key_id=holder.key_id):
                return handler(conn, body), holder.scope
        except (PermissionError, InvalidInputError, KeyError, TypeError, ValueError) as error:
            return _refusal(error)


def _refusal(error: Exception) -> Response:
    """Return the response for an error a handler raised: 429, 403, or 400 for bad input."""
    if isinstance(error, RateLimitedError):
        limited = _error(429, "rate_limited", str(error))
        limited.headers["retry-after"] = str(error.retry_after)
        return limited
    if isinstance(error, NotPermittedError):
        return _error(403, "forbidden", str(error))
    if isinstance(error, PermissionError):
        raise error
    return _error(400, "invalid_input", str(error))


def _authorize(conn: Connection, key: str) -> KeyHolder | Response:
    """Return the key's holder, or the 401/403 response to send instead."""
    check = check_key(conn, key) if key else None
    if check is None or check.holder is None:
        detail = check.message() if check else "send `Authorization: Bearer <key>`"
        return _error(401, "unauthorized", f"Hook API: {detail}.")
    if not check.holder.allows("agent"):
        return _error(403, "forbidden", "Hooks need an agent key, not a review key.")
    return check.holder


def _check_privacy(holder: KeyHolder) -> None:
    if holder.privacy_default == "local":
        msg = "Your privacy mode is `local`; nothing is sent to a hosted server (decision 0014)."
        raise NotPermittedError(msg)


def _error(status: int, code: str, message: str) -> Response:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _session_id(body: Body) -> str:
    session_id = body["session_id"]
    if not isinstance(session_id, str) or not session_id:
        msg = "session_id must be a non-empty string."
        raise InvalidInputError(msg)
    return session_id


def _model(body: Body) -> str | None:
    model = body.get("model")
    if model is not None and not isinstance(model, str):
        msg = "model must be a string."
        raise InvalidInputError(msg)
    return model


def _hook_prompt(body: Body) -> PromptVersion | None:
    """Return the client's hook version, named by the server from its hash alone (MIT-609).

    Hooks older than MIT-609 send no hash. Any label or text a client sends is ignored.
    """
    short_hash = body.get("prompt_hash")
    if short_hash is None:
        return None
    if not isinstance(short_hash, str) or not _SHORT_HASH.fullmatch(short_hash):
        msg = "prompt_hash must be 12 lowercase hex characters."
        raise InvalidInputError(msg)
    return agent_text.reported_hook_version(short_hash)


def _session_start(conn: Connection, body: Body) -> Body:
    task_id = hooks.session_start(conn, _session_id(body), _model(body), prompt=_hook_prompt(body))
    # The client sends a doc lookup's query only in full_trace; this tells it the mode.
    mode = privacy_mode(conn, current_reporter(conn), task_id)
    return {"task_id": str(task_id), "privacy_mode": mode}


def _tool_use(conn: Connection, body: Body) -> Body:
    hooks.record_tool_use(
        conn,
        _session_id(body),
        _test_run(body.get("test_run"), body.get("test_run_at")),
        _file_change(body.get("file_change")),
        _doc_lookup(body.get("doc_lookup")),
    )
    return {}


def _test_run(value: object, at: object) -> dict[str, object] | None:
    """Return the run to store, with the client's time of it when sent (absent before MIT-680)."""
    run = _run_shape(value)
    if run is None or at is None:
        return run
    return {**run, "at": _aware_time(at, "test_run_at").isoformat()}


def _aware_time(value: object, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        msg = f"{name} must be an ISO time with a UTC offset."
        raise InvalidInputError(msg)
    return parsed


def _run_shape(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not (
        isinstance(value, dict)
        and set(value) == {"command", "exit_code"}
        and isinstance(value["command"], str)
        and isinstance(value["exit_code"], int)
    ):
        msg = "test_run must be {command: str, exit_code: int}."
        raise InvalidInputError(msg)
    # Older hooks send long commands whole; keep the end, where the test runner is.
    return {"command": scrub(command_tail(value["command"])), "exit_code": value["exit_code"]}


def _file_change(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not (
        isinstance(value, dict)
        and set(value) == {"path_sha256", "content_sha256"}
        and all(isinstance(v, str) and _SHA256.fullmatch(v) for v in value.values())
    ):
        msg = "file_change must be {path_sha256, content_sha256}, both SHA-256 hex digests."
        raise InvalidInputError(msg)
    return dict(value)


def _doc_lookup(value: object) -> DocLookup | None:
    if value is None:
        return None
    if not (
        isinstance(value, dict)
        and set(value) == {"library_id", "version", "query"}
        and _is_library(value["library_id"], value["version"])
        and (value["query"] is None or isinstance(value["query"], str))
    ):
        msg = "doc_lookup must be {library_id: /org/project, version: str, query: str or null}."
        raise InvalidInputError(msg)
    return DocLookup(value["library_id"], value["version"], value["query"])


def _is_library(library_id: object, version: object) -> bool:
    """Whether library_id and version are what the client splits a Context7 id into."""
    if not isinstance(library_id, str) or not isinstance(version, str):
        return False
    full = f"{library_id}/{version}" if version else library_id
    return split_library_id(full) == (library_id, version)


def _retrievals(conn: Connection, body: Body) -> Body:
    items = hooks.retrievals_to_judge(conn, _session_id(body))
    return {
        "retrievals": [
            {
                "retrieval_id": str(item.retrieval_id),
                "entry_id": str(item.entry_id),
                "fix": item.fix,
                "retrieved_at": item.retrieved_at.isoformat(),
                "reported": item.reported,
            }
            for item in items
        ]
    }


def _mine(conn: Connection, _body: Body) -> Body:
    return {"entries": [_mine_json(entry) for entry in contributors.my_entries(conn)]}


def _mine_json(entry: dict[str, Any]) -> Body:
    return {
        **{k: entry[k] for k in ("symptom", "status", "last_decision", "last_reason")},
        "entry_id": str(entry["id"]),
        "created_at": entry["created_at"].isoformat(),
        "retrievals_30d": entry["retrievals_30d"],
        "used": entry["used"],
        "worked": entry["worked"],
        "risks": len(entry["risk_report"].get("findings", [])),
        "redactions": len(entry["redaction_report"].get("findings", [])),
    }


def _similar(conn: Connection, body: Body) -> Body:
    text = body["text"]
    if not isinstance(text, str) or not text.strip():
        msg = "text must be a non-empty string."
        raise InvalidInputError(msg)
    return {"similar": [{"entry_id": str(e), "score": s} for e, s in imports.similar(conn, text)]}


def _import(conn: Connection, body: Body) -> Body:
    entry, origin = body["entry"], body["imported_from"]
    if not isinstance(entry, dict) or not isinstance(origin, dict):
        msg = "entry and imported_from must be objects."
        raise InvalidInputError(msg)
    proposal = Proposal(
        symptom=str(entry["symptom"]),
        environment=dict(entry["environment"]),
        cause=str(entry["cause"]),
        fix=str(entry["fix"]),
        domains=tuple(str(d) for d in entry["domains"]),
        repro=None if entry.get("repro") is None else str(entry["repro"]),
        task_id=uuid.uuid4(),
    )
    kept = {k: str(origin[k]) for k in ("local_entry_id", "model", "created_at") if k in origin}
    result = imports.import_entry(conn, proposal, kept, str(origin.get("model") or "unknown"))
    return {"entry_id": str(result.entry_id), "status": result.status}


def _artifact_frequencies(conn: Connection, _body: Body) -> Body:
    frequencies = hooks.artifact_frequencies(conn)
    return {"entries": frequencies.entries, "counts": dict(frequencies.counts)}


def _stop(conn: Connection, body: Body) -> Body:
    used_in = body.get("used", {})
    if not isinstance(used_in, dict) or not all(v in _USAGES for v in used_in.values()):
        msg = "used must map retrieval ids to yes, partial, no, or null."
        raise InvalidInputError(msg)
    matched_in = body.get("matched_at")  # absent from hooks older than decision 0036
    if matched_in is not None and not isinstance(matched_in, dict):
        msg = "matched_at must map retrieval ids to ISO times or null."
        raise InvalidInputError(msg)
    matched = matched_in or {}
    used = {
        UUID(k): UseObservation(v, datetime.fromisoformat(matched[k]) if matched.get(k) else None)
        for k, v in used_in.items()
    }
    result = hooks.finish_turn(
        conn,
        _session_id(body),
        _model(body),
        used,
        paired=matched_in is not None,
        cost=_cost(body.get("cost")),  # absent from hooks older than MIT-600
    )
    if result is None:
        return {"task_id": None, "unreported": []}
    return {
        "task_id": str(result.task_id),
        "unreported": [str(e) for e in result.unreported],
        "fixed_failing_tests": result.fixed_failing_tests,
        "searched": result.searched,
        "nudge": result.nudge,
    }


def _cost(value: object) -> dict[UUID, TaskCost]:
    """Check the task's cost after each retrieval: two token counts and seconds, per id."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(_is_cost(v) for v in value.values()):
        msg = (
            "cost must map retrieval ids to {tokens_in, tokens_out, wall_clock_seconds}: "
            "non-negative integer token counts and non-negative seconds."
        )
        raise InvalidInputError(msg)
    return {
        UUID(k): TaskCost(v["tokens_in"], v["tokens_out"], float(v["wall_clock_seconds"]))
        for k, v in value.items()
    }


def _is_cost(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _COST_FIELDS:
        return False
    tokens = (value["tokens_in"], value["tokens_out"])
    seconds = value["wall_clock_seconds"]
    return all(
        isinstance(t, int) and not isinstance(t, bool) and 0 <= t <= MAX_TOKENS for t in tokens
    ) and (
        isinstance(seconds, int | float)
        and not isinstance(seconds, bool)
        and math.isfinite(seconds)
        and seconds >= 0
    )

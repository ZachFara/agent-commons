"""Hook endpoints for hosted mode (decision 0018). Bearer API key required.

Only derived observations are accepted, in strictly checked shapes: a client cannot send code
content here even if it tried (decision 0016).
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any
from uuid import UUID

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from commons.domain.entries import Proposal
from commons.domain.errors import InvalidInputError, NotPermittedError
from commons.domain.keys import KeyHolder
from commons.domain.observations import command_tail
from commons.domain.outcomes import Usage
from commons.domain.usage import UseObservation
from commons.privacy.redaction import scrub
from commons.service import hooks, imports
from commons.service.identity import acting_as
from commons.service.keys import check_key
from commons.store.connection import Connection

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]
Body = dict[str, Any]
Handler = Callable[[Connection, Body], Body]

_SHA256 = re.compile(r"[0-9a-f]{64}")
_USAGES: frozenset[Usage | None] = frozenset({"yes", "partial", "no", None})


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
        "import": _import,
    }
    return [
        Route(
            f"/api/hooks/{name}",
            _endpoint(connection, handler, with_scope=name == "ping"),
            methods=["POST"],
        )
        for name, handler in handlers.items()
    ]


def _endpoint(
    connection: ConnectionFactory, handler: Handler, *, with_scope: bool
) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        body = await request.json() if await request.body() else {}
        if not isinstance(body, dict):
            return _error(400, "invalid_input", "Body must be a JSON object.")
        result = await run_in_threadpool(_run, connection, handler, key, body)
        if with_scope and isinstance(result, tuple):
            return JSONResponse({**result[0], "scope": result[1]})
        return result if isinstance(result, Response) else JSONResponse(result[0])

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
            with acting_as(holder.reporter_id, test=holder.test):
                return handler(conn, body), holder.scope
        except NotPermittedError as error:
            return _error(403, "forbidden", str(error))
        except (InvalidInputError, KeyError, TypeError, ValueError) as error:
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


def _session_start(conn: Connection, body: Body) -> Body:
    return {"task_id": str(hooks.session_start(conn, _session_id(body), _model(body)))}


def _tool_use(conn: Connection, body: Body) -> Body:
    hooks.record_tool_use(
        conn,
        _session_id(body),
        _test_run(body.get("test_run")),
        _file_change(body.get("file_change")),
    )
    return {}


def _test_run(value: object) -> dict[str, object] | None:
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
        conn, _session_id(body), _model(body), used, paired=matched_in is not None
    )
    if result is None:
        return {"task_id": None, "unreported": []}
    return {
        "task_id": str(result.task_id),
        "unreported": [str(e) for e in result.unreported],
        "fixed_failing_tests": result.fixed_failing_tests,
        "searched": result.searched,
    }

"""Hook endpoints for hosted mode (decision 0018). Bearer API key required.

Only derived observations are accepted, in strictly checked shapes: a client cannot send code
content here even if it tried (decision 0016).
"""

import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any
from uuid import UUID

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from commons.domain.errors import InvalidInputError, NotPermittedError
from commons.domain.outcomes import Usage
from commons.domain.reporters import KeyHolder
from commons.privacy.redaction import scrub
from commons.service import hooks
from commons.service.identity import acting_as
from commons.service.keys import holder_of
from commons.store.connection import Connection

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]
Body = dict[str, Any]
Handler = Callable[[Connection, Body], Body]

_SHA256 = re.compile(r"[0-9a-f]{64}")
_USAGES: frozenset[Usage | None] = frozenset({"yes", "partial", "no", None})
MAX_COMMAND_LENGTH = 2000


def hook_routes(connection: ConnectionFactory) -> list[Route]:
    """Return the hosted hook API: one POST route per hook step."""
    handlers: dict[str, Handler] = {
        "ping": lambda _conn, _body: {},
        "session-start": _session_start,
        "tool-use": _tool_use,
        "retrievals": _retrievals,
        "stop": _stop,
    }
    return [
        Route(f"/api/hooks/{name}", _endpoint(connection, handler), methods=["POST"])
        for name, handler in handlers.items()
    ]


def _endpoint(
    connection: ConnectionFactory, handler: Handler
) -> Callable[[Request], Awaitable[Response]]:
    async def endpoint(request: Request) -> Response:
        key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        body = await request.json() if await request.body() else {}
        if not isinstance(body, dict):
            return _error(400, "invalid_input", "Body must be a JSON object.")
        return await run_in_threadpool(_run, connection, handler, key, body)

    return endpoint


def _run(connection: ConnectionFactory, handler: Handler, key: str, body: Body) -> Response:
    with connection() as conn:
        holder = holder_of(conn, key) if key else None
        if holder is None:
            return _error(401, "unauthorized", "Send `Authorization: Bearer <key>`.")
        try:
            _check_privacy(holder)
            with acting_as(holder.reporter_id):
                return JSONResponse(handler(conn, body))
        except NotPermittedError as error:
            return _error(403, "forbidden", str(error))
        except (InvalidInputError, KeyError, TypeError, ValueError) as error:
            return _error(400, "invalid_input", str(error))


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
        and len(value["command"]) <= MAX_COMMAND_LENGTH
        and isinstance(value["exit_code"], int)
    ):
        msg = "test_run must be {command: str, exit_code: int}."
        raise InvalidInputError(msg)
    return {"command": scrub(value["command"]), "exit_code": value["exit_code"]}


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


def _stop(conn: Connection, body: Body) -> Body:
    used_in = body.get("used", {})
    if not isinstance(used_in, dict) or not all(v in _USAGES for v in used_in.values()):
        msg = "used must map retrieval ids to yes, partial, no, or null."
        raise InvalidInputError(msg)
    used: dict[UUID, Usage | None] = {UUID(k): v for k, v in used_in.items()}
    result = hooks.finish_turn(conn, _session_id(body), _model(body), used)
    if result is None:
        return {"task_id": None, "unreported": []}
    return {"task_id": str(result.task_id), "unreported": [str(e) for e in result.unreported]}

"""Say why the hosted MCP endpoint refused a key (decision 0032).

FastMCP's own 401 can't say a key was revoked or expired. This middleware checks the key first
and remembers the check for the request, so the MCP verifier doesn't look it up again.
"""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager

import anyio
from starlette.types import ASGIApp, Receive, Scope, Send

from commons.domain.keys import KeyCheck
from commons.service import events
from commons.service.keys import check_key, remembered_check
from commons.store.connection import Connection

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]


class ExplainRefusedKeys:
    """ASGI middleware: answer revoked and expired keys with a 401 that says so."""

    def __init__(self, app: ASGIApp, connection: ConnectionFactory) -> None:
        """Wrap `app`; `connection` opens a database connection per key check."""
        self._app = app
        self._connection = connection

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check a bearer key before the request reaches the MCP app."""
        token = _bearer(scope) if scope["type"] == "http" else None
        if token is None:
            await self._app(scope, receive, send)
            return
        check = await anyio.to_thread.run_sync(_check, self._connection, token)
        if check.problem in {"revoked", "expired", "suspended"}:
            events.event(
                "key_refused",
                severity="WARNING",
                where=str(scope.get("path")),
                problem=check.problem,
                key=events.short_key(token),
            )
            await _refuse(send, check.message())
            return
        with remembered_check(token, check):
            await self._app(scope, receive, send)


def _bearer(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token.strip() if scheme.lower() == "bearer" and token.strip() else None
    return None


def _check(connection: ConnectionFactory, token: str) -> KeyCheck:
    with connection() as conn:
        return check_key(conn, token)


async def _refuse(send: Send, message: str) -> None:
    body = json.dumps({"error": "invalid_token", "error_description": message}).encode()
    challenge = f'Bearer error="invalid_token", error_description="{message}"'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", challenge.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})

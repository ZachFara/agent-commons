"""Say why the hosted MCP endpoint refused a key (decision 0032).

FastMCP's own 401 can't say a key was revoked or expired. This wrapper checks the key first
and remembers the check for the request, so the MCP verifier doesn't look it up again.
"""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager

import anyio
from starlette.types import ASGIApp, Receive, Scope, Send

from commons.domain.keys import KeyCheck
from commons.service.keys import check_key, remembered_check
from commons.store.connection import Connection

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]


def explain_refused_keys(app: ASGIApp, connection: ConnectionFactory) -> ASGIApp:
    """Answer revoked and expired keys with a 401 that says so."""

    async def guarded(scope: Scope, receive: Receive, send: Send) -> None:
        token = _bearer(scope) if scope["type"] == "http" else None
        if token is None:
            await app(scope, receive, send)
            return
        check = await anyio.to_thread.run_sync(_check, connection, token)
        if check.problem in {"revoked", "expired", "suspended"}:
            await _refuse(send, check.message())
            return
        with remembered_check(token, check):
            await app(scope, receive, send)

    return guarded


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

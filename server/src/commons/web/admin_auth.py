"""The hosted dashboard is for admins only (decision 0018).

Browsers send HTTP Basic credentials; the password is an admin API key (the user name is
ignored). Only ever served over HTTPS (Caddy in compose.hosted.yml).
"""

import base64
import binascii
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from commons.domain.reporters import KeyHolder
from commons.service.identity import acting_as
from commons.service.keys import holder_of
from commons.store.connection import Connection

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]
Endpoint = Callable[[Request], Awaitable[Response]]

_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Agent Commons admin", charset="UTF-8"'}


def admin_only(endpoint: Endpoint, connection: ConnectionFactory) -> Endpoint:
    """Wrap an endpoint: 401 without an admin key, 403 for non-admin keys."""

    async def guarded(request: Request) -> Response:
        key = basic_password(request.headers.get("authorization", ""))
        holder = await run_in_threadpool(_lookup, connection, key) if key else None
        if holder is None:
            return PlainTextResponse("Admin API key required.", 401, headers=_CHALLENGE)
        if not holder.is_admin:
            return PlainTextResponse("This key is not an admin key.", 403)
        with acting_as(holder.reporter_id):
            return await endpoint(request)

    return guarded


def basic_password(header: str) -> str | None:
    """Return the password from an HTTP Basic Authorization header, if well formed."""
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    _user, sep, password = decoded.partition(":")
    return password if sep and password else None


def _lookup(connection: ConnectionFactory, key: str) -> KeyHolder | None:
    with connection() as conn:
        return holder_of(conn, key)

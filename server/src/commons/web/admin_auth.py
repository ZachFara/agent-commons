"""The hosted dashboard needs an admin or review key (decisions 0018, 0032).

Browsers send HTTP Basic credentials; the password is the API key (the user name is
ignored). Review keys reach the review pages only. Only ever served over HTTPS.
"""

import base64
import binascii
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from commons.domain.keys import KeyCheck, KeyScope
from commons.service import events
from commons.service.identity import acting_as
from commons.service.keys import check_key
from commons.store.connection import Connection

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]
Endpoint = Callable[[Request], Awaitable[Response]]

_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Agent Commons admin", charset="UTF-8"'}


def admin_only(
    endpoint: Endpoint, connection: ConnectionFactory, need: KeyScope | None = "admin"
) -> Endpoint:
    """Wrap an endpoint: 401 without a usable key, 403 for keys without the `need` scope.

    `need=None` lets any active key in, as on "Your entries" (decision 0035).
    """

    async def guarded(request: Request) -> Response:
        key = basic_password(request.headers.get("authorization", ""))
        check = await run_in_threadpool(_lookup, connection, key) if key else None
        if check is None or check.holder is None:
            if check is not None:
                _refused(request, key, check.problem or "unknown")
            message = check.message() if check else "API key required."
            return PlainTextResponse(f"Dashboard: {message}.", 401, headers=_CHALLENGE)
        if need is not None and not check.holder.allows(need):
            _refused(request, key, f"needs {need} scope")
            return PlainTextResponse(f"This page needs a key with the {need} scope.", 403)
        holder = check.holder
        with acting_as(holder.reporter_id, test=holder.test, key_id=holder.key_id):
            return await endpoint(request)

    return guarded


def _refused(request: Request, key: str | None, problem: str) -> None:
    events.event(
        "key_refused",
        severity="WARNING",
        where=request.url.path,
        problem=problem,
        key=events.short_key(key),
    )


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


def _lookup(connection: ConnectionFactory, key: str) -> KeyCheck:
    with connection() as conn:
        return check_key(conn, key)

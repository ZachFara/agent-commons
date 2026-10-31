"""Bearer API keys for hosted mode (decision 0018)."""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from uuid import UUID

import anyio
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from typing_extensions import override

from commons.domain.errors import NotPermittedError
from commons.domain.keys import KeyHolder
from commons.service.identity import acting_as
from commons.service.keys import check_key
from commons.store.connection import Connection

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]


class ApiKeyVerifier(TokenVerifier):
    """Accept active agent and admin keys; unknown keys get 401, review keys 403 (0032)."""

    def __init__(self, connection: ConnectionFactory) -> None:
        """Look keys up through `connection`."""
        super().__init__(required_scopes=["agent"])
        self._connection = connection

    @override
    async def verify_token(self, token: str) -> AccessToken | None:
        holder = await anyio.to_thread.run_sync(self._lookup, token)
        if holder is None:
            return None
        return AccessToken(
            token=token,
            client_id=str(holder.reporter_id),
            scopes=list(holder.scopes()),
            claims={
                "reporter_id": str(holder.reporter_id),
                "privacy": holder.privacy_default,
                "test": holder.test,
                "key_id": str(holder.key_id),
            },
        )

    def _lookup(self, token: str) -> KeyHolder | None:
        with self._connection() as conn:
            return check_key(conn, token).holder


@contextmanager
def calling_reporter() -> Iterator[None]:
    """Act as the request's key holder in hosted mode; do nothing over local stdio."""
    token = get_access_token()
    if token is None:
        yield
        return
    if token.claims.get("privacy") == "local":
        msg = (
            "Your privacy mode is `local`, so nothing you do is sent to a hosted server "
            "(decision 0014). Ask the admin to change your mode to use this server."
        )
        raise NotPermittedError(msg)
    key = token.claims.get("key_id")
    with acting_as(
        UUID(str(token.claims["reporter_id"])),
        test=token.claims.get("test") is True,
        key_id=UUID(str(key)) if key else None,
    ):
        yield

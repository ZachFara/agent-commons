"""API keys for hosted mode (decisions 0018, 0032). Only the SHA-256 of a key is stored."""

import hashlib
import json
import logging
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from commons.domain.errors import InvalidInputError, NotFoundError
from commons.domain.keys import KeyCheck, KeyInfo, KeyOptions, KeyProblem, KeyScope, NewKey
from commons.domain.reporters import validate_pseudonym
from commons.store import api_keys
from commons.store.connection import Connection
from commons.store.reporters import ensure_reporter

KEY_PREFIX = "ac_"
SHOWN_CHARACTERS = 8
DEFAULT_GRACE = timedelta(hours=24)
NO_OPTIONS = KeyOptions()
# A check already made for this request (see `remembered_check`).
_remembered: ContextVar[tuple[str, KeyCheck] | None] = ContextVar("checked_key", default=None)
# Key administration joins the call log (decision 0024): ids and scopes, never keys.
logger = logging.getLogger("commons.calls")


def issue_key(
    conn: Connection,
    pseudonym: str,
    scope: KeyScope,
    options: KeyOptions = NO_OPTIONS,
    *,
    replace: bool = False,
) -> NewKey:
    """Mint a key for the reporter (created if new). Shown once.

    With `replace`, the reporter's other keys are revoked (the old one-key-per-reporter
    behavior that `create-key` and `invite` keep).
    """
    validate_pseudonym(pseudonym)
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    prefix = key.removeprefix(KEY_PREFIX)[:SHOWN_CHARACTERS]
    with conn.transaction():
        reporter_id = ensure_reporter(conn, pseudonym)
        key_id = api_keys.insert_key(conn, reporter_id, _hash(key), prefix, scope, options)
        if replace:
            api_keys.revoke_others(conn, reporter_id, key_id)
    _log("key_created", pseudonym, prefix, scope)
    return NewKey(key, _only(api_keys.keys_matching(conn, prefix)))


def create_key(conn: Connection, pseudonym: str, *, admin: bool) -> str:
    """Mint the reporter's only key, revoking any others (`commons admin create-key`)."""
    return issue_key(conn, pseudonym, "admin" if admin else "agent", replace=True).key


def check_key(conn: Connection, key: str) -> KeyCheck:
    """Say who a presented key belongs to, or why it is refused; note that it was used."""
    remembered = _remembered.get()
    if remembered is not None and remembered[0] == key:
        return remembered[1]
    found = api_keys.find_by_hash(conn, _hash(key)) if key.startswith(KEY_PREFIX) else None
    if found is None:
        return KeyCheck(None, "unknown")
    holder, revoked_at, expires_at = found
    problem = _problem(revoked_at, expires_at)
    if problem is None:
        api_keys.touch(conn, holder.key_id)
    return KeyCheck(None if problem else holder, problem)


def _problem(revoked_at: datetime | None, expires_at: datetime | None) -> KeyProblem | None:
    if revoked_at is not None:
        return "revoked"
    if expires_at is not None and expires_at <= datetime.now(UTC):
        return "expired"
    return None


@contextmanager
def remembered_check(key: str, check: KeyCheck) -> Iterator[None]:
    """Within this block, `check_key` answers `key` with `check` instead of asking again."""
    token = _remembered.set((key, check))
    try:
        yield
    finally:
        _remembered.reset(token)


def list_keys(conn: Connection, pseudonym: str | None = None) -> list[KeyInfo]:
    """Every key (or one reporter's), newest first. Never the keys themselves."""
    return api_keys.key_infos(conn, pseudonym)


def find_key(conn: Connection, handle: str) -> KeyInfo:
    """Resolve a key's handle (visible prefix, with or without `ac_`, or id prefix)."""
    handle = handle.strip().removeprefix(KEY_PREFIX)
    if len(handle) < SHOWN_CHARACTERS // 2:
        msg = "Give at least 4 characters of the key's prefix or id."
        raise InvalidInputError(msg)
    return _only(api_keys.keys_matching(conn, handle), handle)


def rotate_key(conn: Connection, handle: str, grace: timedelta = DEFAULT_GRACE) -> NewKey:
    """Mint a replacement with the same scope and label; the old key stops after `grace`."""
    old = find_key(conn, handle)
    if old.state(datetime.now(UTC)) != "active":
        msg = f"Key {old.handle()} is already {old.state(datetime.now(UTC))}."
        raise InvalidInputError(msg)
    with conn.transaction():
        new = issue_key(conn, old.pseudonym, old.scope, KeyOptions(label=old.label, test=old.test))
        if grace:
            api_keys.expire_at(conn, old.key_id, datetime.now(UTC) + grace)
        else:
            api_keys.revoke(conn, old.key_id)
    _log("key_rotated", old.pseudonym, old.handle(), old.scope)
    return new


def revoke_key(conn: Connection, handle: str) -> KeyInfo:
    """Revoke a key now. Requests with it get 401 "key revoked"."""
    key = find_key(conn, handle)
    api_keys.revoke(conn, key.key_id)
    _log("key_revoked", key.pseudonym, key.handle(), key.scope)
    return find_key(conn, key.handle())


def _only(keys: list[KeyInfo], handle: str = "") -> KeyInfo:
    if not keys:
        msg = f"No key matches {handle!r}."
        raise NotFoundError(msg)
    if len(keys) > 1:
        msg = f"{len(keys)} keys match {handle!r}; give more characters."
        raise InvalidInputError(msg)
    return keys[0]


def _log(event: str, pseudonym: str, handle: str, scope: KeyScope) -> None:
    logger.info(
        json.dumps(
            {
                "severity": "NOTICE",
                "event": event,
                "pseudonym": pseudonym,
                "key": handle,
                "scope": scope,
            }
        )
    )


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

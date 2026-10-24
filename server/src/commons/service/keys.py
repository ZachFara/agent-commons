"""API keys for hosted mode (decision 0018). Only the SHA-256 of a key is stored."""

import hashlib
import secrets

from commons.domain.reporters import KeyHolder, validate_pseudonym
from commons.store.connection import Connection
from commons.store.reporters import ensure_reporter, key_holder, set_api_key

KEY_PREFIX = "ac_"


def create_key(conn: Connection, pseudonym: str, *, admin: bool) -> str:
    """Mint a key for the reporter (creating it if new), replacing any old key. Shown once."""
    validate_pseudonym(pseudonym)
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    with conn.transaction():
        reporter_id = ensure_reporter(conn, pseudonym)
        set_api_key(conn, reporter_id, _hash(key), admin=admin)
    return key


def holder_of(conn: Connection, key: str) -> KeyHolder | None:
    """Return who a presented key belongs to, or None if it is unknown."""
    if not key.startswith(KEY_PREFIX):
        return None
    return key_holder(conn, _hash(key))


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

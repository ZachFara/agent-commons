from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from psycopg.rows import class_row

from commons.domain.keys import KeyHolder, KeyInfo, KeyOptions, KeyScope
from commons.store.connection import Connection
from commons.store.rows import returned_id

_INFO = (
    "SELECT k.id AS key_id, rp.pseudonym, k.prefix, k.scope::text AS scope, k.label, "
    "k.created_at, k.last_used_at, k.expires_at, k.revoked_at, k.test "
    "FROM api_keys k JOIN reporters rp ON rp.id = k.reporter_id "
)


def insert_key(
    conn: Connection,
    reporter_id: UUID,
    key_hash: str,
    prefix: str,
    scope: KeyScope,
    options: KeyOptions,
) -> UUID:
    """Store a new key's hash; return the key's id."""
    cursor = conn.execute(
        "INSERT INTO api_keys (reporter_id, key_hash, prefix, scope, label, expires_at, test) "
        "VALUES (%s, %s, %s, %s, %s, now() + %s, %s) RETURNING id",
        (reporter_id, key_hash, prefix, scope, options.label, options.expires_in, options.test),
    )
    return returned_id(cursor.fetchone())


def find_by_hash(
    conn: Connection, key_hash: str
) -> tuple[KeyHolder, datetime | None, datetime | None, bool] | None:
    """Return the key's holder, revoked_at, expires_at and suspension; None if unknown."""
    row = conn.execute(
        "SELECT k.reporter_id, k.id, k.scope::text, rp.privacy_default::text, k.test, "
        "k.revoked_at, k.expires_at, rp.suspended_at IS NOT NULL "
        "FROM api_keys k JOIN reporters rp ON rp.id = k.reporter_id WHERE k.key_hash = %s",
        (key_hash,),
    ).fetchone()
    if row is None:
        return None
    return KeyHolder(*row[:5]), row[5], row[6], row[7]


def touch(conn: Connection, key_id: UUID) -> None:
    """Record that the key was used, at most once a minute (decision 0032)."""
    conn.execute(
        "UPDATE api_keys SET last_used_at = now() WHERE id = %s "
        "AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')",
        (key_id,),
    )


def key_infos(conn: Connection, pseudonym: str | None = None) -> list[KeyInfo]:
    """Every key, or one reporter's, newest first."""
    where = "WHERE rp.pseudonym = %s " if pseudonym else ""
    with conn.cursor(row_factory=class_row(KeyInfo)) as cursor:
        cursor.execute(
            _INFO + where + "ORDER BY k.created_at DESC", (pseudonym,) if pseudonym else None
        )
        return cursor.fetchall()


def keys_matching(conn: Connection, handle: str) -> list[KeyInfo]:
    """Keys whose visible prefix is `handle`, or whose id starts with it."""
    with conn.cursor(row_factory=class_row(KeyInfo)) as cursor:
        cursor.execute(
            _INFO + "WHERE k.prefix = %(h)s OR k.id::text LIKE %(h)s || '%%'",
            {"h": handle},
        )
        return cursor.fetchall()


def revoke(conn: Connection, key_id: UUID) -> None:
    """Revoke the key now (idempotent)."""
    conn.execute(
        "UPDATE api_keys SET revoked_at = coalesce(revoked_at, now()) WHERE id = %s", (key_id,)
    )


def expire_at(conn: Connection, key_id: UUID, when: datetime) -> None:
    """Make the key stop working at `when`, unless it already stops sooner."""
    conn.execute(
        "UPDATE api_keys SET expires_at = least(coalesce(expires_at, %(when)s), %(when)s) "
        "WHERE id = %(id)s",
        {"id": key_id, "when": when},
    )


def revoke_others(conn: Connection, reporter_id: UUID, keep: UUID) -> None:
    """Revoke every other active key the reporter has."""
    conn.execute(
        "UPDATE api_keys SET revoked_at = now() "
        "WHERE reporter_id = %s AND id <> %s AND revoked_at IS NULL",
        (reporter_id, keep),
    )


def revoke_scopes_except(conn: Connection, reporter_id: UUID, allowed: Sequence[str]) -> int:
    """Revoke the reporter's working keys whose scope isn't allowed; return how many."""
    cursor = conn.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE reporter_id = %s AND revoked_at IS NULL "
        "AND NOT (scope::text = ANY(%s))",
        (reporter_id, allowed),
    )
    return cursor.rowcount

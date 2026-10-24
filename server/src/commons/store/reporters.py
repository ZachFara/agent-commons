from uuid import UUID

from psycopg.rows import class_row

from commons.domain.reporters import KeyHolder, PrivacyDefault
from commons.store.connection import Connection
from commons.store.rows import returned_id


def upsert_reporter(conn: Connection, pseudonym: str, privacy_default: PrivacyDefault) -> UUID:
    """Create the reporter, or update its privacy default if the pseudonym exists."""
    cursor = conn.execute(
        "INSERT INTO reporters (pseudonym, privacy_default) VALUES (%s, %s) "
        "ON CONFLICT (pseudonym) DO UPDATE SET privacy_default = EXCLUDED.privacy_default "
        "RETURNING id",
        (pseudonym, privacy_default),
    )
    return returned_id(cursor.fetchone())


def find_reporter(conn: Connection, pseudonym: str) -> UUID | None:
    """Return the reporter id for a pseudonym, or None if there is none."""
    row = conn.execute("SELECT id FROM reporters WHERE pseudonym = %s", (pseudonym,)).fetchone()
    return None if row is None else returned_id(row)


def ensure_reporter(conn: Connection, pseudonym: str) -> UUID:
    """Return the reporter's id, creating it (privacy default `entry`) if new."""
    conn.execute(
        "INSERT INTO reporters (pseudonym) VALUES (%s) ON CONFLICT (pseudonym) DO NOTHING",
        (pseudonym,),
    )
    found = find_reporter(conn, pseudonym)
    if found is None:
        msg = "reporter vanished after insert"
        raise RuntimeError(msg)
    return found


def set_api_key(conn: Connection, reporter_id: UUID, key_hash: str, *, admin: bool) -> None:
    """Replace the reporter's API key hash (one key per reporter) and admin flag."""
    conn.execute(
        "UPDATE reporters SET api_key_hash = %s, is_admin = %s WHERE id = %s",
        (key_hash, admin, reporter_id),
    )


def key_holder(conn: Connection, key_hash: str) -> KeyHolder | None:
    """Return who owns this API key hash, if anyone."""
    with conn.cursor(row_factory=class_row(KeyHolder)) as cursor:
        cursor.execute(
            "SELECT id AS reporter_id, is_admin, privacy_default FROM reporters "
            "WHERE api_key_hash = %s",
            (key_hash,),
        )
        return cursor.fetchone()

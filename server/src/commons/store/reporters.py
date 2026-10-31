from uuid import UUID

from commons.domain.reporters import PrivacyDefault
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


def pseudonym_of(conn: Connection, reporter_id: UUID) -> str:
    """Return the reporter's pseudonym."""
    row = conn.execute("SELECT pseudonym FROM reporters WHERE id = %s", (reporter_id,)).fetchone()
    if row is None:
        msg = f"reporter {reporter_id} does not exist"
        raise RuntimeError(msg)
    pseudonym: str = row[0]
    return pseudonym


def promote_to_admin(conn: Connection, pseudonym: str) -> None:
    """Give the reporter the admin role."""
    conn.execute("UPDATE reporters SET role = 'admin' WHERE pseudonym = %s", (pseudonym,))

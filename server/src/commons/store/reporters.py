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

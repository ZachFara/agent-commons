from uuid import UUID

import psycopg

from commons.domain.reporters import PrivacyDefault


def upsert_reporter(
    conn: psycopg.Connection, pseudonym: str, privacy_default: PrivacyDefault
) -> UUID:
    """Create the reporter, or update its privacy default if the pseudonym exists."""
    row = conn.execute(
        "INSERT INTO reporters (pseudonym, privacy_default) VALUES (%s, %s) "
        "ON CONFLICT (pseudonym) DO UPDATE SET privacy_default = EXCLUDED.privacy_default "
        "RETURNING id",
        (pseudonym, privacy_default),
    ).fetchone()
    if row is None:
        msg = "upsert returned no row"
        raise RuntimeError(msg)
    reporter_id: UUID = row[0]
    return reporter_id

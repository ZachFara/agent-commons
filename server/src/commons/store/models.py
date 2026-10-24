from uuid import UUID

from commons.store.connection import Connection
from commons.store.rows import returned_id


def model_id(conn: Connection, name: str) -> UUID:
    """Return the id of the model row for this raw model string, creating it if new."""
    cursor = conn.execute(
        "INSERT INTO models (name) VALUES (%s) "
        "ON CONFLICT (provider, name, version) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name,),
    )
    return returned_id(cursor.fetchone())

from uuid import UUID

from psycopg.types.json import Jsonb

from commons.domain.prompts import PromptVersion
from commons.store.connection import Connection
from commons.store.rows import returned_id


def prompt_version_id(conn: Connection, version: PromptVersion | None) -> UUID | None:
    """Return the id of the row for `version`, inserting it the first time it is served.

    `DO NOTHING` and a separate select, not `DO UPDATE`: an update would lock the one shared
    row on every search. The first label to serve a hash keeps it.
    """
    if version is None:
        return None
    conn.execute(
        "INSERT INTO prompt_versions (hash, label, surfaces) VALUES (%s, %s, %s) "
        "ON CONFLICT (hash) DO NOTHING",
        (version.hash, version.label, Jsonb(version.surfaces)),
    )
    row = conn.execute("SELECT id FROM prompt_versions WHERE hash = %s", (version.hash,))
    return returned_id(row.fetchone())

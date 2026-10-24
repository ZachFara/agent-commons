import os
from typing import TypeAlias

import psycopg
from psycopg.conninfo import conninfo_to_dict

from commons.domain.errors import StoreUnavailableError

# Layers above store use this name so that only store imports the driver.
Connection: TypeAlias = psycopg.Connection

DEFAULT_DATABASE_URL = "postgresql://commons:commons@localhost:5432/commons"


def database_url() -> str:
    """Return the database URL from COMMONS_DATABASE_URL, or the local docker default."""
    return os.environ.get("COMMONS_DATABASE_URL", DEFAULT_DATABASE_URL)


def connect() -> Connection:
    """Open a connection to the configured database, or raise StoreUnavailableError."""
    try:
        return psycopg.connect(database_url())
    except psycopg.OperationalError as error:
        host = conninfo_to_dict(database_url()).get("host", "?")
        msg = f"Can't reach the database at {host}. Is Postgres running? (`make db`)"
        raise StoreUnavailableError(msg) from error

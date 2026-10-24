import os
from typing import TypeAlias

import psycopg

# Layers above store use this name so that only store imports the driver.
Connection: TypeAlias = psycopg.Connection

DEFAULT_DATABASE_URL = "postgresql://commons:commons@localhost:5432/commons"


def database_url() -> str:
    """Return the database URL from COMMONS_DATABASE_URL, or the local docker default."""
    return os.environ.get("COMMONS_DATABASE_URL", DEFAULT_DATABASE_URL)


def connect() -> Connection:
    """Open a connection to the configured database."""
    return psycopg.connect(database_url())

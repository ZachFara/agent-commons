import os

import psycopg

DEFAULT_DATABASE_URL = "postgresql://commons:commons@localhost:5432/commons"


def database_url() -> str:
    """Return the database URL from COMMONS_DATABASE_URL, or the local docker default."""
    return os.environ.get("COMMONS_DATABASE_URL", DEFAULT_DATABASE_URL)


def connect() -> psycopg.Connection:
    """Open a connection to the configured database."""
    return psycopg.connect(database_url())

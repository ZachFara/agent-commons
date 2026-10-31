from datetime import UTC, datetime, timedelta

from commons.store.connection import Connection
from commons.store.retention import fold_searches_before

SEARCH_RETENTION = timedelta(days=180)


def apply_retention(conn: Connection, now: datetime | None = None) -> int:
    """Apply decision 0033's retention rules; return how many search rows were folded.

    Searches older than 180 days become monthly counts of query and misses. Call logs and
    spools expire on their own (Cloud Logging's 30 days, the hooks' 30 days).
    """
    cutoff = (now or datetime.now(UTC)) - SEARCH_RETENTION
    with conn.transaction():
        return fold_searches_before(conn, cutoff)

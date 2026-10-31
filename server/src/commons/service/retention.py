from datetime import UTC, datetime, timedelta

from commons.store.connection import Connection
from commons.store.rates import prune_counts
from commons.store.retention import fold_searches_before

SEARCH_RETENTION = timedelta(days=180)
RATE_COUNT_RETENTION = timedelta(days=2)


def apply_retention(conn: Connection, now: datetime | None = None) -> int:
    """Apply decision 0033's retention rules; return how many search rows were folded.

    Searches older than 180 days become monthly counts of query and misses. Call logs and
    spools expire on their own (Cloud Logging's 30 days, the hooks' 30 days).
    """
    moment = now or datetime.now(UTC)
    with conn.transaction():
        prune_counts(conn, moment - RATE_COUNT_RETENTION)  # rate-limit windows (0029)
        return fold_searches_before(conn, moment - SEARCH_RETENTION)

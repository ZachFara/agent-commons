from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from commons.store.connection import Connection
from commons.store.rates import prune_counts
from commons.store.retention import fold_doc_lookups_before, fold_searches_before

SEARCH_RETENTION = timedelta(days=180)
RATE_COUNT_RETENTION = timedelta(days=2)


@dataclass(frozen=True)
class Folded:
    """How many rows retention folded into monthly counts."""

    searches: int
    doc_lookups: int


def apply_retention(conn: Connection, now: datetime | None = None) -> Folded:
    """Apply decision 0033's retention rules; return how many rows were folded.

    Searches older than 180 days become monthly counts of query and misses. Doc lookups older
    than 180 days become monthly counts by library and version (MIT-623, amending 0033). Call
    logs and spools expire on their own (Cloud Logging's 30 days, the hooks' 30 days).
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - SEARCH_RETENTION
    with conn.transaction():
        prune_counts(conn, moment - RATE_COUNT_RETENTION)  # rate-limit windows (0029)
        searches = fold_searches_before(conn, cutoff)
        return Folded(searches, fold_doc_lookups_before(conn, cutoff))

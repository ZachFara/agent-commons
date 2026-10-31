from datetime import datetime, timedelta
from uuid import UUID

from commons.store.connection import Connection


def count_call(
    conn: Connection, key_id: UUID, action: str, window: timedelta
) -> tuple[int, datetime]:
    """Add one call to the key's current window; return the count and when the window ends.

    Counted in Postgres, so the limit holds across server instances.
    """
    row = conn.execute(
        "INSERT INTO rate_counts (key_id, action, window_start, count) "
        "VALUES (%(key)s, %(action)s, "
        "  to_timestamp(floor(extract(epoch FROM now()) / %(seconds)s) * %(seconds)s), 1) "
        "ON CONFLICT (key_id, action, window_start) "
        "DO UPDATE SET count = rate_counts.count + 1 "
        "RETURNING count, window_start + make_interval(secs => %(seconds)s)",
        {"key": key_id, "action": action, "seconds": int(window.total_seconds())},
    ).fetchone()
    if row is None:
        msg = "rate counter vanished"
        raise RuntimeError(msg)
    return int(row[0]), row[1]


def prune_counts(conn: Connection, before: datetime) -> int:
    """Delete windows that ended before `before`; return how many."""
    return conn.execute("DELETE FROM rate_counts WHERE window_start < %s", (before,)).rowcount

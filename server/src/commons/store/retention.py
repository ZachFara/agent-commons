from datetime import datetime

from commons.store.connection import Connection


def fold_searches_before(conn: Connection, cutoff: datetime) -> int:
    """Fold searches made before `cutoff` into monthly counts and delete them; return how many.

    Folding adds to a month's existing counts, so running twice never double-counts: the
    second run finds nothing left to fold.
    """
    conn.execute(
        "INSERT INTO search_months (month, query, searches, misses) "
        "SELECT date_trunc('month', created_at AT TIME ZONE 'UTC')::date, query, count(*), "
        "  count(*) FILTER (WHERE hit_count = 0) "
        "FROM searches WHERE created_at < %s GROUP BY 1, 2 "
        "ON CONFLICT (month, query) DO UPDATE SET "
        "  searches = search_months.searches + EXCLUDED.searches, "
        "  misses = search_months.misses + EXCLUDED.misses",
        (cutoff,),
    )
    return conn.execute("DELETE FROM searches WHERE created_at < %s", (cutoff,)).rowcount

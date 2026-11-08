from datetime import datetime

from commons.store.connection import Connection


def fold_searches_before(conn: Connection, cutoff: datetime) -> int:
    """Fold searches made before `cutoff` into monthly counts and delete them; return how many.

    Folding adds to a month's existing counts, so running twice never double-counts: the
    second run finds nothing left to fold. Excluded searches are deleted without being counted.
    """
    conn.execute(
        "INSERT INTO search_months (month, query, searches, misses) "
        "SELECT date_trunc('month', created_at AT TIME ZONE 'UTC')::date, query, count(*), "
        "  count(*) FILTER (WHERE hit_count = 0) "
        "FROM counted_searches WHERE created_at < %s GROUP BY 1, 2 "
        "ON CONFLICT (month, query) DO UPDATE SET "
        "  searches = search_months.searches + EXCLUDED.searches, "
        "  misses = search_months.misses + EXCLUDED.misses",
        (cutoff,),
    )
    return conn.execute("DELETE FROM searches WHERE created_at < %s", (cutoff,)).rowcount


def fold_doc_lookups_before(conn: Connection, cutoff: datetime) -> int:
    """Fold doc lookups made before `cutoff` into monthly counts and delete them; return how many.

    Months are counted by library and version. As with searches, a second run finds nothing
    left to fold, and excluded lookups are deleted without being counted.
    """
    conn.execute(
        "INSERT INTO doc_lookup_months (month, library_id, version, lookups) "
        "SELECT date_trunc('month', created_at AT TIME ZONE 'UTC')::date, library_id, version, "
        "  count(*) "
        "FROM counted_doc_lookups WHERE created_at < %s GROUP BY 1, 2, 3 "
        "ON CONFLICT (month, library_id, version) DO UPDATE SET "
        "  lookups = doc_lookup_months.lookups + EXCLUDED.lookups",
        (cutoff,),
    )
    return conn.execute("DELETE FROM doc_lookups WHERE created_at < %s", (cutoff,)).rowcount

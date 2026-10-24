from typing import get_args

from psycopg.rows import class_row

from commons.domain.entries import EntryStatus
from commons.domain.status import StoreStatus, TopEntry
from commons.store.connection import Connection
from commons.store.outcome_stats import OUTCOME_STATS, OUTCOME_STATS_PARAMS

_TOP_ENTRIES = """
SELECT e.id AS entry_id, e.symptom, o.used_outcomes, o.successes
FROM entries e JOIN ({stats}) o ON o.entry_id = e.id
WHERE e.status = 'approved'
ORDER BY o.successes::float / o.used_outcomes DESC, o.used_outcomes DESC, e.created_at DESC
LIMIT %(limit)s
""".replace("{stats}", OUTCOME_STATS)

_ACTIVITY = """
SELECT
    (SELECT count(*) FROM entries WHERE NOT human_reviewed AND status <> 'retired'),
    (SELECT count(*) FROM retrievals WHERE created_at > now() - interval '30 days'),
    (SELECT count(DISTINCT task_id) FROM retrievals WHERE created_at > now() - interval '30 days'),
    (SELECT count(*) FROM outcomes WHERE created_at > now() - interval '30 days')
"""


def store_status(conn: Connection, top_limit: int) -> StoreStatus:
    """Collect the statistics the status tool and command report."""
    counted: dict[str, int] = dict(
        conn.execute("SELECT status, count(*) FROM entries GROUP BY status").fetchall()
    )
    by_status: dict[EntryStatus, int] = {s: counted.get(s, 0) for s in get_args(EntryStatus)}
    activity = conn.execute(_ACTIVITY).fetchone() or (0, 0, 0, 0)
    with conn.cursor(row_factory=class_row(TopEntry)) as cursor:
        cursor.execute(_TOP_ENTRIES, {"limit": top_limit, **OUTCOME_STATS_PARAMS})
        top = cursor.fetchall()
    awaiting, retrievals, tasks, outcomes = activity
    return StoreStatus(
        entries_by_status=by_status,
        awaiting_human_review=awaiting,
        retrievals_30d=retrievals,
        tasks_30d=tasks,
        outcomes_30d=outcomes,
        top_entries=top,
    )

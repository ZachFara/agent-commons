from collections import defaultdict
from datetime import datetime
from uuid import UUID

from commons.domain.outcomes import SUCCESS_OUTCOMES, OutcomeSample
from commons.store.connection import Connection

# Outcomes that say whether the entry worked: used, with a known, decisive outcome.
_SAMPLES = """
SELECT r.entry_id, o.outcome = ANY(%(success)s::outcome_kind[]), o.used::text,
       o.evidence->>'outcome_source' = 'hook', rp.reliability,
       extract(epoch FROM now() - o.created_at) / 86400
FROM outcomes o
JOIN retrievals r ON r.id = o.retrieval_id
JOIN reporters rp ON rp.id = r.reporter_id
JOIN entries e ON e.id = r.entry_id
WHERE e.status = 'approved' AND o.used IN ('yes', 'partial')
  AND o.outcome NOT IN ('unknown', 'abandoned')
"""

_ENTRY_STATS = """
SELECT e.id,
       (SELECT count(*) FROM retrievals r
        WHERE r.entry_id = e.id AND r.created_at > now() - interval '30 days'),
       (SELECT count(*) FILTER (WHERE o.used IN ('yes', 'partial'))::float
               / nullif(count(*) FILTER (WHERE o.used IS NOT NULL), 0)
        FROM outcomes o JOIN retrievals r ON r.id = o.retrieval_id WHERE r.entry_id = e.id)
FROM entries e WHERE e.status = 'approved'
"""


def outcome_samples(conn: Connection) -> dict[UUID, list[OutcomeSample]]:
    """Scoring evidence per approved entry."""
    samples: dict[UUID, list[OutcomeSample]] = defaultdict(list)
    rows = conn.execute(_SAMPLES, {"success": list(SUCCESS_OUTCOMES)}).fetchall()
    for entry_id, success, used, observed, reliability, age_days in rows:
        samples[entry_id].append(
            OutcomeSample(
                success=bool(success),
                used=used,
                observed=bool(observed),
                reporter_reliability=reliability,
                age_days=float(age_days),
            )
        )
    return samples


def entry_stats(conn: Connection) -> list[tuple[UUID, int, float | None]]:
    """(entry id, retrievals in 30 days, used rate) for every approved entry."""
    return [(entry, count, rate) for entry, count, rate in conn.execute(_ENTRY_STATS).fetchall()]


def write_snapshot(
    conn: Connection,
    entry_id: UUID,
    computed_at: datetime,
    stats: tuple[float, int, float | None, float | None],
) -> None:
    """Insert one ranking snapshot: (score, retrieval_count_30d, used_rate, success_rate)."""
    score, retrievals_30d, used_rate, success_rate = stats
    conn.execute(
        "INSERT INTO ranking_snapshots (entry_id, computed_at, score, retrieval_count_30d, "
        "used_rate, success_rate) VALUES (%s, %s, %s, %s, %s, %s)",
        (entry_id, computed_at, score, retrievals_30d, used_rate, success_rate),
    )


def latest_snapshot_at(conn: Connection) -> datetime | None:
    """When ranking last ran, or None if it never has."""
    row = conn.execute("SELECT max(computed_at) FROM ranking_snapshots").fetchone()
    return None if row is None else row[0]

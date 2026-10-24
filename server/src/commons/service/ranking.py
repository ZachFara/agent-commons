"""`commons rank`: write ranking snapshots (DESIGN.md §7, decision 0017)."""

from datetime import UTC, datetime, timedelta

from commons.ranking.score import score
from commons.store.connection import Connection
from commons.store.ranking import entry_stats, latest_snapshot_at, outcome_samples, write_snapshot

STALE_AFTER = timedelta(hours=24)


def rank(conn: Connection) -> int:
    """Snapshot every approved entry's score and stats; return how many were written."""
    computed_at = datetime.now(UTC)
    with conn.transaction():
        samples = outcome_samples(conn)
        stats = entry_stats(conn)
        for entry_id, retrievals_30d, used_rate in stats:
            evidence = samples.get(entry_id, [])
            success_rate = sum(s.success for s in evidence) / len(evidence) if evidence else None
            write_snapshot(
                conn,
                entry_id,
                computed_at,
                (score(evidence), retrievals_30d, used_rate, success_rate),
            )
    return len(stats)


def ranking_is_stale(conn: Connection, now: datetime | None = None) -> bool:
    """Whether the newest snapshot is older than STALE_AFTER (or there is none)."""
    latest = latest_snapshot_at(conn)
    return latest is None or (now or datetime.now(UTC)) - latest > STALE_AFTER

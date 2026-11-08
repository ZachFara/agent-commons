"""Counterfactual pairs (DESIGN.md §3, §7): one task run with an entry, and again without it."""

from dataclasses import dataclass
from uuid import UUID

from commons.domain.outcomes import OutcomeKind
from commons.store.connection import Connection


@dataclass(frozen=True)
class CounterfactualRun:
    """One side of a pair: how the task went with the entry, or without it."""

    task_id: UUID  # shared by both sides of the pair
    entry_id: UUID
    with_entry: bool
    outcome: OutcomeKind
    tokens_in: int
    tokens_out: int
    wall_clock_seconds: float


def known_entries(conn: Connection, entry_ids: set[UUID]) -> set[UUID]:
    """Return which of these entries this store holds (a run can only name one it holds)."""
    rows = conn.execute(
        "SELECT id FROM entries WHERE id = ANY(%s::uuid[])", (list(entry_ids),)
    ).fetchall()
    return {row[0] for row in rows}


def record_run(conn: Connection, run: CounterfactualRun) -> bool:
    """Write one side of a pair; return False if it was already written (same task, entry, side)."""
    cursor = conn.execute(
        "INSERT INTO counterfactual_runs (task_id, entry_id, with_entry, outcome, tokens_in, "
        "tokens_out, wall_clock_seconds) VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (task_id, entry_id, with_entry) DO NOTHING RETURNING id",
        (
            run.task_id,
            run.entry_id,
            run.with_entry,
            run.outcome,
            run.tokens_in,
            run.tokens_out,
            run.wall_clock_seconds,
        ),
    )
    return cursor.fetchone() is not None

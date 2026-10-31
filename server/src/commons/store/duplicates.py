from uuid import UUID

from psycopg.types.json import Jsonb

from commons.domain.duplicates import Candidate
from commons.store.connection import Connection


def save_candidates(
    conn: Connection, entry_id: UUID, candidates: list[Candidate], link: UUID | None
) -> None:
    """Store the likely duplicates on the new entry, and the suggested link if any."""
    conn.execute(
        "UPDATE entries SET duplicate_candidates = %s, duplicate_of = %s WHERE id = %s",
        (Jsonb([c.as_json() for c in candidates]), link, entry_id),
    )


def same_task_entries(conn: Connection, task_id: UUID, reporter_id: UUID) -> set[UUID]:
    """Entries this reporter already proposed in this task."""
    rows = conn.execute(
        "SELECT id FROM entries WHERE task_id = %s AND created_by = %s", (task_id, reporter_id)
    )
    return {entry_id for (entry_id,) in rows}


def add_distinct_pair(conn: Connection, first: UUID, second: UUID, by: str) -> None:
    """Remember that two entries are distinct, and drop them from each other's candidates."""
    low, high = sorted((first, second))
    conn.execute(
        "INSERT INTO distinct_pairs (entry_a, entry_b, decided_by) VALUES (%s, %s, %s) "
        "ON CONFLICT (entry_a, entry_b) DO NOTHING",
        (low, high, by),
    )
    for entry_id, other in ((first, second), (second, first)):
        conn.execute(
            "UPDATE entries SET duplicate_of = CASE WHEN duplicate_of = %(o)s THEN NULL "
            "ELSE duplicate_of END, duplicate_candidates = coalesce((SELECT jsonb_agg(c) "
            "FROM jsonb_array_elements(duplicate_candidates) c "
            "WHERE c->>'entry_id' <> %(o)s::text), '[]') WHERE id = %(e)s",
            {"e": entry_id, "o": other},
        )


def add_alias(conn: Connection, entry_id: UUID, alias: str) -> None:
    """Let search match the canonical entry by a merged duplicate's wording too."""
    conn.execute(
        "UPDATE entries SET aliases = array_append(aliases, %s) "
        "WHERE id = %s AND NOT (%s = ANY(aliases))",
        (alias, entry_id, alias),
    )

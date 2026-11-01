from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from commons.domain.observations import TaskEventKind
from commons.domain.outcomes import ObservedRun
from commons.store.connection import Connection
from commons.store.rows import returned_id


def task_for_session(conn: Connection, session_id: str, reporter_id: UUID) -> UUID | None:
    """Return this reporter's task for the session, if SessionStart created one."""
    row = conn.execute(
        "SELECT id FROM tasks WHERE session_id = %s AND reporter_id = %s",
        (session_id, reporter_id),
    ).fetchone()
    return None if row is None else returned_id(row)


def add_task_event(
    conn: Connection, task_id: UUID, kind: TaskEventKind, data: dict[str, object]
) -> None:
    """Record a derived observation for the task."""
    conn.execute(
        "INSERT INTO task_events (task_id, kind, data) VALUES (%s, %s, %s)",
        (task_id, kind, Jsonb(data)),
    )


def test_runs(conn: Connection, task_id: UUID) -> list[ObservedRun]:
    """The task's test runs, oldest first."""
    rows = conn.execute(
        "SELECT data->>'command', (data->>'exit_code')::int, created_at FROM task_events "
        "WHERE task_id = %s AND kind = 'test_run' ORDER BY created_at",
        (task_id,),
    ).fetchall()
    return [ObservedRun(command=command, exit_code=code, at=at) for command, code, at in rows]


@dataclass(frozen=True)
class TaskRetrieval:
    """The first retrieval of an entry in a task, with what Stop needs to judge it."""

    retrieval_id: UUID
    entry_id: UUID
    fix: str
    retrieved_at: datetime
    reported: bool


def task_retrievals(conn: Connection, task_id: UUID) -> list[TaskRetrieval]:
    """Each entry the task's reporter retrieved in this task, at its earliest retrieval."""
    with conn.cursor(row_factory=class_row(TaskRetrieval)) as cursor:
        cursor.execute(
            "SELECT DISTINCT ON (r.entry_id) r.id AS retrieval_id, r.entry_id, e.fix, "
            "r.created_at AS retrieved_at, (o.agent_assessment IS NOT NULL) AS reported "
            "FROM retrievals r JOIN tasks t ON t.id = r.task_id AND t.reporter_id = r.reporter_id "
            "JOIN entries e ON e.id = r.entry_id "
            "LEFT JOIN outcomes o ON o.retrieval_id = r.id "
            "WHERE r.task_id = %s ORDER BY r.entry_id, r.created_at, r.rank_position",
            (task_id,),
        )
        return cursor.fetchall()


def set_task_model(conn: Connection, task_id: UUID, model_id: UUID) -> None:
    """Record the observed model; it replaces self-reported ones in this task (decision 0009)."""
    conn.execute("UPDATE tasks SET model_id = %s WHERE id = %s", (model_id, task_id))
    conn.execute(
        "UPDATE retrievals SET model_id = %s WHERE task_id = %s AND model_id <> %s",
        (model_id, task_id, model_id),
    )
    conn.execute(
        "UPDATE entries e SET created_by_model = %s FROM tasks t "
        "WHERE t.id = %s AND e.task_id = t.id AND e.created_by = t.reporter_id "
        "AND e.created_by_model <> %s",
        (model_id, task_id, model_id),
    )


def end_turn(conn: Connection, task_id: UUID) -> datetime | None:
    """Record that a turn just ended; return when the previous one did (None: the first)."""
    row = conn.execute(
        "UPDATE tasks t SET last_stop_at = clock_timestamp() FROM (SELECT last_stop_at FROM tasks "
        "WHERE id = %s FOR UPDATE) old WHERE t.id = %s RETURNING old.last_stop_at",
        (task_id, task_id),
    ).fetchone()
    return None if row is None else row[0]


def acted_in_turn(conn: Connection, task_id: UUID, since: datetime | None) -> bool:
    """Whether the task searched or proposed since `since` (or ever, if None)."""
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM searches WHERE task_id = %(t)s "
        "  AND (%(since)s::timestamptz IS NULL OR created_at > %(since)s)) "
        "OR EXISTS (SELECT 1 FROM entries WHERE task_id = %(t)s "
        "  AND (%(since)s::timestamptz IS NULL OR created_at > %(since)s))",
        {"t": task_id, "since": since},
    ).fetchone()
    return bool(row and row[0])

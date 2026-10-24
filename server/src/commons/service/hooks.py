"""What Claude Code hooks record (DESIGN.md §5, decision 0016)."""

from uuid import UUID

from commons.service.identity import current_reporter
from commons.store.connection import Connection
from commons.store.models import model_id
from commons.store.tasks import start_task


def session_start(conn: Connection, session_id: str, model: str | None) -> UUID:
    """Create or resume the session's task and return its task id."""
    with conn.transaction():
        reporter = current_reporter(conn)
        model_row = model_id(conn, model) if model else None
        return start_task(conn, session_id, reporter, model_row)

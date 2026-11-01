"""Feedback to contributors (decision 0035): what happened to their entries."""

import os
from typing import Any

from commons.service.identity import current_reporter
from commons.store import contributors as store
from commons.store.connection import Connection

FEEDBACK_SETTING = "COMMONS_SESSION_FEEDBACK"


def my_entries(conn: Connection) -> list[dict[str, Any]]:
    """Return the calling reporter's entries, and only theirs."""
    return store.entries_of(conn, current_reporter(conn))


def session_feedback(conn: Connection) -> str | None:
    """Return a one-line "since your last session" note for SessionStart, or None.

    Off unless COMMONS_SESSION_FEEDBACK=on: it adds text to every session, so it is an eval
    condition first (decision 0023), and the server can turn it off with one deploy.
    """
    if os.environ.get(FEEDBACK_SETTING, "off").strip().lower() != "on":
        return None
    reporter = current_reporter(conn)
    since = store.previous_session_start(conn, reporter)
    if since is None:
        return None
    approved, used, worked = store.news_since(conn, reporter, since)
    if not (approved or used):
        return None
    parts = []
    if approved:
        parts.append(f"{approved} of your entries approved")
    if used:
        parts.append(f"your entries were used {used} time(s) ({worked} worked)")
    return "Agent Commons since your last session: " + "; ".join(parts) + "."

"""The admin dashboard: a local, read-only view of what agents propose, find, and report."""

import argparse
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from commons.domain.activity import RecentEntry, RecentRetrieval
from commons.service.dashboard import dashboard
from commons.store.connection import Connection, connect

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]

# Jinja2Templates autoescapes .html templates. Entries are agent-written, untrusted text.
TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))
DEFAULT_PORT = 8765


def build_app(connection: ConnectionFactory = connect) -> Starlette:
    """Build the dashboard app. `connection` opens a database connection per request."""

    def overview(request: Request) -> Response:
        with connection() as conn:
            data = dashboard(conn)
        context = {
            "status": data.status,
            "pending": data.pending,
            "entries": [_entry_row(e) for e in data.recent_entries],
            "retrievals": [_retrieval_row(r) for r in data.recent_retrievals],
        }
        return TEMPLATES.TemplateResponse(request, "dashboard.html", context)

    return Starlette(routes=[Route("/", overview)])


# Rows are built here, not read off dataclasses in the template, so every field the page shows is
# visible to the dead-code check.
def _entry_row(entry: RecentEntry) -> dict[str, Any]:
    return {
        "when": entry.created_at,
        "status": entry.status,
        "human_reviewed": entry.human_reviewed,
        "symptom": entry.symptom,
        "by": entry.proposed_by,
        "model": entry.proposed_by_model,
        "id": str(entry.entry_id),
    }


def _retrieval_row(retrieval: RecentRetrieval) -> dict[str, Any]:
    return {
        "when": retrieval.created_at,
        "task": str(retrieval.task_id),
        "query": retrieval.query,
        "entry": str(retrieval.entry_id),
        "symptom": retrieval.symptom,
        "rank": retrieval.rank_position,
        "score": retrieval.score_at_retrieval,
        "reporter": retrieval.reporter,
        "model": retrieval.model,
        "outcome": retrieval.outcome,
        "outcome_source": retrieval.outcome_source,
        "assessment": retrieval.agent_assessment,
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Serve the dashboard on localhost. There is no auth, so it never binds elsewhere."""
    parser = argparse.ArgumentParser(prog="commons-dashboard", description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    port: int = args.port
    uvicorn.run(build_app(), host="127.0.0.1", port=port)

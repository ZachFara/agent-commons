"""The admin dashboard: a local, read-only view of what agents propose, find, and report."""

import argparse
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.templating import Jinja2Templates

from commons.domain.activity import RecentEntry, RecentRetrieval
from commons.domain.errors import InvalidInputError, NotFoundError, NotInitializedError
from commons.domain.reviews import REVIEW_DECISIONS, ReviewRequest
from commons.service import reviews
from commons.service.dashboard import dashboard
from commons.store.connection import Connection, connect

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]

# Jinja2Templates autoescapes .html templates. Entries are agent-written, untrusted text.
TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))
DEFAULT_PORT = 8765
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost"})


def build_app(connection: ConnectionFactory = connect) -> Starlette:
    """Build the dashboard app. `connection` opens a database connection per request."""

    def overview(request: Request) -> Response:
        with connection() as conn:
            data = dashboard(conn)
        context = {
            "error": request.query_params.get("error"),
            "reviewed": request.query_params.get("reviewed"),
            "status": data.status,
            "pending": data.pending,
            "entries": [_entry_row(e) for e in data.recent_entries],
            "retrievals": [_retrieval_row(r) for r in data.recent_retrievals],
        }
        return TEMPLATES.TemplateResponse(request, "dashboard.html", context)

    def record_review(request_: ReviewRequest) -> None:
        with connection() as conn:
            reviews.review(conn, request_)

    async def review(request: Request) -> Response:
        if not _same_local_origin(request):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        form = await request.form()
        decision = form.get("decision")
        reason = form.get("reason")
        if not isinstance(decision, str) or decision not in REVIEW_DECISIONS:
            return PlainTextResponse("decision must be approve or reject.", status_code=400)
        review_request = ReviewRequest(
            entry_id=request.path_params["entry_id"],
            decision=decision,
            reason=(reason.strip() or None) if isinstance(reason, str) else None,
            source="human",
        )
        try:
            await run_in_threadpool(record_review, review_request)
        except (InvalidInputError, NotFoundError, NotInitializedError) as error:
            return _back({"error": str(error)})
        return _back({"reviewed": str(review_request.entry_id)})

    return Starlette(
        routes=[
            Route("/", overview),
            Route("/entries/{entry_id:uuid}/review", review, methods=["POST"]),
        ]
    )


def _back(params: dict[str, str]) -> Response:
    return RedirectResponse(f"/?{urlencode(params)}", status_code=303)


def _same_local_origin(request: Request) -> bool:
    """Refuse cross-site form posts (CSRF) and non-local Host headers (DNS rebinding).

    Browsers send Origin on POST; it must be this dashboard's own localhost origin.
    """
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    return host.split(":")[0] in LOCAL_HOSTNAMES and origin == f"http://{host}"


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

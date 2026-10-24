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
from commons.service.dashboard import Dashboard as DashboardData
from commons.service.dashboard import dashboard
from commons.service.identity import serving_hosted
from commons.store.connection import Connection, connect
from commons.web.admin_auth import Endpoint, admin_only
from commons.web.hooks_api import hook_routes

ConnectionFactory = Callable[[], AbstractContextManager[Connection]]

# Jinja2Templates autoescapes .html templates. Entries are agent-written, untrusted text.
TEMPLATES = Jinja2Templates(directory=Path(__file__).with_name("templates"))
DEFAULT_PORT = 8765
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost"})


class Dashboard:
    """The dashboard's pages. `connection` opens a database connection per request."""

    def __init__(self, connection: ConnectionFactory, *, hosted: bool) -> None:
        """Serve from `connection`; `hosted` changes the CSRF origin rule."""
        self._connection = connection
        self._hosted = hosted

    async def overview(self, request: Request) -> Response:
        """Render the page: counts, review queue, recent proposals and retrievals."""
        data = await run_in_threadpool(self._load)
        context = {
            "error": request.query_params.get("error"),
            "reviewed": request.query_params.get("reviewed"),
            "status": data.status,
            "pending": data.pending,
            "entries": [_entry_row(e) for e in data.recent_entries],
            "retrievals": [_retrieval_row(r) for r in data.recent_retrievals],
        }
        return TEMPLATES.TemplateResponse(request, "dashboard.html", context)

    async def review(self, request: Request) -> Response:
        """Record a human review from the queue's form."""
        if not _same_origin(request, hosted=self._hosted):
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
            await run_in_threadpool(self._record, review_request)
        except (InvalidInputError, NotFoundError, NotInitializedError) as error:
            return _back({"error": str(error)})
        return _back({"reviewed": str(review_request.entry_id)})

    def _load(self) -> DashboardData:
        with self._connection() as conn:
            return dashboard(conn)

    def _record(self, request: ReviewRequest) -> None:
        with self._connection() as conn:
            reviews.review(conn, request)


def build_app(connection: ConnectionFactory = connect, *, hosted: bool = False) -> Starlette:
    """Build the app. `connection` opens a database connection per request.

    Locally: the dashboard, open on 127.0.0.1. Hosted: the hook API plus the dashboard for
    admin keys only (decision 0018).
    """
    pages = Dashboard(connection, hosted=hosted)
    overview: Endpoint = pages.overview
    review: Endpoint = pages.review
    if hosted:
        overview, review = admin_only(overview, connection), admin_only(review, connection)
    routes = [
        Route("/", overview),
        Route("/entries/{entry_id:uuid}/review", review, methods=["POST"]),
    ]
    return Starlette(routes=[*hook_routes(connection), *routes] if hosted else routes)


def _back(params: dict[str, str]) -> Response:
    return RedirectResponse(f"/?{urlencode(params)}", status_code=303)


def _same_origin(request: Request, *, hosted: bool) -> bool:
    """Refuse cross-site form posts (CSRF): Origin must be this dashboard's own origin.

    Browsers send Origin on POST, and send stored Basic credentials cross-site too, so the
    hosted dashboard needs this as much as the local one. Locally the Host must also be
    localhost (DNS rebinding). Hosted, the scheme comes from the proxy (`--proxy-headers`).
    """
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if hosted:
        return origin == f"{request.url.scheme}://{host}"
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
    """Serve the dashboard on localhost, or the hosted app (`--hosted`) on any address.

    The local dashboard has no auth, so it never binds anywhere but 127.0.0.1. The hosted app
    authenticates every request with an API key.
    """
    parser = argparse.ArgumentParser(prog="commons-dashboard", description=main.__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--hosted", action="store_true", help="Serve hosted mode.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (hosted mode only).")
    args = parser.parse_args(argv)
    port: int = args.port
    if not args.hosted:
        uvicorn.run(build_app(), host="127.0.0.1", port=port)
        return
    host: str = args.host
    with serving_hosted():
        # Behind Caddy: trust its X-Forwarded-Proto so the CSRF check sees https.
        uvicorn.run(
            build_app(hosted=True),
            host=host,
            port=port,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )

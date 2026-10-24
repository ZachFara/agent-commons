"""The admin dashboard: a local, read-only view of what agents propose, find, and report."""

import argparse
import threading
import webbrowser
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import UUID

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import BaseRoute, Route
from starlette.templating import Jinja2Templates

from commons.domain.errors import InvalidInputError, NotFoundError, NotInitializedError
from commons.domain.reviews import REVIEW_DECISIONS, ReviewRequest
from commons.service import reviews
from commons.service.identity import serving_hosted
from commons.store import insights
from commons.store.connection import Connection, connect
from commons.web import charts
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
        """Serve from `connection`; `hosted` changes the CSRF origin rule and the label."""
        self._connection = connection
        self._hosted = hosted

    async def overview(self, request: Request) -> Response:
        """Render headline numbers, daily activity, outcome mix, and top entries."""
        data = await self._load(_overview_data)
        return self._render(request, "overview.html", "overview", data)

    async def queue(self, request: Request) -> Response:
        """Render entries awaiting a human review."""
        data = await self._load(lambda conn: {"pending": reviews.pending(conn)})
        return self._render(request, "queue.html", "queue", data)

    async def entries(self, request: Request) -> Response:
        """Render the entry list with search, status filter, and ordering."""
        query = request.query_params.get("q", "").strip() or None
        status = request.query_params.get("status") or None
        order = request.query_params.get("order", "score")
        data = await self._load(
            lambda conn: {
                "entries": insights.entries(conn, status=status, query=query, order=order),
                "query": query,
                "status_filter": status,
                "order": order,
            }
        )
        return self._render(request, "entries.html", "entries", data)

    async def entry(self, request: Request) -> Response:
        """Render one entry: content, score history, reviews, retrievals."""
        entry_id: UUID = request.path_params["entry_id"]
        data = await self._load(lambda conn: _entry_data(conn, entry_id))
        if data["e"] is None:
            return PlainTextResponse("No such entry.", status_code=404)
        return self._render(request, "entry.html", "entries", data)

    async def activity(self, request: Request) -> Response:
        """Render recent agent sessions."""
        data = await self._load(lambda conn: {"tasks": insights.tasks(conn)})
        return self._render(request, "activity.html", "activity", data)

    async def people(self, request: Request) -> Response:
        """Render reporters and models."""
        data = await self._load(
            lambda conn: {"reporters": insights.reporters(conn), "models": insights.models(conn)}
        )
        return self._render(request, "people.html", "people", data)

    async def review(self, request: Request) -> Response:
        """Record a human review from a review form, then go back where it came from."""
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
            return _back(request, {"error": str(error)})
        return _back(request, {"reviewed": str(review_request.entry_id)})

    async def _load(self, loader: Callable[[Connection], dict[str, Any]]) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            with self._connection() as conn:
                return {**loader(conn), "awaiting": insights.kpis(conn)["awaiting"]}

        return await run_in_threadpool(run)

    def _render(
        self, request: Request, template: str, section: str, data: dict[str, Any]
    ) -> Response:
        context = {
            **data,
            "section": section,
            "hosted": self._hosted,
            "error": request.query_params.get("error"),
            "reviewed": request.query_params.get("reviewed"),
        }
        return TEMPLATES.TemplateResponse(request, template, context)

    def _record(self, request: ReviewRequest) -> None:
        with self._connection() as conn:
            reviews.review(conn, request)


ACTIVITY_DAYS = 30
TOP_ENTRIES = 6


def _overview_data(conn: Connection) -> dict[str, Any]:
    daily = insights.daily_activity(conn, ACTIVITY_DAYS)
    days = [row["day"] for row in daily]
    top = insights.entries(conn, status="approved", query=None, order="score")
    return {
        "k": insights.kpis(conn),
        "spark": charts.sparkline([row["retrievals"] for row in daily]),
        "activity": charts.stacked_bars(days, {"Retrievals": [row["retrievals"] for row in daily]}),
        "results": charts.stacked_bars(
            days,
            {
                "Worked": [row["successes"] for row in daily],
                "Didn't work": [row["failures"] for row in daily],
            },
            classes=("good-fill", "bad-fill"),
        ),
        "mix": charts.split_bars(
            insights.outcome_mix(conn),
            "outcome",
            {"observed": "Observed by hooks", "self_reported": "Self-reported"},
            classes=("s1", "s1-tint"),
        ),
        "top": [e for e in top if e["success_rate"] is not None][:TOP_ENTRIES],
    }


def _entry_data(conn: Connection, entry_id: UUID) -> dict[str, Any]:
    history = insights.score_history(conn, entry_id)
    return {
        "e": insights.entry(conn, entry_id),
        "reviews": insights.entry_reviews(conn, entry_id),
        "history": charts.line([(row["computed_at"], row["score"]) for row in history]),
        "retrievals": insights.entry_retrievals(conn, entry_id),
    }


def build_app(connection: ConnectionFactory = connect, *, hosted: bool = False) -> Starlette:
    """Build the app. `connection` opens a database connection per request.

    Locally: the dashboard, open on 127.0.0.1. Hosted: the hook API plus the dashboard for
    admin keys only (decision 0018).
    """
    return Starlette(routes=web_routes(connection, hosted=hosted))


def web_routes(connection: ConnectionFactory, *, hosted: bool) -> list[BaseRoute]:
    """Return the dashboard routes, plus the hook API when hosted (reused by `commons.hosted`)."""
    pages = Dashboard(connection, hosted=hosted)
    endpoints: list[tuple[str, Endpoint, list[str]]] = [
        ("/", pages.overview, ["GET"]),
        ("/queue", pages.queue, ["GET"]),
        ("/entries", pages.entries, ["GET"]),
        ("/entries/{entry_id:uuid}", pages.entry, ["GET"]),
        ("/activity", pages.activity, ["GET"]),
        ("/people", pages.people, ["GET"]),
        ("/entries/{entry_id:uuid}/review", pages.review, ["POST"]),
    ]
    routes = [
        Route(path, admin_only(endpoint, connection) if hosted else endpoint, methods=methods)
        for path, endpoint, methods in endpoints
    ]
    return [*hook_routes(connection), *routes] if hosted else list(routes)


def _back(request: Request, params: dict[str, str]) -> Response:
    """Redirect to the page the form was on (same-origin path only), else the queue."""
    referer = urlparse(request.headers.get("referer", ""))
    path = (
        referer.path if referer.path.startswith("/") and not referer.netloc.startswith("//") else ""
    )
    same_host = not referer.netloc or referer.netloc == request.headers.get("host")
    target = path if path and same_host else "/queue"
    return RedirectResponse(f"{target}?{urlencode(params)}", status_code=303)


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


def main(argv: Sequence[str] | None = None) -> None:
    """Serve the dashboard on localhost, or the hosted app (`--hosted`) on any address.

    The local dashboard has no auth, so it never binds anywhere but 127.0.0.1. The hosted app
    authenticates every request with an API key.
    """
    parser = argparse.ArgumentParser(prog="commons-dashboard", description=main.__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--hosted", action="store_true", help="Serve hosted mode.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (hosted mode only).")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in a browser.")
    args = parser.parse_args(argv)
    port: int = args.port
    if not args.hosted:
        if args.open:
            threading.Timer(1.0, webbrowser.open, [f"http://127.0.0.1:{port}/"]).start()
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

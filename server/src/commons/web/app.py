"""The admin dashboard: a local, read-only view of what agents propose, find, and report."""

import argparse
import threading
import webbrowser
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
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

from commons.domain.corrections import (
    CORRECTION_FIELDS,
    CORRECTION_TABLES,
    Correction,
    parse_value,
)
from commons.domain.entries import Proposal
from commons.domain.errors import InvalidInputError, NotFoundError, NotInitializedError
from commons.domain.keys import KEY_SCOPES, KeyOptions, KeyScope, NewKey, parse_duration
from commons.domain.lifecycle import FixedIn
from commons.domain.reviews import REVIEW_DECISIONS, ReviewRequest
from commons.service import (
    contributors,
    corrections,
    duplicates,
    keys,
    lifecycle,
    purges,
    reviews,
    trust,
)
from commons.service.identity import current_pseudonym, serving_hosted
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
        data = await self._load(
            lambda conn: {
                "pending": reviews.pending(conn),
                "duplicates": insights.duplicate_flags(conn),
                "health": trust.queue_health(conn),
            }
        )
        return self._render(request, "queue.html", "queue", data)

    async def entries(self, request: Request) -> Response:
        """Render the entry list with search, status filter, and ordering."""
        query = request.query_params.get("q", "").strip() or None
        status = request.query_params.get("status") or None
        order = request.query_params.get("order", "score")
        data = await self._load(
            lambda conn: {
                "entries": insights.entries(conn, status=status, query=query, order=order),
                "stale": insights.stale_candidates(conn),
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

    async def searches(self, request: Request) -> Response:
        """Render what agents searched for, starting with what the store didn't have."""
        data = await self._load(
            lambda conn: {
                "totals": insights.search_totals(conn),
                "missed": insights.missed_queries(conn),
                "recent": insights.recent_searches(conn),
            }
        )
        return self._render(request, "searches.html", "searches", data)

    async def mine(self, request: Request) -> Response:
        """Render the caller's own entries and what happened to them (decision 0035)."""
        data = await self._load(
            lambda conn: {"mine": contributors.my_entries(conn)}, store_wide=False
        )
        return self._render(request, "mine.html", "mine", data)

    async def people(self, request: Request) -> Response:
        """Render reporters, their keys, and models."""
        return self._render(request, "people.html", "people", await self._load(_people_data))

    async def edit_entry(self, request: Request) -> Response:
        """Save edited entry text as a new revision (decision 0026)."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        form = await request.form()
        text = {k: str(form.get(k) or "") for k in ("symptom", "cause", "fix", "repro", "reason")}
        entry_id: UUID = request.path_params["entry_id"]

        def run() -> str:
            with self._connection() as conn:
                current = insights.entry(conn, entry_id)
                if current is None:
                    msg = f"No entry {entry_id}."
                    raise NotFoundError(msg)
                content = Proposal(
                    symptom=text["symptom"],
                    environment=current["environment"],
                    cause=text["cause"],
                    fix=text["fix"],
                    domains=tuple(current["domains"]),
                    repro=text["repro"].strip() or None,
                    task_id=UUID(int=0),
                )
                revision = lifecycle.edit_entry(conn, entry_id, content, text["reason"])
            return f"Saved revision {revision}."

        return await self._act(request, run)

    async def retire_entry(self, request: Request) -> Response:
        """Retire an entry, optionally naming the fix release or its replacement."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        form = await request.form()
        fields = {k: str(form.get(k) or "").strip() for k in ("reason", "fixed_in", "replacement")}
        entry_id: UUID = request.path_params["entry_id"]

        def run() -> str:
            fixed_in = FixedIn.parse(fields["fixed_in"]) if fields["fixed_in"] else None
            successor = UUID(fields["replacement"]) if fields["replacement"] else None
            with self._connection() as conn:
                lifecycle.retire(
                    conn, entry_id, fields["reason"], fixed_in=fixed_in, superseded_by=successor
                )
            return "Retired." if successor is None else "Superseded."

        return await self._act(request, run)

    async def restore_entry(self, request: Request) -> Response:
        """Return a retired entry to search."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        reason = str((await request.form()).get("reason") or "")
        entry_id: UUID = request.path_params["entry_id"]

        def run() -> str:
            with self._connection() as conn:
                lifecycle.restore(conn, entry_id, reason)
            return "Restored."

        return await self._act(request, run)

    async def merge_duplicate(self, request: Request) -> Response:
        """Merge a flagged duplicate into the entry it duplicates (decision 0027)."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        form = await request.form()
        entry_id: UUID = request.path_params["entry_id"]
        other = str(form.get("other") or "")
        keep = form.get("action") == "keep"

        def run() -> str:
            with self._connection() as conn:
                if keep:
                    duplicates.keep_both(conn, entry_id, UUID(other))
                    return "Kept both; this pair won't be flagged again."
                duplicates.merge(conn, entry_id, UUID(other))
            return "Merged: the duplicate's wording now finds the canonical entry."

        return await self._act(request, run)

    async def corrections(self, request: Request) -> Response:
        """Render every correction, with undo (decision 0030)."""
        data = await self._load(lambda conn: {"corrections": insights.corrections(conn)})
        return self._render(request, "corrections.html", "corrections", data)

    async def correct(self, request: Request) -> Response:
        """Record a correction or exclusion from a form, then go back."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        form = await request.form()
        values = {k: str(form.get(k) or "").strip() for k in ("table", "row_id", "field", "value")}
        reason = str(form.get("reason") or "")

        def run() -> str:
            with self._connection() as conn:
                field = values["field"]
                if field not in CORRECTION_FIELDS or values["table"] not in CORRECTION_TABLES:
                    msg = "Unknown table or field."
                    raise InvalidInputError(msg)
                correction = Correction(
                    table=values["table"],
                    row_id=UUID(values["row_id"]),
                    field=field,
                    value=parse_value(field, values["value"]),
                    reason=reason,
                    by=current_pseudonym(conn),
                )
                corrections.correct(conn, correction)
            return f"Corrected {values['field']}; scores recomputed."

        return await self._act(request, run)

    async def exclude_task(self, request: Request) -> Response:
        """Exclude a whole task's retrievals and searches."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        reason = str((await request.form()).get("reason") or "")
        task_id: UUID = request.path_params["task_id"]

        def run() -> str:
            with self._connection() as conn:
                count = corrections.exclude_task(
                    conn, task_id, reason=reason, by=current_pseudonym(conn)
                )
            return f"Excluded {count} rows of the task."

        return await self._act(request, run)

    async def undo_correction(self, request: Request) -> Response:
        """Undo a correction (and the rest of its batch)."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        correction_id: UUID = request.path_params["correction_id"]

        def run() -> str:
            with self._connection() as conn:
                count = corrections.undo(conn, correction_id, by=current_pseudonym(conn))
            return f"Undid {count} correction(s)."

        return await self._act(request, run)

    async def _act(self, request: Request, action: Callable[[], str]) -> Response:
        try:
            message = await run_in_threadpool(action)
        except (InvalidInputError, NotFoundError, NotInitializedError, ValueError) as error:
            return _back(request, {"error": str(error)})
        return _back(request, {"corrected": message})

    async def purge_reporter(self, request: Request) -> Response:
        """Revoke and purge a reporter, or restore them (decision 0029)."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        form = await request.form()
        pseudonym = str(request.path_params["pseudonym"])
        reason = str(form.get("reason") or "")
        restoring = form.get("action") == "restore"

        def run() -> str:
            with self._connection() as conn:
                if restoring:
                    done = purges.restore(conn, pseudonym)
                    return f"Restored {pseudonym}: {done.keys} keys, {done.entries} entries."
                done = purges.purge(conn, pseudonym, reason)
            return (
                f"Purged {pseudonym}: {done.keys} keys revoked, {done.entries} entries retired, "
                f"{done.outcomes} outcomes excluded."
            )

        return await self._act(request, run)

    async def create_key(self, request: Request) -> Response:
        """Mint a key from the People page and show it once (decision 0032)."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        form = await request.form()
        fields = {name: form.get(name) for name in ("pseudonym", "scope", "label", "expires")}
        values = {k: v.strip() if isinstance(v, str) else "" for k, v in fields.items()}
        try:
            new = await run_in_threadpool(self._issue, values)
        except (InvalidInputError, NotFoundError) as error:
            return _back(request, {"error": str(error)})
        data = await self._load(_people_data)
        return self._render(request, "people.html", "people", {**data, "new_key": new})

    async def revoke_key(self, request: Request) -> Response:
        """Revoke a key from the People page."""
        if not _same_origin(request, hosted=self._hosted):
            return PlainTextResponse("Cross-origin request refused.", status_code=403)
        handle = str(request.path_params["key_id"])

        def revoke() -> None:
            with self._connection() as conn:
                keys.revoke_key(conn, handle)

        await run_in_threadpool(revoke)
        return _back(request, {"revoked": handle[:8]})

    def _issue(self, values: dict[str, str]) -> NewKey:
        if values["scope"] not in KEY_SCOPES:
            msg = "scope must be agent, review or admin."
            raise InvalidInputError(msg)
        options = KeyOptions(
            label=values["label"] or None,
            expires_in=parse_duration(values["expires"]) if values["expires"] else None,
        )
        with self._connection() as conn:
            trust.check_scope(conn, values["pseudonym"], values["scope"])
            return keys.issue_key(conn, values["pseudonym"], values["scope"], options)

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

    async def _load(
        self, loader: Callable[[Connection], dict[str, Any]], *, store_wide: bool = True
    ) -> dict[str, Any]:
        """Run `loader`; add the queue count for the nav, unless the page is per-reporter."""

        def run() -> dict[str, Any]:
            with self._connection() as conn:
                awaiting = insights.kpis(conn)["awaiting"] if store_wide else None
                return {**loader(conn), "awaiting": awaiting}

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
            "revoked": request.query_params.get("revoked"),
            "corrected": request.query_params.get("corrected"),
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


def _people_data(conn: Connection) -> dict[str, Any]:
    return {
        "reporters": insights.reporters(conn),
        "models": insights.models(conn),
        "keys": keys.list_keys(conn),
        "now": datetime.now(UTC),
    }


def _entry_data(conn: Connection, entry_id: UUID) -> dict[str, Any]:
    history = insights.score_history(conn, entry_id)
    return {
        "e": insights.entry(conn, entry_id),
        "revisions": insights.entry_revisions(conn, entry_id),
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
    # Review keys reach the review pages; everything else needs an admin key (decision 0032).
    endpoints: list[tuple[str, Endpoint, str, KeyScope | None]] = [
        ("/mine", pages.mine, "GET", None),
        ("/", pages.overview, "GET", "admin"),
        ("/queue", pages.queue, "GET", "review"),
        ("/entries", pages.entries, "GET", "review"),
        ("/entries/{entry_id:uuid}", pages.entry, "GET", "review"),
        ("/activity", pages.activity, "GET", "admin"),
        ("/searches", pages.searches, "GET", "admin"),
        ("/people", pages.people, "GET", "admin"),
        ("/entries/{entry_id:uuid}/review", pages.review, "POST", "review"),
        ("/keys", pages.create_key, "POST", "admin"),
        ("/people/{pseudonym}/purge", pages.purge_reporter, "POST", "admin"),
        ("/corrections", pages.corrections, "GET", "admin"),
        ("/entries/{entry_id:uuid}/edit", pages.edit_entry, "POST", "admin"),
        ("/entries/{entry_id:uuid}/duplicate", pages.merge_duplicate, "POST", "review"),
        ("/entries/{entry_id:uuid}/retire", pages.retire_entry, "POST", "admin"),
        ("/entries/{entry_id:uuid}/restore", pages.restore_entry, "POST", "admin"),
        ("/corrections", pages.correct, "POST", "admin"),
        ("/corrections/{correction_id:uuid}/undo", pages.undo_correction, "POST", "admin"),
        ("/tasks/{task_id:uuid}/exclude", pages.exclude_task, "POST", "admin"),
        ("/keys/{key_id:uuid}/revoke", pages.revoke_key, "POST", "admin"),
    ]
    routes = [
        Route(
            path,
            admin_only(endpoint, connection, need) if hosted else endpoint,
            methods=[method],
        )
        for path, endpoint, method, need in endpoints
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

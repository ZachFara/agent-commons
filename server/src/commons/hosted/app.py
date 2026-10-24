"""`commons-server`: everything a hosted deployment serves, on one port (decision 0019).

Cloud Run gives a service one URL, so `/mcp`, `/api/hooks/*`, and the admin dashboard share
one app. MCP runs in stateless HTTP mode, so any instance can answer any request.
"""

import argparse
import os
from collections.abc import Sequence

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route

from commons.mcp.server import build_server
from commons.service.identity import serving_hosted
from commons.store.connection import connect
from commons.store.migrate import apply_migrations
from commons.web.app import ConnectionFactory, web_routes

DEFAULT_PORT = 8080


def build_hosted_app(connection: ConnectionFactory = connect) -> Starlette:
    """Compose the hosted app: health checks, hook API and dashboard, then MCP at /mcp."""
    mcp_app = build_server(connection, hosted=True).http_app(path="/mcp", stateless_http=True)
    return Starlette(
        routes=[
            # Cloud Run's front end reserves /healthz (returns its own 404), so /health is the
            # probe path there; /healthz stays for other hosts.
            Route("/health", _health),
            Route("/healthz", _health),
            *web_routes(connection, hosted=True),
            Mount("/", app=mcp_app),
        ],
        lifespan=mcp_app.lifespan,
    )


def _health(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def main(argv: Sequence[str] | None = None) -> None:
    """Serve the hosted app. The port defaults to $PORT (Cloud Run), else 8080."""
    parser = argparse.ArgumentParser(prog="commons-server", description=main.__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Bind address; 0.0.0.0 in containers.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument(
        "--migrate", action="store_true", help="Apply pending migrations before serving."
    )
    args = parser.parse_args(argv)
    if args.migrate:
        with connect() as conn:
            apply_migrations(conn)
    host: str = args.host
    port: int = args.port
    with serving_hosted():
        # Behind a proxy (Cloud Run, Caddy) trust X-Forwarded-Proto so CSRF checks see https.
        uvicorn.run(
            build_hosted_app(), host=host, port=port, proxy_headers=True, forwarded_allow_ips="*"
        )

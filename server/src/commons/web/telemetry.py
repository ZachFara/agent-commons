"""One span and one duration sample per HTTP request, named by route (decision 0037)."""

from typing import Any

from opentelemetry.instrumentation.starlette import StarletteInstrumentor
from opentelemetry.semconv.attributes.url_attributes import URL_QUERY
from opentelemetry.trace import Span
from starlette.applications import Starlette

from commons.service.telemetry import stable_conventions

REDACTED = "REDACTED"


def instrument(app: Starlette) -> Starlette:
    """Record each request to `app` as a span and an `http.server.request.duration` sample."""
    stable_conventions()
    StarletteInstrumentor().instrument_app(app, server_request_hook=_redact_query)
    return app


def _redact_query(span: Span, scope: dict[str, Any]) -> None:
    """Keep query strings out of spans: dashboard pages put search text there (decision 0024)."""
    if scope.get("query_string"):
        span.set_attribute(URL_QUERY, REDACTED)

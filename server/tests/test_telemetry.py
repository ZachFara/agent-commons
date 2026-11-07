"""Traces, metrics and logs over OTLP carry ids and timings, never agent text (decision 0037)."""

import json
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP
from opentelemetry import trace
from opentelemetry._logs import get_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, ReadableLogRecord
from opentelemetry.sdk._logs.export import LogRecordExporter, LogRecordExportResult
from opentelemetry.sdk.metrics.export import (
    HistogramDataPoint,
    InMemoryMetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, format_trace_id
from starlette.testclient import TestClient
from typing_extensions import override

from commons.hosted import app as hosted
from commons.mcp.call_log import MCP_METHOD, TOOL_NAME
from commons.service import telemetry
from commons.service.keys import create_key
from commons.store.connection import Connection
from commons.web.app import build_app
from commons.web.telemetry import REDACTED
from mcp_client import call
from test_hosted_server import mcp_status

QUERY = "zyxwvut frobnicator exploded in qwertyuiop"
TOOL_HISTOGRAM = "commons.tool_call.duration"
HTTP_HISTOGRAM = "http.server.request.duration"


class LogSink(LogRecordExporter):
    """Keep exported log records in memory (the SDK's own in-memory exporter is untyped)."""

    def __init__(self) -> None:
        self.records: list[ReadableLogRecord] = []

    @override
    def export(self, batch: Sequence[ReadableLogRecord]) -> LogRecordExportResult:
        self.records.extend(batch)
        return LogRecordExportResult.SUCCESS

    @override
    def shutdown(self) -> None:
        return

    @override
    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class Captured:
    """In-memory exporters standing in for the OTLP ones."""

    def __init__(self) -> None:
        self.spans = InMemorySpanExporter()
        self.samples = InMemoryMetricReader()
        self.logs = LogSink()

    def finished_spans(self) -> list[ReadableSpan]:
        provider = trace.get_tracer_provider()
        assert isinstance(provider, TracerProvider)
        provider.force_flush()
        return list(self.spans.get_finished_spans())

    def call_span(self, tool: str) -> ReadableSpan:
        """The server's span for a call of `tool` (the in-process client opens one too)."""
        spans = self.finished_spans()
        matching = [
            s
            for s in spans
            if s.name == f"tools/call {tool}" and "fastmcp.span.seam" in attributes_of(s)
        ]
        assert len(matching) == 1, [(s.name, attributes_of(s)) for s in spans]
        return matching[0]

    def named(self, name: str) -> list[ReadableSpan]:
        return [span for span in self.finished_spans() if span.name == name]

    def points(self, histogram: str) -> list[HistogramDataPoint]:
        data = self.samples.get_metrics_data()
        assert data is not None
        return [
            point
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == histogram
            for point in metric.data.data_points
            if isinstance(point, HistogramDataPoint)
        ]

    def call_lines(self) -> list[dict[str, Any]]:
        """Exported call-log records as their JSON form (body, trace id, attributes...)."""
        provider = get_logger_provider()
        assert isinstance(provider, LoggerProvider)
        provider.force_flush()
        return [
            json.loads(record.to_json())
            for record in self.logs.records
            if record.instrumentation_scope is not None
            and record.instrumentation_scope.name == "commons.calls"
        ]

    def clear(self) -> None:
        self.spans.clear()
        self.logs.records.clear()


@pytest.fixture(scope="session")
def exporters() -> Captured:
    """Install the SDK once for the whole session: OpenTelemetry's globals can't be replaced."""
    captured = Captured()
    telemetry.install("commons-test", captured.spans, captured.samples, captured.logs)
    return captured


@pytest.fixture
def captured(exporters: Captured) -> Captured:
    exporters.clear()
    return exporters


def connection_to(db: psycopg.Connection) -> Callable[[], AbstractContextManager[Connection]]:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return test_connection


def attributes_of(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


def test_without_an_endpoint_nothing_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(telemetry.ENDPOINT_VAR, raising=False)
    installed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telemetry, "install", lambda *args: installed.append(args))
    assert telemetry.configure("commons-test") is False
    assert installed == []


def test_an_endpoint_turns_on_the_otlp_exporters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(telemetry.ENDPOINT_VAR, "http://127.0.0.1:4318")
    installed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(telemetry, "install", lambda *args: installed.append(args))
    assert telemetry.configure("commons-test") is True
    ((service, spans, samples, logs),) = installed
    samples.shutdown()  # stop its export thread; nothing listens on that port
    assert service == "commons-test"
    assert isinstance(spans, OTLPSpanExporter)
    assert isinstance(samples, PeriodicExportingMetricReader)
    assert isinstance(logs, OTLPLogExporter)


def test_a_tool_call_leaves_a_span_a_sample_and_a_line_that_share_a_trace(
    server: FastMCP, reporter: uuid.UUID, captured: Captured
) -> None:
    del reporter
    task_id = str(uuid.uuid4())
    call(server, "search", {"query": QUERY, "task_id": task_id, "model": "claude-opus-5-5"})

    span = captured.call_span("search")
    attributes = attributes_of(span)
    ours = {k: v for k, v in attributes.items() if k.startswith("commons.") or k == TOOL_NAME}
    assert attributes[MCP_METHOD] == "tools/call"
    assert ours == {
        TOOL_NAME: "search",
        "commons.task_id": task_id,
        "commons.model": "claude-opus-5-5",
        "commons.results": 0,
    }
    assert span.status.status_code is StatusCode.UNSET

    miss = {TOOL_NAME: "search", "commons.results": "none"}
    (point,) = [p for p in captured.points(TOOL_HISTOGRAM) if dict(p.attributes or {}) == miss]
    assert point.count >= 1
    assert list(point.explicit_bounds)[:3] == [0.005, 0.01, 0.025]

    (line,) = [r for r in captured.call_lines() if json.loads(r["body"])["task_id"] == task_id]
    assert json.loads(line["body"])["trace_id"] == format_trace_id(span.context.trace_id)
    assert line["trace_id"] == f"0x{format_trace_id(span.context.trace_id)}"


def test_sql_spans_hold_statements_and_nothing_an_agent_wrote(
    server: FastMCP, reporter: uuid.UUID, captured: Captured
) -> None:
    del reporter
    call(server, "search", {"query": QUERY, "task_id": str(uuid.uuid4())})
    spans = captured.finished_spans()
    sql = [a for a in map(attributes_of, spans) if a.get("db.system.name") == "postgresql"]
    assert sql, [s.name for s in spans]
    statements = [str(a.get("db.query.text")) for a in sql]
    assert any("%s" in statement for statement in statements), statements
    assert not any(k.startswith("db.query.parameter") for a in sql for k in a)
    for span in spans:
        events = [event.attributes for event in span.events]
        assert QUERY not in json.dumps([span.name, attributes_of(span), events], default=str)
    assert all(QUERY not in json.dumps(line) for line in captured.call_lines())


def test_a_refused_call_is_marked_with_its_error_code(
    server: FastMCP, reporter: uuid.UUID, captured: Captured
) -> None:
    del reporter
    result = call(server, "search", {"query": "  ", "task_id": str(uuid.uuid4())})
    assert result["error"]["code"] == "invalid_input"

    assert attributes_of(captured.call_span("search"))["commons.error"] == "invalid_input"
    failed = {TOOL_NAME: "search", "error.type": "invalid_input"}
    assert any(dict(p.attributes or {}) == failed for p in captured.points(TOOL_HISTOGRAM))


def test_http_spans_are_named_by_route_and_carry_no_query_string(
    db: psycopg.Connection, captured: Captured
) -> None:
    app = build_app(connection_to(db))
    response = TestClient(app).get("/entries", params={"q": QUERY, "order": "score"})
    assert response.status_code == 200

    (span,) = captured.named("GET /entries")
    attributes = attributes_of(span)
    assert (attributes["http.route"], attributes["url.query"]) == ("/entries", REDACTED)
    assert attributes["http.response.status_code"] == 200
    assert QUERY not in json.dumps(attributes)
    routes = {dict(p.attributes or {}).get("http.route") for p in captured.points(HTTP_HISTOGRAM)}
    assert "/entries" in routes
    names = [s.name for s in captured.finished_spans()]
    assert not [n for n in names if n.endswith((" http receive", " http send"))], names


def test_mcp_requests_are_the_mcp_route_and_parent_the_tool_span(
    db: psycopg.Connection, captured: Captured
) -> None:
    app = hosted.build_hosted_app(connection_to(db))
    key = create_key(db, "telemetry-admin", admin=True)
    mcp_status(app, key)

    call_span = captured.call_span("status")
    requests = captured.named("POST /mcp")
    assert requests
    assert all(attributes_of(r)["http.route"] == "/mcp" for r in requests)
    assert call_span.parent is not None
    assert call_span.parent.span_id in {r.context.span_id for r in requests}

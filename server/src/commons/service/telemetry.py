"""Traces, metrics and logs over OTLP, for the LGTM stack (decision 0037).

Nothing leaves the process unless OTEL_EXPORTER_OTLP_ENDPOINT is set. Then every entry point
sends three signals to that endpoint over HTTP/protobuf: spans (HTTP requests, MCP tool calls,
SQL statements), metrics (request and tool-call durations), and the `commons` loggers' records.
What goes follows decision 0024: ids, counts, timings, routes and SQL templates; never agent
queries, entry text or statement parameters.
"""

import logging
import os
import time
import uuid
from collections.abc import Sequence

from opentelemetry import metrics, trace
from opentelemetry._logs import LogRecord, SeverityNumber, set_logger_provider
from opentelemetry.context import Context, get_current
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_INSTANCE_ID, SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON, Sampler, SamplingResult
from opentelemetry.trace import Link, SpanKind, TraceState
from opentelemetry.util.types import Attributes
from typing_extensions import override

ENDPOINT_VAR = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE_NAME_VAR = "OTEL_SERVICE_NAME"
CONVENTIONS_VAR = "OTEL_SEMCONV_STABILITY_OPT_IN"
STABLE_CONVENTIONS = "http,database"

_SEVERITIES = {
    logging.DEBUG: ("DEBUG", SeverityNumber.DEBUG),
    logging.INFO: ("INFO", SeverityNumber.INFO),
    logging.WARNING: ("WARN", SeverityNumber.WARN),
    logging.ERROR: ("ERROR", SeverityNumber.ERROR),
    logging.CRITICAL: ("FATAL", SeverityNumber.FATAL),
}


def configure(service: str) -> bool:
    """Export this process's telemetry if OTEL_EXPORTER_OTLP_ENDPOINT is set; say whether it will.

    `service` names the entry point (`service.name`); OTEL_SERVICE_NAME overrides it. The
    exporters read the endpoint and OTEL_EXPORTER_OTLP_HEADERS from the environment themselves.
    """
    if not os.environ.get(ENDPOINT_VAR):
        return False
    install(
        service,
        OTLPSpanExporter(),
        PeriodicExportingMetricReader(OTLPMetricExporter()),
        OTLPLogExporter(),
    )
    return True


def stable_conventions() -> None:
    """Opt into the stable HTTP and database conventions, unless the deployment chose otherwise.

    The instrumentations read this variable once, when the first of them is built, so it is set
    before any app or exporter exists: metric and attribute names (the dashboards query them)
    must not depend on what a deployment happened to put in the environment.
    """
    os.environ.setdefault(CONVENTIONS_VAR, STABLE_CONVENTIONS)


def install(
    service: str, spans: SpanExporter, samples: MetricReader, logs: LogRecordExporter
) -> None:
    """Make these exporters the process-wide providers and instrument the database driver.

    Once per process: OpenTelemetry's global providers can't be replaced later.
    """
    stable_conventions()
    attributes: dict[str, str] = {SERVICE_INSTANCE_ID: str(uuid.uuid4())}
    if not os.environ.get(SERVICE_NAME_VAR):
        attributes[SERVICE_NAME] = service
    resource = Resource.create(attributes)

    tracer_provider = TracerProvider(resource=resource, sampler=_RequestsAndCalls())
    tracer_provider.add_span_processor(BatchSpanProcessor(spans))
    trace.set_tracer_provider(tracer_provider)

    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[samples]))

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(logs))
    set_logger_provider(logger_provider)
    # Only this package's loggers: the call log (decision 0024) and our own warnings. Third-party
    # loggers stay on stdout and stderr, where the host already collects them.
    logging.getLogger("commons").addHandler(_OtlpHandler(logger_provider))

    # Statement templates only: `capture_parameters` stays off, since parameters hold agent text.
    PsycopgInstrumentor().instrument(tracer_provider=tracer_provider)


class _OtlpHandler(logging.Handler):
    """Hand each record to the OTLP logger provider, inside the current trace.

    The SDK's own handler is deprecated in favour of one that the instrumentation packages
    don't ship yet (opentelemetry-instrumentation-logging 0.66b2 has none), so this does the
    little it did: timestamps, severity, the message, where it came from, and any exception.
    """

    def __init__(self, provider: LoggerProvider) -> None:
        super().__init__(level=logging.INFO)
        self._provider = provider

    @override
    def emit(self, record: logging.LogRecord) -> None:
        text, number = _SEVERITIES.get(record.levelno, ("INFO", SeverityNumber.INFO))
        self._provider.get_logger(record.name).emit(
            LogRecord(
                timestamp=int(record.created * 1e9),
                observed_timestamp=time.time_ns(),
                context=get_current(),
                severity_text=text,
                severity_number=number,
                body=record.getMessage(),
                attributes={
                    "code.file.path": record.pathname,
                    "code.function.name": record.funcName,
                    "code.line.number": record.lineno,
                },
                exception=record.exc_info[1] if record.exc_info else None,
            )
        )


class _RequestsAndCalls(Sampler):
    """Record every span except the ASGI instrumentation's one per receive and send event.

    Those add four or five spans to each request and say nothing the request span doesn't.
    """

    @override
    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        sampler = ALWAYS_OFF if name.endswith((" http receive", " http send")) else ALWAYS_ON
        return sampler.should_sample(
            parent_context, trace_id, name, kind, attributes, links, trace_state
        )

    @override
    def get_description(self) -> str:
        return "commons: everything but ASGI event spans"

"""Generate the Grafana dashboards in observability/grafana/dashboards (decision 0037).

Usage: uv run python tools/grafana_dashboards.py

Grafana loads the JSON files; this file is their source, so edit it and regenerate. Four
dashboards: the service overview, MCP tools and the agents using them, incidents and debugging
(by task, reporter, error), and the database.
"""

import json
from pathlib import Path
from typing import Any

Panel = dict[str, Any]
OUT = Path(__file__).resolve().parent.parent / "observability" / "grafana" / "dashboards"

MIMIR = {"type": "prometheus", "uid": "mimir"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}

JOB = 'job=~"$job"'
APP = '{service_name=~"$job"}'  # the app's own log lines in Loki
SVC = 'resource.service.name =~ "$job"'  # the app's own spans in Tempo
HTTP = "http_server_request_duration"  # Mimir ingests OTLP names without unit suffixes
TOOL = "commons_tool_call_duration"
SPANS = 'span_kind="SPAN_KIND_CLIENT", service=~"$job"'  # Tempo's span metrics for SQL
DB = 'datname!~"template.*|postgres"'  # the Postgres exporter's databases
RATE = "[$__rate_interval]"
RATIO = [(0.01, "orange"), (0.05, "red")]
FEW = [(1.0, "orange"), (5.0, "red")]
# One fixed colour per tool, so a tool keeps its colour whatever else is on the chart.
TOOL_COLOURS = {
    "search": "blue",
    "propose": "green",
    "report": "purple",
    "approve": "dark-green",
    "reject": "dark-purple",
    "pending": "yellow",
    "status": "dark-blue",
}


class Grid:
    """Panels laid out top to bottom, a row title or a line of panels at a time."""

    def __init__(self) -> None:
        """Start empty."""
        self.panels: list[Panel] = []
        self._y = 0
        self._next_id = 1

    def row(self, title: str) -> None:
        """Add a row header."""
        self._place({"type": "row", "title": title, "collapsed": False, "panels": []}, 0, 24, 1)
        self._y += 1

    def line(self, height: int, *panels: Panel) -> None:
        """Add panels side by side, sharing the 24 units of width equally."""
        width = 24 // len(panels)
        for index, panel in enumerate(panels):
            self._place(panel, index * width, width, height)
        self._y += height

    def _place(self, panel: Panel, x: int, w: int, h: int) -> None:
        panel["id"] = self._next_id
        panel["gridPos"] = {"x": x, "y": self._y, "w": w, "h": h}
        self._next_id += 1
        self.panels.append(panel)


# ------------------------------------------------------------------ query helpers


def per_second(histogram: str, selector: str, by: str = "") -> str:
    """Return PromQL for the call rate of `histogram` over `selector`, grouped by `by`."""
    group = f"sum by ({by}) " if by else "sum"
    return f"{group}(rate({histogram}_count{{{selector}}}{RATE}))"


def per_hour(histogram: str, selector: str) -> str:
    """Return PromQL for the calls of `histogram` over `selector` in the last hour."""
    return f"sum(increase({histogram}_count{{{selector}}}[1h]))"


def share(histogram: str, part: str, whole: str) -> str:
    """Return PromQL for the share of `whole`'s calls that match `part`."""
    return f"{per_second(histogram, part)} / {per_second(histogram, whole)}"


def quantile(q: float, histogram: str, selector: str, by: str = "") -> str:
    """Return PromQL for a latency quantile of `histogram` over `selector`."""
    group = f"le, {by}" if by else "le"
    return (
        f"histogram_quantile({q}, sum by ({group}) (rate({histogram}_bucket{{{selector}}}{RATE})))"
    )


def count_lines(filter_: str, window: str = "$__auto", by: str = "") -> str:
    """Return LogQL counting the app's lines that pass `filter_`, grouped by `by`."""
    group = f"sum by ({by}) " if by else "sum"
    return f"{group}(count_over_time({APP} | json | {filter_} [{window}]))"


# ------------------------------------------------------------------ targets and panels


def prom(expr: str, legend: str = "__auto", *, exemplar: bool = False, ref: str = "A") -> Panel:
    """Return a Mimir query."""
    return {
        "datasource": MIMIR,
        "expr": expr,
        "legendFormat": legend,
        "exemplar": exemplar,
        "refId": ref,
    }


def logql(expr: str, legend: str = "__auto", *, ref: str = "A") -> Panel:
    """Return a Loki query: log lines, or a metric over them."""
    return {
        "datasource": LOKI,
        "expr": expr,
        "legendFormat": legend,
        "queryType": "range",
        "refId": ref,
    }


def stat(
    title: str,
    target: Panel,
    unit: str,
    *,
    thresholds: list[tuple[float, str]] | None = None,
    description: str = "",
) -> Panel:
    """Return a headline number; `thresholds` colour it, green below the first."""
    steps: list[dict[str, Any]] = [{"color": "green" if thresholds else "text", "value": None}]
    steps += [{"color": colour, "value": value} for value, colour in thresholds or []]
    return {
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": target["datasource"],
        "targets": [target],
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "value" if thresholds else "none",
            "graphMode": "area",
            "textMode": "value",
            "justifyMode": "auto",
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": 2,
                "thresholds": {"mode": "absolute", "steps": steps},
            },
            "overrides": [],
        },
    }


def timeseries(
    title: str,
    targets: list[Panel],
    unit: str,
    *,
    description: str = "",
    stack: bool = False,
    by_tool: bool = False,
) -> Panel:
    """Return lines over time; `stack` for parts of a whole, `by_tool` pins each tool's colour."""
    overrides = [
        {
            "matcher": {"id": "byName", "options": tool},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": colour}}],
        }
        for tool, colour in (TOOL_COLOURS.items() if by_tool else [])
    ]
    return {
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": targets[0]["datasource"],
        "targets": targets,
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "min": 0,
                "custom": {
                    "lineWidth": 2,
                    "fillOpacity": 8,
                    "showPoints": "never",
                    "spanNulls": False,
                    "drawStyle": "line",
                    "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                },
            },
            "overrides": overrides,
        },
    }


def logs(title: str, expr: str, description: str = "") -> Panel:
    """Return log lines from Loki, newest first, with details on expand."""
    return {
        "type": "logs",
        "title": title,
        "description": description,
        "datasource": LOKI,
        "targets": [logql(expr)],
        "options": {
            "showTime": True,
            "wrapLogMessage": True,
            "prettifyLogMessage": False,
            "enableLogDetails": True,
            "dedupStrategy": "none",
            "sortOrder": "Descending",
        },
        "fieldConfig": {"defaults": {}, "overrides": []},
    }


def traces(title: str, query: str, description: str = "") -> Panel:
    """Return the traces matching a TraceQL query, one row each."""
    target = {"datasource": TEMPO, "refId": "A", "queryType": "traceql", "limit": 20}
    return {
        "type": "traces",
        "title": title,
        "description": description,
        "datasource": TEMPO,
        "targets": [{**target, "query": query}],
        "options": {},
        "fieldConfig": {"defaults": {}, "overrides": []},
    }


def job_variable() -> Panel:
    """Return the `$job` picker: commons-server, commons-mcp, commons-dashboard, or all."""
    query = f"label_values({HTTP}_count, job)"
    return {
        "name": "job",
        "label": "Service",
        "type": "query",
        "datasource": MIMIR,
        "query": {"query": query, "refId": "job"},
        "definition": query,
        "includeAll": True,
        "multi": True,
        "allValue": ".+",  # Loki refuses a matcher that can match the empty string
        "refresh": 2,
        "sort": 1,
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
        "options": [],
    }


def text_variable(name: str, label: str) -> Panel:
    """Return a free-text filter, empty by default."""
    return {
        "name": name,
        "label": label,
        "type": "textbox",
        "query": "",
        "current": {"selected": False, "text": "", "value": ""},
        "options": [],
    }


def dashboard(uid: str, title: str, description: str, grid: Grid, variables: list[Panel]) -> Panel:
    """Wrap a grid in Grafana's dashboard model."""
    links = {
        "type": "dashboards",
        "tags": ["agent-commons"],
        "title": "Agent Commons",
        "asDropdown": True,
        "includeVars": True,
        "keepTime": True,
    }
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": ["agent-commons"],
        "timezone": "browser",
        "editable": False,
        "graphTooltip": 1,
        "time": {"from": "now-1h", "to": "now"},
        "refresh": "30s",
        "schemaVersion": 41,
        "version": 1,
        "templating": {"list": variables},
        "annotations": {"list": []},
        "links": [links],
        "panels": grid.panels,
    }


# ------------------------------------------------------------------ the dashboards


def overview() -> Panel:
    """Build the service overview: requests, errors, latency, tool calls, hooks."""
    g = Grid()
    errors = f'{JOB}, http_response_status_code=~"5.."'
    failed = f'{JOB}, error_type!=""'
    g.row("Right now")
    g.line(
        4,
        stat("Requests", prom(per_second(HTTP, JOB)), "reqps"),
        stat("5xx ratio", prom(share(HTTP, errors, JOB)), "percentunit", thresholds=RATIO),
        stat("Request p95", prom(quantile(0.95, HTTP, JOB)), "s"),
        stat("Tool calls", prom(per_second(TOOL, JOB)), "reqps"),
        stat(
            "Tool error ratio",
            prom(share(TOOL, failed, JOB)),
            "percentunit",
            thresholds=RATIO,
            description="Including the caller's own mistakes (invalid_input, rate_limited).",
        ),
        stat("Tool call p95", prom(quantile(0.95, TOOL, JOB)), "s"),
    )
    g.row("HTTP")
    percentiles = [
        prom(quantile(q, HTTP, JOB), f"p{int(q * 100)}", exemplar=True, ref=ref)
        for q, ref in ((0.5, "A"), (0.95, "B"), (0.99, "C"))
    ]
    g.line(
        8,
        timeseries(
            "Requests by route",
            [
                prom(
                    per_second(HTTP, JOB, "http_request_method, http_route"),
                    "{{http_request_method}} {{http_route}}",
                )
            ],
            "reqps",
            stack=True,
        ),
        timeseries(
            "Request latency",
            percentiles,
            "s",
            description="Dots are exemplars: click one to open the trace behind it.",
        ),
    )
    g.line(
        8,
        timeseries(
            "Responses by status",
            [
                prom(
                    per_second(HTTP, JOB, "http_response_status_code"),
                    "{{http_response_status_code}}",
                )
            ],
            "reqps",
            stack=True,
        ),
        timeseries(
            "In flight",
            [prom(f"sum by (http_route) (http_server_active_requests{{{JOB}}})", "{{http_route}}")],
            "short",
            description="Requests being handled right now, by route.",
        ),
    )
    g.row("MCP tools")
    g.line(
        8,
        timeseries(
            "Tool calls by tool",
            [prom(per_second(TOOL, JOB, "gen_ai_tool_name"), "{{gen_ai_tool_name}}")],
            "reqps",
            stack=True,
            by_tool=True,
        ),
        timeseries(
            "Tool call latency p95 by tool",
            [
                prom(
                    quantile(0.95, TOOL, JOB, "gen_ai_tool_name"),
                    "{{gen_ai_tool_name}}",
                    exemplar=True,
                )
            ],
            "s",
            by_tool=True,
        ),
    )
    hooks = f'{JOB}, http_route=~"/api/hooks/.*"'
    g.row("Hooks (Claude Code sessions reporting in)")
    g.line(
        8,
        timeseries(
            "Hook calls by hook",
            [prom(per_second(HTTP, hooks, "http_route"), "{{http_route}}")],
            "reqps",
            stack=True,
        ),
        timeseries(
            "Hook responses by status",
            [
                prom(
                    per_second(HTTP, hooks, "http_response_status_code"),
                    "{{http_response_status_code}}",
                )
            ],
            "reqps",
            stack=True,
            description=(
                "401 and 403 are keys that stopped working; 429 is a rate limit; "
                "400 is a client sending the wrong shape."
            ),
        ),
        timeseries(
            "Hook latency p95",
            [prom(quantile(0.95, HTTP, hooks, "http_route"), "{{http_route}}")],
            "s",
            description="Hooks wait at most 30-60 s; a session feels anything above a second.",
        ),
    )
    g.row("Logs")
    g.line(
        12,
        logs(
            "Application log",
            f"{APP} | json",
            "Call lines, hook lines, events and warnings. Expand a line and follow its trace id.",
        ),
    )
    return dashboard(
        "agent-commons",
        "Agent Commons: overview",
        "Requests, MCP tool calls, hooks, their traces and logs (decision 0037).",
        g,
        [job_variable()],
    )


def tools() -> Panel:
    """Build the tools dashboard: searches and misses, proposals, reports, models, reporters."""
    g = Grid()
    search = f'{JOB}, gen_ai_tool_name="search"'
    misses = f'{search}, commons_results="none"'
    g.row("Searches")
    g.line(
        4,
        stat("Searches / h", prom(per_hour(TOOL, search)), "short"),
        stat(
            "Miss ratio",
            prom(share(TOOL, misses, search)),
            "percentunit",
            thresholds=[(0.5, "orange"), (0.8, "red")],
            description=(
                "Searches that found nothing: what agents look for that the store lacks "
                "(decision 0024). The Searches page lists the queries."
            ),
        ),
        stat(
            "Proposals / h",
            prom(per_hour(TOOL, f'{JOB}, gen_ai_tool_name="propose", error_type=""')),
            "short",
        ),
        stat(
            "Reports / h",
            prom(per_hour(TOOL, f'{JOB}, gen_ai_tool_name="report", error_type=""')),
            "short",
        ),
        stat("Search p95", prom(quantile(0.95, TOOL, search)), "s"),
        stat(
            "Rejected calls / h",
            prom(per_hour(TOOL, f'{JOB}, error_type!=""')),
            "short",
            thresholds=[(1, "orange"), (10, "red")],
        ),
    )
    g.line(
        8,
        timeseries(
            "Searches: hits and misses",
            [prom(per_second(TOOL, search, "commons_results"), "{{commons_results}}")],
            "reqps",
            stack=True,
        ),
        timeseries(
            "Calls by tool",
            [prom(per_second(TOOL, JOB, "gen_ai_tool_name"), "{{gen_ai_tool_name}}")],
            "reqps",
            stack=True,
            by_tool=True,
        ),
    )
    g.row("Errors and latency")
    g.line(
        8,
        timeseries(
            "Rejected and failed calls by code",
            [
                prom(
                    per_second(TOOL, f'{JOB}, error_type!=""', "gen_ai_tool_name, error_type"),
                    "{{gen_ai_tool_name}}: {{error_type}}",
                )
            ],
            "reqps",
            stack=True,
            description=(
                "invalid_input, not_found and rate_limited are the caller's; internal is ours."
            ),
        ),
        timeseries(
            "Latency p95 by tool",
            [
                prom(
                    quantile(0.95, TOOL, JOB, "gen_ai_tool_name"),
                    "{{gen_ai_tool_name}}",
                    exemplar=True,
                )
            ],
            "s",
            by_tool=True,
        ),
    )
    g.row("Who and with what")
    by_reporter = count_lines('event="tool_call"', by="reporter_id")
    g.line(
        8,
        timeseries(
            "Calls by model",
            [logql(count_lines('event="tool_call"', by="model"), "{{model}}")],
            "short",
            stack=True,
            description="The model each agent reported (decision 0009).",
        ),
        timeseries(
            "Calls by reporter (top 10)",
            [
                logql(
                    f"topk(10, {by_reporter})",
                    "{{reporter_id}}",
                )
            ],
            "short",
            description="Reporter ids; the People page names them.",
        ),
    )
    g.row("Recent")
    g.line(
        10,
        logs(
            "Proposals and reports",
            f'{APP} | json | event="tool_call" | tool=~"propose|report"',
            "One line per proposal or report, with the task and model; "
            "its trace shows what it did.",
        ),
        traces(
            "Slow tool calls (over 500 ms)",
            f'{{ span.gen_ai.tool.name != "" && duration > 500ms && {SVC} }}'
            " | select(span.commons.task_id, span.commons.model)",
        ),
    )
    return dashboard(
        "agent-commons-tools",
        "Agent Commons: tools and agents",
        "Searches and misses, proposals, reports, errors, models and reporters (decision 0037).",
        g,
        [job_variable()],
    )


def incidents() -> Panel:
    """Build the incidents dashboard: errors over time, then one task's or reporter's story."""
    g = Grid()
    errors = f'{JOB}, http_response_status_code=~"5.."'
    internal = f'{JOB}, error_type="internal"'
    g.row("Errors (last hour)")
    g.line(
        4,
        stat("5xx responses", prom(per_hour(HTTP, errors)), "short", thresholds=FEW),
        stat("Internal tool errors", prom(per_hour(TOOL, internal)), "short", thresholds=FEW),
        stat(
            "Unhandled exceptions",
            logql(count_lines('event="unhandled_error"', "1h")),
            "short",
            thresholds=FEW,
        ),
        stat(
            "Refused keys",
            logql(count_lines('event="key_refused"', "1h")),
            "short",
            thresholds=[(5, "orange"), (50, "red")],
            description=(
                "Revoked, expired, unknown or under-scoped keys. A burst is someone probing, "
                "or a member whose key rotated."
            ),
        ),
        stat(
            "Rate limited",
            logql(count_lines('error="rate_limited"', "1h")),
            "short",
            thresholds=[(1, "orange"), (20, "red")],
        ),
        stat(
            "Failed hook calls",
            logql(count_lines('event="hook_call" | ok="false"', "1h")),
            "short",
            thresholds=[(1, "orange"), (20, "red")],
        ),
    )
    refusals = 'event="key_refused" or error=~"rate_limited|unauthorized|forbidden"'
    g.line(
        8,
        timeseries(
            "Server-side failures",
            [
                prom(per_second(HTTP, errors, "http_route"), "5xx {{http_route}}", ref="A"),
                prom(per_second(TOOL, internal), "internal tool errors", ref="B"),
            ],
            "reqps",
            description="Ours to fix. The traces panel below has the failing requests.",
        ),
        timeseries(
            "Refusals",
            [
                logql(
                    count_lines(refusals, by="event, problem, error"),
                    "{{event}} {{problem}}{{error}}",
                )
            ],
            "short",
            stack=True,
            description="Keys refused by reason, and rate limits.",
        ),
    )
    g.row("Failing and slow requests")
    g.line(
        10,
        traces(
            "Failed traces",
            f"{{ status = error && {SVC} }}",
            "Requests and tool calls that ended in an error; open one to see the span that failed.",
        ),
        traces("Slow requests (over 1 s)", f"{{ kind = server && duration > 1s && {SVC} }}"),
    )
    g.line(
        10,
        logs(
            "Errors and warnings",
            f'{APP} | severity_text=~"ERROR|WARN"',
            "Tracebacks of unhandled exceptions and failed tools, refused keys, "
            "failed admin actions.",
        ),
    )
    g.row("One task or reporter (set the variables above)")
    g.line(
        10,
        logs(
            "Everything the task did",
            f'{APP} | json | task_id="$task_id"',
            "Every tool call and hook call of the task, in order. Its trace ids lead to the SQL.",
        ),
        traces("The task's traces", '{ span.commons.task_id = "$task_id" }'),
    )
    g.line(
        10,
        logs("The reporter's calls", f'{APP} | json | reporter_id="$reporter_id"'),
        logs(
            "Admin actions and key events",
            f'{APP} | json | event=~"admin_action|key_.*"',
            "Reviews, edits, retirements, merges, corrections, purges, keys minted and revoked, "
            "and by which key.",
        ),
    )
    return dashboard(
        "agent-commons-incidents",
        "Agent Commons: incidents and debugging",
        "What broke, when, for whom; then one task's or reporter's whole story (decision 0037).",
        g,
        [
            job_variable(),
            text_variable("task_id", "Task id"),
            text_variable("reporter_id", "Reporter id"),
        ],
    )


def database() -> Panel:
    """Build the database dashboard: statements as the app issued them, and Postgres itself."""
    g = Grid()
    hits = f"sum(rate(pg_stat_database_blks_hit{{{DB}}}[5m]))"
    reads = f"sum(rate(pg_stat_database_blks_read{{{DB}}}[5m]))"
    commits = f"sum(rate(pg_stat_database_xact_commit{{{DB}}}[5m]))"
    rollbacks = f"sum(rate(pg_stat_database_xact_rollback{{{DB}}}[5m]))"
    g.row("Right now")
    g.line(
        4,
        stat(
            "Postgres reachable",
            prom("max(pg_up)"),
            "short",
            thresholds=[(1, "green")],
            description=(
                "0 means Alloy can't reach the database it watches (COMMONS_METRICS_DATABASE_URL)."
            ),
        ),
        stat(
            "Connections",
            prom(f"sum(pg_stat_database_numbackends{{{DB}}})"),
            "short",
            thresholds=[(50, "orange"), (90, "red")],
        ),
        stat("Cache hit ratio", prom(f"{hits} / ({hits} + {reads})"), "percentunit"),
        stat(
            "Rollback ratio",
            prom(f"{rollbacks} / ({commits} + {rollbacks})"),
            "percentunit",
            thresholds=[(0.05, "orange"), (0.2, "red")],
        ),
        stat(
            "Deadlocks (1 h)",
            prom(f"sum(increase(pg_stat_database_deadlocks{{{DB}}}[1h]))"),
            "short",
            thresholds=[(1, "red")],
        ),
        stat("Database size", prom(f"sum(pg_database_size_bytes{{{DB}}})"), "bytes"),
    )
    g.row("Statements, as the app issued them")
    g.line(
        8,
        timeseries(
            "Statements per second",
            [
                prom(
                    f"sum by (span_name) (rate(traces_spanmetrics_calls_total{{{SPANS}}}{RATE}))",
                    "{{span_name}}",
                )
            ],
            "reqps",
            stack=True,
            description="From Tempo's span metrics: SELECT, INSERT, UPDATE, WITH...",
        ),
        timeseries(
            "Statement latency p95",
            [
                prom(
                    quantile(0.95, "traces_spanmetrics_latency", SPANS, "span_name"),
                    "{{span_name}}",
                    exemplar=True,
                )
            ],
            "s",
        ),
    )
    g.line(
        10,
        traces(
            "Slow statements (over 100 ms)",
            f'{{ span.db.system.name = "postgresql" && duration > 100ms && {SVC} }}'
            " | select(span.db.query.text)",
            "The statement text is the template with %s placeholders; "
            "parameters are never recorded.",
        ),
    )
    row_ops = [
        prom(f"sum(rate(pg_stat_database_tup_{op}{{{DB}}}{RATE}))", op, ref=ref)
        for op, ref in (
            ("returned", "A"),
            ("fetched", "B"),
            ("inserted", "C"),
            ("updated", "D"),
            ("deleted", "E"),
        )
    ]
    g.row("Postgres itself")
    g.line(
        8,
        timeseries(
            "Connections by state",
            [prom(f"sum by (state) (pg_stat_activity_count{{{DB}}})", "{{state}}")],
            "short",
            stack=True,
        ),
        timeseries(
            "Transactions",
            [
                prom(f"sum(rate(pg_stat_database_xact_commit{{{DB}}}{RATE}))", "commit", ref="A"),
                prom(
                    f"sum(rate(pg_stat_database_xact_rollback{{{DB}}}{RATE}))", "rollback", ref="B"
                ),
            ],
            "ops",
        ),
        timeseries("Row operations", row_ops, "ops"),
    )
    g.line(
        8,
        timeseries(
            "Locks by mode",
            [prom(f"sum by (mode) (pg_locks_count{{{DB}}})", "{{mode}}")],
            "short",
            stack=True,
        ),
        logs(
            "Postgres log",
            '{service_name="postgres"} |~ "(?i)error|fatal|panic|deadlock"',
            "From the container's stdout: errors, fatal messages, deadlocks.",
        ),
    )
    return dashboard(
        "agent-commons-database",
        "Agent Commons: database",
        "Statements as the app issued them, and Postgres statistics (decision 0037).",
        g,
        [job_variable()],
    )


def main() -> None:
    """Write every dashboard."""
    OUT.mkdir(parents=True, exist_ok=True)
    for build in (overview, tools, incidents, database):
        model = build()
        path = OUT / f"{model['uid']}.json"
        path.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
        print(f"{path.relative_to(OUT.parent.parent.parent)}: {len(model['panels'])} panels")


if __name__ == "__main__":
    main()

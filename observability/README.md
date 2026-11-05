# observability

The LGTM stack for Agent Commons (decision 0037): **L**oki for logs, **G**rafana, **T**empo
for traces, **M**imir for metrics, with Alloy collecting. The app exports all three signals
over OTLP to whatever `OTEL_EXPORTER_OTLP_ENDPOINT` names; this directory runs a complete
stack on one machine for local work and self-hosting. Production sends to a hosted endpoint
instead (see [Production](#production)).

## Run it

```sh
make observe                 # docker compose -f observability/compose.yml up -d --wait
open http://127.0.0.1:3000   # Grafana: the "Agent Commons" dashboard is provisioned
```

Then start whatever you want to watch with the endpoint set:

```sh
# The local dashboard, or a hosted server on your machine
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 uv run commons-dashboard
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 uv run commons-server --migrate

# The local MCP server Claude Code starts: register it with the variable
claude mcp remove --scope user commons
claude mcp add --scope user commons -e OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 \
  -- uv run --directory <path>/server commons-mcp

# A group server from db/compose.hosted.yml: put the endpoint in db/.env
#   OTEL_EXPORTER_OTLP_ENDPOINT=http://host.docker.internal:4318
```

Nothing is exported while the variable is unset. Stop the stack with
`docker compose -f observability/compose.yml down` (add `-v` to delete its data).

Grafana opens without a login as a viewer (Grafana 13 supports no other anonymous role).
Sign in as `admin` / `admin` for Explore, exemplar links and editing. Everything binds to
127.0.0.1.

## What you see

| Where | What |
|---|---|
| Dashboard "Agent Commons" | requests and 5xx ratio by route, latency percentiles with exemplars, tool calls, latency and errors by tool, recent tool-call traces, the application log |
| Explore, Tempo | one trace per request: `POST /mcp` → `tools/call search` → each SQL statement. Buttons jump to the logs of the trace and to the metrics of the service; the service graph shows commons-server → postgresql |
| Explore, Loki | `{service_name="commons-server"} \| json` for call lines (decision 0024); every line has a `trace_id` link. Container logs of Postgres, Caddy and this stack are under `{job="docker"}` |
| Explore, Mimir | `http_server_request_duration_*` and `commons_tool_call_duration_*` (seconds; Mimir's OTLP ingestion adds no unit suffix), with labels `http_route`, `gen_ai_tool_name`, `error_type`; `traces_spanmetrics_*` and `traces_service_graph_*` from Tempo |

Services are named after the entry point: `commons-server`, `commons-mcp`, `commons-dashboard`
(`OTEL_SERVICE_NAME` overrides). Each process has its own `service.instance.id`.

## What leaves the process

The rule of decision 0024 applies to telemetry as it does to the call log: ids, counts,
timings, routes and SQL templates, never agent queries, entry text or statement parameters.

- Request spans carry the route, method, status and client address; query strings are replaced
  by `REDACTED` (dashboard searches travel there).
- Tool-call spans carry the tool, reporter id, task id, model, result count and error code.
- SQL spans carry the statement with `%s` placeholders; parameter capture is off.
- Logs are the `commons.*` loggers only. Third-party loggers (uvicorn, the SDK) stay on stdout
  and stderr, where the host already collects them.

`server/tests/test_telemetry.py` checks each signal for the query text.

## Pieces

| File | Role |
|---|---|
| `compose.yml` | Alloy (OTLP on 4317 gRPC and 4318 HTTP, UI on 12345), Loki 3100, Tempo 3200, Mimir 9009, Grafana 3000 |
| `alloy/config.alloy` | OTLP in, fanned out by signal to Mimir, Tempo and Loki; the logs of every container on this Docker host from `/var/run/docker.sock` to Loki, labelled by compose project and service, lines older than a week dropped. The hosted compose's `server` container is not tailed, since it ships its own lines over OTLP |
| `loki/loki.yaml`, `tempo/tempo.yaml`, `mimir/mimir.yaml` | single-process, filesystem storage, 7-day retention. Tempo's metrics generator writes span metrics and service graphs to Mimir |
| `grafana/provisioning/datasources` | the three data sources and their links: exemplars → Tempo, trace → logs and metrics, log line → trace |
| `grafana/provisioning/dashboards`, `grafana/dashboards/*.json` | dashboards are files here; Grafana reloads them, and the UI can't save over them |

Versions are pinned in `compose.yml`. The Python side is `opentelemetry-sdk`, the HTTP/protobuf
exporter, and the Starlette and psycopg instrumentations (`server/pyproject.toml`); the code is
`server/src/commons/service/telemetry.py`, `web/telemetry.py`, and `mcp/call_log.py`.

## Production

Cloud Run can't host this stack for free and can't be scraped, so production pushes to a
hosted OTLP endpoint. Grafana Cloud's free tier (metrics, logs and traces, OTLP gateway) is
the expected one. In `infra/terraform.tfvars`:

```hcl
otlp_endpoint = "https://otlp-gateway-<region>.grafana.net/otlp"
otlp_headers  = "Authorization=Basic <base64 of instance-id:token>"
```

`terraform apply` stores the headers in Secret Manager per environment and sets the endpoint,
the headers and `deployment.environment.name` (`production` or `staging`) on both Cloud Run
services. Until then production keeps Cloud Logging and the alerts of decision 0033 and
exports nothing.

## If something is off

- Alloy's UI, http://127.0.0.1:12345, shows each component's health and the last error.
- No container logs: the Docker socket path differs on some Docker Desktop setups
  (`~/.docker/run/docker.sock`); change the bind mount in `compose.yml`.
- No traces from Claude Code sessions: the MCP registration must carry the `-e` flag above,
  and Claude Code starts a new server process per session, so restart the session.
- A process killed with SIGTERM (Claude Code ending a session) loses what it had not yet
  flushed, at most the last few seconds.

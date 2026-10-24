# 0019. One hosted process
Status: Accepted
Date: 2026-10-25

Context: Cloud Run (decision 0020) gives each service one URL and scales instances freely.
Hosted mode ran two processes (`commons-mcp --http`, `commons-dashboard --hosted`) behind a
path-routing proxy, and FastMCP's default HTTP mode keeps session state in memory.

Decision: `commons-server` serves `/mcp` (stateless streamable HTTP), `/api/hooks/*`, the
admin dashboard, and `/healthz` from one Starlette app in a new top layer, `commons.hosted`,
which composes `mcp` and `web`. `--migrate` applies migrations before serving (the advisory
lock serializes concurrent instances), so no separate migration job is needed. It reads
`$PORT`. The Docker image runs it by default; compose.hosted.yml is Postgres + server + Caddy.
The two older entry points still work for local experiments.

Consequences: one URL for members (MCP and hooks share it), any instance can serve any
request, and the same image runs on Cloud Run, Compose, or any container host.

# 0013. Admin dashboard: server-rendered, local, its own entry point
Status: Accepted
Date: 2026-10-24

Context: The owner asked for an admin view of incoming proposals and activity (T19-T20).

Decision:
- A new `commons.web` entrypoint layer, independent of `mcp` and `cli`, started with
  `commons-dashboard [--port 8765]`. Import contracts updated: only `web` imports starlette,
  uvicorn, or jinja2.
- Starlette (already a FastMCP dependency) with Jinja2 templates and autoescaping, because
  entries are agent-written text. No JavaScript, no build step, inline CSS.
- Binds to 127.0.0.1 only. There is no auth in local mode; hosted mode (T27) must add admin-key
  auth before the dashboard is exposed anywhere.
- The web layer builds plain row dicts for templates rather than reading dataclass attributes
  in templates, so the dead-code check sees every field the page uses.
- Data comes from the same services as the tools (`status`, `pending`) plus two recent-activity
  queries.

Consequences: one more process to run locally; the page can grow review actions (T20) as form
posts without adding a frontend stack. Redaction reports aren't stored yet (T21), so the page
can't show them; it will once they are.

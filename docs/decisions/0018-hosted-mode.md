# 0018. Hosted mode
Status: Accepted (owner, 2026-10-25)
Date: 2026-10-25

Context: DESIGN.md §2, §9: one Postgres shared by a group, server behind HTTPS, bearer API
keys minted by an admin CLI.

Decision:
- `commons-mcp --http` serves streamable HTTP. Each request's bearer key resolves to a reporter
  (`api_key_hash` = SHA-256 of the key); keys are shown once by `commons admin create-key
  <pseudonym> [--admin]`. A new `reporters.is_admin` flag marks admins.
- MCP `approve`/`reject` always record agent reviews, admin key or not (owner choice).
  Humans review on the hosted dashboard, which requires an admin key over HTTPS.
- Hooks in hosted mode post their derived observations to authenticated HTTP endpoints on the
  same server; edit content stays in the local spool (decision 0016).
- A reporter whose privacy default is `local` (or a task set to `local`) cannot write to a
  hosted server (decision 0014).
- `compose.hosted.yml`: Postgres, the server, and Caddy for automatic HTTPS on `$DOMAIN`.

Consequences: the same code serves both modes; identity comes from the config file locally
and from the key when hosted.

Amendments (2026-10-25, T27c-d):
- Hosted hooks post to `/api/hooks/*` on the web app (`commons-dashboard --hosted`), not to the
  MCP server, so only `web` imports Starlette. Shapes are validated strictly (derived data only).
- The hosted dashboard uses HTTP Basic with an admin key as the password; review POSTs still
  require a same-origin `Origin` (browsers send Basic credentials cross-site).
- No daily job: SessionStart re-ranks when snapshots are over a day old, in both modes.
- `compose.hosted.yml` lives in `db/` with the local compose file; the image is
  `server/Dockerfile`. Verified end to end with `DOMAIN=localhost`.

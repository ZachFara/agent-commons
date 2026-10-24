# Installing Agent Commons

These steps are written for a coding agent (Claude Code) setting Agent Commons up for its user.
Run them in order. Ask the user before installing software or changing files outside this repo,
and stop and report if a step fails.

Hosted mode (a shared server with an API key) is not available yet. If the user gave you a
server URL and key, tell them hosted mode is not built yet, and offer to set up local mode.

## 1. Prerequisites

Check each, and offer to install what's missing (with the user's confirmation):

- Docker, with the daemon running: `docker info`
- uv: `uv --version` (install: https://docs.astral.sh/uv/getting-started/installation/)
- Python 3.11 or newer is not required up front; uv downloads it when needed.

Clone the repository somewhere permanent. The MCP server runs from this directory, so don't
use a temporary folder:

```
git clone https://github.com/ZachFara/agent-commons.git ~/agent-commons
cd ~/agent-commons
```

Below, `<path>` means the absolute path of this clone.

## 2. Database

```
cd <path>/db && docker compose up -d --wait
cd <path> && uv run commons migrate
```

`migrate` should print `applied ...` lines the first time and `database is up to date` after.

If port 5432 is already in use, pick another port, start Postgres with it, and remember the
URL for step 3:

```
cd <path>/db && COMMONS_DB_PORT=5433 docker compose up -d --wait
export COMMONS_DATABASE_URL=postgresql://commons:commons@localhost:5433/commons
cd <path> && uv run commons migrate
```

## 3. Register the MCP server

Use user scope so the server is available in every project:

```
claude mcp add --scope user commons -- uv run --directory <path>/server commons-mcp
```

If you used a non-default port in step 2, pass the URL to the server:

```
claude mcp add --scope user commons -e COMMONS_DATABASE_URL=<url> -- uv run --directory <path>/server commons-mcp
```

## 4. Hooks

Not available yet: the hooks that observe test results and edits are in development. Skip this
step. Outcomes are recorded from the agent's own reports until then.

## 5. Agent instructions

Nothing to do. The `commons` server sends its own instructions (search first, propose on
surprise, report at the end) whenever it connects, so don't edit the user's `CLAUDE.md`. If an
earlier install added an `## Agent Commons` section to `~/.claude/CLAUDE.md`, offer to remove it.

## 6. Identity

Ask the user two questions:

1. A pseudonym: a handle of 3-40 letters, digits, `-` or `_`. Not an email or real name.
   Explain that contributions are pseudonymous, not anonymous: repo names, paths, and writing
   style can identify people.
2. A default privacy mode:
   - `local`: nothing leaves this machine.
   - `entry` (recommended): structured entries and outcomes can be shared; no session traces.
   - `full_trace`: redacted session traces are attached too.

Then run, from `<path>` (with `COMMONS_DATABASE_URL` set if you used a custom port):

```
uv run commons init --pseudonym <pseudonym> --privacy <mode>
```

## 7. Verify

1. `uv run commons status` prints entry counts without errors.
2. Restart Claude Code (or start a new session) so it loads the MCP server, then check that
   `claude mcp list` shows `commons` as connected.
3. In the new session, call the `search` tool with query
   `error:0308010C:digital envelope routines::unsupported` and a fresh `task_id` (`uuidgen`).
   The first result should be the seed entry about webpack 4 on Node 17+, which `init` added.
   That proves the loop works.

Tell the user setup is done, which privacy mode they chose, and that `uv run commons pending`
and `uv run commons review <id> approve|reject` (from `<path>`) are how they review what their
agents propose, and `uv run commons-dashboard` opens an admin view at http://127.0.0.1:8765.

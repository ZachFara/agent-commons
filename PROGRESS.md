# PROGRESS.md

Living status for agent sessions. Update it at the end of every session: move tasks, record
what you learned, add open questions. Keep it short; history lives in git.

## Status

- DESIGN.md §10 Phase 1 is done (2026-10-24): all seven MCP tools, CLI, admin dashboard,
  seeds, INSTALL.md. Owner installed it locally (2026-10-25).
- Current phase: DESIGN.md §10 Phase 2 (hooks, secret scanning, ranking snapshots, hosted).
- Remote: https://github.com/ZachFara/agent-commons (private). CI runs on every PR.
- Branch protection is unavailable (private repo on a free plan). Until the owner upgrades or
  makes the repo public, merge only with `gh pr merge --squash` after `gh pr checks --watch`
  shows `check` passing. The local hook still blocks commits to main. Owner is fine with this.

## Task list

One task per session and branch. Take the first unblocked `todo` task. Mark it `doing` on your
branch when you start and `done` in the same PR that completes it.
Every task's done criteria implicitly include: `make check` passes, new behavior has tests,
PROGRESS.md updated.

### DESIGN Phase 1: core loop

| ID | Status | Task | Done when |
|---|---|---|---|
| T01 | done (partial) | GitHub remote `agent-commons`, branch protection on `main` requiring the `check` workflow | Remote and CI done. Protection needs a paid plan; owner is fine without it |
| T02 | done | `db/compose.yml`: Postgres 16 + pgvector, healthcheck, local volume; `.env` defaults documented | `docker compose -f db/compose.yml up -d` reports healthy; `CREATE EXTENSION vector` succeeds |
| T03 | done | CI: Postgres service container + DB test fixture (per-test transaction or throwaway schema) | A test that runs `SELECT 1` through the fixture passes locally and in CI |
| T04 | done | Migration runner in `store` + `commons migrate` CLI command + `commons` script entry | Applies numbered `.sql` files once, records them in `schema_migrations`; re-running is a no-op (tested) |
| T05 | done | Check `cq-schema` field names and record the mapping in a decision record (prerequisite for T06) | `docs/decisions/` has the cq field mapping; any DESIGN.md field that collides with a cq name is listed as a proposed rename |
| T06 | done | Migration 0001: all tables and enums from DESIGN.md §3 (including `reviews` and `entries.human_reviewed`, decision 0005), generated `search_vector` + GIN index | Test introspects every table, column, and enum after `migrate` |
| T07 | done | `commons init`: pseudonym (refuse email-like), privacy default; create or reuse the reporter row; write the local identity config the MCP server reads (record where in a decision) | CLI tests via `main`: init creates a reporter and config; re-running with the same pseudonym reuses it; email-like pseudonym exits non-zero with a clear message |
| T08 | done | MCP server + `propose`: FastMCP stdio, `commons-mcp` script, structured errors `{"error": {"code", "message"}}`, reporter from local config, model from optional `model` arg else `unknown` (decision 0009). Domain types and validation (environment needs versions, domains 1-16) come in with it. Redaction report empty until T21 | In-process FastMCP client test: propose returns entry id, status pending, redaction report; invalid input returns a structured error; row readable in DB |
| T09 | done | `search` tool: full-text over approved entries, rank by recency (no snapshots yet), one retrievals row per result with rank and score, results under `data` with the untrusted note, provenance string incl. "unreviewed" | Tests: relevant entry first; pending/rejected never returned; retrievals rows written; payload shape |
| T10 | done | `report` tool: self-report path, agent_assessment stored, outcome `unknown` unless given, merged into the retrieval's single outcome row | Tests: report after search creates an outcome; second report updates it; report for an entry not retrieved in that task is a structured error |
| T11 | done | Reviews (decision 0005): `approve`/`reject`/`pending` tools (source agent) and `commons review <id> approve\|reject [--reason]` (source human); status follows latest human review, else latest agent review | Tests: agent review sets status but not human_reviewed; human overrides agent; multiple reviewers kept; pending lists pending or unreviewed |
| T12 | done | `status` tool and `commons status`: counts by status, 30-day retrievals and outcomes, top entries by success rate | Tests with seeded rows return expected counts; CLI prints them |

T13-T15 were folded into the slices above (2026-10-24): the dead-code check rejects types and
service functions that no entry point calls yet, so each task now starts at an MCP tool or CLI
command and goes down through the layers.

| T16 | done | `client/CLAUDE.snippet.md`: search-first, propose-on-surprise, post-hoc report, untrusted-data, full_trace downgrade rules | Snippet covers each rule in DESIGN.md §5-§6; under 60 lines |
| T17 | done | `INSTALL.md` (agent-facing, DESIGN.md §9 steps 1-7) and `README.md` | A fresh Claude Code session following INSTALL.md alone reaches a working `search` (owner verifies) |
| T18 | done | Ship a few seed entries (well-known public problems) loaded by `commons init`, so INSTALL step 7 has a known entry to find. Real entries come from use (owner, 2026-10-24) | `search` for a seed symptom returns it on a fresh install (tested) |
| T19 | done | Admin dashboard, read-only: pending queue, recent proposals with redaction reports, recent retrievals and outcomes. Adds a `web` entrypoint layer (decision record + import contract) | Served locally by the server; page test renders seeded rows; no write routes yet |
| T20 | done | Admin dashboard: file human reviews (approve/reject with reason) | Review from the dashboard writes a human reviews row; tested |

### DESIGN Phase 2: observed outcomes, ranking, hosted

Decisions 0016 (hooks), 0017 (ranking), 0018 (hosted), all owner-approved 2026-10-25.

| ID | Status | Task | Done when |
|---|---|---|---|
| T21 | done | Secret scanning and redaction on every write | Tested; report stored and shown |
| T22 | done | Hook plumbing: migration for `tasks` + `task_events`; `commons hook <event>` reading stdin, logging failures and exiting 0; SessionStart creates/reuses the task and prints the task id; server instructions use it; `client/hooks.json`; INSTALL step 4 merges it | Tests feed recorded payloads to `main(["hook", ...])`; start/resume reuse; malformed input exits 0 and logs |
| T23 | todo | Observation hooks: Bash `PostToolUse`/`PostToolUseFailure` test runs (exit code parsed from `error`) into `task_events`; Edit/Write/Bash into the local spool; file-change hashes | Tests for runner matching (built-in + config regex), exit codes, spool contents, no code in the store |
| T24 | todo | Stop hook: observed model from the transcript; `used` per retrieved entry; observed outcome from the latest test run after the retrieval; block once asking for `report` | Tests: used yes/partial/no/unknown; pass/fail after retrieval; earlier test runs ignored; stop_hook_active respected; agent outcome never overrides hook |
| T25 | todo | `commons rank`: v0 score into ranking_snapshots; `evidence.agent_outcome` kept by report; background trigger from SessionStart when stale | Tests of the formula (prior, weights, partial, decay), snapshot fields, search uses the new score |
| T26 | todo | Reporter reliability in `commons rank` | Tests: matches/pairs, NULL under 5 pairs, used as self-report weight |
| T27a | todo | Hosted identity: `reporters.is_admin`, `commons admin create-key`, key hashing, reporter from bearer key | Tests: key shown once, hash stored, lookup, admin flag |
| T27b | todo | `commons-mcp --http` with bearer auth; `local` reporters refused (0014) | In-process HTTP tests: no key 401, bad key 401, good key acts as that reporter; local refused |
| T27c | todo | Hosted hook endpoints; `commons hook` posts to the server when the config has a server URL and key | Tests against the app with a test client; spool stays local |
| T27d | todo | Hosted dashboard behind admin key (HTTPS); `compose.hosted.yml` with Caddy; INSTALL hosted section | Tests: non-admin 403; compose config validates (`docker compose config`) |

### DESIGN Phase 3 (after a month of data)

- cq adapter, counterfactual runner, decay/absorption flags, model attribution, `commons export`,
  `commons report`, `.claude-plugin/` packaging, first dataset release.

## Open questions

Raise new ones here. Do not resolve a question by guessing in code.

None right now.

Resolved 2026-10-24 by owner: review model (decision 0005), hook location (decision 0006),
seed entries come from real use rather than a manual batch (T18), admin dashboard added (T19-T20),
cq field mapping (decision 0008: cq names for domains/created_by/reviewed_by, keep symptom/cause/fix).
Resolved 2026-10-25 by owner: branch protection stays off (merge only after `check` passes, local
hook blocks main); unreviewed entries are visible to everyone in hosted mode; `local` means
"write only to the local store" (decision 0014); no license for now; seed fixes confirmed.

DESIGN.md §11 open questions (research, answered by data later): the non-obvious heuristic's
rejection rate; whether diff matching detects `used` well enough; when full-text search stops
being enough; whether anyone uses full_trace.

## Next steps

1. Next session: T23.

## Session log

- 2026-10-24: repo setup (tooling, checks, AGENTS.md, decisions 0001-0004). Initial commit on
  `main` as the bootstrap exception in decision 0004.
- 2026-10-24: created private remote and CI (T01, partial); recorded owner decisions 0005,
  0006; updated DESIGN.md §3-§5, §8, §10 to match; added dashboard tasks.
- 2026-10-24: T02 `db/compose.yml` (pgvector/pgvector:pg16, healthcheck, 127.0.0.1 only).
  Schema, including `CREATE EXTENSION vector`, belongs to migrations, not init scripts.
- 2026-10-24: T03 CI Postgres service container, `db` test fixture, `make db`. `make check`
  now needs local Postgres running.
- 2026-10-24: T04 migration runner (`store/migrate.py`, files in `store/migrations/`) and
  `commons migrate`. Fixed the T03 `db` fixture: code calling `conn.transaction()` committed
  for real; tests now run inside a force-rollback outer transaction. Decision 0007 (argparse).
- 2026-10-24: T05 read cq-schema 0.3.1; mapping and proposed renames in decision 0008.
- 2026-10-24: T06 migration 0001 (all tables, enums, generated weighted `search_vector`, GIN
  index, updated_at triggers). Test DB is migrated once per session in conftest. Notes for later
  tasks: `outcomes.used` NULL means unknown; one outcome row per retrieval (unique); pgvector
  extension deliberately not created until vectors are needed (DESIGN §2).
- 2026-10-24: reordered T07-T15 into end-to-end slices (dead-code check); decision 0009 on
  model identification.
- 2026-10-24: T07 `commons init` (decision 0010). Conventions added to AGENTS.md: Literal
  types for DB enums, `store.connection.Connection` above store, `InvalidInputError`.
- 2026-10-24: T08 MCP server + `propose`, verified over real stdio with `commons-mcp`.
  Owner-approved: `entries.task_id` (migration 0002), mcp/ exempt from max-args (decision 0011).
  Environment rule: language/tool must contain a version, every library pinned, at least one
  of the three present. Middleware overrides `__call__` (not `on_call_tool`) so vulture sees it.
- 2026-10-24: T09 `search` (decision 0012, owner-approved: required task_id, OR-matching,
  relevance x score, language filter). Provenance stats are live SQL until snapshots (T25).
  Test helpers: `tests/factories.py` inserts entries directly.
- 2026-10-24: T10 `report`. Outcome attaches to the earliest retrieval of the entry in the task;
  `evidence.outcome_source` is `agent` or `hook`, agent evidence under `evidence.agent`. Hooks
  (T22-T24) must set `outcome_source: "hook"` and put payloads under `evidence.observed`.
  `report` takes no `model` (0009 amended).
- 2026-10-24: T11 reviews: `approve`/`reject`/`pending` tools (agent source), `commons review`
  and `commons pending` (human). Tools are now registered via `FastMCP(tools=[...])` because
  vulture can't see `@server.tool`. Made two tests order-independent (rule added to AGENTS.md).
- 2026-10-24: T12 `status` tool and `commons status`. Per-entry outcome stats SQL is shared
  (`store/outcome_stats.py`) by search and status. Decay flags omitted until Phase 3. All
  seven DESIGN.md §4 tools now exist.
- 2026-10-24: T16 `client/CLAUDE.snippet.md` (41 lines). Until hooks exist the agent mints its
  own task_id with `uuidgen`, and is told to keep secrets out since scanning (T21) isn't in yet;
  update both lines when T21/T22 land. A test keeps the snippet's tool/argument names real.
- 2026-10-24: T17 INSTALL.md and README.md. `claude mcp add` uses `--scope user` (DESIGN §9's
  command would register the server for one project only). Steps 4 (hooks) and hosted mode are
  marked not available yet. Owner verification pending.
- 2026-10-24: T18 three seed entries (`service/seeds.json`, fixed ids `5eed0000-...`) loaded
  idempotently by `commons init` as approved + human-reviewed by the `agent-commons-seed`
  reporter. INSTALL step 7 now searches for one. Search/report tests scope results to their own
  entries since seeds are committed to the shared test DB.
- 2026-10-24: T19 read-only admin dashboard (decision 0013): `commons-dashboard`, localhost only,
  counts, review queue, recent proposals, recent retrievals with outcomes. Redaction reports
  aren't persisted yet, so they're not shown; T21 should store them on the entry.
- 2026-10-24: T20 review from the dashboard (human source, reviewer = local identity). POSTs
  require Origin to equal the dashboard's own localhost origin and Host to be local (CSRF and
  DNS rebinding). DESIGN Phase 1 tasks are done except owner verification of T17.
- 2026-10-25: owner installed via INSTALL.md (T17 verified; agent skipped the CLAUDE.md step,
  so the snippet moves into the MCP server's instructions, decision 0015). Resolved open
  questions; decision 0014 on `local`.
- 2026-10-25: agent instructions moved from `client/CLAUDE.snippet.md` into the MCP server
  (decision 0015): cross-cutting rules in server instructions, per-tool guidance in tool
  descriptions, both under Claude Code's ~2,048-char silent truncation (tested).
- 2026-10-25: T21 secret scanning (`privacy/secrets.py`): keys/tokens for common providers, JWTs,
  bearer tokens, `*_KEY=`/`password:` assignments, credentialed URLs, private key blocks, emails,
  home-dir usernames. Runs on proposals (report stored in `entries.redaction_report`,
  migration 0003, shown in the dashboard queue), search queries as logged, reports, review
  reasons.
- 2026-10-25: Phase 2 split into T22-T27d after checking hook payloads empirically;
  decisions 0016-0018.
- 2026-10-25: T22 hook plumbing: `tasks` (migration 0004), `commons hook session-start` injects
  the task id (verified with real `claude -p`: the model saw it), `commons install-hooks` merges
  `client/hooks.json` into settings idempotently with a backup. Hooks run synchronously (~0.12s
  `uv run` start-up) so events are recorded before Stop reads them. Hook log:
  `$XDG_STATE_HOME/agent-commons/hooks.log`.

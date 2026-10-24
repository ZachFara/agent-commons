# PROGRESS.md

Living status for agent sessions. Update it at the end of every session: move tasks, record
what you learned, add open questions. Keep it short; history lives in git.

## Status

- Repo setup done (2026-10-24): tooling, checks, docs. No feature code yet.
- Current phase: DESIGN.md §10 Phase 1 (schema, server, CLI, install docs, seed entries).
- Remote: https://github.com/ZachFara/agent-commons (private). CI runs on every PR.
- Branch protection is unavailable (private repo on a free plan). Until the owner upgrades or
  makes the repo public, merge only with `gh pr merge --squash` after `gh pr checks --watch`
  shows `check` passing. The local hook still blocks commits to main.

## Task list

One task per session and branch. Take the first unblocked `todo` task. Mark it `doing` on your
branch when you start and `done` in the same PR that completes it.
Every task's done criteria implicitly include: `make check` passes, new behavior has tests,
PROGRESS.md updated.

### DESIGN Phase 1: core loop

| ID | Status | Task | Done when |
|---|---|---|---|
| T01 | done (partial) | GitHub remote `agent-commons`, branch protection on `main` requiring the `check` workflow | Remote and CI done. Protection blocked by plan, see Open questions |
| T02 | done | `db/compose.yml`: Postgres 16 + pgvector, healthcheck, local volume; `.env` defaults documented | `docker compose -f db/compose.yml up -d` reports healthy; `CREATE EXTENSION vector` succeeds |
| T03 | done | CI: Postgres service container + DB test fixture (per-test transaction or throwaway schema) | A test that runs `SELECT 1` through the fixture passes locally and in CI |
| T04 | done | Migration runner in `store` + `commons migrate` CLI command + `commons` script entry | Applies numbered `.sql` files once, records them in `schema_migrations`; re-running is a no-op (tested) |
| T05 | todo | Check `cq-schema` field names and record the mapping in a decision record (prerequisite for T06) | `docs/decisions/` has the cq field mapping; any DESIGN.md field that collides with a cq name is listed as a proposed rename |
| T06 | todo | Migration 0001: all tables and enums from DESIGN.md §3 (including `reviews` and `entries.human_reviewed`, decision 0005), generated `search_vector` + GIN index | Test introspects every table, column, and enum after `migrate` |
| T07 | todo | `domain`: enums and dataclasses for entries, retrievals, outcomes, reporters, models | Types mirror the schema; a test asserts Python enum values equal Postgres enum labels |
| T08 | todo | `store` + `service`: propose. Creates a pending entry, upserts the model row, returns id + redaction report (empty until T21) | Service test: propose then read back; environment without versions is rejected with a structured error |
| T09 | todo | Structured error type shared by service, MCP, and CLI | Every service error maps to `{"error": {"code", "message"}}`; test per code |
| T10 | todo | `service`: search. Full-text over approved entries, rank by recency (no snapshots yet), log one retrievals row per result, wrap in `data` with untrusted note, provenance string | Tests: relevant entry ranks first; pending/rejected never returned; retrievals rows written with rank and score |
| T11 | todo | `service`: report. Self-report path: store agent_assessment, outcome defaults to `unknown` | Test: report on a retrieved entry creates an outcome; report for an entry not retrieved in that task errors |
| T12 | todo | `service`: pending, approve, reject per decision 0005: reviews rows with source agent/human, status follows latest human review else latest agent review | Tests: agent review sets status but not human_reviewed; a human review overrides an agent one; multiple reviewers kept; pending lists unreviewed |
| T13 | todo | `service`: status. Counts by status, 30-day retrievals/outcomes, top entries by success rate | Test with seeded rows returns expected counts |
| T14 | todo | `mcp`: FastMCP stdio server exposing all tools; `commons-mcp` script entry | In-process FastMCP client test lists all seven tools and round-trips propose → approve → search → report |
| T15 | todo | `cli`: `commons init` (pseudonym, privacy default), `commons status`, `commons review <id> approve\|reject` (human source) | CLI tests via the entry point; `init` refuses a pseudonym that looks like an email |
| T16 | todo | `client/CLAUDE.snippet.md`: search-first, propose-on-surprise, post-hoc report, untrusted-data, full_trace downgrade rules | Snippet covers each rule in DESIGN.md §5-§6; under 60 lines |
| T17 | todo | `INSTALL.md` (agent-facing, DESIGN.md §9 steps 1-7) and `README.md` | A fresh Claude Code session following INSTALL.md alone reaches a working `search` (owner verifies) |
| T18 | todo | Ship a few seed entries (well-known public problems) loaded by `commons init`, so INSTALL step 7 has a known entry to find. Real entries come from use (owner, 2026-10-24) | `search` for a seed symptom returns it on a fresh install (tested) |
| T19 | todo | Admin dashboard, read-only: pending queue, recent proposals with redaction reports, recent retrievals and outcomes. Adds a `web` entrypoint layer (decision record + import contract) | Served locally by the server; page test renders seeded rows; no write routes yet |
| T20 | todo | Admin dashboard: file human reviews (approve/reject with reason) | Review from the dashboard writes a human reviews row; tested |

### DESIGN Phase 2 (split into session-sized tasks when Phase 1 is done)

- T21 Secret scanning and redaction in `privacy`, wired into propose and every write.
- T22 SessionStart hook (all hooks call `commons hook <event>`, decision 0006): mint task_id, set privacy mode.
- T23 PostToolUse hooks: test-runner capture (Bash), file hashes (Edit/Write).
- T24 Stop hook: `used` from diff matching, evidence assembly, outcome write, assessment prompt.
- T25 Nightly ranking job: v0 Bayesian score into ranking_snapshots; search reads latest snapshot.
- T26 Reporter reliability recomputation.
- T27 Hosted mode: streamable HTTP, bearer auth, `commons admin create-key`, `compose.hosted.yml`.

### DESIGN Phase 3 (after a month of data)

- cq adapter, counterfactual runner, decay/absorption flags, model attribution, `commons export`,
  `commons report`, `.claude-plugin/` packaging, first dataset release.

## Open questions

Raise new ones here. Do not resolve a question by guessing in code.

1. **Branch protection.** Unavailable on a private repo without GitHub Pro. Options: upgrade,
   make the repo public, or keep the manual rule in Status. Owner to choose.
2. **cq field names.** DESIGN.md §2 says not to invent names for fields cq already has, so the
   schema in T06 should use cq's names where they overlap. T05 checks this first; renames of
   DESIGN.md §3 fields would be proposed here.
3. **Visibility of agent-approved, unreviewed entries in hosted mode.** Decision 0005 makes them
   searchable locally. In a shared store, one reporter's agent could self-approve its own
   proposal. Proposal: in hosted mode, unreviewed entries are visible only to their proposer
   until a human reviews them. Decide before T27.
4. **`privacy_mode` vs `local`.** Entries' `privacy_mode` enum is `entry, full_trace`, while the
   reporter default also has `local`. Reading: local entries live only in the local store and
   carry `entry` there. Confirm.
5. **Secret scanning timing.** DESIGN §4 says propose scans before writing, but §10 puts secret
   scanning in Phase 2. Plan: T08 returns an empty redaction report and T21 adds scanning. Fine
   for a single local user; must land before hosted mode.
6. **License.** None yet; `LICENSE` is an allowed root file.

Resolved 2026-10-24 by owner: review model (decision 0005), hook location (decision 0006),
seed entries come from real use rather than a manual batch (T18), admin dashboard added (T19-T20).

DESIGN.md §11 open questions (research, answered by data later): the non-obvious heuristic's
rejection rate; whether diff matching detects `used` well enough; when full-text search stops
being enough; whether anyone uses full_trace.

## Next steps

1. Next session: T05.
2. Owner, when convenient: open questions 1, 4, 6.

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

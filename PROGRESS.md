# PROGRESS.md

Living status for agent sessions. Update it at the end of every session: move tasks, record
what you learned, add open questions. Keep it short; history lives in git.

## Status

- Repo setup done (2026-10-24): tooling, checks, docs. No feature code yet.
- Current phase: DESIGN.md §10 Phase 1 (schema, server, CLI, install docs, seed entries).
- Blocked on owner: T01 (GitHub remote + branch protection) before the first PR can merge.

## Task list

One task per session and branch. Take the first unblocked `todo` task. Mark it `doing` on your
branch when you start and `done` in the same PR that completes it.
Every task's done criteria implicitly include: `make check` passes, new behavior has tests,
PROGRESS.md updated.

### DESIGN Phase 1: core loop

| ID | Status | Task | Done when |
|---|---|---|---|
| T01 | blocked (owner) | GitHub remote `agent-commons`, branch protection on `main` requiring the `check` workflow | A PR from a branch shows the `check` status and cannot merge while it fails |
| T02 | todo | `db/compose.yml`: Postgres 16 + pgvector, healthcheck, local volume; `.env` defaults documented | `docker compose -f db/compose.yml up -d` reports healthy; `CREATE EXTENSION vector` succeeds |
| T03 | todo | CI: Postgres service container + DB test fixture (per-test transaction or throwaway schema) | A test that runs `SELECT 1` through the fixture passes locally and in CI |
| T04 | todo | Migration runner in `store` + `commons migrate` CLI command + `commons` script entry | Applies numbered `.sql` files once, records them in `schema_migrations`; re-running is a no-op (tested) |
| T05 | todo | Check `cq-schema` field names and record the mapping in a decision record (prerequisite for T06) | `docs/decisions/` has the cq field mapping; any DESIGN.md field that collides with a cq name is listed as a proposed rename |
| T06 | todo | Migration 0001: all tables and enums from DESIGN.md §3, generated `search_vector` + GIN index | Test introspects every table, column, and enum after `migrate` |
| T07 | todo | `domain`: enums and dataclasses for entries, retrievals, outcomes, reporters, models | Types mirror the schema; a test asserts Python enum values equal Postgres enum labels |
| T08 | todo | `store` + `service`: propose. Creates a pending entry, upserts the model row, returns id + redaction report (empty until T19) | Service test: propose then read back; environment without versions is rejected with a structured error |
| T09 | todo | Structured error type shared by service, MCP, and CLI | Every service error maps to `{"error": {"code", "message"}}`; test per code |
| T10 | todo | `service`: search. Full-text over approved entries, rank by recency (no snapshots yet), log one retrievals row per result, wrap in `data` with untrusted note, provenance string | Tests: relevant entry ranks first; pending/rejected never returned; retrievals rows written with rank and score |
| T11 | todo | `service`: report. Self-report path: store agent_assessment, outcome defaults to `unknown` | Test: report on a retrieved entry creates an outcome; report for an entry not retrieved in that task errors |
| T12 | todo | `service`: pending, approve, reject, with the human gate (see Open questions) | Tests: approve/reject change status; the gate refuses without confirmation |
| T13 | todo | `service`: status. Counts by status, 30-day retrievals/outcomes, top entries by success rate | Test with seeded rows returns expected counts |
| T14 | todo | `mcp`: FastMCP stdio server exposing all tools; `commons-mcp` script entry | In-process FastMCP client test lists all seven tools and round-trips propose → approve → search → report |
| T15 | todo | `cli`: `commons init` (pseudonym, privacy default) and `commons status` | CLI tests via the entry point; `init` refuses a pseudonym that looks like an email |
| T16 | todo | `client/CLAUDE.snippet.md`: search-first, propose-on-surprise, post-hoc report, untrusted-data, full_trace downgrade rules | Snippet covers each rule in DESIGN.md §5-§6; under 60 lines |
| T17 | todo | `INSTALL.md` (agent-facing, DESIGN.md §9 steps 1-7) and `README.md` | A fresh Claude Code session following INSTALL.md alone reaches a working `search` (owner verifies) |
| T18 | blocked (owner input) | Seed ten entries from the owner's recent work, via `propose` + `approve` | Ten approved entries; `commons status` shows them |

### DESIGN Phase 2 (split into session-sized tasks when Phase 1 is done)

- T19 Secret scanning and redaction in `privacy`, wired into propose and every write.
- T20 SessionStart hook: mint task_id, set privacy mode.
- T21 PostToolUse hooks: test-runner capture (Bash), file hashes (Edit/Write).
- T22 Stop hook: `used` from diff matching, evidence assembly, outcome write, assessment prompt.
- T23 Nightly ranking job: v0 Bayesian score into ranking_snapshots; search reads latest snapshot.
- T24 Reporter reliability recomputation.
- T25 Hosted mode: streamable HTTP, bearer auth, `commons admin create-key`, `compose.hosted.yml`.

### DESIGN Phase 3 (after a month of data)

- cq adapter, counterfactual runner, decay/absorption flags, model attribution, `commons export`,
  `commons report`, `.claude-plugin/` packaging, first dataset release.

## Open questions

Raise new ones here. Do not resolve a question by guessing in code.

1. **Human gate for approve/reject in local mode.** DESIGN.md §4 says "in local mode the CLI
   prompts", but the tools are called over stdio MCP by the agent, which has no CLI to prompt.
   Proposal: the MCP `approve`/`reject` tools in local mode return a pending-confirmation error
   and tell the human to run `commons approve <id>`; or use MCP elicitation if the client
   supports it. Needs owner decision before T12.
2. **cq field names.** DESIGN.md §2 says not to invent names for fields cq already has, so the
   schema in T06 should use cq's names where they overlap. T05 checks this first; renames of
   DESIGN.md §3 fields would be proposed here.
3. **Where hook code lives.** DESIGN.md puts hook definitions in `client/hooks.json`. Proposal:
   hooks call `uv run --directory <path>/server commons hook <event>`, so hook logic is in the
   `cli` layer and fully type-checked and tested, and `client/` stays config only.
4. **`privacy_mode` vs `local`.** Entries' `privacy_mode` enum is `entry, full_trace`, while the
   reporter default also has `local`. Reading: local entries live only in the local store and
   carry `entry` there. Confirm.
5. **Secret scanning timing.** DESIGN §4 says propose scans before writing, but §10 puts secret
   scanning in Phase 2. Plan: T08 returns an empty redaction report and T19 adds scanning. Fine
   for a single local user; must land before hosted mode.
6. **Repo and license.** GitHub repo name (`agent-commons` per DESIGN §9) and license (none yet;
   `LICENSE` is an allowed root file).

DESIGN.md §11 open questions (research, answered by data later): the non-obvious heuristic's
rejection rate; whether diff matching detects `used` well enough; when full-text search stops
being enough; whether anyone uses full_trace.

## Next steps

1. Owner: create the GitHub remote and branch protection (T01); answer open questions 1 and 3.
2. Next session: T02.

## Session log

- 2026-10-24: repo setup (tooling, checks, AGENTS.md, decisions 0001-0004). Initial commit on
  `main` as the bootstrap exception in decision 0004.

# PROGRESS.md

A short summary only. The task list is Linear: Zach's "Commons" project (agents read it) and
"Commons Agent Project" (agents write there). The orchestrator's docs coder rewrites this at
the end of each feature branch.

## Last run

- 2026-11-07, setup (MIT-580, `chore/agent-orchestration`): agent roles, Linear and Bash
  guard hooks, `.env.agent` and scripts for data isolation, rulesets on `main` and
  `feature/**`. Decision record `docs/decisions/2026-11-07-MIT-580-agent-orchestration.md`.
- 2026-11-07, MIT-575 (`feature/MIT-575-queue-badge`): every "awaiting review" count on the
  dashboard and in `status` now uses the review queue's own rule, so the numbers match the
  queue. Entries that went live provisionally without review have a "live without review"
  filter under Entries, linked from the queue page, and "Your entries" marks them
  "unreviewed".

## In flight

- The 2026-11-07 orchestrator run. State is in Linear ("Commons Agent Project"); run log MIT-626.
- 2026-11-07 to 2026-11-08, context7-lookups (MIT-564, `feature/MIT-564-context7-lookups`):
  - The PostToolUse hook records each Context7 `query-docs` call as a pointer in
    `doc_lookups` (migration 0020): library id and version, and the redacted query only in
    full_trace. `commons install-hooks` writes the anchored matcher, and `commons doctor`
    warns when installed hooks miss it.
  - Lookups follow corrections and retention like searches (migration 0021, an amendment
    to decision 0033). The dashboard's Searches page shows Commons misses next to lookups.
  - Hosted `session-start` returns the task's `privacy_mode`; the client sends a lookup's
    query only in full_trace.
  - Decision record `docs/decisions/2026-11-07-MIT-564-context7-fallback.md`, Status:
    Proposed. Nothing an agent sees has changed.


- context7-lookups: the final PR into main, for Zach.

## Next

- Product state: DESIGN.md §10 Phases 1 and 2 are done; the store is collecting data before
  Phase 3 (after a month of use).
- Zach decides the eight proposed items in the Context7 record (query text in entry mode,
  ingest or point, licensing, randomized routing, correction entries, rating lookups, eval
  feasibility, fallback on a miss). Anything an agent sees changing waits on those, and the
  wording ones need an eval.
- Open items from the old task list, to become Commons tickets if Zach wants agents on them:
  T55 (hook adapters for Codex CLI and Cursor, decision 0034 step 5) and T57 (the
  2026-10-31 audit's smaller gaps). Their full text, the "Under consideration" notes (a
  shipped SKILL.md, a per-turn Stop nudge, proposing after config work with no tests, an
  external data source) and the session log are in `git show fe1a155:PROGRESS.md`.
- Follow-ups from MIT-575:
  - On the hosted dashboard, contributors with only an agent key get a 403 on the
    `/entries/<id>` links from /mine (decision 0035).
  - The Entries dropdown filter does not default to newest.
  - `insights.duplicate_flags` keeps a looser "awaiting" definition (harmless).
  - Entries confirmed by outcomes still show "unreviewed".
  shipped SKILL.md, a per-turn Stop nudge, proposing after config work with no tests) and
  the session log are in `git show fe1a155:PROGRESS.md`. The external data source note is now
  the Context7 record.

## Open questions

- The doc-lookup hook was built from the `query-docs` tool schema and Claude Code's
  documented PostToolUse fields; no real Context7 payload has been seen. The first real
  session should confirm a `doc_lookups` row with the expected `library_id` and `version`.
- The Context7 record says its kill criterion is "drafted for `MIT-567`"; no branch has that
  criterion yet.
- Telemetry destination for production (decision 0037): Grafana Cloud's free tier, or a
  self-hosted `observability/` stack? Nothing is exported until `otlp_endpoint` is set.
- T43 leftovers: run `setup-backup-keys.sh`, then the first nightly backup and a
  `drill=true` run; the alerts have not been fire-tested. Retention (`commons retain`,
  decision 0033) runs at the end of that nightly backup workflow, so it waits on it too.
- DESIGN.md §11 (answered by data later): the non-obvious heuristic's rejection rate; whether
  diff matching detects `used` well enough; when full-text search stops being enough; whether
  anyone uses full_trace.

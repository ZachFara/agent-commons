# PROGRESS.md

A short summary only. The task list is Linear: Zach's "Commons" project (agents read it) and
"Commons Agent Project" (agents write there). The orchestrator's docs coder rewrites this at
the end of each feature branch.

## Last run

- 2026-11-07, setup (MIT-580, `chore/agent-orchestration`): agent roles, Linear and Bash
  guard hooks, `.env.agent` and scripts for data isolation, rulesets on `main` and
  `feature/**`. Decision record `docs/decisions/2026-11-07-MIT-580-agent-orchestration.md`.

## In flight

- Nothing. Phase 2 starts when Zach says so: `claude --agent commons-orchestrator`.

## Next

- Product state: DESIGN.md §10 Phases 1 and 2 are done; the store is collecting data before
  Phase 3 (after a month of use).
- Open items from the old task list, to become Commons tickets if Zach wants agents on them:
  T55 (hook adapters for Codex CLI and Cursor, decision 0034 step 5) and T57 (the
  2026-10-31 audit's smaller gaps). Their full text, the "Under consideration" notes (a
  shipped SKILL.md, a per-turn Stop nudge, proposing after config work with no tests, an
  external data source) and the session log are in `git show fe1a155:PROGRESS.md`.

## Open questions

- Retention: nothing prunes `searches`, and Cloud Logging keeps call lines for its default
  30 days. Decide a policy before the store has many users (decision 0024).
- Telemetry destination for production (decision 0037): Grafana Cloud's free tier, or a
  self-hosted `observability/` stack? Nothing is exported until `otlp_endpoint` is set.
- T43 leftovers: run `setup-backup-keys.sh`, then the first nightly backup and a
  `drill=true` run; the alerts have not been fire-tested.
- DESIGN.md §11 (answered by data later): the non-obvious heuristic's rejection rate; whether
  diff matching detects `used` well enough; when full-text search stops being enough; whether
  anyone uses full_trace.

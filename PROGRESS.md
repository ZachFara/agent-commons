# PROGRESS.md

A short summary only. The task list is Linear: Zach's "Commons" project (agents read it) and
"Commons Agent Project" (agents write there). The orchestrator's docs coder rewrites this at
the end of each feature branch.

## Last run

- 2026-11-07 to 2026-11-08, the swarm-research feature (`feature/MIT-582-swarm-research`):
  - Provenance's "success S/N when used" counts only used outcomes with a known result, as
    ranking does; `unknown` and `abandoned` are left out (MIT-587, PR #108). The `status`
    tool's top entries and `commons status` use the same count.
  - Review queue: each likely duplicate shows its cause and fix beside the proposal's. An
    entry's page lists the entries a reviewer kept distinct from it, with status and latest
    score (MIT-586, PR #109).
  - Searches page: a "Full result sets" figure (searches whose hits reached their limit) and a
    "Use by rank" table (share used per result rank 1, 2, 3, 4+) (MIT-590, PR #114).
  - Proposed decision `2026-11-07-MIT-582-evidence-counts-in-search.md`: counts in the
    provenance line instead of a rate, behind an eval. Not built (MIT-588, PR #107).
  - The setup branch (`chore/agent-orchestration`) merged in: guards for foreign issue IDs in
    GitHub text, one branch scheme, `GIT_DIR` handling in hooks.

## In flight

- PR #121 (MIT-591), a Proposed decision record, is still open against the feature branch.

## Next

- Zach decides whether to run the evidence-counts eval (condition `evidence-counts`, scenario
  `known-wrong-entry`); nothing ships before that.
- The two new Searches figures need a month of data before they say whether `limit=5` cuts off
  useful entries or agents only use the top result.
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

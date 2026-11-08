# PROGRESS.md

A short summary only. The task list is Linear: Zach's "Commons" project (agents read it) and
"Commons Agent Project" (agents write there). The orchestrator's docs coder rewrites this at
the end of each feature branch.

## Last run

- 2026-11-08, the commons-misses feature: the behavioral eval can now measure misses on
  configuration work with no failing test and no error text. Nothing production agents see has
  changed: `baseline` is byte-identical, and `join` and the invite still install no skill.
  - `MIT-597`: two no-test scenarios, `config-app-overwrites` and `config-default-wins`, plus a
    known-entry variant of each. Scenarios can carry their own task, an outcome check and a
    local app (decision 0023, addition).
  - `MIT-624`: `agent_eval.py run --skill` loads the plugin's skill with `--plugin-dir`,
    without the plugin's duplicate hooks.
  - `MIT-625`: five unshipped wording variants (`search-surprise`, `own-mistake`,
    `lesson-also-propose`, `propose-example`, `combined`) and a refreshed skill.
  - `MIT-635`: the skill's and the drafted example's worked examples name no scenario's cause;
    `test_no_example_names_a_scenarios_cause` enforces it.
  - `MIT-643`: this summary, the Learned sections folded into AGENTS.md, DESIGN.md §5.
- 2026-11-07, setup (`MIT-580`, `chore/agent-orchestration`, merged into this feature branch):
  agent roles, Linear and Bash guard hooks, `.env.agent` and scripts for data isolation,
  rulesets on `main` and `feature/**`. Decision record
  `docs/decisions/2026-11-07-MIT-580-agent-orchestration.md`.

## In flight

- The commons-misses feature branch, waiting for its final PR into main.

## Next

- No real session has run on any of this feature's work. First the one-session checks in
  `docs/eval/README.md`: each no-test scenario once, transcript read before it counts
  (decision 0023), and the `--skill` smoke run.
- Then the measured batches in `docs/eval/README.md`, once Question `MIT-598` decides which to
  run: the skill against the shipped text, and `combined` against the shipped text (108
  sessions each, Sonnet and Opus); the four single drafts only if `combined` passes the bar.
- Product state: DESIGN.md §10 Phases 1 and 2 are done; the store is collecting data before
  Phase 3 (after a month of use).
- Open items from the old task list, to become Commons tickets if Zach wants agents on them:
  T55 (hook adapters for Codex CLI and Cursor, decision 0034 step 5) and T57 (the
  2026-10-31 audit's smaller gaps). Their full text, the "Under consideration" notes (a
  shipped SKILL.md, a per-turn Stop nudge, proposing after config work with no tests, an
  external data source; this feature built the eval side of the first and third) and the
  session log are in `git show fe1a155:PROGRESS.md`.

## Open questions

- Question `MIT-598`: the go/no-go on which variants to run (and so how many sessions).
- Question `MIT-640`: the shipped `propose` worked example (whenever `ignore_dst`) is the cause
  of the hold-out scenario `real-whenever-ignore-dst`, so hold-out numbers measured with the
  shipped text are fitted to it. Changing it changes `baseline`.
- Retention: nothing prunes `searches`, and Cloud Logging keeps call lines for its default
  30 days. Decide a policy before the store has many users (decision 0024).
- Telemetry destination for production (decision 0037): Grafana Cloud's free tier, or a
  self-hosted `observability/` stack? Nothing is exported until `otlp_endpoint` is set.
- T43 leftovers: run `setup-backup-keys.sh`, then the first nightly backup and a
  `drill=true` run; the alerts have not been fire-tested.
- DESIGN.md §11 (answered by data later): the non-obvious heuristic's rejection rate; whether
  diff matching detects `used` well enough; when full-text search stops being enough; whether
  anyone uses full_trace.

# PROGRESS.md

A short summary only. The task list is Linear: Zach's "Commons" project (agents read it) and
"Commons Agent Project" (agents write there). The orchestrator's docs coder rewrites this at
the end of each feature branch.

## Last run

- 2026-11-07, setup (MIT-580, `chore/agent-orchestration`): agent roles, Linear and Bash
  guard hooks, `.env.agent` and scripts for data isolation, rulesets on `main` and
  `feature/**`. Decision record `docs/decisions/2026-11-07-MIT-580-agent-orchestration.md`.
- 2026-11-07 to 2026-11-08, commons gaps (`feature/MIT-567-commons-gaps`). Records:
  `docs/decisions/2026-11-07-MIT-567-kill-criterion.md` and
  `docs/decisions/2026-11-07-MIT-567-outcome-signals.md` (both Proposed); implementation
  notes in 0025 and 0029; DESIGN.md §3, §7 and §8; INSTALL.md "Hosting".
  - MIT-600: the Stop hook records each retrieval's cost (`outcomes.tokens_in`, `tokens_out`,
    `wall_clock_seconds`) from the transcript, locally and through the hosted hooks API.
  - MIT-601: eval sessions record tokens; the summaries print the median per passing session.
  - MIT-602: retrievals log the three factors of their score (migration 0019).
  - MIT-603: the kill-criterion and outcome-signals records.
  - MIT-604: `commons admin rescan [--apply]` reruns the risk scanner over stored entries.
  - MIT-610: the dashboard shows each entry's size in search results, tokens per success and
    the median tokens when it worked and failed.
  - MIT-611: the entry page's retrieval funnel and relevance column; "Found, not used" on the
    Searches page.
  - MIT-612: the Overview's proposal funnel and approved-entry use;
    `commons admin relevance-candidates [--days N]`.
  - MIT-636: the rescan covers every entry search can return, retired ones with a fix version
    included.
  - MIT-642: restoring an entry rescans it.
  - MIT-637: docs (this file, DESIGN.md, INSTALL.md, 0025 and 0029, AGENTS.md Learned) and
    the setup branch merged in.

## In flight

- The commons-gaps feature branch is ready for Zach's review as one PR into main. The two
  records stay Proposed until he picks the margin and the option.

## Next

- Product state: DESIGN.md §10 Phases 1 and 2 are done; the store is collecting data before
  Phase 3 (after a month of use). The 20-task run of the kill criterion waits on Zach's
  choices in its record.
- Small gaps the commons-gaps feature left, each a possible agent ticket:
  - `store/entries.py` imports search's private `_SEARCHABLE`; a public name in
    `store/search.py` would be cleaner if more modules need it.
  - The `cli_on_db` fixture is duplicated in `test_safety.py` and `test_cli.py`; it could
    move to `conftest.py`.
  - The token columns are int4, so sums are clamped at 2^31-1. A bigint migration would
    remove the clamp.
  - Retrievals have no `search_id`, so searches and retrievals are matched on four shared
    columns. A migration could link them exactly.
  - Store queries name the seed reporter as the literal `'agent-commons-seed'`
    (`store/insights.py`, `store/entries.py`). `SEED_REPORTER` is in `service/seeds.py`,
    which `store` can't import, so sharing it means a `domain` constant.
- Open items from the old task list, to become Commons tickets if Zach wants agents on them:
  T55 (hook adapters for Codex CLI and Cursor, decision 0034 step 5) and T57 (the
  2026-10-31 audit's smaller gaps). Their full text, the "Under consideration" notes (a
  shipped SKILL.md, a per-turn Stop nudge, proposing after config work with no tests, an
  external data source) and the session log are in `git show fe1a155:PROGRESS.md`.

## Open questions

- MIT-613: the six design calls for the commons-gaps feature.
- MIT-644: reopening entries that become approved with findings no human has reviewed.
- The pseudonym `agent-commons-seed` is not reserved, so a real reporter could take it.
  Several queries leave that reporter's rows out by pseudonym, the new proposal funnel and
  approved-entry figures among them, so a reporter using it would vanish from them.
- Retention: nothing prunes `searches`, and Cloud Logging keeps call lines for its default
  30 days. Decide a policy before the store has many users (decision 0024).
- Telemetry destination for production (decision 0037): Grafana Cloud's free tier, or a
  self-hosted `observability/` stack? Nothing is exported until `otlp_endpoint` is set.
- T43 leftovers: run `setup-backup-keys.sh`, then the first nightly backup and a
  `drill=true` run; the alerts have not been fire-tested.
- DESIGN.md §11 (answered by data later): the non-obvious heuristic's rejection rate; whether
  diff matching detects `used` well enough; when full-text search stops being enough; whether
  anyone uses full_trace.

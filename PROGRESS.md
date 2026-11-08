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
- 2026-11-07 to 2026-11-08, prompt versions (`feature/MIT-566-prompt-versions`). Record:
  `docs/decisions/2026-11-07-MIT-566-prompt-versions.md` (Proposed); DESIGN.md §3
  `prompt_versions` and §5 SessionStart.
  - MIT-606: `search` and `propose` record the server prompt version they were served under
    (`prompt_versions`, migration 0017; `prompt_version_id` on searches, retrievals, entries).
  - MIT-607: the dashboard Prompts page, the Prompt column on entry pages, `commons prompts`.
  - MIT-608: `tool_call` log lines and `tools/call` spans carry `prompt` (12 hex).
  - MIT-609: SessionStart records the hook-text version on the task (`prompt_versions.kind`
    `hooks`, `tasks.hook_prompt_version_id`, migration 0018); hosted clients send only
    `prompt_hash`, and the server labels it from `RELEASED_HOOK_TEXTS` or `unrecognized`.
  - MIT-633: seeds and imports are left out of the prompt-version figures.
  - MIT-634: docs (this file, DESIGN.md, the record, AGENTS.md Learned) and the setup branch
    merged in.


- The prompt-versions feature branch is ready for Zach's review as one PR into main. The
  decision record stays Proposed until he accepts it.
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


- The commons-misses feature branch, waiting for its final PR into main.
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


- The commons-gaps feature branch is ready for Zach's review as one PR into main. The two
  records stay Proposed until he picks the margin and the option.

## Next

- No real session has run on any of this feature's work. First the one-session checks in
  `docs/eval/README.md`: each no-test scenario once, transcript read before it counts
  (decision 0023), and the `--skill` smoke run.
- Then the measured batches in `docs/eval/README.md`, once Question `MIT-598` decides which to
  run: the skill against the shipped text, and `combined` against the shipped text (108
  sessions each, Sonnet and Opus); the four single drafts only if `combined` passes the bar.
- Product state: DESIGN.md §10 Phases 1 and 2 are done; the store is collecting data before
  Phase 3 (after a month of use).
- Zach decides the eight proposed items in the Context7 record (query text in entry mode,
  ingest or point, licensing, randomized routing, correction entries, rating lookups, eval
  feasibility, fallback on a miss). Anything an agent sees changing waits on those, and the
  wording ones need an eval.
  Phase 3 (after a month of use). Prompt versions now let a change in search, propose or
  report rates be tied to the text that caused it.
- Small gaps the prompt-versions feature left, each a possible agent ticket:
  - `tools/demo_data.py` writes no prompt versions, so `/prompts` on the demo database shows
    only the "before versioning" line.
  - On the hosted dashboard the entry page's prompt hash links to `/prompts`, which a review
    key can't open (403). Templates don't know the key's scope.
  - `domain/prompts.py` comments `PromptVersion.hash` as 64 hex characters; an unrecognized
    hook version holds 12.
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
- Test keys' tasks record no hook version, because `tasks` has no `excluded` flag (decision
  0030 covers searches, retrievals and outcomes). If verifiers need their hook versions,
  adding a task-level flag is a schema decision.
  external data source; this feature built the eval side of the first and third) and the
  session log are in `git show fe1a155:PROGRESS.md`.


- Question `MIT-598`: the go/no-go on which variants to run (and so how many sessions).
- Question `MIT-640`: the shipped `propose` worked example (whenever `ignore_dst`) is the cause
  of the hold-out scenario `real-whenever-ignore-dst`, so hold-out numbers measured with the
  shipped text are fitted to it. Changing it changes `baseline`.
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
  `drill=true` run; the alerts have not been fire-tested. Retention (`commons retain`,
  decision 0033) runs at the end of that nightly backup workflow, so it waits on it too.
- DESIGN.md §11 (answered by data later): the non-obvious heuristic's rejection rate; whether
  diff matching detects `used` well enough; when full-text search stops being enough; whether
  anyone uses full_trace.

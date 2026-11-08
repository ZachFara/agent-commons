# PROGRESS.md

A short summary only. The task list is Linear: Zach's "Commons" project (agents read it) and
"Commons Agent Project" (agents write there). The orchestrator's docs coder rewrites this at
the end of each feature branch.

## Last run: 2026-11-07 to 2026-11-08

One integration branch (`final/integration`) carries the setup and the seven features below,
merged in this order. Nothing production agents see has changed except that `/commons:record`
is now offered by default (decision 0034, owner 2026-11-08); the shipped text (`baseline`) is
byte-identical.

### Setup (`MIT-580`, `chore/agent-orchestration`)

Agent roles (`.claude/agents/`), Linear and Bash guard hooks (`tools/agent_hooks/`),
`.env.agent` and `scripts/` for data isolation, rulesets on `main` and `feature/**`. Record:
`docs/decisions/2026-11-07-MIT-580-agent-orchestration.md`. The guard was hardened twice after
the features branched (foreign issue IDs in GitHub text, config and text it can't read); the
integration branch carries the hardened version everywhere.

### Queue badge (`MIT-575`)

Every "awaiting review" count on the dashboard and in `status` uses the review queue's own rule
(`store.reviews.IN_QUEUE`), so the numbers match the queue. Entries that went live
provisionally without review have a "live without review" filter under Entries, linked from
the queue page, and "Your entries" marks them "unreviewed" (`MIT-594`).

Open: on the hosted dashboard, contributors with only an agent key get a 403 on the
`/entries/<id>` links from /mine (decision 0035); the Entries dropdown filter does not default
to newest; `insights.duplicate_flags` keeps a looser "awaiting" definition (harmless); entries
confirmed by outcomes still show "unreviewed".

### Context7 lookups (`MIT-564`)

- The PostToolUse hook records each Context7 `query-docs` call as a pointer in `doc_lookups`
  (migration 0020): library id and version, and the redacted query only in full_trace.
  `commons install-hooks` writes the anchored matcher, and `commons doctor` warns when
  installed hooks miss it (`MIT-622`).
- Lookups follow corrections and retention like searches (migration 0021, an amendment to
  decision 0033). The Searches page shows Commons misses next to lookups (`MIT-623`).
- Hosted `session-start` returns the task's `privacy_mode`; the client keeps it with the
  session binding and sends a lookup's query only in full_trace (`MIT-631`).
- Record `docs/decisions/2026-11-07-MIT-564-context7-fallback.md`, Status: Proposed, with the
  fallback on a miss (`MIT-632`). Nothing an agent sees has changed.

Open: no real Context7 payload has been seen (the hook was built from the tool schema and
Claude Code's documented fields); the first real session should confirm a `doc_lookups` row
with the expected `library_id` and `version`. Zach decides the eight proposed items in the
record; the wording ones need an eval.

### Prompt versions (`MIT-566`)

Record `docs/decisions/2026-11-07-MIT-566-prompt-versions.md` (Proposed); DESIGN.md §3
`prompt_versions` and §5 SessionStart.

- `search` and `propose` record the server prompt version they were served under
  (`prompt_versions`, migration 0017; `prompt_version_id` on searches, retrievals, entries)
  (`MIT-606`).
- The dashboard Prompts page, the Prompt column on entry pages, `commons prompts` (`MIT-607`).
- `tool_call` log lines and `tools/call` spans carry `prompt` (12 hex) (`MIT-608`).
- SessionStart records the hook-text version on the task (`prompt_versions.kind` `hooks`,
  `tasks.hook_prompt_version_id`, migration 0018); hosted clients send only `prompt_hash`, and
  the server labels it from `RELEASED_HOOK_TEXTS` or `unrecognized` (`MIT-609`).
- Seeds and imports are left out of the prompt-version figures (`MIT-633`).
- With `/commons:record` on by default, the shipped server label is
  `baseline:<wording>+record`; the record prompt is among the hashed surfaces.

Open: `tools/demo_data.py` writes no prompt versions, so `/prompts` on the demo database shows
only the "before versioning" line; on the hosted dashboard the entry page's prompt hash links
to `/prompts`, which a review key can't open (403); `domain/prompts.py` comments
`PromptVersion.hash` as 64 hex characters, but an unrecognized hook version holds 12; test
keys' tasks record no hook version, because `tasks` has no `excluded` flag (a schema decision
if verifiers need theirs).

### Commons misses (`MIT-595`)

The behavioral eval can measure misses on configuration work with no failing test and no error
text. `baseline` is byte-identical; `join` and the invite still install no skill.

- Two no-test scenarios, `config-app-overwrites` and `config-default-wins`, plus a known-entry
  variant of each. Scenarios can carry their own task, an outcome check and a local app
  (decision 0023, addition) (`MIT-597`).
- `agent_eval.py run --skill` loads the plugin's skill with `--plugin-dir`, without the
  plugin's duplicate hooks (`MIT-624`).
- Five unshipped wording variants (`search-surprise`, `own-mistake`, `lesson-also-propose`,
  `propose-example`, `combined`) and a refreshed skill (`MIT-625`).
- The skill's and the drafted example's worked examples name no scenario's cause;
  `test_no_example_names_a_scenarios_cause` enforces it (`MIT-635`).
- `/commons:record` is offered unless `COMMONS_RECORD_PROMPT=off` (`MIT-650`; decision 0034
  note).

Open: no real session has run on this work. First the one-session checks in
`docs/eval/README.md` (each no-test scenario once, transcript read before it counts; the
`--skill` smoke run), then the measured batches per Zach's answer on `MIT-598`.

### Commons gaps (`MIT-567`)

Records `docs/decisions/2026-11-07-MIT-567-kill-criterion.md` and
`docs/decisions/2026-11-07-MIT-567-outcome-signals.md` (both Proposed); notes in 0025, 0026
and 0029; DESIGN.md §3, §7 and §8; INSTALL.md "Hosting".

- The Stop hook records each retrieval's cost (`outcomes.tokens_in`, `tokens_out`,
  `wall_clock_seconds`), locally and through the hosted hooks API (`MIT-600`); eval sessions
  record tokens and the summaries print the median per passing session (`MIT-601`).
- Retrievals log the three factors of their score (migration 0019) (`MIT-602`).
- `commons admin rescan [--apply]` reruns the risk scanner over every entry search can return
  (`MIT-604`, `MIT-636`); restoring an entry rescans it (`MIT-642`).
- The dashboard shows each entry's size in search results, tokens per success and the median
  tokens when it worked and failed (`MIT-610`); the entry page's retrieval funnel and
  relevance column and "Found, not used" on Searches (`MIT-611`); the Overview's proposal
  funnel and approved-entry use, `commons admin relevance-candidates` (`MIT-612`).
- Human-approved entries that keep failing go back to the queue at the next `commons rank`
  (decision 0026, amended); the Entries page lists failure rates with how many more failures
  reopen each (`MIT-649`). A reopened entry is unreviewed and so in the queue under `IN_QUEUE`.

Open: the 20-task run of the kill criterion waits on Zach's choices in its record. Small gaps,
each a possible ticket: `store/entries.py` imports search's private `_SEARCHABLE`; the
`cli_on_db` fixture is duplicated in `test_safety.py` and `test_cli.py`; the token columns
are int4, so sums are clamped at 2^31-1; retrievals have no `search_id`, so searches and
retrievals are matched on four shared columns; store queries name the seed reporter as the
literal `'agent-commons-seed'`, and that pseudonym is not reserved.

### Swarm research (`MIT-582`)

- Provenance's "success S/N when used" counts only used outcomes with a known result, as
  ranking does; the `status` tool's top entries and `commons status` use the same count
  (`MIT-587`).
- Review queue: each likely duplicate shows its cause and fix beside the proposal's; an entry's
  page lists the entries a reviewer kept distinct from it (`MIT-586`).
- Searches page: "Full result sets" and "Use by rank" (share used per rank 1, 2, 3, 4+)
  (`MIT-590`).
- Proposed decisions, not built: `2026-11-07-MIT-582-evidence-counts-in-search.md` (counts in
  the provenance line instead of a rate, behind an eval; `MIT-588`) and exploration in search
  ranking (`MIT-591`).

Open: Zach decides whether to run the evidence-counts eval (condition `evidence-counts`,
scenario `known-wrong-entry`). The two Searches figures need a month of data before they say
whether `limit=5` cuts off useful entries or agents only use the top result.

### GitHub tasks (`MIT-565`)

Record `docs/decisions/2026-11-07-MIT-565-github-task-arms.md`; `docs/eval/README.md`.

- GitHub task records, materialization and `check` (`MIT-615`).
- Eval arms (Commons+Context7, Context7, Commons, blind) with tokens, retrievals and the
  agent's patch; a session that didn't get its arm's MCP servers is errored (`MIT-616`).
- The experiment store, counterfactual pairs and the `compare` report (`MIT-617`).
- Patch capture reads nothing outside the project; the ledger keeps a scrubbed excerpt
  (`MIT-645`).
- Merged with commons-misses and commons-gaps: one `Result` (skill, arm, tokens, duration,
  retrievals, patch), one token helper (`session_usage`), `check` handling GitHub tasks,
  no-test tasks and written scenarios.

## Next

- Zach reviews the integration PR; the four Proposed records (Context7 fallback, prompt
  versions, kill criterion, outcome signals) and the two swarm-research records stay Proposed
  until he accepts them.
- The eval runs above, in order: one-session checks, then measured batches.
- Product state: DESIGN.md §10 Phases 1 and 2 are done; the store is collecting data before
  Phase 3 (after a month of use). Prompt versions now let a change in search, propose or
  report rates be tied to the text that caused it.
- Standing items from before the run: telemetry destination for production (decision 0037:
  Grafana Cloud's free tier, or the self-hosted `observability/` stack; nothing is exported
  until `otlp_endpoint` is set); T43 leftovers (`setup-backup-keys.sh`, the first nightly
  backup and a `drill=true` run; the alerts have not been fire-tested; retention runs at the
  end of that workflow); a retention policy for `searches` and call-log lines (decision 0024);
  DESIGN.md §11's questions answered by data later.
- Open items from the old task list, to become Commons tickets if Zach wants agents on them:
  T55 (hook adapters for Codex CLI and Cursor, decision 0034 step 5) and T57 (the 2026-10-31
  audit's smaller gaps). Their full text, the "Under consideration" notes (a shipped
  SKILL.md, a per-turn Stop nudge, proposing after config work with no tests; the external
  data source note is now the Context7 record) and the session log are in
  `git show fe1a155:PROGRESS.md`.

## Open questions

- `MIT-640`: the shipped `propose` worked example (whenever `ignore_dst`) is the cause of the
  hold-out scenario `real-whenever-ignore-dst`, so hold-out numbers measured with the shipped
  text are fitted to it. Changing it changes `baseline`.
- `MIT-644`: reopening entries that become approved with findings no human has reviewed
  (decision 0026's amendment covers failures only).

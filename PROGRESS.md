# PROGRESS.md

A short summary only. The task list is Linear: Zach's "Commons" project (agents read it) and
"Commons Agent Project" (agents write there). The orchestrator's docs coder rewrites this at
the end of each feature branch.

## Last run

- 2026-11-07, setup (MIT-580, `chore/agent-orchestration`): agent roles, Linear and Bash
  guard hooks, `.env.agent` and scripts for data isolation, rulesets on `main` and
  `feature/**`. Decision record `docs/decisions/2026-11-07-MIT-580-agent-orchestration.md`.
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

## In flight

- The prompt-versions feature branch is ready for Zach's review as one PR into main. The
  decision record stays Proposed until he accepts it.

## Next

- Product state: DESIGN.md §10 Phases 1 and 2 are done; the store is collecting data before
  Phase 3 (after a month of use). Prompt versions now let a change in search, propose or
  report rates be tied to the text that caused it.
- Small gaps the prompt-versions feature left, each a possible agent ticket:
  - `tools/demo_data.py` writes no prompt versions, so `/prompts` on the demo database shows
    only the "before versioning" line.
  - On the hosted dashboard the entry page's prompt hash links to `/prompts`, which a review
    key can't open (403). Templates don't know the key's scope.
  - `domain/prompts.py` comments `PromptVersion.hash` as 64 hex characters; an unrecognized
    hook version holds 12.
- Open items from the old task list, to become Commons tickets if Zach wants agents on them:
  T55 (hook adapters for Codex CLI and Cursor, decision 0034 step 5) and T57 (the
  2026-10-31 audit's smaller gaps). Their full text, the "Under consideration" notes (a
  shipped SKILL.md, a per-turn Stop nudge, proposing after config work with no tests, an
  external data source) and the session log are in `git show fe1a155:PROGRESS.md`.

## Open questions

- Test keys' tasks record no hook version, because `tasks` has no `excluded` flag (decision
  0030 covers searches, retrievals and outcomes). If verifiers need their hook versions,
  adding a task-level flag is a schema decision.
- Retention: nothing prunes `searches`, and Cloud Logging keeps call lines for its default
  30 days. Decide a policy before the store has many users (decision 0024).
- Telemetry destination for production (decision 0037): Grafana Cloud's free tier, or a
  self-hosted `observability/` stack? Nothing is exported until `otlp_endpoint` is set.
- T43 leftovers: run `setup-backup-keys.sh`, then the first nightly backup and a
  `drill=true` run; the alerts have not been fire-tested.
- DESIGN.md §11 (answered by data later): the non-obvious heuristic's rejection rate; whether
  diff matching detects `used` well enough; when full-text search stops being enough; whether
  anyone uses full_trace.

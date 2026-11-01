# PROGRESS.md

Living status for agent sessions. Update it at the end of every session: move tasks, record
what you learned, add open questions. Keep it short; history lives in git.

## Status

- DESIGN.md §10 Phase 1 is done (2026-10-24): all seven MCP tools, CLI, admin dashboard,
  seeds, INSTALL.md. Owner installed it locally (2026-10-25).
- DESIGN.md §10 Phase 2 is done (2026-10-25): hooks and observed outcomes, secret scanning,
  ranking and reliability, hosted mode.
- Current phase: collecting data before Phase 3 (DESIGN §10: after a month of use).
- Remote: https://github.com/ZachFara/agent-commons (public since 2026-10-25). Branch protection
  on `main` requires the `check` workflow.

## Task list

One task per session and branch. Take the first unblocked `todo` task. Mark it `doing` on your
branch when you start and `done` in the same PR that completes it.
Every task's done criteria implicitly include: `make check` passes, new behavior has tests,
PROGRESS.md updated.

### DESIGN Phase 1: core loop

| ID | Status | Task | Done when |
|---|---|---|---|
| T01 | done (partial) | GitHub remote `agent-commons`, branch protection on `main` requiring the `check` workflow | Remote and CI done. Protection needs a paid plan; owner is fine without it |
| T02 | done | `db/compose.yml`: Postgres 16 + pgvector, healthcheck, local volume; `.env` defaults documented | `docker compose -f db/compose.yml up -d` reports healthy; `CREATE EXTENSION vector` succeeds |
| T03 | done | CI: Postgres service container + DB test fixture (per-test transaction or throwaway schema) | A test that runs `SELECT 1` through the fixture passes locally and in CI |
| T04 | done | Migration runner in `store` + `commons migrate` CLI command + `commons` script entry | Applies numbered `.sql` files once, records them in `schema_migrations`; re-running is a no-op (tested) |
| T05 | done | Check `cq-schema` field names and record the mapping in a decision record (prerequisite for T06) | `docs/decisions/` has the cq field mapping; any DESIGN.md field that collides with a cq name is listed as a proposed rename |
| T06 | done | Migration 0001: all tables and enums from DESIGN.md §3 (including `reviews` and `entries.human_reviewed`, decision 0005), generated `search_vector` + GIN index | Test introspects every table, column, and enum after `migrate` |
| T07 | done | `commons init`: pseudonym (refuse email-like), privacy default; create or reuse the reporter row; write the local identity config the MCP server reads (record where in a decision) | CLI tests via `main`: init creates a reporter and config; re-running with the same pseudonym reuses it; email-like pseudonym exits non-zero with a clear message |
| T08 | done | MCP server + `propose`: FastMCP stdio, `commons-mcp` script, structured errors `{"error": {"code", "message"}}`, reporter from local config, model from optional `model` arg else `unknown` (decision 0009). Domain types and validation (environment needs versions, domains 1-16) come in with it. Redaction report empty until T21 | In-process FastMCP client test: propose returns entry id, status pending, redaction report; invalid input returns a structured error; row readable in DB |
| T09 | done | `search` tool: full-text over approved entries, rank by recency (no snapshots yet), one retrievals row per result with rank and score, results under `data` with the untrusted note, provenance string incl. "unreviewed" | Tests: relevant entry first; pending/rejected never returned; retrievals rows written; payload shape |
| T10 | done | `report` tool: self-report path, agent_assessment stored, outcome `unknown` unless given, merged into the retrieval's single outcome row | Tests: report after search creates an outcome; second report updates it; report for an entry not retrieved in that task is a structured error |
| T11 | done | Reviews (decision 0005): `approve`/`reject`/`pending` tools (source agent) and `commons review <id> approve\|reject [--reason]` (source human); status follows latest human review, else latest agent review | Tests: agent review sets status but not human_reviewed; human overrides agent; multiple reviewers kept; pending lists pending or unreviewed |
| T12 | done | `status` tool and `commons status`: counts by status, 30-day retrievals and outcomes, top entries by success rate | Tests with seeded rows return expected counts; CLI prints them |

T13-T15 were folded into the slices above (2026-10-24): the dead-code check rejects types and
service functions that no entry point calls yet, so each task now starts at an MCP tool or CLI
command and goes down through the layers.

| T16 | done | `client/CLAUDE.snippet.md`: search-first, propose-on-surprise, post-hoc report, untrusted-data, full_trace downgrade rules | Snippet covers each rule in DESIGN.md §5-§6; under 60 lines |
| T17 | done | `INSTALL.md` (agent-facing, DESIGN.md §9 steps 1-7) and `README.md` | A fresh Claude Code session following INSTALL.md alone reaches a working `search` (owner verifies) |
| T18 | done | Ship a few seed entries (well-known public problems) loaded by `commons init`, so INSTALL step 7 has a known entry to find. Real entries come from use (owner, 2026-10-24) | `search` for a seed symptom returns it on a fresh install (tested) |
| T19 | done | Admin dashboard, read-only: pending queue, recent proposals with redaction reports, recent retrievals and outcomes. Adds a `web` entrypoint layer (decision record + import contract) | Served locally by the server; page test renders seeded rows; no write routes yet |
| T20 | done | Admin dashboard: file human reviews (approve/reject with reason) | Review from the dashboard writes a human reviews row; tested |

### DESIGN Phase 2: observed outcomes, ranking, hosted

Decisions 0016 (hooks), 0017 (ranking), 0018 (hosted), all owner-approved 2026-10-25.

| ID | Status | Task | Done when |
|---|---|---|---|
| T21 | done | Secret scanning and redaction on every write | Tested; report stored and shown |
| T22 | done | Hook plumbing: migration for `tasks` + `task_events`; `commons hook <event>` reading stdin, logging failures and exiting 0; SessionStart creates/reuses the task and prints the task id; server instructions use it; `client/hooks.json`; INSTALL step 4 merges it | Tests feed recorded payloads to `main(["hook", ...])`; start/resume reuse; malformed input exits 0 and logs |
| T23 | done | Observation hooks: Bash `PostToolUse`/`PostToolUseFailure` test runs (exit code parsed from `error`) into `task_events`; Edit/Write/Bash into the local spool; file-change hashes | Tests for runner matching (built-in + config regex), exit codes, spool contents, no code in the store |
| T24 | done | Stop hook: observed model from the transcript; `used` per retrieved entry; observed outcome from the latest test run after the retrieval; block once asking for `report` | Tests: used yes/partial/no/unknown; pass/fail after retrieval; earlier test runs ignored; stop_hook_active respected; agent outcome never overrides hook |
| T25 | done | `commons rank`: v0 score into ranking_snapshots; runs from SessionStart when stale | Tests of the formula (prior, weights, partial, decay), snapshot fields, search uses the new score |
| T26 | done | Reporter reliability in `commons rank` | Tests: matches/pairs, NULL under 5 pairs, used as self-report weight |
| T27a | done (with T27b) | Hosted identity: `reporters.is_admin`, `commons admin create-key`, key hashing, reporter from bearer key | Tests: key shown once, hash stored, lookup, admin flag |
| T27b | done | `commons-mcp --http` with bearer auth; `local` reporters refused (0014) | In-process HTTP tests: no key 401, bad key 401, good key acts as that reporter; local refused |
| T27c | done | Hosted hook endpoints; `commons hook` posts to the server when the config has a server URL and key | Tests against the app with a test client; spool stays local |
| T27d | done | Hosted dashboard behind admin key (HTTPS); `compose.hosted.yml` with Caddy; INSTALL hosted section | Tests: non-admin 403; compose config validates (`docker compose config`) |

### Usability and hosting (owner request, 2026-10-25)

| ID | Status | Task | Done when |
|---|---|---|---|
| T28 | done | Dashboard redesign: navigation; overview with charts (activity over time, outcome mix, self-report accuracy); review queue; entries list with search/filter and an entry page (content, provenance, score history, retrievals, outcomes, reviews); tasks; reporters and models. Checked in a real browser | Page tests for every route; screenshots reviewed in light and dark |
| T29 | done | CLI utilities: `commons doctor` (config, DB, migrations, hooks, server), `commons search` (preview, no retrieval logged), `commons show <id>`, `commons-dashboard --open` | CLI tests per command |
| T30 | done | One hosted process: `/mcp`, hook API, dashboard in a single ASGI app with stateless HTTP (Cloud Run gives one URL per service) | In-process tests over the combined app; compose updated |
| T31 | done | Terraform in `infra/` for Neon + Cloud Run + Artifact Registry + Secret Manager (decision record), GitHub Actions deploy, cost estimate | `terraform validate` passes; docs give the exact apply steps |
| T32 | done | Friend onboarding: `uv tool install` from GitHub, hooks call the installed `commons` directly, `commons join`, `commons admin invite` prints a paste-ready message | Tests for join/invite; a fresh tool install joins a local hosted server |
| T33 | done | Staging and production (decision 0022): import the Neon project, staging branch, per-environment Cloud Run services and secrets, build → staging → approved production pipeline; apply with the owner | Both URLs answer `/healthz`; a friend can join production; staging redeploys on merge |

### Behavioral eval (owner request, 2026-10-25)

| ID | Status | Task | Done when |
|---|---|---|---|
| T34 | done | Pre-registered behavioral eval (decision 0023): isolated parallel `claude -p` sessions, 13 verified scenarios, evidence-based grading, baseline of 5 runs each | Baseline report committed with per-scenario search/propose/report rates and proposal quality |
| T35 | done | First: hold-out scenarios from real post-cutoff breaking changes (owner, 2026-10-25), verified by transcript. Then experiments on what the agent sees, one named condition at a time, developed on `docs-retry-flag` + `version-silent-rename`, measured on the hold-out set: (a) argument descriptions with examples so `propose`/`search` succeed first time; (b) Stop-hook nudge to propose when the agent searched, found nothing, then fixed failing tests; (c) instruction wording | Each condition has a report with the same run count as the baseline; changes that measurably help are adopted, the rest are recorded |
| T36 | done | Eval report with charts (funnel per scenario and condition, rates with intervals, proposal quality) published as a shareable page; raw results committed under `docs/eval/` | Page link in PROGRESS; raw JSON in the repo |

### Observability (owner request, 2026-10-31)

| ID | Status | Task | Done when |
|---|---|---|---|
| T37 | done | Log every search, misses included, by privacy mode; one structured log line per MCP tool call; dashboard Searches page (decision 0024) | Tests: misses logged, query redacted, full_trace extras only in full_trace (DB check too), task mode wins, preview and invalid calls log nothing, call lines carry no query text; page renders |

### Follow-ups (owner, 2026-10-31)

| ID | Status | Task | Done when |
|---|---|---|---|
| T38 | done | `propose` and `search` say which store they used: `"store": "local"` or the hosted server URL. Why: after the owner switched to production, a session that was already open kept its local MCP server while its hooks went to production. It proposed two entries to the local store and reported them as "pending review", while the owner looked at the production queue (2026-10-31). | Tests: local and hosted responses carry the right store. The tool descriptions were left alone: they are the eval-measured wording |
| T39 | done | Investigate: a retrieval made only to check the store counted as a use. In production, entry `1f0aa91a` shows 1 used outcome and a 0% success rate after a session searched only to see whether the store was empty. That session had applied the same fix earlier, so the Stop hook's diff matching probably marked it `used`. Decide whether `used` should only count changes made after the retrieval (2026-10-31) | Cause confirmed with a test that reproduces it; fixed or recorded as accepted |
| T40 | moved to T49 | Tunable duplicate resolution (now decision 0027, task T49). Today the only guard is the instruction "search before proposing; report on an existing entry instead", so near-identical proposals reach the review queue (owner, 2026-10-31). On `propose`, find similar entries (same full-text match `search` uses, later pgvector) and resolve by a setting: flag the likely duplicate in the queue and dashboard, or link it to the existing entry, or return the existing entry to the agent so it can `report` instead. The similarity threshold and the action are configurable, and the eval measures false merges on distinct entries | Tests: a near-duplicate is detected at the default threshold and not below it; each action behaves as configured; distinct entries with shared words are left alone |
| T41 | moved to T46 | `used` matching counts generic words and talk about an entry. On 2026-10-31 a deploy-check search returned the Keycloak entry, and Stop marked it `partial` + `tests_passed` from `re-run`, `--server` (from `commons join --server`) and `sslrequired=external` (typed into PROGRESS.md while writing up that session), plus a passing `make check`. Corrected by hand in production. Fix: stop ordinary hyphenated words and common flags from counting as distinctive; weigh commands run and code edits above prose (Markdown, docs); consider requiring a test run or command after the match | Tests from the 2026-10-31 case: the Keycloak fix against that session's text is `no`; true uses in the existing tests still match |

### Long-term gaps (decisions 0025-0036, owner-accepted 2026-10-31)

Listed in the suggested order: urgent and protective first, then correctness, then scale.
Dependencies are noted.

| ID | Status | Task | Done when |
|---|---|---|---|
| T42 | done | Rotate the owner's printed admin key with today's commands (0032 step 5) | Old key gets 401; owner's MCP and dashboard work on the new one. Done 2026-10-31: old key 401 on `/` and `/mcp`, new key 200; config and MCP registration updated |
| T43 | done (partial) | Operations: nightly encrypted `pg_dump` to GCS, monthly restore drill, uptime and error alerts, budget alerts in Terraform, retention job (0033) | A drill restores and matches row counts; a forced 5xx and a stopped service each email the owner. 2026-10-31: applied (bucket, ops account, uptime check, two alert policies, budget at 50%/90% of $4); a local dump, restore and exact count compare of production passed. Left: the owner runs `setup-backup-keys.sh` (age keys, GitHub variables), then the first nightly run and a `drill=true` run; the alerts have not been fire-tested, since that means taking production down or forcing errors in it |
| T44 | done | Keys: `api_keys` table, scopes (agent/review/admin), several keys per reporter, rotate/revoke/expire, `last_used_at` (0032) | Tests per scope and lifecycle step; `join` refuses admin keys; staging soak before production |
| T45 | done | Data correction: `corrections` table, `excluded` flags, dashboard and CLI actions, immediate rescoring, test keys (0030; test keys need T44) | Excluded rows vanish from ranking, status, charts and export (tested per reader); 2026-10-31 fixes backfilled |
| T46 | done | `used` detection v1: rarity weights, generic-token list, prose files at zero, test runs paired with matched work (0036; supersedes T41's sketch) | Labeled set from the eval plus the 2026-10-31 cases: precision up, recall on true uses held |
| T47 | done (steps 1, 2, 4) | Search relevance: judged set and `make search-eval`; IDF and coverage scoring with a cutoff; then pgvector hybrid with RRF; then version awareness (0025) | Each step ships only if precision@5 and recall@5 don't drop; numbers recorded in the PR. 2026-10-31: precision@5 0.243 to 0.899, recall@5 0.983 held; hybrid (step 3) not needed on these numbers (owner: decide on numbers) |
| T48 | done (agent `revises` waits for an eval) | Entry lifecycle: `entry_revisions`, admin edits, agent `revises` (after an eval), `superseded_by`, retire with `fixed_in`, stale-candidates list (0026) | Tests for each transition; retrievals record the revision shown; search hides superseded and retired |
| T49 | done (`return` waits for an eval) | Duplicate resolution: candidates on propose, threshold and action settings (default `flag`), merge and keep-both, same-task retries (0027; needs T48, replaces T40) | Labeled pairs: false merges at or below 1 in 20 at the default threshold; each action tested |
| T50 | done | Entry safety: risk scanner and report, Unicode stripping, provenance labels, per-key rate limits, `revoke --purge` and `--restore` (0029; needs T44) | A test per pattern, including false-positive cases; limits hold across two app instances; purge is reversible |
| T51 | done | Review at scale: roles, trust levels with sampled review, outcome confirmation, queue-age alert (0028; needs T44, T50, T43) | Tests per trust level and demotion; risky findings always force the queue |
| T52 | done | Switching stores: store bound at session start, stale local server refuses, `join` and `doctor` warn, `commons import --from-local` (0031; import needs T49) | Tests reproduce the 2026-10-31 split and show it can't happen; import dry run and real run |
| T53 | done (session line off until an eval; no digest) | Contributor feedback: "Your entries" page and `commons mine`; SessionStart line and digest only after measurement (0035; needs T44 scopes) | Scoping test: one reporter can't see another's entries |
| T54 | done (wider trigger adopted; nudge, record and skill off) | Reliable propose and report: multi-turn eval scenarios, then measure the per-turn nudge, `/commons:record`, the SKILL.md plugin, a wider trigger; survey other clients' hooks (0034) | Each change adopted only on evidence on Sonnet and Opus, as in decision 0023 |
| T55 | todo | Hook adapters for Codex CLI and Cursor (decision 0034 step 5): parse their hook payloads into the same derived observations | Tests per client payload; a session on each records test runs and turn ends |
| T56 | in progress | Fix what the 2026-10-31 audit of decisions 0025-0036 found: agent keys reviewing, import skipping rate limits, purge/restore gaps, demotion on merge, used/outcome pairing, silent backup skips, scanner misses | A failing test first for each bug |

### DESIGN Phase 3 (after a month of data)

- cq adapter, counterfactual runner, decay/absorption flags, model attribution, `commons export`,
  `commons report`, `.claude-plugin/` packaging, first dataset release.

## Open questions

Raise new ones here. Do not resolve a question by guessing in code.

- Retention: nothing prunes `searches`, and Cloud Logging keeps call lines for its default 30
  days. Fine at current volume; decide a policy before the store has many users (decision 0024).

Resolved 2026-10-24 by owner: review model (decision 0005), hook location (decision 0006),
seed entries come from real use rather than a manual batch (T18), admin dashboard added (T19-T20),
cq field mapping (decision 0008: cq names for domains/created_by/reviewed_by, keep symptom/cause/fix).
Resolved 2026-10-25 by owner: branch protection stays off (merge only after `check` passes, local
hook blocks main); unreviewed entries are visible to everyone in hosted mode; `local` means
"write only to the local store" (decision 0014); no license for now; seed fixes confirmed.

DESIGN.md §11 open questions (research, answered by data later): the non-obvious heuristic's
rejection rate; whether diff matching detects `used` well enough; when full-text search stops
being enough; whether anyone uses full_trace.

## Next steps

1. T34 baseline, then T35 experiments; invite the friend once propose works reliably.
2. Next: DESIGN Phase 3 after a month of data (DESIGN §10).

### Under consideration: ship a SKILL.md with the server (owner, 2026-10-31)

Not started. Idea: package a Claude Code skill with the MCP server and hooks, probably as the
`.claude-plugin/` already planned for Phase 3. Why it might help:
- The skill's name and description would be one more thing the agent sees before tool search
  loads our tools, alongside the server instructions and tool names. That might raise how often
  agents search and propose.
- The skill body only loads when used, so it could hold more than the 1,500-character
  instructions budget allows: worked examples of good and bad proposals, and when to report.
- A plugin install could replace the three commands in the friend invite.

Anticipated next steps:
1. Check the current Claude Code docs before designing anything. How are skill descriptions
   loaded, and what are their length limits and listing budget? Can one plugin bundle the MCP
   server, the hooks and the skill? Which other clients (Codex, Cursor) read skills?
2. Draft the SKILL.md: a short description written to trigger on library and runtime failures,
   and a body with the search, propose and report rules plus examples. Count its characters
   against the documented limits, as `test_mcp_instructions.py` does for the instructions.
3. Teach the eval harness to install the skill (or plugin) into each isolated session. Then run
   it as a condition against the shipped wording on Sonnet and Opus with `make eval-confirm`'s
   13 scenarios. Watch the controls for false proposals, and the extra tokens per session.
4. Adopt it only if it beats the shipped wording on both models without new control
   proposals. Then build the plugin and update INSTALL.md and `commons admin invite`.
5. If the skill carries the detail, decide whether the server instructions can shrink. Clients
   that don't read skills would still depend on the instructions alone.

### Under consideration: a Stop hook that asks about proposing, every turn (owner, 2026-10-31)

Not started. Evidence comes from one real session (Fable 5.1, a self-hosted Cosmo setup):
- The agent proposed the first library error unprompted.
- It missed two later surprises until the owner asked by hand. Cosmo Router returned 401s
  because the metrics collector defaults to Cosmo Cloud. Keycloak showed "HTTPS required"
  because of a seeded `sslRequired=external` behind Docker port forwarding.
- Asked why, the agent gave three reasons. "Before finishing" never comes in a multi-turn
  session. Neither miss had error text to search. And both were config-default surprises
  rather than exceptions.
- In the same session, the existing Stop hook's `report` reminder fired and worked.

What exists: the `nudge` variant in `agent_text.py` blocks Stop once when a session fixed
failing tests without searching. It has never been measured validly: it never fired in the
eval because of the piped exit codes fixed in PR #52. It is not shipped.

The idea:
- Block Stop at most once per turn when that turn shows a failure that was later fixed
  (failing tests then passing, or a failing command then a working one) and nothing was
  searched or proposed. Show one line asking whether the fix is commons-worthy. The agent may
  decline, because the owner rejected forced calls (the forced SessionStart search).
- Add a manual version: a user-run command (an MCP prompt, e.g. `/commons:record`) that asks
  the agent to review the session and propose what qualifies. That is what the owner did by
  hand here.

Anticipated next steps:
1. Re-run the `nudge` eval now that hooks see piped exit codes.
2. Widen it from test runs to failing commands, and from once per session to once per turn.
3. Add multi-turn scenarios to the eval: today every scenario is one `claude -p` prompt, so a
   per-turn nudge can't show its effect. Include a config-default surprise with no error text.
4. Measure the manual command separately. It needs no trigger, only a clear checklist.
5. Related wording candidate: make the trigger cover surprising defaults and error pages, not
   only error text.
6. Not pursued: an automatic search on every failing command. It is the same kind of forced
   call the owner rejected.

### Long-term gaps (owner, 2026-10-31)

Each gap now has a proposed decision and a task row (T42-T54 above):
search relevance 0025 (T47); entry lifecycle 0026 (T48); duplicates 0027 (T49, was T40);
review bottleneck 0028 (T51); entry safety 0029 (T50); data correction 0030 (T45);
switching stores 0031 (T52); keys and identity 0032 (T42, T44); operations 0033 (T43);
reliable propose and report 0034 (T54, covering the two considerations above); contributor
feedback 0035 (T53); `used` detection 0036 (T46, was T41).

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
- 2026-10-24: T05 read cq-schema 0.3.1; mapping and proposed renames in decision 0008.
- 2026-10-24: T06 migration 0001 (all tables, enums, generated weighted `search_vector`, GIN
  index, updated_at triggers). Test DB is migrated once per session in conftest. Notes for later
  tasks: `outcomes.used` NULL means unknown; one outcome row per retrieval (unique); pgvector
  extension deliberately not created until vectors are needed (DESIGN §2).
- 2026-10-24: reordered T07-T15 into end-to-end slices (dead-code check); decision 0009 on
  model identification.
- 2026-10-24: T07 `commons init` (decision 0010). Conventions added to AGENTS.md: Literal
  types for DB enums, `store.connection.Connection` above store, `InvalidInputError`.
- 2026-10-24: T08 MCP server + `propose`, verified over real stdio with `commons-mcp`.
  Owner-approved: `entries.task_id` (migration 0002), mcp/ exempt from max-args (decision 0011).
  Environment rule: language/tool must contain a version, every library pinned, at least one
  of the three present. Middleware overrides `__call__` (not `on_call_tool`) so vulture sees it.
- 2026-10-24: T09 `search` (decision 0012, owner-approved: required task_id, OR-matching,
  relevance x score, language filter). Provenance stats are live SQL until snapshots (T25).
  Test helpers: `tests/factories.py` inserts entries directly.
- 2026-10-24: T10 `report`. Outcome attaches to the earliest retrieval of the entry in the task;
  `evidence.outcome_source` is `agent` or `hook`, agent evidence under `evidence.agent`. Hooks
  (T22-T24) must set `outcome_source: "hook"` and put payloads under `evidence.observed`.
  `report` takes no `model` (0009 amended).
- 2026-10-24: T11 reviews: `approve`/`reject`/`pending` tools (agent source), `commons review`
  and `commons pending` (human). Tools are now registered via `FastMCP(tools=[...])` because
  vulture can't see `@server.tool`. Made two tests order-independent (rule added to AGENTS.md).
- 2026-10-24: T12 `status` tool and `commons status`. Per-entry outcome stats SQL is shared
  (`store/outcome_stats.py`) by search and status. Decay flags omitted until Phase 3. All
  seven DESIGN.md §4 tools now exist.
- 2026-10-24: T16 `client/CLAUDE.snippet.md` (41 lines). Until hooks exist the agent mints its
  own task_id with `uuidgen`, and is told to keep secrets out since scanning (T21) isn't in yet;
  update both lines when T21/T22 land. A test keeps the snippet's tool/argument names real.
- 2026-10-24: T17 INSTALL.md and README.md. `claude mcp add` uses `--scope user` (DESIGN §9's
  command would register the server for one project only). Steps 4 (hooks) and hosted mode are
  marked not available yet. Owner verification pending.
- 2026-10-24: T18 three seed entries (`service/seeds.json`, fixed ids `5eed0000-...`) loaded
  idempotently by `commons init` as approved + human-reviewed by the `agent-commons-seed`
  reporter. INSTALL step 7 now searches for one. Search/report tests scope results to their own
  entries since seeds are committed to the shared test DB.
- 2026-10-24: T19 read-only admin dashboard (decision 0013): `commons-dashboard`, localhost only,
  counts, review queue, recent proposals, recent retrievals with outcomes. Redaction reports
  aren't persisted yet, so they're not shown; T21 should store them on the entry.
- 2026-10-24: T20 review from the dashboard (human source, reviewer = local identity). POSTs
  require Origin to equal the dashboard's own localhost origin and Host to be local (CSRF and
  DNS rebinding). DESIGN Phase 1 tasks are done except owner verification of T17.
- 2026-10-25: owner installed via INSTALL.md (T17 verified; agent skipped the CLAUDE.md step,
  so the snippet moves into the MCP server's instructions, decision 0015). Resolved open
  questions; decision 0014 on `local`.
- 2026-10-25: agent instructions moved from `client/CLAUDE.snippet.md` into the MCP server
  (decision 0015): cross-cutting rules in server instructions, per-tool guidance in tool
  descriptions, both under Claude Code's ~2,048-char silent truncation (tested).
- 2026-10-25: T21 secret scanning (`privacy/secrets.py`): keys/tokens for common providers, JWTs,
  bearer tokens, `*_KEY=`/`password:` assignments, credentialed URLs, private key blocks, emails,
  home-dir usernames. Runs on proposals (report stored in `entries.redaction_report`,
  migration 0003, shown in the dashboard queue), search queries as logged, reports, review
  reasons.
- 2026-10-25: Phase 2 split into T22-T27d after checking hook payloads empirically;
  decisions 0016-0018.
- 2026-10-25: T22 hook plumbing: `tasks` (migration 0004), `commons hook session-start` injects
  the task id (verified with real `claude -p`: the model saw it), `commons install-hooks` merges
  `client/hooks.json` into settings idempotently with a backup. Hooks run synchronously (~0.12s
  `uv run` start-up) so events are recorded before Stop reads them. Hook log:
  `$XDG_STATE_HOME/agent-commons/hooks.log`.
- 2026-10-25: T23 observation hooks: `task_events` (migration 0005) for test runs (scrubbed
  command, exit code from `PostToolUse`=0 / `PostToolUseFailure` "Exit code N") and file-change
  hashes; scrubbed edit text and commands in the 0600 local spool. Runner matching ignores
  installs and non-command mentions (`cat pytest.ini`). Verified with real `claude -p`.
  TODO in T24: prune old spools.
- 2026-10-25: T24 Stop hook: observed model from the transcript (replaces self-reported model on
  the task's retrievals/entries), `used` per retrieved entry from the spool, observed outcome from
  the latest test run after the retrieval, one block asking for missing reports. `report` now
  keeps `evidence.agent_outcome`. Spools older than 30 days are pruned at SessionStart. pytest
  now fails on warnings. Real `claude -p` run: Stop prompted, the agent reported both entries,
  model observed; a test that ran in parallel with (before) the search correctly didn't count.
- 2026-10-25: T25 ranking: `ranking/score.py` (pure, decision 0017 formula), `commons rank`
  writes snapshots for approved entries; SessionStart re-ranks inline when snapshots are >24h
  old (no background process: fast at local scale, and avoids a subprocess). Search already
  multiplies relevance by the latest snapshot score.
- 2026-10-25: T26 reliability computed first in `commons rank` from (agent_outcome, observed)
  pairs: agreement on success vs not (0017 amended), NULL below 5 pairs, then used as the
  self-report weight.
- 2026-10-25: T27a+b together (the key lookup is only reachable from the HTTP server, so the
  dead-code check needs both): `reporters.is_admin` (migration 0006), `commons admin create-key`
  (SHA-256 stored, shown once, rotation), `commons-mcp --http` with FastMCP `TokenVerifier`;
  hosted identity only from the key (`serving_hosted`), `local`-privacy reporters get
  `forbidden`. vulture now ignores `@override` methods (owner-approved, 0003 amended).
- 2026-10-25: T27c part 1 (refactor): hook logic split into `service/hook_client.py` (runs on the
  user's machine: spool, runner matching, transcript model, `used`) and `service/hooks.py` (store
  side), joined by a `HookBackend` protocol with a `LocalBackend`. Spool keyed by a hash of the
  session id (`$XDG_STATE_HOME/agent-commons/sessions/`). Tasks are scoped to their reporter.
- 2026-10-25: T27c part 2: hosted hook API in the web layer (`commons-dashboard --hosted`,
  `/api/hooks/*`, bearer key, strict derived-only shapes), `RemoteBackend`, `commons connect`
  (verifies the key, saves server+key in a 0600 config). Found and fixed: an existing config
  file kept 0644 after a key was written. Verified across real processes.
- 2026-10-25: T27d hosted deployment: admin-only hosted dashboard (Basic auth with an admin
  key, same-origin reviews behind the proxy), `server/Dockerfile`, `db/compose.hosted.yml` +
  `db/Caddyfile`, INSTALL "Joining a hosted server" and "Hosting". Ran the whole stack locally
  with DOMAIN=localhost: HTTPS, 401s without keys, dashboard with an admin key, MCP over HTTPS,
  CSRF check through the proxy. Found and fixed: parallel image builds collided on one tag.
  Phase 2 complete.
- 2026-10-25: repo made public (history scanned: only test fixtures; owner OK with the one
  commit showing their email); branch protection on. Planned T28-T32 (owner: Neon + Cloud Run).
- 2026-10-25: T28 dashboard redesign: sidebar navigation; Overview (KPIs, daily retrievals,
  outcomes per day, outcome mix observed vs self-reported, top entries); Review queue; Entries
  (search, status filter, ordering) with entry pages (score history, reviews, retrievals);
  Activity (sessions with tests); People & models (reliability, model attribution). Server-
  rendered SVG charts, reference dataviz palette, light/dark. Checked in Chrome on demo data
  (`tools/demo_data.py` builds a separate `commons_demo` database).
- 2026-10-25: T29 CLI utilities: `commons doctor` (config, DB, migrations, identity or hosted
  server, hooks, MCP registration, recent hook errors), `commons search` (preview, no retrieval
  logged), `commons show`, `commons-dashboard --open`. `connect()` now raises
  `StoreUnavailableError` with a readable message. Doctor is all green on the owner's machine.
- 2026-10-25: T30 `commons-server` (decision 0019): MCP (stateless HTTP), hook API, dashboard,
  /healthz in one app; `--migrate` on start; image CMD; compose is Postgres + server + Caddy.
  Ran the stack locally over HTTPS: all routes behave.
- 2026-10-25: T31 Terraform in `infra/` (decision 0020): Neon project, Cloud Run service (scale to
  zero, max 2 instances, secret-backed DB URL, public with app-level auth), Artifact Registry
  (keeps 3 images to stay in the free 0.5 GB), Workload Identity Federation for GitHub Actions
  (`deploy.yml`, main only), optional $5 budget alert. Validated with the official Terraform
  image against google v6.50.0 and neon v0.18.0; CI job `infra`. Not applied: needs the owner's
  accounts. infra/README.md has the steps and cost table.
- 2026-10-25: T32 onboarding (decision 0021): hooks generated in code and pointing at the
  installed `commons` (no clone, no `uv run` per hook), `commons join`, `commons admin invite`.
  Verified as a friend would: invite, isolated `uv tool install`, join, a hook through the
  installed binary, `claude mcp add` in a scratch Claude config (Claude reported Connected),
  doctor all green.
- 2026-10-25: T33 applied (owner-approved): Neon project imported (PG 18), staging branch, per-env
  roles/databases/secrets, Cloud Run production https://agent-commons-jzxq74etsa-uc.a.run.app and
  staging https://agent-commons-staging-jzxq74etsa-uc.a.run.app, GitHub variables and environments
  (production needs the owner's approval). First apply hit a transient Cloud Run "internal error"
  on a fresh project; a retry succeeded. Fixed perpetual drift on Cloud Run's service-level scaling.
- 2026-10-25: T33 done; production and staging live, owner's machine on production as `zach`.
  Eval harness built (T34): 13 scenarios verified by transcript; two real, well-formed
  proposals observed; `used` bug fixed from an eval finding (PR #44); grader fixed twice
  (task-id check, compact JSON). Owner's standing request: experiment with everything the
  agent sees (T35) if the baseline is low, without spamming evals.
- 2026-10-25: T34 baseline (65 sessions): search 4/45 on discoveries, propose 2/45 (both good),
  0/20 false proposals. Raw results in docs/eval/baseline. Owner: scenarios share training data
  with the eval model, so build hold-outs from real post-cutoff breaking changes.
- 2026-10-25: hard-set baseline (20 sessions): search 4/20, propose 0/20, fixed 20/20; two
  searches returned nothing and still no proposal. Structural screen built: variants `rule`
  (propose keyed on cause, not attempts), `start` (SessionStart asks for a search), `invite`
  (empty search result points at propose), `nudge` (Stop blocks once when tests were fixed
  without a search). Agent-facing text moved to `service/agent_text.py` (both entrypoints
  consume it; the boundary check caught `cli` importing `mcp`). Mandatory-search-rule condition
  cut (owner). Owner's confidence-based rule noted for the wording phase.
- 2026-10-25: structural screen finished; `nudge` never fired. Cause: 210 of 210 eval sessions
  ran tests as `pytest -q 2>&1 | tail -n`, so the shell's exit code is tail's 0 and hooks
  recorded failing runs as passes (also wrong for observed outcomes in production). Fixed: the
  runner's summary line decides a masked 0; with no summary the run is unknown, never a pass.
- 2026-10-25: screen results committed (docs/eval/README.md): `start` search 20/20 (CI 84-100%)
  vs baseline 4/20; propose stayed 0-1/20 everywhere. Empty search was read as "nothing to
  do". `rule` back-filled into the ledger; `nudge` trials marked invalid (PR #52). Next round:
  combinations on top of `start` (invite, rule, worked argument shapes).
- 2026-10-25: round two (60 sessions): `start-rule` propose 7/20, `start-all` 9/20, all graded
  good; `start-invite` 1/20. The propose rule's wording was the second gate. SQLAlchemy 0/15
  (agent doesn't know 2.1 changed autoflush). 14/30 propose calls rejected, mostly unversioned
  `tool` (example omits it). Grader fix for runtime pins (PR #55). Next: controls under
  `start-all` (false proposals must stay 0), then the wording search.
- 2026-10-25: `start-all` safety check (30): false proposals 1/20 on controls, planted
  instructions read 5/5 and followed 0/5, known-entry loop 5/5. Owner: a forced search on every
  session (15/20 controls searched) is not something people would install; search and propose
  must come from the text alone, propose weighted highest. Built the wording search (PR #57):
  nine surfaces incl. tool names, argument descriptions, and the owner's hindsight rule;
  balanced random configs repeated per exact text.
- 2026-10-25: wording search done (517 sessions). Config B (`w-a0b2c2d1e0f1g1h0n0`) confirmed
  on unseen scenarios: good proposals 19/21 discoveries, 9/12 real post-cutoff changes (today's
  text 3/21, 0/12); search 17/21 discoveries, 0/12 controls; 0 false proposals; injection
  ignored. Owner's hindsight rule is the biggest single lever. Found and fixed: a broken control
  (`control-documented-gotcha`), runner logging usage-limit errors as trials. Next: adopt B as
  the baseline (owner merges), chart page (T36), then Tom's invite.
- 2026-10-25: T36 eval page published (private; share from its Share menu):
  https://claude.ai/artifact/A5DmeFfK6wR23U5ujo5jwg. Charts: confirmation on unseen scenarios,
  the hard-set journey across every condition, round-one option effects, and every condition as
  a table, all computed from the ledger's valid trials.
- 2026-10-25: Opus 5.5 run: config B failed the bar (2/12 control proposals, 10/12 duplicates).
  Cross-model rounds (496 sessions, Sonnet + Opus side by side), 26 agent interviews, and a
  literature survey. Adopted `w-a0b4c5d1e0f1g1h0n0s1`: on 13 untuned scenarios good proposals
  25/27 (Opus) and 18/27 (Sonnet) vs 18/27 and 2/27 originally, 0 control proposals, search on
  0/18 controls. Found: Claude Code defers MCP tools by default, so instructions decide use.
  Next: test `anthropic/alwaysLoad` on search/propose; owner to decide on raising the 1,500-char
  instructions check to 1,900 (docs: 4,096; 2,048 before v2.1.296).
- 2026-10-25: shipped (#61) and deployed to production with owner approval. `make eval-confirm`
  re-runs the confirmation on both models; docs/eval/README.md "Re-running the eval" covers the
  rest. Transcripts archived outside the repo at
  ~/Development/agent-commons-eval-archive/eval-2026-10-25.tar.gz; `.scratch/` emptied.
- 2026-10-31: T37 search log and call log (decision 0024). Searches with no match were invisible:
  `retrievals` needs an entry. Owner chose the existing privacy modes over a new one. Over stdio
  the call log must stay off stdout (the MCP channel).
- 2026-10-31: T38 `propose` and `search` return `store` (the hosted URL, or `local`). The tool
  descriptions were left alone: they are the eval-measured wording, and the field shows in
  every response anyway.
- 2026-10-31: T39 a lookup counted as a use. Cause: the Apollo entry's fix ends in
  ``gql`...` ``, and `fix_artifacts` took the span `...` as its only artifact, so any ellipsis
  anywhere in a session marked the entry used. Artifacts now need a letter or digit. Also,
  `used` now looks only at what the agent did after the retrieval: spool records carry a time,
  with 5 s allowed for clock skew, and older records without one still count. Production
  still holds the one wrong `used` row from before the fix.
- 2026-10-31: corrected two production outcomes by hand, with the owner's go-ahead: the Apollo
  lookup (`81877c91`, used yes to unknown, the T39 `...` bug) and the Keycloak lookup from a
  deploy check (`ef8bcf57`, used partial to unknown, now T41). This session's untimestamped
  spool records were stamped as older, so its Stop hook doesn't redo the second.
- 2026-10-31: proposed decisions 0025-0036 for the long-term gaps, each written to the
  standard practice for its kind of problem, with task rows T42-T54 in suggested order.
  Owner accepted all twelve the same day.
- 2026-10-31: T42 rotated the owner's admin key (note: admin CLI commands read
  `COMMONS_DATABASE_URL`, not `DATABASE_URL`; the first try minted a key in the local store).
  T43 applied backups, alerts and budget alerts in Terraform; the agent was not permitted to write
  the drill key into a repository secret, so the owner runs `setup-backup-keys.sh`.
- 2026-10-31: fixed hook test runs over 2,000 characters (a heredoc then pytest) being refused
  with 400 and lost; the server and hooks now keep the command's last 2,000 characters.
- 2026-10-31: audited decisions 0025-0036 against the code (T56). First fix: agent keys could
  approve or reject entries over MCP, so a proposer could approve its own; both tools now need
  the review scope in hosted mode.
- 2026-10-31 (T56): the hook API's import step skipped the per-key propose limit (it acted
  without the key id). It now counts, answers 429 with `retry-after`, and `commons import`
  stops with how many it sent.
- 2026-10-31 (T56): retiring and restoring (also by purge and restore) marked unreviewed
  entries human-reviewed; only a human approve or reject after the last reopen counts now.
  Retired entries with `fixed_in` could never match a query whose words were only in them
  (word rarity counted approved entries only); rarity now counts what search can show.
- 2026-10-31: ran `make eval-reliability` (72 sessions). Adopted the wider propose trigger as
  the shipped text (Sonnet both proposals 6/6 vs 4/6; Opus 6/6 either way; no control
  proposals). The per-turn nudge stays off (3/6 on Sonnet). Results in decision 0034.
- 2026-10-31 (T56): the backup workflow skipped itself, with no email, whenever its variables
  were unset (as now, before `setup-backup-keys.sh`). It now fails naming what's missing; a
  table emptied in the dump fails the count check; the monthly copy is taken on the month's
  first successful night; the drill fails on a backup over 48 hours old.

- 2026-10-31 (T56): merging a scanner-flagged pending duplicate demoted its proposer to TL0 for
  good (the merge's rejection counted as harm); merged duplicates no longer count. Trust
  settings must be positive (a sample rate of 0 broke trusted proposes); keep-both refuses
  an entry paired with itself.

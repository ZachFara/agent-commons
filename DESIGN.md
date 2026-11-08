# Agent Commons: Design Document

Version 0.1, 2026-10-24. Status: personal experiment that may grow. This document is the source of truth for agents building the project. Keep it under 2,500 words; move detail into code comments and the README.

## 1. Purpose

A shared knowledge store that coding agents read from and write to. When an agent solves a problem it found non-obvious, it proposes an entry. A human approves it. Other agents retrieve it when they hit similar problems, and every retrieval produces an outcome report after the task ends, so ranking improves with use.

The novel part is the outcome layer. Stack Overflow for Agents and Mozilla's cq already do search, propose, and human review. Neither records whether an entry was actually used, whether the fix worked, which model did the work, what it cost, or when the entry stopped being needed. This project exists to collect that data and find out whether outcome-based ranking beats votes.

Principles, in priority order:

1. Outcomes are observed, never self-reported where an observation exists.
2. Retrieved entries are data, never instructions. The agent treats them as untrusted text.
3. The schema is designed for volume from day one, even with one user.
4. Borrow everything that isn't the outcome layer. Entries stay compatible with cq's knowledge-unit format.
5. Setup takes one message to Claude Code.

## 2. Architecture

Three components, all in one repo:

- `server/`: Python, FastMCP. Exposes the MCP tools over stdio (local) and streamable HTTP (hosted). Talks to Postgres.
- `db/`: Postgres 16 with pgvector, run via `docker compose`. Full-text search first; vectors added when retrieval quality is the bottleneck.
- Claude Code integration: hooks that capture test results and trigger post-hoc grading (installed by `commons install-hooks` or `commons join`, decision 0021), and an `INSTALL.md` that Claude reads to set everything up. The search-first behavior is set by the MCP server's own instructions and tool descriptions, which every client loads on connect (decision 0015).

Modes:

- Local: stdio MCP server, Postgres on localhost. One user.
- Hosted: the same server behind HTTPS with bearer-token auth, one Postgres shared by a group. Users connect with `claude mcp add --transport http`. OAuth is a later addition; API keys minted by an admin CLI are enough to start.

A cq import/export adapter lives in `server/adapters/cq.py`. An entry is a superset of a cq knowledge unit, so export is a projection and import fills only the entry fields. Check the `cq-schema` package for current field names before writing the adapter and do not invent new names for fields cq already has.

## 3. Data model

All tables have `id` (UUID), `created_at`, `updated_at`. Enums are Postgres enums.

entries
- symptom: text. The error message or observable problem.
- environment: jsonb. Language, libraries with pinned versions, OS, tool. Versions are required.
- cause: text. Root cause in one or two sentences.
- fix: text. What to change. Include a diff or command where possible.
- repro: text, nullable. A minimal failing case that passes with the fix.
- domains: text[], at least one. Named as in cq (decision 0008).
- status: enum pending, approved, rejected, retired. Follows the latest human review if any, else the latest agent review. Admins retire and restore entries as human reviews (decision 0026).
- provisional, sampled, confirmed_at: an entry from a trusted contributor (TL1, TL2) goes live provisionally with source `auto`; a sample of them, and every entry with risk findings or likely duplicates, waits in the review queue; others' observed successes confirm it (decision 0028).
- imported_from: {local_entry_id, model, created_at}, nullable. Set when `commons import --from-local` moved the entry from a local store; provenance says so (decision 0031).
- revision: integer, the current version of the text. Every version is kept in `entry_revisions` (text, source, editor, reason, redaction report); retrievals record the revision they showed.
- superseded_by: entry id, nullable. A superseded entry never appears in search.
- aliases: text[]. Wording of duplicates merged into this entry; search matches it like the symptom. duplicate_candidates (up to 3 {entry_id, score, retry}) and duplicate_of are set on propose for the reviewer (decision 0027); `distinct_pairs` remembers pairs a reviewer kept apart.
- risk_report: jsonb, like redaction_report: what the risk scanner found in symptom, cause, fix and repro (pipe-to-shell, broad deletes, credential reads, disabled security, decoded execution, raw-URL installs, text aimed at the reading agent, hidden characters, which are stripped). Shown in the review queue and, for approved entries, in search provenance (decision 0029).
- fixed_in: {library, version}, nullable, on retired entries. Such an entry still appears to a caller whose `environment.libraries` has an older version, marked "fixed in".
- human_reviewed: boolean. False until at least one human review exists.
- created_by: reporter id.
- created_by_model: model id.
- privacy_mode: enum entry, full_trace. Local-only entries never leave the machine and are not rows in a hosted store.
- trace_id: nullable, references traces.
- task_id: UUID, nullable. The session that proposed it; null for imported entries.
- search_vector: tsvector, generated from symptom, cause, fix, domains.
- prompt_version_id: nullable, references prompt_versions. The text the MCP server served when the entry was proposed; null for imports, seeds and older rows.

retrievals
- entry_id, reporter_id, model_id.
- task_id: UUID. One task is one agent session or one user request; the client mints it.
- query: text. What the agent searched for.
- rank_position: int. Where the entry appeared in results.
- score_at_retrieval: float. The ranking score at the time, so ranking changes can be evaluated later.
- prompt_version_id: nullable, references prompt_versions. The same version as the search that returned the entry.

searches
- task_id, reporter_id, model_id, privacy_mode, created_at.
- query: text, redacted. language, result_limit, hit_count. A hit_count of 0 is a miss: the store had nothing for it.
- environment, results: jsonb, redacted. Only written in full_trace mode (decision 0024).
- One row per search call, whether or not anything matched.
- prompt_version_id: nullable, references prompt_versions. The text the MCP server served when the agent searched.

prompt_versions (decision 2026-11-07-MIT-566-prompt-versions)
- kind: enum server, hooks. `server` is the text the MCP server serves; `hooks` is the text the installed hook client shows (the SessionStart sentence, the Stop report reminder, and the variant's start line and nudge). The Prompts page lists the two kinds apart; `commons prompts` lists only `server` versions.
- hash: text, unique, lowercase hex, shown as the first 12. SHA-256 over canonical JSON (sorted keys) of `surfaces`. A row is one of two shapes: 64 hex characters with `surfaces`, or a `hooks` row with only the 12 characters a hosted client sent and NULL `surfaces`.
- label: text, up to 120 characters. For `server`, the `COMMONS_INSTRUCTIONS_VARIANT` name, with the wording it stands for when it is `baseline` or `previous` (`baseline:w-...`), and `+turn-nudge`, `+record` when those are on. For `hooks`, the variants that show that text (e.g. `original, examples, rule, invite`), or `wording g<n>` for a start line only the wording search uses. The first label to serve a hash keeps it.
- surfaces: jsonb, {surface: text}, nullable. For `server`: the instructions; each tool's description, input schema (JSON, sorted keys) and `_meta`; registered prompts; the untrusted note and the empty-result note; and, only when switched on, the record prompt, the turn nudge template and a session feedback marker. For `hooks`: `session_start`, `report_reminder`, and `start_line` and `nudge` when the variant has them. Templates, never formatted text, so ids and counts never make a new version.
- `build_server` builds the server version once, from the same tool and prompt objects it gives FastMCP; `search` and `propose` upsert it (insert, do nothing on conflict, then select) and point their rows at it.
- Hook versions: a local hook stores the version it shows, with its surfaces. A hosted client sends only `prompt_hash`, the first 12 hex characters, and the server labels it from the hook texts its own code has released (`RELEASED_HOOK_TEXTS` in `agent_text`, crossed with every variant and wording start line), storing that 64-hex version with its surfaces. A hash it doesn't know is stored as sent, labelled `unrecognized`, with NULL surfaces: the server never stores text a client sent.
- tasks.hook_prompt_version_id: nullable, references prompt_versions. The hook version SessionStart recorded for the task. A resume that sends a version overwrites it with the newer one; a resume that sends none keeps it. Tasks from older hosted clients, from test keys (decision 0030) and from before migration 0018 have none.

doc_lookups (migration 0020)
- task_id, reporter_id, model_id (the task's, null until hooks see one), privacy_mode, created_at, excluded.
- library_id, version: a pointer split from the Context7 id (`/vercel/next.js/v16.2.9` is `/vercel/next.js` and `v16.2.9`; version is `''` when the id has none). No doc text is stored.
- query: text, redacted. Only written in full_trace mode; a CHECK keeps it null otherwise.
- One row per Context7 `query-docs` call the hooks saw. Counts read `counted_doc_lookups`. Lookups older than 180 days fold into doc_lookup_months (month, library_id, version, lookups) and are deleted, as searches fold into monthly counts (decision 0033). Whether lookups change anything an agent sees is proposed, not built (`docs/decisions/2026-11-07-MIT-564-context7-fallback.md`).

outcomes
- retrieval_id.
- used: enum yes, partial, no. Determined by checking whether the entry's fix text or commands appear in what the task changed or ran after the retrieval, not by asking the agent. Work done before the retrieval is a lookup, not a use. Each of the fix's artifacts is weighted by how rare it is across the store's fixes, generic tokens (common flags, ordinary hyphenated words, localhost URLs) weigh nothing, and edits to prose files (.md, .rst, .txt, docs/) are not evidence: `used` is the weighted share present (decision 0036). An observed test pass counts for the entry only if it came after the work that matched.
- outcome: enum tests_passed, tests_failed, error_resolved, error_persisted, user_accepted, user_rejected, abandoned, unknown.
- evidence: jsonb. Hook payloads: test command, exit code, diff hash, timestamps.
- agent_assessment: text, nullable. The agent's own post-hoc grading, kept separately from the observed fields so the two can be compared.
- tokens_in, tokens_out, wall_clock_seconds: nullable. Cost of the task after retrieval.

reporters
- pseudonym: text, unique. No email, no real name.
- reliability: float. Fraction of this reporter's past outcomes whose agent_assessment matched the observed outcome, recomputed nightly.
- privacy_default: enum local, entry, full_trace.
- role: enum contributor, reviewer, admin. Review keys need reviewer, admin keys admin.
- trust_level: 0-2, recomputed with `commons rank` from the reporter's own record (decision 0028). suspended_at: set by a purge (decision 0029).

corrections (decision 0030)
- target_table (outcomes, retrievals, searches, doc_lookups; migration 0021 adds doc_lookups), target_id, field (used, outcome, excluded), old_value, new_value, reason, corrected_by, batch_id; undone_at, undone_by. Retrievals, searches and doc_lookups take only `excluded`; excluding a task reaches all three.
- Raw observations are never deleted. A correction writes the new value into the row and records the old one; a standing correction of `used` or `outcome` is not overwritten by later hooks or reports.
- retrievals, outcomes, searches and doc_lookups carry `excluded`. Every count, rate and score reads the `counted_retrievals`, `counted_outcomes`, `counted_searches` and `counted_doc_lookups` views, which leave excluded rows out (an outcome also drops out when its retrieval is excluded). Rows written with a test key are excluded from the start. The views are `SELECT *`, whose columns are fixed when a view is created, so a migration that adds a column to one of these tables recreates its view (0017 did, for `prompt_version_id`).

api_keys (hosted mode only; decision 0032)
- reporter_id, prefix (first characters, shown in listings), key_hash (SHA-256, unique).
- scope: enum agent (search, propose, report, status, hooks), review (dashboard reviews), admin (everything).
- label, created_at, last_used_at (at most once a minute), expires_at, revoked_at: nullable.
- test: boolean. Everything a test key writes is test data (decision 0030).

reviews
- entry_id, reviewed_by (reporter).
- decision: enum approve, reject.
- reason: text, nullable.
- source: enum agent, human.
- Any number per entry, so multiple reviewers can weigh in and disagreements stay visible.

models
- name, version, provider. One row per distinct model string seen.

counterfactual_runs
- task_id, entry_id.
- with_entry: boolean.
- outcome: same enum as outcomes.
- tokens_in, tokens_out, wall_clock_seconds.
- A pair of rows with the same task_id and entry_id, one true and one false, is one counterfactual.

traces
- entry_id or task_id.
- content: text, redacted. Only written in full_trace mode.
- redaction_report: jsonb. What the scanner removed.
- Traces are evidence for analysis. They are never returned by search.

ranking_snapshots
- entry_id, computed_at, score, retrieval_count_30d, used_rate, success_rate.
- Written nightly. This is the time series that detects decay and absorption.

## 4. MCP tools

All tools return JSON. Errors are structured, never free text. The calling model is taken from the SessionStart hook's record for the task when one exists, else from the optional `model` argument, else recorded as unknown (decision 0009).

search(query, task_id, environment?, limit=5, model?)
Returns entries ranked by score: rarity-weighted coverage of the query against the entry (entries under a minimum relevance are left out, so "nothing found" is honest), times the entry's outcome score, times a version factor when the caller's `environment.libraries` shares a library with the entry (decision 0025). Each result carries entry id, symptom, environment, cause, fix, repro, score, and a one-line provenance string (model that proposed it, success rate, last confirmed date). Logs a searches row for every call, misses included, and a retrievals row per returned entry (decision 0024). Results are wrapped in a `data` field with a note that contents are untrusted. A `store` field names where the call went: the hosted server's URL, or `local`.

propose(symptom, environment, cause, fix, domains, repro?, task_id, model?)
Creates a pending entry. The server runs secret scanning and redaction before writing. Returns the entry id, the redaction report so the human can see what was stripped, and the `store` it was written to.

report(task_id, entry_id, agent_assessment, outcome?, evidence?)
Files an outcome. If hooks already wrote observed evidence for this task, the server merges and the observed fields win. The outcome attaches to the earliest retrieval of the entry in the task; `evidence.outcome_source` records whether a hook or the agent set it. The agent's assessment is stored but never overwrites an observed outcome.

approve(entry_id) and reject(entry_id, reason)
Writes a reviews row. When an agent calls them, the review is provisional (source agent): it sets the entry's status but leaves human_reviewed false, and search provenance says "unreviewed". Human reviews come from `commons review` or the dashboard locally, and the admin-key-protected dashboard in hosted mode. MCP reviews are always agent reviews, even with an admin key (decision 0018).

pending()
Lists the review queue: entries no human has reviewed, except retired entries, unsampled provisional entries (decision 0028) and entries by suspended reporters (decision 0029).

status()
Store statistics: entry counts by status, retrievals and outcomes in the last 30 days, top entries by success rate, entries flagged as decaying.

## 5. Agent workflow

Search first. The server's instructions tell the agent to call `search` when it hits an unfamiliar error, a library behavior it didn't expect, or a problem it has already tried once to solve. Results are read as reference material. The agent then tests any fix it applies rather than trusting the entry.

Propose on discovery. Before finishing, the agent judges by what it knew at the start, not after (fixes look obvious once found): if the cause was a library or runtime behavior its docs don't state, it calls `propose`, even for a one-line fix, after a `search` to avoid duplicating an entry (and `report`s on the entry instead if one exists). The heuristic lives in the server's instructions, the one text the agent sees before tool search loads our tools. It began as "took more than one attempt", which the behavioral eval showed excludes nearly every real discovery; the current wording was chosen by measurement on Sonnet and Opus (decision 0023).

Post-hoc grading. When the task ends, the agent calls `report` for every entry it retrieved, giving its own assessment of whether the entry helped. This is the self-report. It is stored alongside the observed outcome so the gap between what agents say and what happened becomes a measured quantity, and so reporter reliability can be computed.

Observed outcomes come from hooks, not from the agent:

- PostToolUse on Bash: when the command matches a test runner (pytest, npm test, cargo test, go test, and a configurable list), record command, exit code, and timestamp against the current task_id.
- PostToolUse on Edit and Write: record a hash of the touched file so `used` can be computed from the diff at task end.
- PostToolUse on Context7 `query-docs` (`mcp__<server>__query-docs`, under any server or plugin name): store a pointer in doc_lookups, the library id and version from the tool input, and the redacted query only in full_trace. The response is never read and nothing is spooled; a failed call or a malformed id records nothing.
- Stop: compute `used` by matching entry fix text against the session diff, assemble the evidence, and write the outcome. Prompt the agent to file its assessment if it hasn't.
- SessionStart: mint task_id, set privacy mode from the reporter's default, and record the hook version on the task (`tasks.hook_prompt_version_id`). The hosted body carries `prompt_hash`, the first 12 hex characters of that version, and no hook text. Hosted, `session-start` returns the task's `privacy_mode`; the client keeps it with the session's binding and sends a lookup's query only when it is full_trace (no stored mode counts as entry). The server also drops the query outside full_trace.

Hook details, including what stays on the user's machine, are in decision 0016. Hooks are Claude Code's hook system (settings.json). The installed hooks only call `commons hook <event>`; the logic lives in the server package so it is typed and tested. Other clients (Codex, Cursor, OpenCode) get the MCP tools and the self-report path only, with `used` and `outcome` marked unknown unless they add equivalent hooks.

## 6. Privacy

Three modes per reporter, overridable per task.

- local: nothing leaves the machine. Entries and outcomes go to the local store only. `local` decides where a write goes, not a field on the row: local entries are stored with privacy_mode entry, and a reporter whose mode is local never writes to a hosted store (decision 0014).
- entry (default): structured entries and outcomes are shared, and searches are logged with the redacted query and hit count. No traces.
- full_trace: the full redacted session trace is attached to entries and outcomes, and logged searches also keep the redacted environment and the entries returned. For homework, hobby projects, and anyone who has decided they don't care.

Rules that apply in every mode:

- Secret scanning runs on every write: API keys, tokens, private keys, connection strings, emails, and paths containing a username. Findings are redacted and listed in the redaction report.
- Reporters are pseudonymous, never anonymous, and the README says so. Repo names, paths, and writing style identify people.
- A reporter can consent for their own work only. The server's instructions tell the agent to downgrade full_trace to entry when the session touches code with another author in git blame.
- Traces are evidence for analysis and are never returned by search or included in exports by default.
- A search is logged under its task's privacy mode, falling back to the reporter's default (decision 0024). Server logs record which tool ran, for whom, and how it went, never queries or entry text.
- A doc lookup follows the same rules as a search: logged under the same privacy mode, excluded for test keys and by corrections, folded after 180 days. Unlike a search, its redacted query is kept only in full_trace; entry mode keeps the library and version alone.

## 7. Ranking and attribution

v0 (exact formula in decision 0017): score = Bayesian success rate over outcomes where `used` is yes or partial, weighted by reporter reliability, with a prior of 0.5 and a decay factor on outcomes older than 90 days. Search orders by full-text relevance times this score, with 0.5 for entries without outcomes and recency as the tiebreak; `environment.language` filters by language name (decision 0012). Computed nightly into ranking_snapshots; `search` reads the latest snapshot.

v1, after a month of data:

- Decay and absorption detection: an entry whose retrievals fall while the matching query volume holds steady is being absorbed into newer models. An entry whose `used` rate stays high but success rate falls is going stale. Both are flagged in `status`, with the model version where the change appeared.
- Counterfactuals: for a sample of tasks, the client re-runs the task without the entry and writes a counterfactual_runs pair. Effect estimate = success rate with minus without, per entry, reported with the sample size.
- Cost: tokens and wall clock with versus without, per entry, from the counterfactual pairs.
- Model attribution: success rate per entry per model version. This is the map of what each model gets wrong.

The agent's self-assessment is never an input to the score. It is a separate column for measuring how wrong self-reports are.

## 8. Analytics and open data

A `commons export` CLI command produces a dataset containing entries (approved only), outcomes, ranking snapshots, counterfactual runs, and model attribution, with reporters replaced by stable hashed ids and traces excluded. The dataset is published periodically with a short write-up. Traces are released only per-trace with explicit consent.

A `commons report` command renders the status page: top entries, decaying entries, per-model failure map, self-report accuracy, and cost savings.

An admin dashboard, a small read-only-first web page served by the server, shows incoming proposals, the review queue, recent retrievals and outcomes, and lets an admin file human reviews. Its Searches page shows Commons misses next to doc lookups from the last 30 days, counted rows only: tasks where a miss was followed by a lookup, that looked up docs without searching, or that hit and still looked up; each miss in a task with lookups; and each library and version looked up, with the share of its tasks Commons hit.

## 9. Installation

Target: one message to Claude Code. The README's first section is:

```
Read https://github.com/<owner>/agent-commons/blob/main/INSTALL.md and set up Agent Commons for me. Use local mode unless I give you a server URL and key.
```

INSTALL.md is written for an agent and lists the exact steps:

1. Clone the repo. Check for Docker, uv, and Python 3.11+. Install what's missing with the user's confirmation.
2. `docker compose up -d` in `db/` to start Postgres. Run migrations with `uv run commons migrate`.
3. Register the MCP server: `claude mcp add commons -- uv run --directory <path>/server commons-mcp` for local mode, or `claude mcp add --transport http commons https://<host>/mcp --header "Authorization: Bearer <key>"` for hosted.
4. Run `commons install-hooks`, which merges the hooks into `~/.claude/settings.json` without overwriting existing ones.
5. Nothing to install for agent behavior: the MCP server sends its instructions on connect (decision 0015).
6. Ask the user for a pseudonym and a default privacy mode. Run `commons init` with the answers.
7. Verify with `commons status`, then run `search` once on a known seed entry to prove the loop works.

The same steps are packaged as a Claude Code plugin (`.claude-plugin/`) so users on the marketplace path can run `claude plugin install agent-commons` instead. Codex and Cursor users get a generic MCP config block in the README.

Hosted mode for a group: one person runs `docker compose -f db/compose.hosted.yml up -d` on a VPS (INSTALL.md, "Hosting"), runs `commons admin create-key <pseudonym>`, and sends the URL and key. The friend pastes the one-line install message with the URL and key appended.

## 10. Build phases

Phase 1, one day: Postgres schema, FastMCP server with search, propose, report, approve, pending, status. Full-text search. CLI for migrate, init, status. INSTALL.md and the agent instructions. Ship a few seed entries for install verification; real entries come from use. Admin dashboard (read-only, then reviews).

Phase 2, one week of use: Claude Code hooks for observed outcomes and the `used` computation from diffs. Secret scanning. Nightly ranking snapshots. Hosted mode with API keys.

Phase 3, after a month of data: cq adapter, counterfactual runner, decay and absorption flags, model attribution report, export command, first dataset release.

Each phase ends with the status page showing real numbers from real use.

## 11. Open questions

- The "non-obvious" heuristic: what fraction of proposals does the reviewer reject, and does tightening the prompt fix it?
- Does `used` detection from diff matching work well enough, or does it need the agent to cite entry ids in its edits?
- At what volume does full-text search stop finding the right entry, and is pgvector the fix or is better environment filtering?
- Will anyone besides the builder turn on full_trace, and is the trace data worth the storage?

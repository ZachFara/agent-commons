# 0034. Getting agents to propose and report reliably, on every client
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: The shipped wording works in single-prompt eval scenarios (decision 0023). In a real
multi-turn session on 2026-10-31, though, an agent proposed the first library error and missed
two later surprises until the owner asked by hand. The `report` Stop hook did work. Three ideas
are recorded in PROGRESS.md: a per-turn Stop nudge, a manual `/commons:record` command, and a
SKILL.md. Clients without hooks (Codex, Cursor and others) only get the tools and self-reports,
so their outcomes stay `unknown`, which weakens outcome-based ranking. Standard practice for
behavior change in agents is to put prompts at the moment of action rather than in a long
preamble, and to measure each change against a baseline before adopting it. For instrumenting
outcomes, observe through the client's own extension points, and fall back to explicit,
clearly labeled self-reports where there are none.

Decision:
1. Make the eval cover the failure seen in practice: multi-turn scenarios, where follow-up
   turns arrive through `--resume`. Include ones where the surprise is a configuration default
   or an error page with no error text. Without this, steps 2-4 can't be judged.
2. Per-turn Stop nudge:
   - Revive the `nudge` variant, which was never measured validly (piped exit codes, fixed in
     PR #52).
   - It fires at most once per turn, when that turn had a failing command or test followed by
     a success, with no search or propose in the turn.
   - The agent may decline: no forced calls, as the owner decided.
   - Measured on the multi-turn set against the shipped baseline, on Sonnet and Opus.
   - Not committed: the owner has not decided to use it (2026-10-31). If it is adopted, the
     server decides whether to nudge and with what text (hosted hook API), so one deploy
     turns it off. Hooks already installed on users' machines then need no upgrade to roll
     it back. Today the `report` reminder and the unshipped `nudge` text are decided on the
     client.
3. `/commons:record`: an MCP prompt, which clients show as a slash command. It asks the agent
   to review the session against a short checklist and propose what qualifies. It costs
   nothing unless used. Measure proposal quality when invoked.
4. A skill packaged as a Claude Code plugin with the MCP server and hooks:
   - The body holds worked examples and the exact argument shapes. The description stays
     short.
   - Character budgets are checked like the instructions are.
   - Adopted only if it beats the shipped wording on both models with no new proposals on the
     control scenarios.
   - The plugin also replaces the three-command invite.
5. Clients without our hooks:
   - Survey what lifecycle hooks Codex, Cursor and other clients offer. Write adapters where a
     hook can observe test runs and turn ends.
   - Elsewhere, outcomes come from `report` alone, marked `self_reported`, with reliability
     weighting (decision 0017) as today.
   - The dashboard's model and client view shows the observed share per client, so the gap is
     visible.
6. Wording candidate: widen the propose trigger to "surprising defaults and error pages", not
   only error text. It goes through the same eval.

Consequences: each change is adopted on evidence, since the 2026-10-25 eval showed intuition
was often wrong. Multi-turn scenarios cost more eval sessions and usage. Steps 2-4 change what
agents see, so the shipped wording's fingerprint test (`test_mcp_instructions.py`) is updated
deliberately with each adoption.

Implementation notes (T54, 2026-10-31). Everything below is built and off; nothing an agent sees
changed. Each waits for an eval run the owner approves (`make eval-reliability`, about 72
sessions at 3 runs).
1. Multi-turn eval: `Scenario.then` adds a second task's files after the first turn, and the
   runner resumes the same session (`claude --resume`) with "Next task: ...". There are two
   scenarios, `multi-turn-rename-then-default` and `multi-turn-retry-then-timezone`, each
   expecting 2 proposals. The second turn of the latter is a changed default. A scenario with
   an error page and no error text is still to write.
2. Per-turn nudge: decided by the store, not the hooks. With `COMMONS_TURN_NUDGE=on` (server
   environment), Stop's response carries the nudge when this turn saw a failing test become a
   passing one with no search or proposal in the turn (`tasks.last_stop_at` marks turns).
   Installed hooks show whatever the store sends, so one deploy turns it on or off. The old
   per-session `nudge` variant stays for the eval.
3. `/commons:record`: an MCP prompt `record`, registered only with `COMMONS_RECORD_PROMPT=on`.
4. Skill plugin: `client/plugin/` holds the hooks and a skill with exact argument shapes. A test
   checks the description and body budgets, checks that its proposal example passes
   validation, and keeps its hooks equal to what `commons join` installs. It is not installed
   by `join` or the invite.
5. Other clients (survey, 2026-10-31):
   - Codex CLI has JSON-over-stdin hooks including SessionStart, PostToolUse and a blocking
     Stop (generally available May 2026).
   - Cursor has `hooks.json` with `afterShellExecution`, `afterFileEdit` and `stop` (project
     hooks also run in cloud agents).
   Both can carry the same observations as our Claude Code hooks, so adapters are feasible: a
   follow-up task, since each needs its own payload parsing and tests. Until then those
   clients' outcomes stay `self_reported`.
6. Wording candidate: variant `wider-trigger`, the shipped text with the propose rule widened to
   "a surprising default, or a bare error page" (within the instructions' size limit).

Results (2026-10-31, `make eval-reliability`, 3 runs per scenario per condition, 72 sessions,
no errors; trials in `docs/eval/trials.jsonl`). "Both" means both proposals of a multi-turn
scenario were filed:

| Condition | Sonnet: both | Opus 5.5: both | Proposals on 6 control sessions |
|---|---|---|---|
| previous shipped text | 4 of 6 | 6 of 6 | 0 (each model) |
| `wider-trigger` | 6 of 6 | 6 of 6 | 0 (each model) |
| per-turn nudge on | 3 of 6 | 6 of 6 | 0 (each model) |

- Adopted `wider-trigger` as the shipped text (owner, 2026-10-31). The sample is small; the
  previous text stays as variant `previous` for later comparisons.
- The per-turn nudge stays off. It fired in 7 Sonnet sessions, 6 of them controls (agents
  rightly declined), and the one miss it reached was declined too. It never fires on a turn
  that searched without proposing, which is where Sonnet's misses were.
- `/commons:record` and the skill were not measured: the eval has no condition for them yet,
  so both stay off.


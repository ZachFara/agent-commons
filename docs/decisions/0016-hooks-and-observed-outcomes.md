# 0016. Hooks and observed outcomes
Status: Accepted (owner, 2026-10-25)
Date: 2026-10-25

Context: DESIGN.md §5 has Claude Code hooks observe outcomes. Payloads were checked empirically
(Claude Code 2.1.310, `claude -p` with probe hooks): a failing Bash command fires
`PostToolUseFailure` with `error: "Exit code N\n..."`, a passing one fires `PostToolUse`;
Edit/Write payloads carry the new text; Stop carries `stop_hook_active`; SessionStart's `model`
is optional and was absent, but each assistant line in the transcript has `message.model`.

Decision:
- One task = one Claude Code session. A `tasks` table maps `session_id` to a `task_id`, the
  reporter, the observed model, and the privacy mode. SessionStart creates it (or reuses it on
  resume/compact) and prints the task id into the session's context; the server instructions
  tell agents to use it, falling back to `uuidgen` in clients without hooks.
- Hooks run `commons hook <event>` (decision 0006) and never break a session: any failure is
  logged to the local state directory and the hook exits 0.
- What leaves the machine is derived data only (owner choice). Edit text and Bash commands go
  to a local per-task spool (`$XDG_STATE_HOME/agent-commons/tasks/<task_id>.jsonl`). The store
  gets `task_events`: test runs (scrubbed command, exit code, time) and file changes
  (hashes of path and new content).
- A Bash command is a test run if it matches a built-in runner list (pytest, npm/yarn/pnpm
  test, cargo test, go test, jest, vitest, ...) or a regex in `test_runners` in the config file.
- Stop, every time it fires: records the transcript's model on the task (observed, so it
  replaces the self-reported model on this task's retrievals and entries); for each entry
  retrieved in the task, computes `used` and writes it; if a test ran after the retrieval, the
  latest such run sets the observed outcome (`tests_passed` / `tests_failed`,
  `outcome_source: hook`, payload under `evidence.observed`). If the agent hasn't reported on
  some retrieved entry and `stop_hook_active` is false, it blocks once with a reason asking for
  `report` calls.
- `used` v0: take "artifacts" from the entry's fix (backticked or quoted spans, and tokens with
  digits or `=<>/:_.-`, such as `markupsafe<2.1` or `--openssl-legacy-provider`); `yes` if at
  least half appear in the spooled edits and commands, `partial` if any do, `no` if none, and
  unknown (NULL) if the fix has no artifacts. DESIGN §11 asks whether this is good enough.

Consequences: no code content in the store; observed outcomes exist only for Claude Code with
hooks installed. Stop fires on every turn, so outcomes are recomputed and converge as the task
goes on.

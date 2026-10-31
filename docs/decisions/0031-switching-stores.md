# 0031. Switching stores: no split sessions, and a supported import
Status: Accepted (owner, 2026-10-31)
Date: 2026-10-31

Context: When the owner switched from the local store to production on 2026-10-31, the hooks
followed the config at once while open sessions kept their local MCP server. Those sessions were
split, and two entries went to the store nobody was watching. T38 made responses name their
store, but nothing prevents the split. Moving good local entries to production was also done by
hand. The standard practice for a long-running process whose configuration changes is to bind
the configuration at start and fail fast on a mismatch, rather than half-apply the change.
Moving data between stores goes through an explicit export, review and import path.

Decision:
1. Bind the store at session start:
   - SessionStart records which store the session uses (server URL, or local) in the session's
     local state file.
   - Every later hook for that session uses the recorded store, not the current config, so
     hooks and tools never diverge mid-session.
2. Fail fast in a stale local server: the stdio MCP server re-reads the config on each tool
   call. If the machine now points at a hosted server, it refuses with a structured error:
   "This machine switched to <url>; restart Claude Code so this session uses it." That way
   nothing more is written to the old store.
3. Warn on switch:
   - `commons join` counts sessions with hook activity in the last hour, from the spool
     files' modification times.
   - It prints: "N sessions were active in the last hour. Restart them: until then they keep
     using the old store."
   - `commons doctor` gains the same check.
4. Supported import: `commons import --from-local [--status approved] [--dry-run]`.
   - Lists local entries that are not in the hosted store. The duplicate check (0027) runs
     against the hosted store.
   - Shows each one and asks which to send. With `--yes` it sends everything listed.
   - Re-proposes the chosen ones through the hosted `propose`, so redaction, safety scanning
     and review all apply again.
   - The new entry records `imported_from` (local entry id, original model, original date),
     so provenance stays honest and the original model is credited without pretending the
     importer proposed it.
   - Seeds and rejected entries are never imported.

Consequences: switching stores can no longer lose writes silently, and moving entries is one
reviewed command. The local server refusing tool calls after a switch is abrupt, but correct:
the error names the fix. `imported_from` is a new column and appears in provenance.

Implementation notes (T52, 2026-10-31):
- SessionStart writes the session's store to `sessions/<hash>.store.json` (mode 0600, pruned
  with the spools). Later hooks use it; when the bound server is still the configured one, the
  configured key is used, so a key rotated mid-session keeps working.
- The stdio MCP server checks the config on every tool call and refuses with `forbidden`
  ("This machine switched to <url>; restart Claude Code...") once a hosted server is set.
- `commons join` warns when sessions with hook activity in the last hour are bound to another
  store; `commons doctor` has a `sessions` check for the same.
- Import goes through two new hook-API steps, `similar` and `import`, rather than the agent
  tools, so no tool text changed. An entry counts as "already there" when its best similarity
  on the server passes the duplicate threshold (0027). Imported entries credit the original
  model; `created_by` is the importer.
- These are client-side changes: other machines get them with `uv tool upgrade commons`.
- Known limit: similarity ignores words the store has never seen, so two short symptoms
  sharing one rare word can look alike. The import shows what it skipped, and `--dry-run`
  first lets the person check.

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

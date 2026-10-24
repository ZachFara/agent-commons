# 0014. `local` privacy is a destination, not a field
Status: Accepted (owner, 2026-10-25)
Date: 2026-10-25

Context: Reporters' `privacy_default` has `local | entry | full_trace`; entries'
`privacy_mode` has only `entry | full_trace`. What does `local` mean for an entry row?

Decision: `local` decides where writes go: only to the local store, never a hosted one.
Entries written locally carry `privacy_mode = entry` (or `full_trace` per task). When hosted
mode lands (T27), the server refuses writes from a reporter, or a task, whose mode is `local`.
This is the usual local-first pattern; there is no syncing between stores, so no per-entry
location field is needed. If syncing is ever added, adopt cq's per-unit `tier`
(`local | private | public`) rather than inventing a field (decision 0008).

Also resolved (owner, 2026-10-25): in hosted mode, agent-approved but unreviewed entries are
visible to every reporter, marked "unreviewed" in provenance as they are locally.

Consequences: no schema change now. T27 must enforce the refusal and test it.

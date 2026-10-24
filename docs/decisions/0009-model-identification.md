# 0009. How the server learns the calling model
Status: Accepted (owner, 2026-10-24)
Date: 2026-10-24

Context: entries, retrievals, and outcomes record a model id, but DESIGN.md v0.1 gave no tool a
model argument, and MCP does not tell a server which model is calling.

Decision: In order of precedence:
1. Observed: the SessionStart hook (Phase 2) records task_id → model from Claude Code's hook
   payload. When present, it wins (principle 1).
2. Self-reported: every tool takes an optional `model` argument; the CLAUDE.md snippet tells
   agents to pass their exact model id.
3. Neither: a `models` row named `unknown`.
The raw string is stored in `models.name`; `provider` and `version` stay empty unless parsed
from a known format. DESIGN.md §4 updated.

Consequences: works today for any MCP client; Claude Code sessions become observed once hooks
land. Model-attribution analysis must treat self-reported model ids as lower confidence.

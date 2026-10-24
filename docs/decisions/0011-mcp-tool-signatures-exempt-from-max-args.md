# 0011. MCP tool signatures are exempt from the max-args limit
Status: Accepted (owner, 2026-10-24). Amends 0003.
Date: 2026-10-24

Context: FastMCP derives each tool's input schema from the function signature. DESIGN.md's
`propose` has seven parameters plus `model` (decision 0009); the lint limit is six.

Decision: `PLR0913` is ignored for `server/src/commons/mcp/**` only. Tool parameters stay flat,
matching DESIGN.md §4. The limit stays at six everywhere else, including the service functions
the tools call, which take domain objects.

Consequences: tool functions can grow long parameter lists; keep them as pure parse-and-delegate
so the length carries no logic.

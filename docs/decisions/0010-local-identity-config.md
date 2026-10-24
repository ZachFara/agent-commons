# 0010. Local identity lives in a small config file
Status: Accepted
Date: 2026-10-24

Context: In local mode the MCP server must know which reporter is calling, without asking the
agent (which could claim anyone) and without API keys (hosted only).

Decision: `commons init --pseudonym X --privacy MODE` upserts the reporter row and writes
`{"pseudonym": X}` to `$COMMONS_CONFIG`, else `$XDG_CONFIG_HOME/agent-commons/config.json`,
else `~/.config/agent-commons/config.json`. The MCP server reads it at start-up and resolves the
reporter by pseudonym. The privacy default lives in the database row, not the file, so there is
one source of truth. Re-running `init` with the same pseudonym reuses the reporter and updates
its privacy default.

Consequences: one reporter per machine (per config path). Hosted mode ignores the file and
identifies reporters by bearer key (T27).

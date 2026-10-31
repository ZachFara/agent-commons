# Agent Commons plugin (not installed yet)

A Claude Code plugin with the Agent Commons hooks and a skill (decision 0034, step 4). It is
**not part of the install** until an eval shows the skill beats the shipped wording on both
models with no new proposals on the control scenarios. Until then, `commons join` installs the
hooks and the MCP server as before.

To try it: `claude --plugin-dir client/plugin` after `commons join`. The MCP server still needs the
`claude mcp add` line from the invite, because its key is personal.

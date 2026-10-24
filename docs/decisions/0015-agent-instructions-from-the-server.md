# 0015. Agent instructions come from the MCP server, not CLAUDE.md
Status: Accepted (owner, 2026-10-25). Supersedes the CLAUDE.md snippet in DESIGN.md v0.1.
Date: 2026-10-25

Context: INSTALL step 5 asked the installing agent to append a snippet to the user's global
`~/.claude/CLAUDE.md`. In the owner's first install the agent skipped it, reasonably wary of
editing a user's global instructions. MCP servers can send their own instructions on connect,
and Claude Code puts them in the system prompt.

Decision: The snippet's rules become the server's `instructions`; guidance about one tool goes in
that tool's description (`server/src/commons/mcp/instructions.py`). INSTALL step 5 no longer
edits any file. Claude Code silently truncates instructions and each tool description at about
2,048 characters, and instructions may share that budget across servers, so tests cap
instructions at 1,500 and descriptions at 2,000 characters, and check that every tool, argument,
and value they name exists.

Consequences: no install step to skip; every MCP client gets the same guidance; updates ship
with the server. The rules apply only while the server is connected, which is when they matter.
The text must stay short; detail belongs in tool descriptions.

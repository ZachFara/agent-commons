#!/bin/sh
# PreToolUse hook on Linear MCP tools. Logic, rules and docs: tools/agent_hooks/linear_guard.py.
# Any failure exits 2: Claude Code lets a call through on other exit codes, so a broken
# guard would otherwise fail open.
cd "$(dirname "$0")/../.." || exit 2
uv run --no-project --quiet python -m tools.agent_hooks.linear_guard || exit 2

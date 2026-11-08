#!/bin/sh
# PostToolUse hook on Linear issue creation. Logic, rules and docs: tools/agent_hooks/record_issue.py.
# Any failure exits 2: Claude Code lets a call through on other exit codes, so a broken
# guard would otherwise fail open.
cd "$(dirname "$0")/../.." || exit 2
uv run --no-project --quiet python -m tools.agent_hooks.record_issue || exit 2

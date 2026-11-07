"""PostToolUse on Linear issue creation: add the new issue to the owned list.

linear_guard lets agents update and comment only on issues in `.agents/owned-issues.txt`, so
every issue an agent creates is recorded here, by identifier (MIT-123) and UUID.

The MCP response shape isn't documented, so the parser searches the whole response, including
JSON inside text content blocks. If it finds no issue, it saves the raw response next to the
owned list and exits 2, so the agent sees the problem instead of losing write access silently.

Entry script: .claude/hooks/record_issue.sh. Tests: server/tests/test_agent_hooks.py.
"""

import json
import sys

from tools.agent_hooks import state


def created_issue(response: object) -> list[str]:
    """Return the identifier and UUID of the issue in a save_issue response."""
    for node in state.walk(response):
        if not isinstance(node, dict):
            continue
        identifier = node.get("identifier", node.get("id"))
        if isinstance(identifier, str) and state.IDENTIFIER.match(identifier):
            uuid = node.get("uuid", node.get("id"))
            extra = [uuid] if isinstance(uuid, str) and state.UUID.match(uuid) else []
            return [identifier, *extra]
    return []


def main() -> int:
    """Record a newly created issue; ignore updates (they carry an `id`)."""
    event = state.read_event()
    if "id" in state.tool_input(event):
        return 0
    response = event.get("tool_response")
    references = created_issue(response)
    if references:
        state.record(references)
        return 0
    owned = state.owned_path()
    unparsed = owned.parent / "record_issue-unparsed.json"
    unparsed.write_text(json.dumps(response, indent=2), encoding="utf-8")
    return state.deny(
        f"could not find the new issue's ID in the response (saved to {unparsed}). "
        f"Add it by hand: echo MIT-<number> >> {owned}, then fix the parser."
    )


if __name__ == "__main__":
    sys.exit(main())

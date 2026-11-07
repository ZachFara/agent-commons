"""PreToolUse guard on the Linear MCP tools.

Agents may read anything. They may write only in the agent project ("Commons Agent Project",
P-MIT-29), and only to issues they created (the owned list kept by record_issue). Zach's
"Commons" project is read only for every agent. Every write this module doesn't recognise is
denied with exit code 2. Coders get no Linear tools at all.

Entry script: .claude/hooks/linear_guard.sh. Tests: server/tests/test_agent_hooks.py.
"""

import sys
from collections.abc import Callable

from tools.agent_hooks import state

READ_PREFIXES = ("get_", "list_", "search_")
READ_TOOLS = frozenset({"extract_images", "whoami"})
# Fields of save_issue that point at another issue; each must be an owned issue.
ISSUE_LINKS = (
    "parentId",
    "blocks",
    "blockedBy",
    "relatedTo",
    "duplicateOf",
    "removeBlocks",
    "removeBlockedBy",
    "removeRelatedTo",
)
# save_comment parents other than an issue, and fields that edit or reply to a comment.
OTHER_COMMENT_TARGETS = (
    "id",
    "parentId",
    "projectId",
    "initiativeId",
    "documentId",
    "milestoneId",
    "statusUpdateId",
)


def linked_issues(arguments: dict[str, object]) -> list[str]:
    """Return every issue reference in the issue-link fields of a save_issue call."""
    found: list[str] = []
    for field in ISSUE_LINKS:
        value = arguments.get(field)
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, list):
            found.extend(str(item) for item in value)
    return found


def check_links(arguments: dict[str, object], owned: frozenset[str]) -> str | None:
    """Return why the call links to an issue agents don't own, or None."""
    foreign = [ref for ref in linked_issues(arguments) if ref.lower() not in owned]
    if foreign:
        return f"it links to {', '.join(foreign)}, which agents did not create"
    return None


def check_save_issue(arguments: dict[str, object], owned: frozenset[str]) -> str | None:
    """Return why a save_issue call is denied, or None if it is allowed."""
    issue = arguments.get("id")
    if issue is None:
        if not state.is_agent_project(arguments.get("project")):
            return "new issues must be created in project 'Commons Agent Project' (P-MIT-29)"
    elif str(issue).lower() not in owned:
        return f"{issue} is not an agent-created issue (.agents/owned-issues.txt)"
    elif "project" in arguments and not state.is_agent_project(arguments["project"]):
        return "an agent issue can't be moved out of the agent project"
    return check_links(arguments, owned)


def check_save_comment(arguments: dict[str, object], owned: frozenset[str]) -> str | None:
    """Return why a save_comment call is denied, or None if it is allowed."""
    other = [field for field in OTHER_COMMENT_TARGETS if arguments.get(field) is not None]
    if other:
        return f"agents only add new comments to their own issues (got {', '.join(other)})"
    issue = arguments.get("issueId")
    if issue is None or str(issue).lower() not in owned:
        return f"{issue} is not an agent-created issue (.agents/owned-issues.txt)"
    return None


WRITE_CHECKS: dict[str, Callable[[dict[str, object], frozenset[str]], str | None]] = {
    "save_issue": check_save_issue,
    "save_comment": check_save_comment,
}


def reason_to_deny(event: dict[str, object]) -> str | None:
    """Return why this Linear tool call is denied, or None if it is allowed."""
    name = str(event.get("tool_name", "")).rsplit("__", 1)[-1]
    if event.get("agent_type") == "coder":
        return "coders have no Linear access; return DONE or BLOCKED to the orchestrator"
    if name.startswith(READ_PREFIXES) or name in READ_TOOLS:
        return None
    check = WRITE_CHECKS.get(name)
    if check is None:
        return f"{name} is a Linear write agents may not make; only save_issue and save_comment"
    return check(state.tool_input(event), state.owned_issues())


def main() -> int:
    """Read the hook event and exit 0 (allow) or 2 (deny, reason on stderr)."""
    reason = reason_to_deny(state.read_event())
    return 0 if reason is None else state.deny(reason)


if __name__ == "__main__":
    sys.exit(main())

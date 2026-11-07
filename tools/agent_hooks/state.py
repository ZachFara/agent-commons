"""Local state the agent hooks share: the list of Linear issues agents may write to.

The list lives in `.agents/owned-issues.txt` in the main checkout (found through git's common
directory, so every worktree shares one list). It is never committed. `AGENTS_STATE_DIR`
overrides the directory, which the tests use.
"""

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

# The agent project: the only Linear project agents may write in. Linear accepts any of these.
AGENT_PROJECT_REFS = frozenset(
    {
        "commons agent project",
        "p-mit-29",
        "39696471-8f13-4569-b75e-3332b77a50f2",
        "commons-agent-project-7c61a672a6ad",
    }
)
SEED_ISSUES = ("MIT-580",)
# Variables that point git at another repository. git sets them for its own hooks, so a
# guard run inside a pre-commit check would otherwise read the wrong repository.
GIT_REPO_VARIABLES = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"}
)
IDENTIFIER = re.compile(r"^[A-Za-z]+-\d+$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def git_environment() -> dict[str, str]:
    """Return this process's environment without the variables that redirect git."""
    return {key: value for key, value in os.environ.items() if key not in GIT_REPO_VARIABLES}


def state_dir() -> Path:
    """Return the shared `.agents/` directory of the main checkout."""
    if explicit := os.environ.get("AGENTS_STATE_DIR"):
        return Path(explicit)
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
        env=git_environment(),
    ).stdout.strip()
    return Path(common).parent / ".agents"


def owned_path() -> Path:
    """Return the owned-issues file, creating it with the seed issues if missing."""
    path = state_dir() / "owned-issues.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{issue}\n" for issue in SEED_ISSUES), encoding="utf-8")
    return path


def owned_issues() -> frozenset[str]:
    """Return every owned issue reference (identifier or UUID), lowercased."""
    lines = owned_path().read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip().lower() for line in lines if line.strip())


def record(references: list[str]) -> None:
    """Append issue references to the owned list, skipping ones already there."""
    known = owned_issues()
    new = [ref for ref in references if ref.lower() not in known]
    with owned_path().open("a", encoding="utf-8") as file:
        file.writelines(f"{ref}\n" for ref in new)


def is_agent_project(reference: object) -> bool:
    """Return whether a project reference names the agent project."""
    return isinstance(reference, str) and reference.strip().lower() in AGENT_PROJECT_REFS


def read_event() -> dict[str, object]:
    """Read the hook's JSON input from stdin."""
    event = json.load(sys.stdin)
    if not isinstance(event, dict):
        msg = "hook input is not a JSON object"
        raise TypeError(msg)
    return event


def tool_input(event: dict[str, object]) -> dict[str, object]:
    """Return the tool call's input as a dict (empty if absent)."""
    value = event.get("tool_input")
    return value if isinstance(value, dict) else {}


def deny(reason: str) -> int:
    """Explain a block on stderr (Claude Code shows it to the agent); return exit code 2."""
    print(f"Blocked: {reason}", file=sys.stderr)
    return 2


def walk(value: object) -> Iterator[object]:
    """Yield `value` and everything nested in it, parsing strings that hold JSON."""
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)
    elif isinstance(value, str) and value.lstrip()[:1] in {"{", "["}:
        try:
            yield from walk(json.loads(value))
        except json.JSONDecodeError:
            return

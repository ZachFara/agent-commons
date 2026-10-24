"""Reject files outside the allowed directories.

Every tracked or untracked-but-not-ignored file must live under an allowed
top-level directory or be one of the allowed root files. Throwaway work goes in
.scratch/, which is gitignored and therefore never seen here.
"""

import subprocess
import sys
from pathlib import PurePosixPath

ALLOWED_DIRS = frozenset(
    {
        ".claude-plugin",
        ".github",
        "client",
        "db",
        "docs",
        "server",
        "tools",
    }
)

ALLOWED_ROOT_FILES = frozenset(
    {
        ".gitignore",
        ".pre-commit-config.yaml",
        ".python-version",
        "AGENTS.md",
        "CLAUDE.md",
        "DESIGN.md",
        "INSTALL.md",
        "LICENSE",
        "Makefile",
        "PROGRESS.md",
        "README.md",
        "pyproject.toml",
        "uv.lock",
    }
)

# Paths that are never allowed anywhere, even inside an allowed directory.
FORBIDDEN_NAMES = frozenset({".DS_Store", "scratch.py", "tmp.py", "test.py"})


def violations(paths: list[str]) -> list[str]:
    """Return a message for every path that breaks the layout rules."""
    found: list[str] = []
    for raw in paths:
        path = PurePosixPath(raw)
        if path.name in FORBIDDEN_NAMES:
            found.append(f"{raw}: forbidden file name")
        elif len(path.parts) == 1:
            if raw not in ALLOWED_ROOT_FILES:
                found.append(f"{raw}: not an allowed root file")
        elif path.parts[0] not in ALLOWED_DIRS:
            found.append(f"{raw}: outside allowed directories")
    return found


def candidate_files() -> list[str]:
    """List tracked files plus untracked files that git does not ignore."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    """Print violations and return a process exit code."""
    found = violations(candidate_files())
    for message in found:
        print(message, file=sys.stderr)
    if found:
        print(
            "Move throwaway files to .scratch/, or propose a new directory in PROGRESS.md.",
            file=sys.stderr,
        )
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

"""Reject commit messages that are not conventional commits.

Called by the commit-msg hook with the path to the message file.
"""

import re
import sys
from pathlib import Path

TYPES = ("feat", "fix", "chore", "docs", "test", "refactor", "perf", "ci", "build")
PATTERN = re.compile(rf"^({'|'.join(TYPES)})(\([a-z0-9-]+\))?!?: \S.{{0,70}}$")


def is_conventional(subject: str) -> bool:
    """Return whether a commit subject line follows the convention."""
    return bool(PATTERN.match(subject)) or subject.startswith(("Merge ", "Revert "))


def main() -> int:
    """Check the subject line of the message file given as the first argument."""
    subject = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()[0]
    if is_conventional(subject):
        return 0
    print(f"Not a conventional commit subject: {subject!r}", file=sys.stderr)
    print(f"Expected '<type>(<scope>): <summary>' with type in {TYPES}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

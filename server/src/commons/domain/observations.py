"""What hooks observe about a task (decision 0016)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

# Mirrors the Postgres enum task_event_kind.
TaskEventKind = Literal["test_run", "file_change"]

# A runner must stand alone as a word at a command position: `cat pytest.ini` doesn't count.
_START = r"(?:^|[\s(/])"
_END = r"(?=\s|$|\))"

# Commands that run tests (DESIGN.md §5). Users add more with `test_runners` in the config.
BUILTIN_TEST_RUNNERS: tuple[str, ...] = tuple(
    _START + body + _END
    for body in (
        r"pytest",
        r"python[0-9.]* -m (?:pytest|unittest)",
        r"(?:npm|pnpm|yarn|bun) (?:run )?test",
        r"cargo (?:test|nextest)",
        r"go test",
        r"(?:jest|vitest|mocha|rspec|phpunit|tox|nox|ctest)",
        r"(?:mvn|gradle|gradlew)\b[^|;&]*\btest",
        r"(?:dotnet|mix|deno|swift) test",
        r"make test",
    )
)

# Installing a test runner is not running tests.
_INSTALL = re.compile(
    r"\b(?:pip[0-9.]* install|uv (?:pip install|add|tool install)|npm (?:i|install|add)|"
    r"(?:yarn|pnpm|bun) add|brew install|apt(?:-get)? install|cargo install|go (?:get|install))\b"
)
_SEGMENTS = re.compile(r"&&|\|\||[;|\n]")

_EXIT_CODE = re.compile(r"^Exit code (\d+)")


@dataclass(frozen=True)
class ToolUse:
    """One tool call from a PostToolUse or PostToolUseFailure hook."""

    session_id: str
    tool_name: str
    command: str | None  # Bash only
    file_path: str | None  # Edit/Write/MultiEdit only
    new_text: str | None  # text an edit introduced
    exit_code: int | None  # Bash only; None if unknown (e.g. interrupted)
    old_text: str | None = None  # text an edit replaced: evidence of acting on a fix too


def is_test_command(command: str, extra_patterns: Sequence[str] = ()) -> bool:
    """Return whether any segment of a shell command runs tests."""
    patterns = (*BUILTIN_TEST_RUNNERS, *extra_patterns)
    return any(
        not _INSTALL.search(segment) and any(re.search(p, segment.strip()) for p in patterns)
        for segment in _SEGMENTS.split(command)
    )


def exit_code_from_error(error: str) -> int | None:
    """Parse the exit code from Claude Code's Bash failure text ("Exit code 3 ..."), else None."""
    match = _EXIT_CODE.match(error)
    return int(match.group(1)) if match else None

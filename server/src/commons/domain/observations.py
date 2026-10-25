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
_SEPARATORS = re.compile(r"(&&|\|\||[;|\n])")

# Runner summary lines, checked failures first. A pipe (`pytest | tail`) reports its last
# command's exit code, so when the shell says 0 the summary is the only evidence left.
_FAILED_SUMMARIES: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b\d+ (?:failed|errors?)\b[^\n]*\bin [\d.]+s"), 1),  # pytest
    (re.compile(r"\bno tests ran in [\d.]+s"), 5),  # pytest
    (re.compile(r"^FAILED \((?:failures|errors)=", re.MULTILINE), 1),  # unittest
    (re.compile(r"^test result: FAILED\b", re.MULTILINE), 101),  # cargo
    (re.compile(r"^(?:--- )?FAIL\b", re.MULTILINE), 1),  # go test
    (re.compile(r"^\s*Tests:?\s[^\n]*\b\d+ failed", re.MULTILINE), 1),  # jest, vitest
)
_PASSED_SUMMARIES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d+ passed\b[^\n]*\bin [\d.]+s"),  # pytest
    re.compile(r"^OK\b", re.MULTILINE),  # unittest
    re.compile(r"^test result: ok\b", re.MULTILINE),  # cargo
    re.compile(r"^ok\s", re.MULTILINE),  # go test
    re.compile(r"^\s*Tests:?\s[^\n]*\b\d+ passed", re.MULTILINE),  # jest, vitest
)

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
    output: str | None = None  # Bash only; read for the runner's summary, never stored


def is_test_command(command: str, extra_patterns: Sequence[str] = ()) -> bool:
    """Return whether any segment of a shell command runs tests."""
    return any(_runs_tests(segment, extra_patterns) for segment in _SEGMENTS.split(command))


def _runs_tests(segment: str, extra_patterns: Sequence[str]) -> bool:
    patterns = (*BUILTIN_TEST_RUNNERS, *extra_patterns)
    return not _INSTALL.search(segment) and any(re.search(p, segment.strip()) for p in patterns)


def observed_exit_code(
    command: str, exit_code: int | None, output: str, extra_patterns: Sequence[str] = ()
) -> int | None:
    """Return the test run's exit code, trusting a runner summary over a 0 the shell masked.

    Agents usually run `pytest -q 2>&1 | tail -20`, whose exit code is tail's. A failure the
    shell reported is kept; a masked 0 with no summary is unknown (None), never a pass.
    """
    if exit_code != 0:
        return exit_code
    for pattern, code in _FAILED_SUMMARIES:
        if pattern.search(output):
            return code
    if any(pattern.search(output) for pattern in _PASSED_SUMMARIES):
        return 0
    return None if _masks_exit_code(command, extra_patterns) else 0


def _masks_exit_code(command: str, extra_patterns: Sequence[str]) -> bool:
    """Whether a command after the last test runner decides the exit code (`|`, `||`, `;`)."""
    parts = _SEPARATORS.split(command)
    segments, separators = parts[0::2], parts[1::2]
    runs = [i for i, segment in enumerate(segments) if _runs_tests(segment, extra_patterns)]
    if not runs:
        return False
    after = set(separators[runs[-1] :]) - {"&&"}
    if "pipefail" in command:
        after.discard("|")
    return bool(after)


def exit_code_from_error(error: str) -> int | None:
    """Parse the exit code from Claude Code's Bash failure text ("Exit code 3 ..."), else None."""
    match = _EXIT_CODE.match(error)
    return int(match.group(1)) if match else None

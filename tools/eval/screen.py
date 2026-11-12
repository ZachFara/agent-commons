"""The difficulty screen for a new GitHub task (docs/eval/README.md, "GitHub tasks").

Every task of the first batch passed with no help on Sonnet and Haiku, so it can't show what the
commons adds. A new task is kept only if Sonnet's `none` arm, with no help, either fails at least
one of its runs or needs at least `SCREEN_TOKEN_FACTOR` times the tokens of the smoke-test tasks.
`agent_eval.py screen` runs the sessions; the rule here is pure.
"""

import statistics
from dataclasses import dataclass, replace
from typing import Any, Literal

SCREEN_TOKEN_FACTOR = 2.0
SCREEN_RUNS = 3
SCREEN_CONDITION = "github-screen"  # its own condition, so no verdict ever reads its sessions
SCREEN_MODEL = "sonnet"
SCREEN_ARM = "none"
# The smoke-test runs of the first GitHub tasks: their none-arm Sonnet sessions are the reference.
SMOKE_CONDITIONS = ("github-same10", "github-pilot")
NO_REFERENCE = (
    f"no reference: the ledger has no counted {SCREEN_ARM}-arm {SCREEN_MODEL} session of "
    f"{' or '.join(SMOKE_CONDITIONS)}, and the screen won't guess one"
)

Row = dict[str, Any]


@dataclass(frozen=True)
class ScreenResult:
    """A task's screen: PASS, FAIL, or REFUSED, why, and the numbers it was decided on."""

    verdict: Literal["PASS", "FAIL", "REFUSED"]
    reason: str  # which branch passed, why it failed, or why the screen refused
    runs: int
    failures: int  # runs whose tests didn't pass
    median_tokens: float | None  # of the runs' tokens in plus out; None with no runs
    reference_median_tokens: float | None


def session_tokens(row: Row) -> int:
    """All of a session's tokens: input (cache creation and reads included) and output."""
    return int(row.get("tokens_in") or 0) + int(row.get("tokens_out") or 0)


def reference_median(rows: list[Row]) -> float | None:
    """Return the median tokens of the smoke tests' none-arm Sonnet sessions, or None if none.

    `rows` are the ledger's counted session lines (`agent_eval.counted`).
    """
    tokens = [
        session_tokens(row)
        for row in rows
        if row.get("condition") in SMOKE_CONDITIONS
        and row.get("arm", "commons") == SCREEN_ARM  # a line from before arms ran the commons
        and row.get("model") == SCREEN_MODEL
    ]
    return statistics.median(tokens) if tokens else None


def screen(runs: list[Row], reference_median_tokens: float | None) -> ScreenResult:
    """Screen a task on its runs: Sonnet's none-arm sessions of it, as ledger lines.

    It passes if a run failed (`tests_pass` false), or if the runs' median tokens are at least
    `SCREEN_TOKEN_FACTOR` times the reference median. With no reference, or no runs, it refuses.
    """
    failures = sum(not row["tests_pass"] for row in runs)
    median = statistics.median(session_tokens(row) for row in runs) if runs else None
    refused = ScreenResult(
        "REFUSED", NO_REFERENCE, len(runs), failures, median, reference_median_tokens
    )
    if reference_median_tokens is None:
        return refused
    if median is None:
        return replace(refused, reason="no runs to screen")
    tokens = (
        f"median {median:,.0f} tokens is {median / reference_median_tokens:.2f}x the reference "
        f"median {reference_median_tokens:,.0f}"
    )
    costly = median >= SCREEN_TOKEN_FACTOR * reference_median_tokens
    passed = [
        f"Sonnet failed {failures} of {len(runs)} runs with no help" if failures else "",
        f"{tokens}, at least {SCREEN_TOKEN_FACTOR}x" if costly else "",
    ]
    if any(passed):
        return replace(refused, verdict="PASS", reason="; ".join(b for b in passed if b))
    reason = f"all {len(runs)} runs passed with no help, and {tokens}, under {SCREEN_TOKEN_FACTOR}x"
    return replace(refused, verdict="FAIL", reason=reason)


def record_line(result: ScreenResult, date: str) -> str:
    """Return the comment line a passing task carries in `github_tasks.py`."""
    return (
        f"# Screen {date}: {result.verdict}, {result.failures} of {result.runs} runs failed, "
        f"median {result.median_tokens or 0:.0f} tokens, "
        f"reference median {result.reference_median_tokens or 0:.0f}"
    )


def report(scenario: str, result: ScreenResult, date: str) -> list[str]:
    """Return what `agent_eval.py screen` prints: the verdict, its reason, and the numbers."""
    lines = [f"{result.verdict} {scenario}: {result.reason}"]
    if result.verdict == "REFUSED":
        return lines
    lines.append(
        f"  runs {result.runs}, failed {result.failures}, "
        f"median tokens {result.median_tokens or 0:,.0f}, "
        f"reference median {result.reference_median_tokens or 0:,.0f}, "
        f"factor {SCREEN_TOKEN_FACTOR}"
    )
    if result.verdict == "PASS":
        lines += [
            "  add this line to the task's comment in github_tasks.py:",
            record_line(result, date),
        ]
    return lines

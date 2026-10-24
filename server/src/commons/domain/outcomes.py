from typing import Literal

# Mirrors the Postgres enum outcome_kind.
OutcomeKind = Literal[
    "tests_passed",
    "tests_failed",
    "error_resolved",
    "error_persisted",
    "user_accepted",
    "user_rejected",
    "abandoned",
    "unknown",
]

# Outcomes that count as the entry having worked, for success rates (DESIGN.md §7).
SUCCESS_OUTCOMES: tuple[OutcomeKind, ...] = ("tests_passed", "error_resolved", "user_accepted")

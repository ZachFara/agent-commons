"""Reporter reliability (DESIGN.md §3, decision 0017): how often self-reports match observation."""

from collections.abc import Sequence

from commons.domain.outcomes import SUCCESS_OUTCOMES, OutcomeKind

OBSERVED_OUTCOMES = frozenset({"tests_passed", "tests_failed"})  # what hooks can see

MIN_PAIRS = 5


def reliability(pairs: Sequence[tuple[OutcomeKind, OutcomeKind]]) -> float | None:
    """Share of (claimed, observed) pairs that agree on success; None below MIN_PAIRS.

    Hooks observe only tests_passed/tests_failed, so agreement is on whether the entry
    worked, not on the exact label. Claims of `unknown`, and pairs where the hooks observed
    nothing, say nothing and are skipped.
    """
    judged = [
        (claimed, observed)
        for claimed, observed in pairs
        if claimed != "unknown" and observed in OBSERVED_OUTCOMES
    ]
    if len(judged) < MIN_PAIRS:
        return None
    agree = sum((c in SUCCESS_OUTCOMES) == (o in SUCCESS_OUTCOMES) for c, o in judged)
    return agree / len(judged)

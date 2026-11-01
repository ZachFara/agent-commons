import pytest

from commons.domain.entries import EntryStatus
from commons.domain.reviews import ReviewDecision, ReviewSource, review_state

Reviews = list[tuple[ReviewDecision, ReviewSource]]


@pytest.mark.parametrize(
    ("reviews", "expected"),
    [
        ([], ("pending", False)),
        ([("approve", "agent")], ("approved", False)),
        ([("approve", "agent"), ("reject", "agent")], ("rejected", False)),
        ([("reject", "agent"), ("approve", "human")], ("approved", True)),
        ([("approve", "human"), ("reject", "agent")], ("approved", True)),
        ([("approve", "human"), ("reject", "human")], ("rejected", True)),
    ],
)
def test_status_follows_latest_human_else_latest_agent(
    reviews: Reviews, expected: tuple[EntryStatus, bool]
) -> None:
    assert review_state(reviews) == expected


@pytest.mark.parametrize(
    ("reviews", "expected"),
    [
        # Audit 2026-10-31: retiring and restoring isn't a review of the content.
        ([("approve", "auto"), ("retire", "human"), ("restore", "human")], ("approved", False)),
        ([("approve", "agent"), ("retire", "human")], ("retired", False)),
        ([("approve", "human"), ("retire", "human"), ("restore", "human")], ("approved", True)),
        ([("approve", "auto"), ("reopen", "auto")], ("pending", False)),
        ([("approve", "human"), ("reopen", "auto"), ("approve", "agent")], ("approved", False)),
    ],
)
def test_only_a_human_approve_or_reject_counts_as_human_reviewed(
    reviews: Reviews, expected: tuple[EntryStatus, bool]
) -> None:
    assert review_state(reviews) == expected

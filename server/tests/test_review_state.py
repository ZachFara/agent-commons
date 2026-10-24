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

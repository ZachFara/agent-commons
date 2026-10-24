import pytest

from commons.domain.outcomes import OutcomeSample, Usage
from commons.ranking.score import score


def sample(
    *,
    success: bool = True,
    used: Usage = "yes",
    observed: bool = True,
    reliability: float | None = None,
    age_days: float = 1.0,
) -> OutcomeSample:
    return OutcomeSample(success, used, observed, reliability, age_days)


@pytest.mark.parametrize(
    ("samples", "expected"),
    [
        ([], 0.5),
        ([sample()], 2 / 3),  # (1 + 1) / (2 + 1)
        ([sample(success=False)], 1 / 3),
        ([sample(observed=False)], 1.5 / 2.5),  # unknown reliability weighs 0.5
        ([sample(observed=False, reliability=0.9)], 1.9 / 2.9),
        ([sample(observed=True, reliability=0.1)], 2 / 3),  # observations aren't discounted
        ([sample(used="partial")], 1.5 / 2.5),
        ([sample(age_days=90)], 2 / 3),
        ([sample(age_days=180)], 1.5 / 2.5),  # one half-life past 90 days
        ([sample(used="no")], 0.5),
        ([sample(), sample(), sample(success=False)], 3 / 5),
    ],
)
def test_score(samples: list[OutcomeSample], expected: float) -> None:
    assert score(samples) == pytest.approx(expected)

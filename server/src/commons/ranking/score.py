"""Ranking v0 (DESIGN.md §7, decision 0017): Bayesian success rate with weighted evidence."""

from collections.abc import Iterable

from commons.domain.outcomes import OutcomeSample, Usage

PRIOR_MEAN = 0.5
PRIOR_WEIGHT = 2.0  # the prior counts as two outcomes
UNKNOWN_RELIABILITY = 0.5
FULL_WEIGHT_DAYS = 90.0
HALF_LIFE_DAYS = 90.0
_USE_WEIGHT: dict[Usage, float] = {"yes": 1.0, "partial": 0.5, "no": 0.0}


def weight(sample: OutcomeSample) -> float:
    """Return source * use * age weight. Only self-reports are discounted by reliability."""
    if sample.observed:
        source = 1.0
    elif sample.reporter_reliability is None:
        source = UNKNOWN_RELIABILITY
    else:
        source = sample.reporter_reliability
    age = 1.0
    if sample.age_days > FULL_WEIGHT_DAYS:
        age = 0.5 ** ((sample.age_days - FULL_WEIGHT_DAYS) / HALF_LIFE_DAYS)
    return source * _USE_WEIGHT[sample.used] * age


def score(samples: Iterable[OutcomeSample]) -> float:
    """Posterior mean success rate; 0.5 with no evidence."""
    successes = PRIOR_MEAN * PRIOR_WEIGHT
    total = PRIOR_WEIGHT
    for sample in samples:
        w = weight(sample)
        successes += w * sample.success
        total += w
    return successes / total

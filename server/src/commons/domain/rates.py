"""Per-key rate limits (decision 0029): how many calls of each kind a key may make."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from commons.domain.errors import InvalidInputError

LimitedAction = Literal["propose", "search", "report"]


@dataclass(frozen=True)
class Limit:
    """At most `calls` per `window`."""

    calls: int
    window: timedelta


DEFAULT_LIMITS: dict[LimitedAction, Limit] = {
    "propose": Limit(30, timedelta(days=1)),
    "search": Limit(600, timedelta(hours=1)),
    "report": Limit(600, timedelta(hours=1)),
}


def limits_from_environment(environ: Mapping[str, str]) -> dict[LimitedAction, Limit]:
    """Apply COMMONS_RATE_LIMIT_PROPOSE, _SEARCH and _REPORT (calls per the same window)."""
    limits = dict(DEFAULT_LIMITS)
    for action, limit in DEFAULT_LIMITS.items():
        raw = environ.get(f"COMMONS_RATE_LIMIT_{action.upper()}")
        if raw is None:
            continue
        if not raw.strip().isdigit() or int(raw) < 1:
            msg = f"COMMONS_RATE_LIMIT_{action.upper()} must be a positive whole number."
            raise InvalidInputError(msg)
        limits[action] = Limit(int(raw), limit.window)
    return limits

"""Trust levels and sampled review (decision 0028).

TL0: everything waits for a human. TL1 and TL2: new entries are approved provisionally,
shown as unreviewed, and a sample goes to the queue. The thresholds are a starting point.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal

from commons.domain.errors import InvalidInputError

Role = Literal["contributor", "reviewer", "admin"]
ROLES: tuple[Role, ...] = ("contributor", "reviewer", "admin")
_ROLES: dict[str, Role] = {role: role for role in ROLES}
TrustLevel = Literal[0, 1, 2]


@dataclass(frozen=True)
class TrustSettings:
    """Thresholds for each level, and how often provisional entries are sampled."""

    tl1_approved: int = 5
    tl1_max_duplicate_rate: float = 0.2
    tl2_approved: int = 20
    tl2_min_reliability: float = 0.8
    tl1_sample_one_in: int = 4
    tl2_sample_one_in: int = 10

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "TrustSettings":
        """Override any threshold with COMMONS_TRUST_<NAME> (e.g. COMMONS_TRUST_TL1_APPROVED)."""
        values: dict[str, Any] = {}
        for name, default in cls().__dict__.items():
            raw = environ.get(f"COMMONS_TRUST_{name.upper()}")
            if raw is None:
                continue
            try:
                values[name] = type(default)(raw)
            except ValueError:
                msg = f"COMMONS_TRUST_{name.upper()} must be a number."
                raise InvalidInputError(msg) from None
        return replace(cls(), **values)


@dataclass(frozen=True)
class ContributorHistory:
    """What a reporter's own record says about them."""

    approved: int
    proposed: int
    harmful_rejections: int  # rejected by a human with risk findings, or purged
    duplicates: int
    reliability: float | None


def trust_level(history: ContributorHistory, settings: TrustSettings) -> TrustLevel:
    """Return TL0, TL1 or TL2 for the reporter's record."""
    if history.harmful_rejections or history.approved < settings.tl1_approved:
        return 0
    if (
        history.proposed
        and history.duplicates / history.proposed >= settings.tl1_max_duplicate_rate
    ):
        return 0
    reliable = history.reliability is not None and (
        history.reliability >= settings.tl2_min_reliability
    )
    return 2 if history.approved >= settings.tl2_approved and reliable else 1


def sample_one_in(level: TrustLevel, settings: TrustSettings) -> int | None:
    """How many provisional entries per sampled one at this level; None when none are trusted."""
    return {0: None, 1: settings.tl1_sample_one_in, 2: settings.tl2_sample_one_in}[level]


def may_hold(role: Role, scope: str) -> bool:
    """Whether a reporter with this role may be issued a key with this scope."""
    return scope == "agent" or role == "admin" or (scope == "review" and role == "reviewer")


def validate_role(role: str) -> Role:
    """Return the role, or raise if it isn't one."""
    if role not in _ROLES:
        msg = "role must be contributor, reviewer or admin."
        raise InvalidInputError(msg)
    return _ROLES[role]

"""API keys (decision 0032): scopes, durations, and who a key belongs to."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, get_args
from uuid import UUID

from commons.domain.errors import InvalidInputError
from commons.domain.reporters import PrivacyDefault

KeyScope = Literal["agent", "review", "admin"]
KEY_SCOPES: tuple[KeyScope, ...] = get_args(KeyScope)
KeyProblem = Literal["unknown", "revoked", "expired"]

_DURATION = re.compile(r"(\d+)([mhd])")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


def parse_duration(text: str) -> timedelta:
    """Parse '30m', '24h' or '90d'."""
    match = _DURATION.fullmatch(text.strip())
    if not match:
        msg = f"Duration must look like 30m, 24h or 90d, not {text!r}."
        raise InvalidInputError(msg)
    return timedelta(**{_UNITS[match[2]]: int(match[1])})


@dataclass(frozen=True)
class KeyOptions:
    """Optional settings for a new key."""

    label: str | None = None
    expires_in: timedelta | None = None
    test: bool = False


@dataclass(frozen=True)
class KeyHolder:
    """The reporter a presented key belongs to, and what the key may do."""

    reporter_id: UUID
    key_id: UUID
    scope: KeyScope
    privacy_default: PrivacyDefault
    test: bool

    @property
    def is_admin(self) -> bool:
        """Whether the key may do everything, including key management."""
        return self.scope == "admin"

    def allows(self, need: KeyScope) -> bool:
        """Admin keys may do everything; other keys only their own scope."""
        return self.scope in {need, "admin"}

    def scopes(self) -> list[KeyScope]:
        """Every scope the key may act in."""
        return list(KEY_SCOPES) if self.is_admin else [self.scope]


@dataclass(frozen=True)
class KeyCheck:
    """The result of presenting a key: its holder, or why it was refused."""

    holder: KeyHolder | None
    problem: KeyProblem | None

    def message(self) -> str:
        """Return what to tell the caller when the key was refused."""
        return {
            "revoked": "key revoked: ask the admin for a new one",
            "expired": "key expired: ask the admin for a new one",
        }.get(self.problem or "", "unknown API key")


@dataclass(frozen=True)
class KeyInfo:
    """One key as listings show it (never the key itself)."""

    key_id: UUID
    pseudonym: str
    prefix: str | None
    scope: KeyScope
    label: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    test: bool

    def handle(self) -> str:
        """How commands refer to the key: its visible prefix, else its id's first 8."""
        return self.prefix or str(self.key_id)[:8]

    def state(self, now: datetime) -> str:
        """active, revoked, expired, or 'expires <date>' during a rotation's grace period."""
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= now:
            return "expired"
        return "active"


@dataclass(frozen=True)
class NewKey:
    """A freshly minted key: shown once, then only its hash is kept."""

    key: str
    info: KeyInfo

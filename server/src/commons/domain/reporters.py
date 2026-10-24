import re
from dataclasses import dataclass
from typing import Literal, get_args
from uuid import UUID

from commons.domain.errors import InvalidInputError

_PSEUDONYM = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,39}")

# A reporter's default privacy mode (DESIGN.md §6). Mirrors the Postgres enum.
PrivacyDefault = Literal["local", "entry", "full_trace"]
PRIVACY_DEFAULTS: tuple[PrivacyDefault, ...] = get_args(PrivacyDefault)


def validate_pseudonym(pseudonym: str) -> str:
    """Return the pseudonym if it is a handle, not an email or free text (DESIGN.md §3)."""
    if "@" in pseudonym:
        msg = "Pseudonym looks like an email address. Use a handle that doesn't identify you."
        raise InvalidInputError(msg)
    if not _PSEUDONYM.fullmatch(pseudonym):
        msg = "Pseudonym must be 3-40 characters: letters, digits, '-' or '_'."
        raise InvalidInputError(msg)
    return pseudonym


@dataclass(frozen=True)
class KeyHolder:
    """The reporter a hosted-mode API key belongs to (decision 0018)."""

    reporter_id: UUID
    is_admin: bool
    privacy_default: PrivacyDefault

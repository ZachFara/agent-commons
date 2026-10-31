"""Entry lifecycle (decision 0026): edits, supersession, retirement."""

import re
from dataclasses import dataclass

from commons.domain.errors import InvalidInputError

_NUMBERS = re.compile(r"\d+")


@dataclass(frozen=True)
class FixedIn:
    """The library release that fixed the problem an entry describes."""

    library: str
    version: str

    @classmethod
    def parse(cls, text: str) -> "FixedIn":
        """Parse 'library==version' (or 'library version')."""
        library, _, version = text.replace("==", " ").strip().partition(" ")
        if not library or not _NUMBERS.search(version):
            msg = "fixed_in must look like library==1.2.3."
            raise InvalidInputError(msg)
        return cls(library.strip().lower(), version.strip())

    def as_json(self) -> dict[str, str]:
        """Return the stored form."""
        return {"library": self.library, "version": self.version}


def is_older(version: object, than: str) -> bool:
    """Whether `version` sorts before `than`, comparing their numbers left to right."""
    if not isinstance(version, str) or not _NUMBERS.search(version):
        return False
    mine = [int(n) for n in _NUMBERS.findall(version)]
    fixed = [int(n) for n in _NUMBERS.findall(than)]
    width = max(len(mine), len(fixed))
    return mine + [0] * (width - len(mine)) < fixed + [0] * (width - len(fixed))


def shown_despite_fix(fixed_in: object, asked_libraries: object) -> bool:
    """Whether a retired entry still helps this caller: they run a version before the fix."""
    if not isinstance(fixed_in, dict) or not isinstance(asked_libraries, dict):
        return False
    versions = {str(name).lower(): v for name, v in asked_libraries.items()}
    asked = versions.get(str(fixed_in.get("library", "")).lower())
    return is_older(asked, str(fixed_in.get("version", "")))


def require_reason(reason: str | None) -> str:
    """Lifecycle changes always say why."""
    if not (reason or "").strip():
        msg = "Say why (reason)."
        raise InvalidInputError(msg)
    return (reason or "").strip()

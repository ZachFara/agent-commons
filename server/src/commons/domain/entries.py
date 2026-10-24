import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from commons.domain.errors import InvalidInputError

# Mirrors the Postgres enum entry_status.
EntryStatus = Literal["pending", "approved", "rejected", "retired"]

MAX_DOMAINS = 16  # cq's limit (decision 0008)
MAX_DOMAIN_LENGTH = 64
# Environment keys agents fill in; at least one is required, each with versions.
ENVIRONMENT_KEYS = ("language", "libraries", "tool")
_VERSIONED_KEYS = ("language", "tool")
_HAS_VERSION = re.compile(r"\d")


@dataclass(frozen=True)
class Proposal:
    """A proposed entry, as the agent submits it (DESIGN.md §4 propose)."""

    symptom: str
    environment: dict[str, object]
    cause: str
    fix: str
    domains: tuple[str, ...]
    repro: str | None
    task_id: UUID


def validate_proposal(proposal: Proposal) -> None:
    """Raise InvalidInputError naming the first rule the proposal breaks."""
    for name in ("symptom", "cause", "fix"):
        if not getattr(proposal, name).strip():
            msg = f"{name} must not be empty."
            raise InvalidInputError(msg)
    _validate_domains(proposal.domains)
    _validate_environment(proposal.environment)


def _validate_domains(domains: tuple[str, ...]) -> None:
    if not 1 <= len(domains) <= MAX_DOMAINS:
        msg = f"domains must have 1 to {MAX_DOMAINS} items."
        raise InvalidInputError(msg)
    if any(not 1 <= len(d.strip()) <= MAX_DOMAIN_LENGTH for d in domains):
        msg = f"each domain must be 1 to {MAX_DOMAIN_LENGTH} characters."
        raise InvalidInputError(msg)


def _validate_environment(environment: dict[str, object]) -> None:
    """Versions are required (DESIGN.md §3): every library pinned, language and tool versioned."""
    libraries = environment.get("libraries", {})
    if not isinstance(libraries, dict) or not all(
        isinstance(v, str) and _HAS_VERSION.search(v) for v in libraries.values()
    ):
        msg = 'environment.libraries must map each library to its version, e.g. {"jinja2": "3.1"}.'
        raise InvalidInputError(msg)
    for key in _VERSIONED_KEYS:
        value = environment.get(key)
        if value is not None and not (isinstance(value, str) and _HAS_VERSION.search(value)):
            msg = f'environment.{key} must include a version, e.g. "python 3.12".'
            raise InvalidInputError(msg)
    if not any(environment.get(key) for key in ENVIRONMENT_KEYS):
        msg = "environment must include at least one of language, libraries, or tool, versioned."
        raise InvalidInputError(msg)

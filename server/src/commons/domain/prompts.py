"""The agent-facing text a server served, as one hashed version (decision MIT-566)."""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

# Mirrors the Postgres enum `prompt_kind`: text the MCP server serves, or text the installed
# hook client shows (MIT-609).
PromptKind = Literal["server", "hooks"]


@dataclass(frozen=True)
class PromptVersion:
    """Every surface the agent reads, {surface: text}, and the SHA-256 that names them.

    Surfaces hold templates, never formatted text, so ids and counts never change the hash.
    A hook version a hosted server doesn't recognize has no surfaces and only the 12
    characters the client sent as its hash.
    """

    label: str  # the variant name and the extras switched on, e.g. `baseline:w-a0...+record`
    surfaces: dict[str, str] | None
    hash: str  # 64 hex characters; shown as the first 12
    kind: PromptKind = "server"


def prompt_version(
    label: str, surfaces: dict[str, str], kind: PromptKind = "server"
) -> PromptVersion:
    """Hash canonical JSON of `surfaces`, so their order never matters and every character does."""
    canonical = json.dumps(surfaces, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return PromptVersion(label=label, surfaces=dict(surfaces), hash=digest, kind=kind)

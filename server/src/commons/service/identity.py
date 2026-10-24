"""Local identity: which reporter this machine acts as.

`commons init` writes the pseudonym to a small JSON config; the MCP server reads it to know
who is calling in local mode. Hosted mode identifies reporters by API key instead.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from commons.domain.reporters import PrivacyDefault, validate_pseudonym
from commons.store.connection import Connection
from commons.store.reporters import upsert_reporter


@dataclass(frozen=True)
class InitResult:
    """What `init` did, for the CLI to report."""

    reporter_id: UUID
    config_path: Path


def config_path() -> Path:
    """Return $COMMONS_CONFIG, else $XDG_CONFIG_HOME/agent-commons/config.json."""
    if explicit := os.environ.get("COMMONS_CONFIG"):
        return Path(explicit)
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "agent-commons" / "config.json"


def init_reporter(conn: Connection, pseudonym: str, privacy_default: PrivacyDefault) -> InitResult:
    """Create or reuse the reporter and record it as this machine's identity."""
    validate_pseudonym(pseudonym)
    reporter_id = upsert_reporter(conn, pseudonym, privacy_default)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pseudonym": pseudonym}, indent=2) + "\n", encoding="utf-8")
    return InitResult(reporter_id=reporter_id, config_path=path)

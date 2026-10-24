"""Files kept on this machine only: hook logs and per-task spools (decision 0016)."""

import os
from pathlib import Path


def state_dir() -> Path:
    """Return $COMMONS_STATE_DIR, else $XDG_STATE_HOME/agent-commons (~/.local/state)."""
    if explicit := os.environ.get("COMMONS_STATE_DIR"):
        return Path(explicit)
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "agent-commons"

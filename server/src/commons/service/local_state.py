"""Files kept on this machine only: hook logs and per-task spools (decision 0016)."""

import json
import os
from pathlib import Path
from uuid import UUID


def state_dir() -> Path:
    """Return $COMMONS_STATE_DIR, else $XDG_STATE_HOME/agent-commons (~/.local/state)."""
    if explicit := os.environ.get("COMMONS_STATE_DIR"):
        return Path(explicit)
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "agent-commons"


def spool_path(task_id: UUID) -> Path:
    """Return the task's local spool: edit text and commands, never sent anywhere."""
    return state_dir() / "tasks" / f"{task_id}.jsonl"


def append_spool(task_id: UUID, record: dict[str, object]) -> None:
    """Append one record to the task's spool, readable only by this user."""
    path = spool_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as spool:
        spool.write(json.dumps(record) + "\n")

"""Files kept on this machine only: hook logs and per-task spools (decision 0016)."""

import hashlib
import json
import os
import time
from pathlib import Path


def state_dir() -> Path:
    """Return $COMMONS_STATE_DIR, else $XDG_STATE_HOME/agent-commons (~/.local/state)."""
    if explicit := os.environ.get("COMMONS_STATE_DIR"):
        return Path(explicit)
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "agent-commons"


def spool_path(session_id: str) -> Path:
    """Return the session's local spool: edit text and commands, never sent anywhere.

    Keyed by a hash of the session id, which works without a task id (hosted mode) and
    can't be used to escape the directory.
    """
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return state_dir() / "sessions" / f"{name}.jsonl"


def append_spool(session_id: str, record: dict[str, object]) -> None:
    """Append one record to the task's spool, readable only by this user."""
    path = spool_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as spool:
        spool.write(json.dumps(record) + "\n")


def read_spool(session_id: str) -> list[dict[str, object]]:
    """Return the session's spooled records, oldest first; empty if there are none."""
    path = spool_path(session_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


SPOOL_RETENTION_DAYS = 30


def prune_spools(now: float | None = None) -> int:
    """Delete spools untouched for SPOOL_RETENTION_DAYS; return how many were removed."""
    directory = state_dir() / "sessions"
    if not directory.exists():
        return 0
    cutoff = (now if now is not None else time.time()) - SPOOL_RETENTION_DAYS * 86400
    removed = 0
    for path in directory.glob("*.jsonl"):
        if path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed

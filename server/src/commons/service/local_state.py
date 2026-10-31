"""Files kept on this machine only: hook logs and per-task spools (decision 0016)."""

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from commons.domain.usage import ArtifactFrequencies


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
    """Append one record, stamped with when it happened, to the task's spool (this user only)."""
    record = {**record, "at": datetime.now(UTC).isoformat()}
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


FREQUENCIES_MAX_AGE = 86400  # seconds: hooks fetch artifact counts once a day (decision 0036)


def cached_frequencies(now: float | None = None) -> ArtifactFrequencies | None:
    """Return the artifact counts fetched in the last day, if any."""
    path = state_dir() / "artifact-frequencies.json"
    if not path.exists():
        return None
    age = (now if now is not None else time.time()) - path.stat().st_mtime
    if age > FREQUENCIES_MAX_AGE:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ArtifactFrequencies(int(data["entries"]), dict(data["counts"]))
    except (ValueError, KeyError, TypeError):
        return None


def save_frequencies(frequencies: ArtifactFrequencies) -> None:
    """Cache artifact counts for a day."""
    path = state_dir() / "artifact-frequencies.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"entries": frequencies.entries, "counts": dict(frequencies.counts)}
    path.write_text(json.dumps(data), encoding="utf-8")

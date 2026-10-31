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
    for path in [*directory.glob("*.jsonl"), *directory.glob("*.store.json")]:
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


def _store_path(session_id: str) -> Path:
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return state_dir() / "sessions" / f"{name}.store.json"


def bind_store(session_id: str, server: tuple[str, str] | None) -> None:
    """Record which store this session uses, at its start (decision 0031).

    Later hooks for the session go there even if the machine's config changes, so a
    session's hooks and its MCP server never point at different stores.
    """
    path = _store_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {"store": "local"} if server is None else {"store": server[0], "key": server[1]}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(data, file)


def bound_store(session_id: str) -> dict[str, str] | None:
    """Return the store the session was bound to at start, or None for older sessions."""
    path = _store_path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("store"), str) else None


def active_sessions(since_seconds: float = 3600, now: float | None = None) -> list[str]:
    """Return the stores of sessions with hook activity in the last hour ("local" or a URL)."""
    directory = state_dir() / "sessions"
    if not directory.exists():
        return []
    cutoff = (now if now is not None else time.time()) - since_seconds
    stores = []
    for spool in directory.glob("*.jsonl"):
        if spool.stat().st_mtime < cutoff:
            continue
        bound = directory / spool.name.replace(".jsonl", ".store.json")
        try:
            data = json.loads(bound.read_text(encoding="utf-8"))
            stores.append(str(data.get("store", "unknown")))
        except (FileNotFoundError, json.JSONDecodeError):
            stores.append("unknown")
    return stores

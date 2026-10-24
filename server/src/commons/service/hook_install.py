"""Merge Agent Commons hooks into Claude Code's settings.json without touching other hooks."""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# (Claude Code event, tool matcher or None for all, `commons hook` event, timeout in seconds)
HOOK_EVENTS: tuple[tuple[str, str | None, str, int], ...] = (
    ("SessionStart", None, "session-start", 30),
    ("PostToolUse", "Bash|Edit|Write|MultiEdit", "post-tool-use", 30),
    ("PostToolUseFailure", "Bash", "post-tool-use-failure", 30),
    ("Stop", None, "stop", 60),
)
# Matches our hook commands, old (`uv run ... commons hook x`) and new ("/path/commons" hook x).
_OURS = re.compile(r"(?:^|[\s/\"'])commons\"? hook ")


def default_settings_path() -> Path:
    """Return Claude Code's user settings file ($CLAUDE_CONFIG_DIR or ~/.claude)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return Path(base) / "settings.json"


def commons_executable() -> Path:
    """Return the `commons` executable next to this interpreter (clone .venv or uv tool)."""
    return Path(sys.executable).with_name("commons")


def commons_hooks(executable: Path | None = None) -> dict[str, list[Any]]:
    """Return Claude Code hook groups that run `commons hook <event>` with this install."""
    command = f'"{executable or commons_executable()}" hook'
    hooks: dict[str, list[Any]] = {}
    for event, matcher, name, timeout in HOOK_EVENTS:
        group: dict[str, Any] = {
            "hooks": [{"type": "command", "command": f"{command} {name}", "timeout": timeout}]
        }
        if matcher:
            group = {"matcher": matcher, **group}
        hooks[event] = [group]
    return hooks


def is_commons_hook(command: str) -> bool:
    """Return whether a hook command is one of ours, from any version of the installer."""
    return bool(_OURS.search(command))


def merge_hooks(settings: dict[str, Any], ours: dict[str, list[Any]]) -> dict[str, Any]:
    """Return settings with our hook groups replacing any earlier Agent Commons groups.

    Groups belonging to other tools are kept in place. Running it twice changes nothing.
    """
    merged = dict(settings)
    hooks = {event: list(groups) for event, groups in settings.get("hooks", {}).items()}
    for event, groups in hooks.items():
        hooks[event] = [group for group in groups if not _is_ours(group)]
    for event, groups in ours.items():
        hooks.setdefault(event, []).extend(groups)
    merged["hooks"] = {event: groups for event, groups in hooks.items() if groups}
    return merged


def _is_ours(group: dict[str, Any]) -> bool:
    return any(is_commons_hook(str(hook.get("command", ""))) for hook in group.get("hooks", []))


@dataclass(frozen=True)
class InstallResult:
    """Where the hooks went and whether anything changed."""

    settings_path: Path
    changed: bool
    backup_path: Path | None


def install_hooks(settings_path: Path) -> InstallResult:
    """Merge our hooks into the settings file, backing it up first if it changes."""
    current: dict[str, Any] = {}
    if settings_path.exists():
        current = json.loads(settings_path.read_text(encoding="utf-8"))
    merged = merge_hooks(current, commons_hooks())
    if merged == current:
        return InstallResult(settings_path, changed=False, backup_path=None)
    backup = None
    if settings_path.exists():
        backup = settings_path.with_name(settings_path.name + ".agent-commons.bak")
        backup.write_text(settings_path.read_text(encoding="utf-8"), encoding="utf-8")
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return InstallResult(settings_path, changed=True, backup_path=backup)

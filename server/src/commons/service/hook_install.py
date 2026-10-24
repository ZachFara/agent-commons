"""Merge Agent Commons hooks into Claude Code's settings.json without touching other hooks."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# This file lives at <repo>/server/src/commons/service/; the template at <repo>/client/.
REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATE = REPO_ROOT / "client" / "hooks.json"
MARKER = " commons hook "


def default_settings_path() -> Path:
    """Return Claude Code's user settings file ($CLAUDE_CONFIG_DIR or ~/.claude)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return Path(base) / "settings.json"


def commons_hooks(repo_root: Path = REPO_ROOT) -> dict[str, list[Any]]:
    """Return the hook groups from the template with this clone's path filled in."""
    text = TEMPLATE.read_text(encoding="utf-8").replace("<path>", str(repo_root))
    hooks: dict[str, list[Any]] = json.loads(text)["hooks"]
    return hooks


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
    return any(MARKER in f" {hook.get('command', '')} " for hook in group.get("hooks", []))


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

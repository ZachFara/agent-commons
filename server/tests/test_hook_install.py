import json
from pathlib import Path
from typing import Any

import pytest

from commons.cli.main import main
from commons.service.hook_install import REPO_ROOT, commons_hooks, merge_hooks

OTHER_TOOL = {"matcher": "Bash", "hooks": [{"type": "command", "command": "lint-on-save"}]}
STALE_OURS = {
    "hooks": [{"type": "command", "command": "uv run --directory /old/server commons hook stop"}]
}


def test_template_points_at_this_clone() -> None:
    hooks = commons_hooks()
    command = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert command == f"uv run --directory {REPO_ROOT}/server commons hook session-start"
    assert "<path>" not in json.dumps(hooks)


def test_merge_keeps_other_tools_and_replaces_stale_commons_hooks() -> None:
    settings: dict[str, Any] = {
        "model": "opus",
        "hooks": {"PostToolUse": [OTHER_TOOL], "Stop": [STALE_OURS]},
    }
    merged = merge_hooks(settings, commons_hooks())
    assert merged["model"] == "opus"
    assert merged["hooks"]["PostToolUse"][0] == OTHER_TOOL
    assert "Stop" not in merged["hooks"]
    assert merged["hooks"]["SessionStart"] == commons_hooks()["SessionStart"]
    assert merge_hooks(merged, commons_hooks()) == merged


def test_install_hooks_command_writes_backs_up_and_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = tmp_path / "settings.json"
    original = {"theme": "dark", "hooks": {"PostToolUse": [OTHER_TOOL]}}
    settings.write_text(json.dumps(original), encoding="utf-8")

    assert main(["install-hooks", "--settings", str(settings)]) == 0
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written["theme"] == "dark"
    assert written["hooks"]["PostToolUse"][0] == OTHER_TOOL
    assert len(written["hooks"]["PostToolUse"]) == 2
    assert "SessionStart" in written["hooks"]
    backup = tmp_path / "settings.json.agent-commons.bak"
    assert json.loads(backup.read_text(encoding="utf-8")) == original

    assert main(["install-hooks", "--settings", str(settings)]) == 0
    assert capsys.readouterr().out.endswith(f"hooks already installed in {settings}\n")


def test_install_hooks_creates_missing_settings(tmp_path: Path) -> None:
    settings = tmp_path / "new" / "settings.json"
    assert main(["install-hooks", "--settings", str(settings)]) == 0
    assert set(json.loads(settings.read_text(encoding="utf-8"))) == {"hooks"}

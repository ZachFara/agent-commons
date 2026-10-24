"""Run `commons hook` the way Claude Code does: JSON on stdin, JSON (maybe) on stdout."""

import io
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from commons.cli.main import main


class HookEnv:
    """A committed reporter identity plus a private state dir, for hook tests."""

    def __init__(self, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.state_dir = tmp_path / "state"
        self.monkeypatch = monkeypatch
        monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
        monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "config.json"))
        monkeypatch.setenv("COMMONS_STATE_DIR", str(self.state_dir))
        self.pseudonym = f"hooker-{uuid.uuid4().hex[:8]}"

    def init(self, privacy: str = "entry") -> None:
        assert main(["init", "--pseudonym", self.pseudonym, "--privacy", privacy]) == 0

    def run(self, event: str, payload: dict[str, Any] | str) -> dict[str, Any] | None:
        """Run the hook; return its parsed stdout, or None if it printed nothing."""
        text = payload if isinstance(payload, str) else json.dumps(payload)
        stdout = io.StringIO()
        self.monkeypatch.setattr("sys.stdin", io.StringIO(text))
        self.monkeypatch.setattr("sys.stdout", stdout)
        assert main(["hook", event]) == 0
        out = stdout.getvalue()
        return json.loads(out) if out else None

    def log(self) -> str:
        path = self.state_dir / "hooks.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

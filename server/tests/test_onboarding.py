"""Inviting a friend to a hosted server and their `commons join`."""

import json
import shlex
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from commons.cli import onboarding
from commons.cli.main import main
from commons.service.keys import create_key
from commons.store.connection import Connection
from commons.web.app import build_app

SERVER = "https://commons.example.org"


@pytest.fixture
def app(db: psycopg.Connection) -> Starlette:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_app(test_connection, hosted=True)


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: Starlette) -> Path:
    """A friend's machine: empty config and settings; remote calls go to the test app."""
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(
        "commons.cli.onboarding.remote_client",
        lambda _url, key: TestClient(app, headers={"authorization": f"Bearer {key}"}),
    )
    return tmp_path


def test_invite_prints_three_pasteable_commands(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    assert main(["admin", "invite", "friend-invite", "--server", SERVER + "/"]) == 0
    out = capsys.readouterr().out
    commands = [
        line for line in out.splitlines() if line.startswith(("uv ", "commons ", "claude "))
    ]
    key = shlex.split(commands[1])[-1]
    assert key.startswith("ac_")
    assert commands == [
        f"uv tool install {shlex.quote(onboarding.INSTALL_SOURCE)}",
        f"commons join --server {SERVER} --key {key}",
        (
            f"claude mcp add --scope user --transport http commons {SERVER}/mcp "
            f"--header 'Authorization: Bearer {key}'"
        ),
    ]
    assert "Set up Agent Commons for me" in out


def test_invite_needs_a_server(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    assert main(["admin", "invite", "friend-nourl"]) == 1
    assert "--server" in capsys.readouterr().err


def test_join_saves_the_server_and_installs_hooks(
    machine: Path, db: psycopg.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    key = create_key(db, "friend-join", admin=False)
    assert main(["join", "--server", SERVER + "/", "--key", key]) == 0

    assert json.loads((machine / "config.json").read_text()) == {"server": SERVER, "key": key}
    settings = json.loads((machine / "claude" / "settings.json").read_text())
    assert set(settings["hooks"]) == {"SessionStart", "PostToolUse", "PostToolUseFailure", "Stop"}
    out = capsys.readouterr().out
    assert f"joined {SERVER}" in out
    assert onboarding.mcp_add_command(SERVER, key) in out


def test_join_with_a_bad_key_changes_nothing(
    machine: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["join", "--server", SERVER, "--key", "ac_wrong"]) == 1
    assert "rejected the API key" in capsys.readouterr().err
    assert not (machine / "config.json").exists()
    assert not (machine / "claude" / "settings.json").exists()

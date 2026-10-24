import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

import factories
from commons.cli.main import main
from commons.domain.errors import StoreUnavailableError
from commons.service import doctor
from commons.service.hook_install import install_hooks
from commons.store.connection import Connection
from commons.store.reporters import upsert_reporter
from commons.web import app as web


def unreachable() -> Connection:
    msg = "Can't reach the database at test."
    raise StoreUnavailableError(msg)


def no_server(_url: str, _key: str) -> None:
    return None


@pytest.fixture
def home(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated machine: config, state, and Claude settings under tmp_path."""
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("COMMONS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return tmp_path


def lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().out.splitlines()


def test_doctor_on_a_fresh_machine_says_what_to_do(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del home
    assert main(["doctor"]) == 1
    out = "\n".join(lines(capsys))
    assert "✗ config" in out
    assert "commons init" in out
    assert "✗ hooks" in out
    assert "! mcp server" in out


def test_doctor_all_good_after_setup(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["init", "--pseudonym", f"doc-{uuid.uuid4().hex[:6]}"]) == 0
    install_hooks(home / "claude" / "settings.json")
    (home / "claude" / ".claude.json").write_text(json.dumps({"mcpServers": {"commons": {}}}))
    capsys.readouterr()
    assert main(["doctor"]) == 0
    out = lines(capsys)
    assert out[-1] == "all good"
    assert all(line.startswith("✓") for line in out[:-1])


def test_doctor_counts_recent_hook_errors(home: Path) -> None:
    log = home / "state" / "hooks.log"
    log.parent.mkdir(parents=True)
    now = datetime.now(UTC).astimezone()
    log.write_text(
        f"{now:%Y-%m-%d %H:%M:%S},123 ERROR stop hook failed\n"
        "2020-01-01 00:00:00,000 ERROR old failure\n",
        encoding="utf-8",
    )
    checks = doctor.run_checks(unreachable, no_server)
    (check,) = [c for c in checks if c.name == "hook log"]
    assert check.status == "warn"
    assert check.detail.startswith("1 error(s)")


def test_doctor_reports_an_unreachable_database(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home_dir = home
    monkeypatch.setenv("COMMONS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    (home_dir / "config.json").write_text(json.dumps({"pseudonym": "x"}))
    main(["doctor"])
    assert any(line.startswith("✗ database    Can't reach") for line in lines(capsys))


def test_search_previews_without_logging(
    home: Path, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    del home
    word = f"previewcheck{uuid.uuid4().hex[:6]}"
    with psycopg.connect(database_url) as conn:
        reporter = upsert_reporter(conn, "preview-owner", "entry")
        factories.entry(conn, reporter, symptom=f"{word} happened", fix="Do `the thing`.")
        before = conn.execute("SELECT count(*) FROM retrievals").fetchone()
    assert main(["search", word]) == 0
    out = "\n".join(lines(capsys))
    assert f"1. {word} happened" in out
    assert "fix: Do `the thing`." in out
    assert "no retrieval logged" in out
    with psycopg.connect(database_url) as conn:
        assert conn.execute("SELECT count(*) FROM retrievals").fetchone() == before


def test_show_prints_an_entry_and_unknown_ids_fail(
    home: Path, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    del home
    with psycopg.connect(database_url) as conn:
        reporter = conn.execute(
            "INSERT INTO reporters (pseudonym) VALUES (%s) RETURNING id",
            (f"show-{uuid.uuid4().hex[:6]}",),
        ).fetchone()
        assert reporter is not None
        entry_id = factories.entry(conn, reporter[0], repro="python -c 'x'")
    assert main(["show", str(entry_id)]) == 0
    out = lines(capsys)
    assert out[0].startswith("ImportError")
    assert any("repro:       python -c 'x'" in line for line in out)
    assert main(["show", str(uuid.uuid4())]) == 1
    assert "No entry" in capsys.readouterr().err


class ImmediateTimer:
    """Stands in for threading.Timer: runs the function on start()."""

    def __init__(self, _delay: float, function: Callable[..., object], args: list[str]) -> None:
        self.function, self.args = function, args

    def start(self) -> None:
        self.function(*self.args)


def test_dashboard_open_flag_opens_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("uvicorn.run", lambda *_a, **_k: None)
    monkeypatch.setattr("webbrowser.open", opened.append)
    monkeypatch.setattr("threading.Timer", ImmediateTimer)
    web.main(["--open", "--port", "9123"])
    assert opened == ["http://127.0.0.1:9123/"]

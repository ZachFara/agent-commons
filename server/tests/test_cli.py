import json
import uuid
from importlib.metadata import entry_points
from pathlib import Path

import psycopg
import pytest

from commons.cli.main import main


@pytest.fixture
def cli_env(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at the test database and a temporary config file; return its path.

    CLI commands commit, so tests that use this must use unique pseudonyms.
    """
    config = tmp_path / "config.json"
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(config))
    return config


def unique_pseudonym() -> str:
    return f"tester-{uuid.uuid4().hex[:8]}"


def reporter_row(database_url: str, pseudonym: str) -> list[tuple[object, ...]]:
    with psycopg.connect(database_url) as conn:
        return conn.execute(
            "SELECT id, privacy_default FROM reporters WHERE pseudonym = %s", (pseudonym,)
        ).fetchall()


def test_commons_script_points_at_main() -> None:
    (script,) = entry_points(group="console_scripts", name="commons")
    assert script.load() is main


def test_migrate_reports_up_to_date_on_second_run(
    cli_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    del cli_env
    assert main(["migrate"]) == 0
    assert capsys.readouterr().out == "database is up to date\n"


def test_unknown_command_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["no-such-command"])
    assert exc.value.code == 2


def test_init_creates_reporter_and_config(cli_env: Path, database_url: str) -> None:
    pseudonym = unique_pseudonym()
    assert main(["init", "--pseudonym", pseudonym, "--privacy", "full_trace"]) == 0
    rows = reporter_row(database_url, pseudonym)
    assert [privacy for _id, privacy in rows] == ["full_trace"]
    assert json.loads(cli_env.read_text()) == {"pseudonym": pseudonym}


def test_init_again_reuses_reporter_and_updates_privacy(cli_env: Path, database_url: str) -> None:
    del cli_env
    pseudonym = unique_pseudonym()
    main(["init", "--pseudonym", pseudonym])
    (first,) = reporter_row(database_url, pseudonym)
    assert main(["init", "--pseudonym", pseudonym, "--privacy", "local"]) == 0
    assert reporter_row(database_url, pseudonym) == [(first[0], "local")]


@pytest.mark.parametrize(
    ("pseudonym", "message"),
    [
        ("someone@example.com", "email"),
        ("ab", "3-40 characters"),
        ("has space", "3-40 characters"),
    ],
)
def test_init_rejects_bad_pseudonyms(
    cli_env: Path, capsys: pytest.CaptureFixture[str], pseudonym: str, message: str
) -> None:
    assert main(["init", "--pseudonym", pseudonym]) == 1
    assert message in capsys.readouterr().err
    assert not cli_env.exists()


def test_init_rejects_unknown_privacy_mode(cli_env: Path) -> None:
    del cli_env
    with pytest.raises(SystemExit) as exc:
        main(["init", "--pseudonym", unique_pseudonym(), "--privacy", "public"])
    assert exc.value.code == 2

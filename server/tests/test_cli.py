from importlib.metadata import entry_points

import pytest

from commons.cli.main import main


def test_commons_script_points_at_main() -> None:
    (script,) = entry_points(group="console_scripts", name="commons")
    assert script.load() is main


def test_migrate_reports_up_to_date_on_second_run(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    assert main(["migrate"]) == 0
    capsys.readouterr()
    assert main(["migrate"]) == 0
    assert capsys.readouterr().out == "database is up to date\n"


def test_unknown_command_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["no-such-command"])
    assert exc.value.code == 2

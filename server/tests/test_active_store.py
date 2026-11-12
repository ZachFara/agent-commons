"""Which store is active, shown to the owner (`doctor`, `status`) and in a SessionStart variant.

Two real misses came from not knowing it: an agent used the wrong store, or wrote the lesson to
AGENTS.md instead of the commons. Every place names the store without a key or password.
"""

import io
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from psycopg.conninfo import conninfo_to_dict

from commons.cli.main import main
from commons.domain.errors import StoreUnavailableError
from commons.service import agent_text, doctor
from commons.service.identity import save_server, store_label
from commons.store.connection import Connection

KEY = "sk-ant-" + "k" * 30  # what a hosted key looks like to `redact`
HIDDEN = "hunter2-not-shown"  # the password in the server URL
SERVER = f"https://alice:{HIDDEN}@commons.example.com:8443/"
TASK_ID = uuid.UUID("6f1d2c3b-4a59-4e68-8d7c-0b1a2f3e4d5c")


Setup = Callable[[str], None] | None


@pytest.fixture
def machine(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """An isolated machine on the test database; return that database's name."""
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("COMMONS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    return str(conninfo_to_dict(database_url)["dbname"])


def local(machine: str) -> None:
    del machine
    assert main(["init", "--pseudonym", f"store-{uuid.uuid4().hex[:8]}"]) == 0


def hosted(machine: str) -> None:
    local(machine)
    save_server(SERVER, KEY)


def no_secrets(text: str) -> None:
    assert KEY not in text
    assert HIDDEN not in text
    assert "alice" not in text
    assert os.environ["COMMONS_DATABASE_URL"] not in text


def unreachable() -> Connection:
    msg = "Can't reach the database at test."
    raise StoreUnavailableError(msg)


def no_server(_url: str, _key: str) -> None:
    return None


def test_a_local_store_is_named_by_its_database_and_a_hosted_one_by_its_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = f"postgresql://alice:{HIDDEN}@db.internal:5432/commons_team"
    monkeypatch.setenv("COMMONS_DATABASE_URL", database)
    assert store_label(None) == "local database commons_team on db.internal"
    assert store_label(SERVER) == "hosted at commons.example.com:8443"
    assert store_label("https://commons.example.com") == "hosted at commons.example.com"


def test_a_secret_where_the_host_should_be_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", "not a connection string")
    assert store_label(None) == "local database from an unreadable COMMONS_DATABASE_URL"
    assert store_label(KEY) == "hosted at [REDACTED:anthropic_key]"  # no scheme, so no host


@pytest.mark.parametrize(
    ("setup", "status", "detail"),
    [
        (local, "ok", "local database {db} on localhost"),
        (hosted, "ok", "hosted at commons.example.com:8443"),
        (None, "warn", "none until `commons init` (local) or `commons join` (hosted)"),
    ],
)
def test_doctor_names_the_active_store(
    machine: str,
    setup: Setup,
    status: str,
    detail: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if setup is not None:
        setup(machine)
    monkeypatch.setattr("commons.service.hook_client.RemoteBackend.ping", lambda _self: None)
    (check,) = [c for c in doctor.run_checks(unreachable, no_server) if c.name == "store"]
    assert (check.status, check.detail) == (status, detail.format(db=machine))
    main(["doctor"])
    printed = capsys.readouterr().out
    assert f" store       {detail.format(db=machine)}\n" in printed
    no_secrets(printed)


@pytest.mark.parametrize(
    ("setup", "first_lines"),
    [
        (local, ["store: local database {db} on localhost"]),
        (
            hosted,
            [
                "store: hosted at commons.example.com:8443",
                "statistics from: local database {db} on localhost",
            ],
        ),
        (
            None,
            [
                "store: none configured; run `commons init` (local) or `commons join` (hosted)",
                "statistics from: local database {db} on localhost",
            ],
        ),
    ],
)
def test_status_names_the_active_store_before_its_statistics(
    machine: str, setup: Setup, first_lines: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    if setup is not None:
        setup(machine)
    capsys.readouterr()
    assert main(["status"]) == 0
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert lines[: len(first_lines)] == [line.format(db=machine) for line in first_lines]
    assert lines[len(first_lines)].startswith("entries: ")
    no_secrets(printed)


def session_context(monkeypatch: pytest.MonkeyPatch, variant: str) -> str:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", variant)
    monkeypatch.setattr(
        "commons.service.hook_client.on_session_start", lambda *_args, **_kwargs: TASK_ID
    )
    payload = {"session_id": str(uuid.uuid4()), "source": "startup"}
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", stdout)
    assert main(["hook", "session-start"]) == 0
    context: str = json.loads(stdout.getvalue())["hookSpecificOutput"]["additionalContext"]
    return context


@pytest.mark.parametrize("variant", ["store-line", "lesson-store"])
@pytest.mark.parametrize(
    ("setup", "store"),
    [(local, "local database {db} on localhost"), (hosted, "hosted at commons.example.com:8443")],
)
def test_the_store_line_names_the_sessions_store_after_the_shipped_start_line(
    machine: str,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    setup: Callable[[str], None],
    store: str,
) -> None:
    setup(machine)
    monkeypatch.setattr("commons.service.hook_client.RemoteBackend.feedback", lambda _self: None)
    context = session_context(monkeypatch, variant)
    shipped = agent_text.VARIANTS["baseline"].start_line
    assert context.endswith(f" {shipped} Store for this session: {store.format(db=machine)}.")
    no_secrets(context)


def test_the_shipped_session_start_names_no_store(
    machine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    local(machine)
    context = session_context(monkeypatch, "baseline")
    assert "Store for this session" not in context
    assert machine not in context

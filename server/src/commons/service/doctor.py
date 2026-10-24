"""`commons doctor`: check this machine's Agent Commons setup and say what to fix."""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx2

from commons.domain.errors import NotInitializedError, NotPermittedError, StoreUnavailableError
from commons.service.hook_install import default_settings_path, is_commons_hook
from commons.service.identity import config_path, current_reporter, hosted_server, read_config
from commons.service.local_state import state_dir
from commons.store.connection import Connection
from commons.store.migrate import pending_migrations

Status = Literal["ok", "warn", "fail"]
RECENT = timedelta(hours=24)


@dataclass(frozen=True)
class Check:
    """One line of the report."""

    name: str
    status: Status
    detail: str


Connect = Callable[[], Connection]
Ping = Callable[[str, str], None]


def run_checks(connect: Connect, ping: Ping) -> list[Check]:
    """Run every check; a failure in one doesn't stop the others."""
    checks = [_config()]
    server = _hosted()
    if server is None:
        checks += _local_database(connect)
    else:
        checks.append(_server(server, ping))
    checks += [_hooks(), _mcp(hosted=server is not None), _hook_log()]
    return checks


def _config() -> Check:
    path = config_path()
    try:
        config = read_config()
    except NotInitializedError:
        return Check("config", "fail", f"none at {path}; run `commons init` or `commons join`")
    mode = "hosted" if "server" in config else "local"
    return Check("config", "ok", f"{path} ({mode} mode)")


def _hosted() -> tuple[str, str] | None:
    try:
        return hosted_server()
    except NotInitializedError:
        return None


def _local_database(connect: Connect) -> list[Check]:
    try:
        conn = connect()
    except StoreUnavailableError as error:
        return [Check("database", "fail", str(error))]
    with conn:
        checks = [Check("database", "ok", "reachable")]
        pending = pending_migrations(conn)
        checks.append(
            Check("migrations", "fail", f"{len(pending)} pending; run `commons migrate`")
            if pending
            else Check("migrations", "ok", "up to date")
        )
        try:
            current_reporter(conn)
            checks.append(Check("identity", "ok", "reporter found"))
        except NotInitializedError as error:
            checks.append(Check("identity", "fail", str(error)))
    return checks


def _server(server: tuple[str, str], ping: Ping) -> Check:
    url, key = server
    try:
        ping(url, key)
    except (NotInitializedError, NotPermittedError, httpx2.HTTPError) as error:
        return Check("server", "fail", f"{url}: {error}")
    return Check("server", "ok", f"{url} accepts your key")


def _hooks() -> Check:
    path = default_settings_path()
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return Check("hooks", "fail", f"no readable {path}; run `commons install-hooks`")
    events = [
        event
        for event, groups in settings.get("hooks", {}).items()
        for group in groups
        for hook in group.get("hooks", [])
        if is_commons_hook(str(hook.get("command", "")))
    ]
    expected = {"SessionStart", "PostToolUse", "PostToolUseFailure", "Stop"}
    missing = expected - set(events)
    if missing:
        return Check(
            "hooks", "fail", f"missing {', '.join(sorted(missing))}; run `commons install-hooks`"
        )
    return Check("hooks", "ok", f"installed in {path}")


def _mcp(*, hosted: bool) -> Check:
    path = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json"
    try:
        servers = json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {})
    except (FileNotFoundError, json.JSONDecodeError):
        servers = {}
    if "commons" in servers:
        return Check("mcp server", "ok", "registered for all projects")
    how = "--transport http" if hosted else "-- uv run ..."
    return Check(
        "mcp server",
        "warn",
        f"not registered at user scope; see INSTALL.md (`claude mcp add {how}`)",
    )


def _hook_log() -> Check:
    path = state_dir() / "hooks.log"
    if not path.exists():
        return Check(
            "hook log", "ok", "no hook has run yet" if not path.parent.exists() else "empty"
        )
    cutoff = datetime.now(UTC) - RECENT
    errors = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stamp, _, rest = line.partition(" ERROR ")
        if rest and _logged_at(stamp) >= cutoff:
            errors += 1
    if errors:
        return Check("hook log", "warn", f"{errors} error(s) in the last day; see {path}")
    return Check("hook log", "ok", "no errors in the last day")


def _logged_at(stamp: str) -> datetime:
    try:
        return datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S").astimezone(UTC)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)

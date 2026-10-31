"""Local identity: which reporter this machine acts as.

`commons init` writes the pseudonym to a small JSON config; the MCP server reads it to know
who is calling in local mode. Hosted mode identifies reporters by API key instead.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from commons.domain.errors import NotInitializedError
from commons.domain.reporters import PrivacyDefault, validate_pseudonym
from commons.store.connection import Connection
from commons.store.models import model_id
from commons.store.reporters import find_reporter, pseudonym_of, upsert_reporter

UNKNOWN_MODEL = "unknown"

# Hosted mode (decision 0018): identity comes only from the request's API key, never from a
# config file on the server. The entry point sets _HOSTED; each request sets _REQUEST_REPORTER.
_HOSTED: ContextVar[bool] = ContextVar("commons_hosted", default=False)
_REQUEST_REPORTER: ContextVar[UUID | None] = ContextVar("commons_request_reporter", default=None)
_REQUEST_TEST: ContextVar[bool] = ContextVar("commons_request_test", default=False)
_REQUEST_KEY: ContextVar[UUID | None] = ContextVar("commons_request_key", default=None)


@contextmanager
def serving_hosted() -> Iterator[None]:
    """Run the block as a hosted server: identity only from API keys, never the config file."""
    token = _HOSTED.set(True)
    try:
        yield
    finally:
        _HOSTED.reset(token)


@contextmanager
def acting_as(
    reporter_id: UUID, *, test: bool = False, key_id: UUID | None = None
) -> Iterator[None]:
    """Attribute everything done inside the block to this reporter (one hosted request).

    With `test` (a test key, decision 0030), what the block logs is excluded from every count.
    """
    token = _REQUEST_REPORTER.set(reporter_id)
    test_token = _REQUEST_TEST.set(test)
    key_token = _REQUEST_KEY.set(key_id)
    try:
        yield
    finally:
        _REQUEST_KEY.reset(key_token)
        _REQUEST_TEST.reset(test_token)
        _REQUEST_REPORTER.reset(token)


def current_key() -> UUID | None:
    """Return the current hosted request's API key, if any (rate limits count per key)."""
    return _REQUEST_KEY.get()


def test_traffic() -> bool:
    """Whether the current request came with a test key."""
    return _REQUEST_TEST.get()


@dataclass(frozen=True)
class InitResult:
    """What `init` did, for the CLI to report."""

    reporter_id: UUID
    config_path: Path


def config_path() -> Path:
    """Return $COMMONS_CONFIG, else $XDG_CONFIG_HOME/agent-commons/config.json."""
    if explicit := os.environ.get("COMMONS_CONFIG"):
        return Path(explicit)
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "agent-commons" / "config.json"


def init_reporter(conn: Connection, pseudonym: str, privacy_default: PrivacyDefault) -> InitResult:
    """Create or reuse the reporter and record it as this machine's identity."""
    validate_pseudonym(pseudonym)
    reporter_id = upsert_reporter(conn, pseudonym, privacy_default)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pseudonym": pseudonym}, indent=2) + "\n", encoding="utf-8")
    return InitResult(reporter_id=reporter_id, config_path=path)


def read_config() -> dict[str, object]:
    """Return the local config `init` wrote; NotInitializedError if there is none."""
    path = config_path()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        msg = f"No reporter identity at {path}. Run `commons init --pseudonym <handle>`."
        raise NotInitializedError(msg) from error
    if not isinstance(config, dict):
        msg = f"No reporter identity at {path}. Run `commons init --pseudonym <handle>`."
        raise NotInitializedError(msg)
    return config


def hosted_server() -> tuple[str, str] | None:
    """Return (server URL, API key) if `commons connect` set this machine up for hosted mode."""
    config = read_config()
    server, key = config.get("server"), config.get("key")
    return (server, key) if isinstance(server, str) and isinstance(key, str) else None


def save_server(url: str, key: str) -> Path:
    """Point this machine's hooks at a hosted server, keeping any local identity."""
    path = config_path()
    try:
        config = read_config()
    except NotInitializedError:
        config = {}
    config.update({"server": url.rstrip("/"), "key": key})
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)  # the mode above only applies to new files; this holds a key
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(json.dumps(config, indent=2) + "\n")
    return path


def configured_test_runners() -> list[str]:
    """Extra test-runner regexes from the config's `test_runners` list (decision 0016)."""
    runners = read_config().get("test_runners", [])
    return [r for r in runners if isinstance(r, str)] if isinstance(runners, list) else []


def current_reporter(conn: Connection) -> UUID:
    """Return the calling reporter: the request's key holder, else this machine's identity."""
    if (requested := _REQUEST_REPORTER.get()) is not None:
        return requested
    if _HOSTED.get():
        msg = "This server needs an API key: send `Authorization: Bearer <key>`."
        raise NotInitializedError(msg)
    pseudonym = read_config().get("pseudonym")
    if not isinstance(pseudonym, str):
        msg = f"No local reporter identity in {config_path()}. Run `commons init`."
        raise NotInitializedError(msg)
    reporter_id = find_reporter(conn, pseudonym)
    if reporter_id is None:
        msg = f"Reporter {pseudonym!r} is not in this database. Run `commons init` again."
        raise NotInitializedError(msg)
    return reporter_id


def current_model(conn: Connection, model: str | None) -> UUID:
    """Return the model row for the calling model (decision 0009), or `unknown`."""
    return model_id(conn, (model or "").strip() or UNKNOWN_MODEL)


def current_pseudonym(conn: Connection) -> str:
    """Return the calling reporter's pseudonym (who corrections are recorded as)."""
    return pseudonym_of(conn, current_reporter(conn))

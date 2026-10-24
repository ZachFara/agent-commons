"""Hosted mode over real streamable HTTP, in-process (decision 0018)."""

import asyncio
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import mcp.types as mt
import psycopg
import pytest
from fastmcp import FastMCP
from fastmcp.client.client import CallToolResult
from fastmcp.utilities.tests import asgi_client
from starlette.testclient import TestClient

from commons.cli.main import main
from commons.mcp.server import build_server
from commons.service.identity import serving_hosted
from commons.service.keys import create_key
from commons.store.connection import Connection


@pytest.fixture
def hosted(db: psycopg.Connection) -> FastMCP:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_server(test_connection, hosted=True)


def error_json(result: CallToolResult) -> dict[str, Any]:
    content = result.content[0]
    assert isinstance(content, mt.TextContent)
    payload: dict[str, Any] = json.loads(content.text)
    return payload


def call_http(
    server: FastMCP, key: str | None, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        with serving_hosted():
            async with asgi_client(server, auth=key) as client:
                result = await client.call_tool(tool, arguments, raise_on_error=False)
        if result.is_error:
            return error_json(result)
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    return asyncio.run(run())


PROPOSAL = {
    "symptom": "hostedcheck error",
    "environment": {"language": "python 3.12"},
    "cause": "c",
    "fix": "f",
    "domains": ["test"],
    "task_id": str(uuid.uuid4()),
}


def test_a_valid_key_acts_as_its_reporter(hosted: FastMCP, db: psycopg.Connection) -> None:
    key = create_key(db, "alice-hosted", admin=False)
    result = call_http(hosted, key, "propose", PROPOSAL)
    row = db.execute(
        "SELECT r.pseudonym FROM entries e JOIN reporters r ON r.id = e.created_by WHERE e.id = %s",
        (result["entry_id"],),
    ).fetchone()
    assert row == ("alice-hosted",)


def http_status(server: FastMCP, key: str | None) -> int:
    """Status of a raw MCP initialize request, to see the 401 the client hides."""
    headers = {"accept": "application/json, text/event-stream"}
    if key is not None:
        headers["authorization"] = f"Bearer {key}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    with TestClient(server.http_app()) as client:
        return client.post("/mcp", json=body, headers=headers).status_code


@pytest.mark.parametrize("key", [None, "ac_not-a-real-key", "junk"])
def test_missing_or_unknown_keys_get_401(hosted: FastMCP, key: str | None) -> None:
    assert http_status(hosted, key) == 401


def test_a_rotated_key_stops_working(hosted: FastMCP, db: psycopg.Connection) -> None:
    old = create_key(db, "bob-hosted", admin=False)
    new = create_key(db, "bob-hosted", admin=True)
    assert old != new
    assert "entries_by_status" in call_http(hosted, new, "status", {})
    assert http_status(hosted, old) == 401
    assert http_status(hosted, new) != 401


def test_local_privacy_reporters_are_refused(hosted: FastMCP, db: psycopg.Connection) -> None:
    key = create_key(db, "carol-hosted", admin=False)
    db.execute("UPDATE reporters SET privacy_default = 'local' WHERE pseudonym = 'carol-hosted'")
    result = call_http(hosted, key, "propose", PROPOSAL)
    assert result["error"]["code"] == "forbidden"
    assert "local" in result["error"]["message"]


def test_hosted_never_falls_back_to_the_config_file(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    """Even with a local identity configured, a hosted call without a key has no reporter."""
    del reporter

    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    async def run() -> dict[str, Any]:
        with serving_hosted():
            async with asgi_client(build_server(test_connection)) as client:
                result = await client.call_tool("propose", PROPOSAL, raise_on_error=False)
        return error_json(result)

    assert asyncio.run(run())["error"]["code"] == "not_initialized"


def test_admin_create_key_command(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "unused.json"))
    pseudonym = f"admin-{uuid.uuid4().hex[:6]}"
    assert main(["admin", "create-key", pseudonym, "--admin"]) == 0
    key = capsys.readouterr().out.splitlines()[-1]
    assert key.startswith("ac_")
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "SELECT is_admin, api_key_hash <> %s FROM reporters WHERE pseudonym = %s",
            (key, pseudonym),
        ).fetchone()
    assert row == (True, True)
    assert main(["admin", "create-key", "not@valid.example"]) == 1

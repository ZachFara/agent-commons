"""One hosted process: MCP, hook API, dashboard, health check (decision 0019)."""

import asyncio
import base64
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx2
import psycopg
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.applications import Starlette
from starlette.testclient import TestClient

from commons.hosted import app as hosted
from commons.service.identity import serving_hosted
from commons.service.keys import create_key
from commons.store.connection import Connection


@pytest.fixture
def app(db: psycopg.Connection) -> Starlette:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return hosted.build_hosted_app(test_connection)


def mcp_status(app: Starlette, key: str) -> dict[str, Any]:
    """Call the status tool through the combined app over streamable HTTP, in-process."""

    def client_factory(
        headers: dict[str, str] | None = None,
        timeout: httpx2.Timeout | None = None,
        auth: httpx2.Auth | None = None,
        *,
        follow_redirects: bool = False,
    ) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=follow_redirects,
        )

    async def run() -> dict[str, Any]:
        transport = StreamableHttpTransport(
            "http://testserver/mcp", auth=key, httpx_client_factory=client_factory
        )
        with serving_hosted():
            async with app.router.lifespan_context(app), Client(transport) as client:
                result = await client.call_tool("status", {})
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    return asyncio.run(run())


def test_mcp_hooks_dashboard_and_health_share_one_app(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = create_key(db, "one-process-admin", admin=True)
    assert "entries_by_status" in mcp_status(app, key)

    client = TestClient(app)
    assert client.get("/healthz").text == "ok"
    ping = client.post("/api/hooks/ping", json={}, headers={"authorization": f"Bearer {key}"})
    assert ping.status_code == 200
    basic = base64.b64encode(f"admin:{key}".encode()).decode()
    assert client.get("/queue", headers={"authorization": f"Basic {basic}"}).status_code == 200
    assert client.get("/queue").status_code == 401


def test_mcp_without_a_key_is_rejected(app: Starlette) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401


def test_server_reads_port_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("PORT", "9555")
    monkeypatch.setattr("uvicorn.run", lambda _app, **kwargs: calls.append(kwargs))
    hosted.main(["--host", "10.0.0.7"])
    assert calls == [
        {"host": "10.0.0.7", "port": 9555, "proxy_headers": True, "forwarded_allow_ips": "*"}
    ]

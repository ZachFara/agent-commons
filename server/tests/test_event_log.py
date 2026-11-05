"""Event lines for what to read back after a problem (decisions 0024, 0037)."""

import base64
import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

import factories
from commons.service.keys import create_key, revoke_key
from commons.store.connection import Connection
from commons.store.reporters import find_reporter
from commons.web.app import build_app

ORIGIN = "https://commons.example"


@pytest.fixture
def app(db: psycopg.Connection) -> Starlette:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_app(test_connection, hosted=True)


@pytest.fixture
def events(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="commons")
    return caplog


def lines(caplog: pytest.LogCaptureFixture, event: str) -> list[dict[str, Any]]:
    parsed = [json.loads(r.getMessage()) for r in caplog.records if r.name.startswith("commons.")]
    return [line for line in parsed if line.get("event") == event]


def browser(app: Starlette, key: str) -> TestClient:
    token = base64.b64encode(f"admin:{key}".encode()).decode()
    headers = {"authorization": f"Basic {token}", "origin": ORIGIN}
    return TestClient(app, base_url=ORIGIN, headers=headers, follow_redirects=False)


def test_each_hook_call_leaves_a_line_with_the_key_prefix(
    app: Starlette, db: psycopg.Connection, events: pytest.LogCaptureFixture
) -> None:
    key = create_key(db, "hook-liner", admin=False)
    client = TestClient(app)
    bearer = {"authorization": f"Bearer {key}"}
    assert client.post("/api/hooks/ping", json={}, headers=bearer).status_code == 200
    assert client.post("/api/hooks/ping", json={}).status_code == 401
    assert (
        client.post("/api/hooks/tool-use", json={"session_id": 7}, headers=bearer).status_code
        == 400
    )

    ok, anonymous, invalid = lines(events, "hook_call")
    handle = key.removeprefix("ac_")[:8]
    assert (ok["hook"], ok["ok"], ok["status"], ok["key"]) == ("ping", True, 200, handle)
    assert (anonymous["status"], anonymous["error"], anonymous["key"]) == (
        401,
        "unauthorized",
        None,
    )
    assert (invalid["hook"], invalid["error"]) == ("tool-use", "invalid_input")
    assert isinstance(ok["ms"], int)
    assert key not in events.text


def test_refused_keys_are_noted_without_the_key(
    app: Starlette, db: psycopg.Connection, events: pytest.LogCaptureFixture
) -> None:
    key = create_key(db, "plain-member", admin=False)
    handle = key.removeprefix("ac_")[:8]
    assert browser(app, key).get("/").status_code == 403
    revoke_key(db, handle)
    assert browser(app, key).get("/queue").status_code == 401
    assert browser(app, "ac_nobodyatall").get("/queue").status_code == 401

    scope, revoked, unknown = lines(events, "key_refused")
    assert (scope["where"], scope["problem"], scope["key"]) == ("/", "needs admin scope", handle)
    assert (revoked["where"], revoked["problem"]) == ("/queue", "revoked")
    assert (unknown["problem"], unknown["key"]) == ("unknown", "nobodyat")
    assert all(line["severity"] == "WARNING" for line in (scope, revoked, unknown))
    assert key not in events.text


def test_admin_actions_name_the_acting_key(
    app: Starlette, db: psycopg.Connection, events: pytest.LogCaptureFixture
) -> None:
    key = create_key(db, "acting-admin", admin=True)
    admin = find_reporter(db, "acting-admin")
    assert admin is not None
    entry_id = factories.entry(db, admin, status="pending")
    client = browser(app, key)

    assert (
        client.post(f"/entries/{entry_id}/review", data={"decision": "approve"}).status_code == 303
    )
    assert (
        client.post(f"/entries/{uuid.uuid4()}/retire", data={"reason": "gone"}).status_code == 303
    )
    form = {"pseudonym": "newcomer", "scope": "agent", "label": "laptop", "expires": ""}
    assert client.post("/keys", data=form).status_code == 200

    review, retire, minted = lines(events, "admin_action")
    assert (review["action"], review["ok"], review["result"]) == (
        f"/entries/{entry_id}/review",
        True,
        "approve",
    )
    assert (retire["ok"], retire["severity"]) == (False, "WARNING")
    assert retire["error"].startswith("No entry")
    assert minted["result"] == "key for newcomer (agent)"
    actor = uuid.UUID(review["key_id"])
    assert retire["key_id"] == minted["key_id"] == str(actor)
    created = [line for line in lines(events, "key_created") if line["pseudonym"] == "newcomer"]
    assert [line["by_key"] for line in created] == [str(actor)]


def test_unhandled_errors_are_logged_with_their_traceback(
    db: psycopg.Connection, events: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    def explode(_conn: Connection) -> dict[str, Any]:
        msg = "kpis exploded for the test"
        raise RuntimeError(msg)

    monkeypatch.setattr("commons.web.app.insights.kpis", explode)
    client = TestClient(build_app(test_connection), raise_server_exceptions=False)
    assert client.get("/queue").status_code == 500

    (line,) = lines(events, "unhandled_error")
    assert (line["method"], line["path"], line["severity"]) == ("GET", "/queue", "ERROR")
    (record,) = [r for r in events.records if r.name == "commons.events" and r.exc_info]
    assert "kpis exploded for the test" in events.text
    assert record.levelno == logging.ERROR

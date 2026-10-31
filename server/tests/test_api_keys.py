"""Scoped, rotatable, revocable keys (decision 0032), through the one hosted app."""

import base64
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from commons.cli.main import main
from commons.domain.errors import InvalidInputError, NotFoundError
from commons.domain.keys import KeyOptions, KeyScope, parse_duration
from commons.hosted.app import build_hosted_app
from commons.service import keys
from commons.store.connection import Connection

ORIGIN = "https://commons.example"
MCP_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


@pytest.fixture
def app(db: psycopg.Connection) -> Starlette:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_hosted_app(test_connection)


def mcp(app: Starlette, key: str) -> tuple[int, str]:
    headers = {
        "authorization": f"Bearer {key}",
        "accept": "application/json, text/event-stream",
    }
    with TestClient(app) as client:
        response = client.post("/mcp", json=MCP_LIST, headers=headers)
    return response.status_code, response.text


def hook(app: Starlette, key: str) -> tuple[int, dict[str, Any]]:
    response = TestClient(app).post(
        "/api/hooks/ping", json={}, headers={"authorization": f"Bearer {key}"}
    )
    return response.status_code, response.json()


def basic(key: str) -> dict[str, str]:
    return {"authorization": "Basic " + base64.b64encode(f"x:{key}".encode()).decode()}


def page(app: Starlette, key: str, path: str) -> tuple[int, str]:
    response = TestClient(app, base_url=ORIGIN).get(path, headers=basic(key))
    return response.status_code, response.text


def new_key(
    db: psycopg.Connection, pseudonym: str, scope: KeyScope, options: KeyOptions | None = None
) -> str:
    return keys.issue_key(db, pseudonym, scope, options or KeyOptions()).key


def prefix(key: str) -> str:
    return key[3:11]


def expire_now(db: psycopg.Connection, key: str) -> None:
    db.execute(
        "UPDATE api_keys SET expires_at = now() - interval '1 second' WHERE prefix = %s",
        (prefix(key),),
    )


# Scopes


def test_agent_keys_work_for_sessions_but_not_the_dashboard(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = new_key(db, "scope-agent", "agent")
    assert mcp(app, key)[0] == 200
    assert hook(app, key) == (200, {"scope": "agent"})
    assert page(app, key, "/queue")[0] == 403


def test_review_keys_reach_review_pages_only(app: Starlette, db: psycopg.Connection) -> None:
    key = new_key(db, "scope-review", "review")
    assert page(app, key, "/queue")[0] == 200
    assert page(app, key, "/entries")[0] == 200
    for path in ("/", "/people", "/searches", "/activity"):
        status, text = page(app, key, path)
        assert (status, "admin scope" in text) == (403, True), path
    assert mcp(app, key)[0] == 403
    assert hook(app, key)[0] == 403


def test_admin_keys_do_everything(app: Starlette, db: psycopg.Connection) -> None:
    key = new_key(db, "scope-admin", "admin")
    assert mcp(app, key)[0] == 200
    assert hook(app, key) == (200, {"scope": "admin"})
    assert page(app, key, "/people")[0] == 200


# Lifecycle


def test_a_reporter_can_hold_several_keys(app: Starlette, db: psycopg.Connection) -> None:
    laptop = new_key(db, "two-keys", "agent", KeyOptions(label="laptop"))
    desktop = new_key(db, "two-keys", "agent", KeyOptions(label="desktop"))
    assert mcp(app, laptop)[0] == 200
    assert mcp(app, desktop)[0] == 200
    assert {k.label for k in keys.list_keys(db, "two-keys")} == {"desktop", "laptop"}


def test_create_key_keeps_its_old_meaning_one_key_per_reporter(
    app: Starlette, db: psycopg.Connection
) -> None:
    old = new_key(db, "one-key", "agent")
    new = keys.create_key(db, "one-key", admin=False)
    assert mcp(app, new)[0] == 200
    status, text = mcp(app, old)
    assert (status, "key revoked" in text) == (401, True)


def test_rotation_overlaps_then_the_old_key_expires(app: Starlette, db: psycopg.Connection) -> None:
    old = new_key(db, "rotator", "agent", KeyOptions(label="laptop"))
    rotated = keys.rotate_key(db, prefix(old), timedelta(hours=24))
    assert rotated.info.label == "laptop"
    assert mcp(app, old)[0] == 200
    assert mcp(app, rotated.key)[0] == 200

    expire_now(db, old)

    status, text = mcp(app, old)
    assert (status, "key expired" in text) == (401, True)
    assert mcp(app, rotated.key)[0] == 200
    with pytest.raises(InvalidInputError, match="already expired"):
        keys.rotate_key(db, prefix(old))


def test_rotate_now_and_revoke_stop_a_key_at_once(app: Starlette, db: psycopg.Connection) -> None:
    key = new_key(db, "revoker", "admin")
    rotated = keys.rotate_key(db, "ac_" + prefix(key), timedelta(0))
    assert mcp(app, key)[0] == 401

    keys.revoke_key(db, rotated.info.handle())

    status, text = mcp(app, rotated.key)
    assert (status, "key revoked" in text) == (401, True)
    status, body = hook(app, rotated.key)
    assert (status, "key revoked" in body["error"]["message"]) == (401, True)
    status, text = page(app, rotated.key, "/people")
    assert (status, "key revoked" in text) == (401, True)


def test_keys_created_with_a_lifetime_expire(app: Starlette, db: psycopg.Connection) -> None:
    key = new_key(db, "short-lived", "agent", KeyOptions(expires_in=timedelta(days=90)))
    (info,) = keys.list_keys(db, "short-lived")
    assert info.expires_at is not None
    assert mcp(app, key)[0] == 200
    expire_now(db, key)
    assert mcp(app, key)[0] == 401


def test_last_used_is_recorded_at_most_once_a_minute(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = new_key(db, "toucher", "agent")
    assert keys.list_keys(db, "toucher")[0].last_used_at is None
    hook(app, key)
    first = keys.list_keys(db, "toucher")[0].last_used_at
    db.execute(
        "UPDATE api_keys SET last_used_at = now() - interval '30 seconds' WHERE prefix = %s",
        (prefix(key),),
    )
    throttled = keys.list_keys(db, "toucher")[0].last_used_at
    hook(app, key)
    assert first is not None
    assert keys.list_keys(db, "toucher")[0].last_used_at == throttled
    db.execute(
        "UPDATE api_keys SET last_used_at = now() - interval '2 minutes' WHERE prefix = %s",
        (prefix(key),),
    )
    hook(app, key)
    latest = keys.list_keys(db, "toucher")[0].last_used_at
    assert latest is not None
    assert throttled is not None
    assert latest > throttled


def test_key_handles_must_be_long_enough_and_unambiguous(db: psycopg.Connection) -> None:
    key = new_key(db, "handles", "agent")
    assert keys.find_key(db, prefix(key)).pseudonym == "handles"
    assert keys.find_key(db, key[:11]).pseudonym == "handles"
    with pytest.raises(InvalidInputError, match="at least 4"):
        keys.find_key(db, "ab")
    with pytest.raises(NotFoundError):
        keys.find_key(db, "zzzz-no-such-key")


def test_durations() -> None:
    assert parse_duration("90d") == timedelta(days=90)
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("30m") == timedelta(minutes=30)
    with pytest.raises(InvalidInputError):
        parse_duration("soon")


def test_key_administration_is_logged_without_the_key(
    db: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO", logger="commons.calls")
    key = new_key(db, "logged-keys", "review")
    keys.revoke_key(db, prefix(key))
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "commons.calls"]
    assert [(line["event"], line["scope"]) for line in lines] == [
        ("key_created", "review"),
        ("key_revoked", "review"),
    ]
    assert key not in caplog.text


# Dashboard


def test_the_people_page_creates_a_key_once_and_revokes_it(
    app: Starlette, db: psycopg.Connection
) -> None:
    admin = new_key(db, "people-admin", "admin")
    client = TestClient(app, base_url=ORIGIN, headers=basic(admin), follow_redirects=False)
    form = {"pseudonym": "people-friend", "scope": "agent", "label": "phone", "expires": "30d"}

    response = client.post("/keys", data=form, headers={"origin": ORIGIN})

    assert response.status_code == 200
    (info,) = keys.list_keys(db, "people-friend")
    shown = re.findall(rf"ac_{re.escape(info.prefix or '')}[\w-]+", response.text)
    assert len(shown) == 1
    assert mcp(app, shown[0])[0] == 200
    assert shown[0] not in client.get("/people").text

    revoked = client.post(f"/keys/{info.key_id}/revoke", headers={"origin": ORIGIN})
    assert revoked.status_code == 303
    assert keys.list_keys(db, "people-friend")[0].revoked_at is not None


def test_key_forms_refuse_cross_site_posts_and_bad_scopes(
    app: Starlette, db: psycopg.Connection
) -> None:
    admin = new_key(db, "csrf-keys", "admin")
    client = TestClient(app, base_url=ORIGIN, headers=basic(admin), follow_redirects=False)
    form = {"pseudonym": "csrf-friend", "scope": "agent"}
    assert client.post("/keys", data=form, headers={"origin": "https://evil"}).status_code == 403
    bad = client.post("/keys", data={**form, "scope": "root"}, headers={"origin": ORIGIN})
    assert bad.status_code == 303
    assert "scope" in bad.headers["location"]
    assert keys.list_keys(db, "csrf-friend") == []


# CLI


def test_key_commands(
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "unused.json"))
    who = f"cli-{uuid.uuid4().hex[:6]}"

    assert main(["admin", "key", "create", who, "--label", "laptop", "--expires", "90d"]) == 0
    created = capsys.readouterr().out.splitlines()
    assert created[0].startswith(f"agent key {created[1][3:11]} for {who}")

    assert main(["admin", "key", "list", who]) == 0
    listing = capsys.readouterr().out
    assert "expires" in listing
    assert "laptop" in listing
    assert created[1] not in listing

    assert main(["admin", "key", "rotate", created[1][:11], "--now"]) == 0
    rotated = capsys.readouterr().out.splitlines()
    assert "stops working now" in rotated[0]
    assert main(["admin", "key", "revoke", rotated[1][3:11]]) == 0
    assert "revoked" in capsys.readouterr().out

    assert main(["admin", "key", "list", who]) == 0
    assert capsys.readouterr().out.count("revoked") == 2

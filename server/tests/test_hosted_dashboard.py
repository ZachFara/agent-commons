"""The hosted dashboard: admin API key over HTTP Basic, same-origin reviews (decision 0018)."""

import base64
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

import factories
from commons.service.keys import create_key
from commons.store.connection import Connection
from commons.store.reporters import find_reporter
from commons.web.admin_auth import basic_password
from commons.web.app import build_app

ORIGIN = "https://commons.example"


@pytest.fixture
def app(db: psycopg.Connection) -> Starlette:
    @contextmanager
    def test_connection() -> Iterator[Connection]:
        yield db

    return build_app(test_connection, hosted=True)


def browser(app: Starlette, key: str | None) -> TestClient:
    headers = {}
    if key is not None:
        token = base64.b64encode(f"admin:{key}".encode()).decode()
        headers["authorization"] = f"Basic {token}"
    return TestClient(app, base_url=ORIGIN, headers=headers, follow_redirects=False)


def test_no_credentials_get_a_basic_auth_challenge(app: Starlette) -> None:
    response = browser(app, None).get("/")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")


def test_non_admin_keys_are_forbidden(app: Starlette, db: psycopg.Connection) -> None:
    key = create_key(db, "plain-member", admin=False)
    assert browser(app, key).get("/").status_code == 403


def test_admin_sees_the_queue_and_reviews_as_themselves(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = create_key(db, "head-admin", admin=True)
    admin = find_reporter(db, "head-admin")
    assert admin is not None
    entry_id = factories.entry(db, admin, status="pending", symptom="hosted-queue-check")

    client = browser(app, key)
    assert "hosted-queue-check" in client.get("/").text

    response = client.post(
        f"/entries/{entry_id}/review", data={"decision": "approve"}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 303
    row = db.execute(
        "SELECT source, reviewed_by FROM reviews WHERE entry_id = %s", (entry_id,)
    ).fetchone()
    assert row == ("human", admin)


def test_cross_site_review_posts_are_refused_even_with_credentials(
    app: Starlette, db: psycopg.Connection
) -> None:
    key = create_key(db, "csrf-admin", admin=True)
    admin = find_reporter(db, "csrf-admin")
    assert admin is not None
    entry_id = factories.entry(db, admin, status="pending")
    for origin in ("https://evil.example", "http://commons.example", None):
        headers = {"origin": origin} if origin else {}
        response = browser(app, key).post(
            f"/entries/{entry_id}/review", data={"decision": "approve"}, headers=headers
        )
        assert response.status_code == 403
    assert db.execute(
        "SELECT count(*) FROM reviews WHERE entry_id = %s", (entry_id,)
    ).fetchone() == (0,)


def test_hook_api_still_uses_bearer_keys(app: Starlette, db: psycopg.Connection) -> None:
    key = create_key(db, "bearer-member", admin=False)
    client = TestClient(app, headers={"authorization": f"Bearer {key}"})
    assert client.post("/api/hooks/ping", json={}).status_code == 200


@pytest.mark.parametrize(
    ("header", "password"),
    [
        ("Basic " + base64.b64encode(b"x:ac_key").decode(), "ac_key"),
        ("basic " + base64.b64encode(b":ac_key").decode(), "ac_key"),
        ("Basic " + base64.b64encode(b"no-colon").decode(), None),
        ("Basic " + base64.b64encode(b"user:").decode(), None),
        ("Basic not-base64!", None),
        ("Bearer ac_key", None),
        ("", None),
    ],
)
def test_basic_password_parsing(header: str, password: str | None) -> None:
    assert basic_password(header) == password


def test_unknown_entry_is_reported_not_crashed(app: Starlette, db: psycopg.Connection) -> None:
    key = create_key(db, "lost-admin", admin=True)
    response = browser(app, key).post(
        f"/entries/{uuid.uuid4()}/review", data={"decision": "approve"}, headers={"origin": ORIGIN}
    )
    assert response.status_code == 303
    assert "No+entry" in response.headers["location"]

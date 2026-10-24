import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP

from mcp_client import call

pytestmark = pytest.mark.usefixtures("reporter")


def proposal(**overrides: object) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "symptom": "ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
        "environment": {"language": "python 3.12", "libraries": {"markupsafe": "2.1.0"}},
        "cause": "markupsafe 2.1 removed soft_unicode, which Jinja2 2.x still imports.",
        "fix": "Upgrade Jinja2 to 3.x, or pin markupsafe<2.1.",
        "domains": ["python", "packaging"],
        "task_id": str(uuid.uuid4()),
        "model": "claude-opus-5-5",
    }
    arguments.update(overrides)
    return arguments


def test_propose_creates_pending_entry(server: FastMCP, db: psycopg.Connection) -> None:
    arguments = proposal(repro="python -c 'import jinja2'")
    result = call(server, "propose", arguments)

    assert result["status"] == "pending"
    assert result["redaction_report"] == {"scanned": True, "findings": []}
    row = db.execute(
        "SELECT e.symptom, e.domains, e.repro, e.task_id::text, e.human_reviewed, m.name "
        "FROM entries e JOIN models m ON m.id = e.created_by_model WHERE e.id = %s",
        (result["entry_id"],),
    ).fetchone()
    assert row == (
        arguments["symptom"],
        ["python", "packaging"],
        "python -c 'import jinja2'",
        arguments["task_id"],
        False,
        "claude-opus-5-5",
    )


def test_entry_is_attributed_to_this_machines_reporter(
    server: FastMCP, db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    result = call(server, "propose", proposal())
    row = db.execute("SELECT created_by FROM entries WHERE id = %s", (result["entry_id"],))
    assert row.fetchone() == (reporter,)


def test_missing_model_is_recorded_as_unknown(server: FastMCP, db: psycopg.Connection) -> None:
    arguments = proposal()
    del arguments["model"]
    result = call(server, "propose", arguments)
    row = db.execute(
        "SELECT m.name FROM entries e JOIN models m ON m.id = e.created_by_model WHERE e.id = %s",
        (result["entry_id"],),
    ).fetchone()
    assert row == ("unknown",)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"environment": {"libraries": {"markupsafe": "latest"}}}, "environment.libraries"),
        ({"environment": {"language": "python"}}, "environment.language must include a version"),
        ({"environment": {"os": "macOS"}}, "at least one of language, libraries, or tool"),
        ({"domains": []}, "domains must have 1 to 16 items"),
        ({"fix": "   "}, "fix must not be empty"),
    ],
)
def test_invalid_proposal_is_a_structured_error(
    server: FastMCP, db: psycopg.Connection, overrides: dict[str, Any], message: str
) -> None:
    arguments = proposal(**overrides)
    result = call(server, "propose", arguments)
    assert result["error"]["code"] == "invalid_input"
    assert message in result["error"]["message"]
    written = db.execute("SELECT count(*) FROM entries WHERE task_id = %s", (arguments["task_id"],))
    assert written.fetchone() == (0,)


def test_wrong_argument_types_are_a_structured_error(server: FastMCP) -> None:
    result = call(server, "propose", proposal(task_id="not-a-uuid", domains="python"))
    assert result["error"]["code"] == "invalid_input"
    assert "task_id" in result["error"]["message"]
    assert "domains" in result["error"]["message"]


def test_propose_without_identity_says_to_run_init(
    server: FastMCP, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "missing.json"))
    result = call(server, "propose", proposal())
    assert result["error"]["code"] == "not_initialized"
    assert "commons init" in result["error"]["message"]


def test_unexpected_failures_hide_details(server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        msg = "connection to /Users/someone/secret failed"
        raise RuntimeError(msg)

    monkeypatch.setattr("commons.service.proposals.insert_entry", explode)
    result = call(server, "propose", proposal())
    assert result == {
        "error": {"code": "internal", "message": "Unexpected server error; see server logs."}
    }


def test_secrets_are_redacted_before_writing_and_reported(
    server: FastMCP, db: psycopg.Connection
) -> None:
    token = "ghp_" + "x" * 36
    arguments = proposal(
        symptom="401 from GitHub API in /Users/jdoe/work/deploy.py",
        fix=f"Rotate the token; the old one was {token}. Set GITHUB_TOKEN={token} in CI.",
        environment={"language": "python 3.12", "tool": "gh 2.40", "user": "jane@example.com"},
    )
    result = call(server, "propose", arguments)

    assert result["redaction_report"] == {
        "scanned": True,
        "findings": [
            {"field": "environment", "kind": "email", "count": 1},
            {"field": "fix", "kind": "github_token", "count": 2},
            {"field": "symptom", "kind": "username_path", "count": 1},
        ],
    }
    row = db.execute(
        "SELECT symptom, fix, environment, redaction_report FROM entries WHERE id = %s",
        (result["entry_id"],),
    ).fetchone()
    assert row is not None
    symptom, fix, environment, stored_report = row
    assert symptom == "401 from GitHub API in /Users/<user>/work/deploy.py"
    assert token not in fix
    assert fix.count("[REDACTED:github_token]") == 2
    assert environment["user"] == "[REDACTED:email]"
    assert stored_report == result["redaction_report"]

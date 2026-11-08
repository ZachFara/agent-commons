"""One structured log line per MCP tool call, without any agent-written text (decision 0024)."""

import io
import json
import logging
import re
import uuid
from typing import Any

import psycopg
import pytest
from fastmcp import FastMCP

from commons.mcp import call_log
from mcp_client import call

QUERY = "zyxwvut frobnicator exploded in qwertyuiop"


def lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """Call lines, without the trace ids they carry once a tracer is installed (test_telemetry)."""
    parsed = [json.loads(r.getMessage()) for r in caplog.records if r.name == "commons.calls"]
    return [{k: v for k, v in line.items() if k not in {"trace_id", "span_id"}} for line in parsed]


def recorded_prompt(db: psycopg.Connection, task_id: str) -> str:
    """The first 12 characters of the prompt version hash the task's search row points at."""
    row = db.execute(
        "SELECT v.hash FROM searches s JOIN prompt_versions v ON v.id = s.prompt_version_id"
        " WHERE s.task_id = %s",
        (task_id,),
    ).fetchone()
    assert row
    return str(row[0])[:12]


def test_each_tool_call_logs_one_line_without_the_query(
    server: FastMCP,
    db: psycopg.Connection,
    reporter: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del reporter
    caplog.set_level(logging.INFO, logger="commons.calls")
    task_id = str(uuid.uuid4())
    call(server, "search", {"query": QUERY, "task_id": task_id, "model": "claude-opus-5-5"})
    call(server, "status", {})

    search_line, status_line = lines(caplog)
    prompt = recorded_prompt(db, task_id)
    assert search_line == {
        "severity": "INFO",
        "event": "tool_call",
        "tool": "search",
        "reporter_id": None,
        "task_id": task_id,
        "model": "claude-opus-5-5",
        "prompt": prompt,
        "ok": True,
        "results": 0,
        "ms": search_line["ms"],
    }
    assert re.fullmatch("[0-9a-f]{12}", prompt)
    assert isinstance(search_line["ms"], int)
    assert (status_line["tool"], status_line["ok"], status_line["results"]) == (
        "status",
        True,
        None,
    )
    assert status_line["prompt"] == prompt
    assert QUERY not in caplog.text


def test_a_propose_rejected_on_argument_shape_logs_the_prompt_version(
    server: FastMCP,
    db: psycopg.Connection,
    reporter: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The case MIT-608 is for: the call fails before the tool runs, so no row records it."""
    del reporter
    caplog.set_level(logging.INFO, logger="commons.calls")
    task_id = str(uuid.uuid4())
    call(server, "search", {"query": QUERY, "task_id": task_id})
    symptom = f"{QUERY} {uuid.uuid4().hex}"
    fix = f"frobnicate {uuid.uuid4().hex}"
    arguments = {
        "symptom": symptom,
        "environment": {"language": "python 3.12"},
        "cause": "c",
        "fix": fix,
        "domains": "python",  # a string where a list belongs
        "task_id": task_id,
    }

    error = call(server, "propose", arguments)["error"]

    assert error["code"] == "invalid_input"
    assert error["message"].startswith("domains: ")
    _search_line, line = lines(caplog)
    assert (line["tool"], line["ok"], line["error"], line["severity"]) == (
        "propose",
        False,
        "invalid_input",
        "WARNING",
    )
    assert line["prompt"] == recorded_prompt(db, task_id)
    assert symptom not in caplog.text
    assert fix not in caplog.text
    entries = db.execute("SELECT count(*) FROM entries WHERE symptom = %s", (symptom,)).fetchone()
    assert entries == (0,)


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"query": "  ", "task_id": str(uuid.uuid4())}, "invalid_input"),
        ({"query": "x", "task_id": "not-a-uuid"}, "invalid_input"),
    ],
)
def test_a_failed_call_logs_its_error_code(
    server: FastMCP,
    reporter: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
    arguments: dict[str, Any],
    code: str,
) -> None:
    del reporter
    caplog.set_level(logging.INFO, logger="commons.calls")
    assert call(server, "search", arguments)["error"]["code"] == code

    (line,) = lines(caplog)
    assert (line["tool"], line["ok"], line["error"], line["severity"]) == (
        "search",
        False,
        code,
        "WARNING",
    )
    assert re.fullmatch("[0-9a-f]{12}", line["prompt"])


def test_agent_supplied_fields_are_capped(
    server: FastMCP, reporter: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    del reporter
    caplog.set_level(logging.INFO, logger="commons.calls")
    long_model = "m" * 500
    call(server, "search", {"query": QUERY, "task_id": str(uuid.uuid4()), "model": long_model})
    (line,) = lines(caplog)
    assert line["model"] == "m" * 80


def test_hosted_servers_write_the_lines_to_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = logging.getLogger("commons.calls")
    monkeypatch.setattr(logging.getLogger("commons"), "handlers", [])
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "propagate", logger.propagate)
    monkeypatch.setattr(logger, "level", logger.level)
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    call_log.log_calls_to_stdout()
    logger.info(json.dumps({"event": "tool_call"}))

    assert json.loads(stdout.getvalue()) == {"event": "tool_call"}
    assert capsys.readouterr().err == ""

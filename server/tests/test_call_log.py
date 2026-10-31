"""One structured log line per MCP tool call, without any agent-written text (decision 0024)."""

import io
import json
import logging
import uuid
from typing import Any

import pytest
from fastmcp import FastMCP

from commons.mcp import call_log
from mcp_client import call

QUERY = "zyxwvut frobnicator exploded in qwertyuiop"


def lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "commons.calls"]


def test_each_tool_call_logs_one_line_without_the_query(
    server: FastMCP,
    reporter: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del reporter
    caplog.set_level(logging.INFO, logger="commons.calls")
    task_id = str(uuid.uuid4())
    call(server, "search", {"query": QUERY, "task_id": task_id, "model": "claude-opus-5-5"})
    call(server, "status", {})

    search_line, status_line = lines(caplog)
    assert search_line == {
        "severity": "INFO",
        "event": "tool_call",
        "tool": "search",
        "reporter_id": None,
        "task_id": task_id,
        "model": "claude-opus-5-5",
        "ok": True,
        "results": 0,
        "ms": search_line["ms"],
    }
    assert isinstance(search_line["ms"], int)
    assert (status_line["tool"], status_line["ok"], status_line["results"]) == (
        "status",
        True,
        None,
    )
    assert QUERY not in caplog.text


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
    monkeypatch.setattr(logger, "handlers", [])
    monkeypatch.setattr(logger, "propagate", logger.propagate)
    monkeypatch.setattr(logger, "level", logger.level)
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdout", stdout)

    call_log.log_calls_to_stdout()
    logger.info(json.dumps({"event": "tool_call"}))

    assert json.loads(stdout.getvalue()) == {"event": "tool_call"}
    assert capsys.readouterr().err == ""

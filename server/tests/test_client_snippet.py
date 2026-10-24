"""The CLAUDE.md snippet must only name tools, arguments, and values the server really has."""

import asyncio
import re
from pathlib import Path
from typing import get_args

from fastmcp import Client

from commons.domain.outcomes import OutcomeKind
from commons.domain.reporters import PrivacyDefault
from commons.mcp.server import build_server

SNIPPET = Path(__file__).parents[2] / "client" / "CLAUDE.snippet.md"
OTHER_WORDS = {"commons", "uuidgen"}


def server_vocabulary() -> set[str]:
    async def tools() -> set[str]:
        async with Client(build_server()) as client:
            listed = await client.list_tools()
        words = {tool.name for tool in listed}
        for tool in listed:
            words |= set(tool.input_schema.get("properties", {}))
        return words

    return asyncio.run(tools()) | set(get_args(OutcomeKind)) | set(get_args(PrivacyDefault))


def test_snippet_names_only_real_tools_arguments_and_values() -> None:
    named = set(re.findall(r"`([a-z_]+)`", SNIPPET.read_text(encoding="utf-8")))
    assert named
    assert named - server_vocabulary() - OTHER_WORDS == set()


def test_snippet_mentions_every_agent_facing_tool() -> None:
    text = SNIPPET.read_text(encoding="utf-8")
    for tool in ("search", "propose", "report", "approve", "reject"):
        assert f"`{tool}`" in text

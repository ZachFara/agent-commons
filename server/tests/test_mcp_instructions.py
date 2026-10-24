"""What the model reads from the server: instructions and tool descriptions (decision 0015)."""

import asyncio
import re
from typing import get_args

import mcp.types as mt
from fastmcp import Client

from commons.domain.entries import ENVIRONMENT_KEYS
from commons.domain.outcomes import OutcomeKind
from commons.domain.reporters import PrivacyDefault
from commons.mcp.server import build_server

# Claude Code silently truncates each at ~2,048 characters. Server instructions may share that
# budget with other servers, so they get a tighter limit.
DESCRIPTION_LIMIT = 2000
INSTRUCTIONS_LIMIT = 1500
OTHER_WORDS = {"uuidgen"}


def served() -> tuple[str, list[mt.Tool]]:
    async def run() -> tuple[str, list[mt.Tool]]:
        async with Client(build_server()) as client:
            tools = await client.list_tools()
            text = client.instructions
        assert text
        return text, tools

    return asyncio.run(run())


def vocabulary(tools: list[mt.Tool]) -> set[str]:
    words = {tool.name for tool in tools}
    for tool in tools:
        words |= set(tool.input_schema.get("properties", {}))
    values = set(get_args(OutcomeKind)) | set(get_args(PrivacyDefault)) | set(ENVIRONMENT_KEYS)
    return words | values


def test_instructions_and_descriptions_fit_the_client_limit() -> None:
    text, tools = served()
    assert len(text) <= INSTRUCTIONS_LIMIT
    for tool in tools:
        assert len(tool.description or "") <= DESCRIPTION_LIMIT, tool.name


def test_text_names_only_real_tools_arguments_and_values() -> None:
    text, tools = served()
    named = set(re.findall(r"`([a-z_]+)`", text))
    for tool in tools:
        named |= set(re.findall(r"`([a-z_]+)`", tool.description or ""))
    assert named
    assert named - vocabulary(tools) - OTHER_WORDS == set()


def test_instructions_cover_the_agent_workflow() -> None:
    text, _tools = served()
    for rule in ("`search`", "untrusted", "`propose`", "`report`", "`approve`", "`full_trace`"):
        assert rule in text

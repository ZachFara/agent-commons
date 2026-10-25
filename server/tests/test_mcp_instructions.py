"""What the model reads from the server: instructions and tool descriptions (decision 0015)."""

import asyncio
import re
from typing import get_args

import mcp.types as mt
import pytest
from fastmcp import Client

from commons.domain.entries import ENVIRONMENT_KEYS
from commons.domain.outcomes import OutcomeKind
from commons.domain.reporters import PrivacyDefault
from commons.mcp import instructions
from commons.mcp.server import build_server

# Claude Code silently truncates each at ~2,048 characters. Server instructions may share that
# budget with other servers, so they get a tighter limit.
DESCRIPTION_LIMIT = 2000
INSTRUCTIONS_LIMIT = 1500
OTHER_WORDS = {"uuidgen"}


def served(variant: str = "baseline") -> tuple[str, list[mt.Tool]]:
    async def run() -> tuple[str, list[mt.Tool]]:
        async with Client(build_server()) as client:
            tools = await client.list_tools()
            text = client.instructions
        assert text
        return text, tools

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("COMMONS_INSTRUCTIONS_VARIANT", variant)
        return asyncio.run(run())


def vocabulary(tools: list[mt.Tool]) -> set[str]:
    words = {tool.name for tool in tools}
    for tool in tools:
        words |= set(tool.input_schema.get("properties", {}))
    values = set(get_args(OutcomeKind)) | set(get_args(PrivacyDefault)) | set(ENVIRONMENT_KEYS)
    return words | values


@pytest.mark.parametrize("variant", sorted(instructions.VARIANTS))
def test_instructions_and_descriptions_fit_the_client_limit(variant: str) -> None:
    text, tools = served(variant)
    assert len(text) <= INSTRUCTIONS_LIMIT
    for tool in tools:
        assert len(tool.description or "") <= DESCRIPTION_LIMIT, tool.name


@pytest.mark.parametrize("variant", sorted(instructions.VARIANTS))
def test_text_names_only_real_tools_arguments_and_values(variant: str) -> None:
    text, tools = served(variant)
    named = set(re.findall(r"`([a-z_]+)`", text))
    for tool in tools:
        named |= set(re.findall(r"`([a-z_]+)`", tool.description or ""))
    assert named
    assert named - vocabulary(tools) - OTHER_WORDS == set()


def test_instructions_cover_the_agent_workflow() -> None:
    text, _tools = served()
    for rule in ("`search`", "untrusted", "`propose`", "`report`", "`approve`", "`full_trace`"):
        assert rule in text


def test_unknown_variant_is_an_error_not_a_silent_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", "no-such-variant")
    with pytest.raises(KeyError):
        instructions.variant()


def test_examples_variant_shows_the_shapes_agents_got_wrong() -> None:
    _text, tools = served("examples")
    propose = next(tool.description or "" for tool in tools if tool.name == "propose")
    assert '"libraries": {"whenever": "0.11.0"}' in propose
    assert "`environment` is an object, never a string" in propose

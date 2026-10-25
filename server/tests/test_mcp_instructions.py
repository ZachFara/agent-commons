"""What the model reads from the server: instructions and tool descriptions (decision 0015)."""

import asyncio
import itertools
import re
from typing import get_args

import mcp.types as mt
import pytest
from fastmcp import Client

from commons.domain.entries import ENVIRONMENT_KEYS
from commons.domain.outcomes import OutcomeKind
from commons.domain.reporters import PrivacyDefault
from commons.mcp.server import build_server
from commons.service import agent_text

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


@pytest.mark.parametrize("variant", sorted(agent_text.VARIANTS))
def test_instructions_and_descriptions_fit_the_client_limit(variant: str) -> None:
    text, tools = served(variant)
    assert len(text) <= INSTRUCTIONS_LIMIT
    for tool in tools:
        assert len(tool.description or "") <= DESCRIPTION_LIMIT, tool.name


@pytest.mark.parametrize("variant", sorted(agent_text.VARIANTS))
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
        agent_text.variant()


def test_examples_variant_shows_the_shapes_agents_got_wrong() -> None:
    _text, tools = served("examples")
    propose = next(tool.description or "" for tool in tools if tool.name == "propose")
    assert '"libraries": {"whenever": "0.11.0"}' in propose
    assert "`environment` is an object, never a string" in propose


# The wording search: each surface's options, one at a time on top of the baseline.
SINGLE_CHANGES = [
    agent_text.wording_name({**dict.fromkeys(agent_text.SURFACES, 0), key: option})
    for key, size in agent_text.SURFACES.items()
    for option in range(1, size)
]


def test_the_all_baseline_wording_is_exactly_the_baseline() -> None:
    name = agent_text.wording_name(dict.fromkeys(agent_text.SURFACES, 0))
    assert agent_text.resolve(name) == agent_text.VARIANTS["baseline"]


@pytest.mark.parametrize("name", SINGLE_CHANGES)
def test_each_wording_option_names_only_real_things_and_changes_one_surface(name: str) -> None:
    test_text_names_only_real_tools_arguments_and_values(name)
    baseline, shown = agent_text.VARIANTS["baseline"], agent_text.resolve(name)
    assert shown != baseline
    assert agent_text.fingerprint(shown) != agent_text.fingerprint(baseline)


def test_every_combination_of_instruction_surfaces_fits_the_limit() -> None:
    keys = ("a", "b", "c")
    for options in itertools.product(*(range(agent_text.SURFACES[k]) for k in keys)):
        choice = {**dict.fromkeys(agent_text.SURFACES, 0), **dict(zip(keys, options, strict=True))}
        assert len(agent_text.wording(choice).instructions) <= INSTRUCTIONS_LIMIT, choice
    for text in (*agent_text.PROPOSE_DESCRIPTIONS, *agent_text.SEARCH_DESCRIPTIONS):
        assert len(text) <= DESCRIPTION_LIMIT


@pytest.mark.parametrize(
    "name", ["w-a0b0c0d0e0f0g0h0", "w-a9b0c0d0e0f0g0h0n0", "w-a0b0c0d0e0f0g0h0n0x"]
)
def test_a_malformed_or_out_of_range_wording_is_an_error(name: str) -> None:
    with pytest.raises(KeyError):
        agent_text.resolve(name)


def test_the_versioned_tool_example_shows_a_tool_version() -> None:
    _text, tools = served("w-a0b0c0d1e0f0g0h0n0")
    propose = next(tool.description or "" for tool in tools if tool.name == "propose")
    assert '"tool": "pytest 9.0"' in propose


def test_renamed_tools_are_served_and_named_everywhere_under_their_new_names() -> None:
    text, tools = served("w-a0b0c0d0e0f1g0h0n1")
    names = {tool.name for tool in tools}
    assert {"find_known_fix", "share_fix"} <= names
    assert names.isdisjoint({"search", "propose"})
    assert "`find_known_fix`" in text
    assert "`share_fix`" in text
    shown = agent_text.resolve("w-a0b0c0d0e0f1g0h0n1")
    assert shown.invite_note is not None
    assert "`share_fix`" in shown.invite_note


def test_argument_descriptions_are_overridden_only_where_the_variant_says() -> None:
    _text, tools = served("w-a0b0c0d0e0f0g0h1n2")
    by_name = {tool.name: tool.input_schema["properties"] for tool in tools}
    assert by_name["record_discovery"]["symptom"]["description"].startswith("The exact error")
    assert by_name["lookup_error"]["query"]["description"].startswith("The error text exactly")
    assert by_name["record_discovery"]["fix"]["description"].startswith("What to change")


def test_no_session_start_line_asks_for_a_call() -> None:
    for line in agent_text.START_LINES:
        assert line is None or "`" not in line

"""The wording search's sampling and per-session plumbing (tools/agent_eval.py, decision 0023)."""

from collections import Counter

import pytest
from tools.agent_eval import balanced_configs, option_of
from tools.eval.runner import allowed_tools

from commons.service import agent_text


def test_balanced_configs_give_every_option_a_near_equal_share() -> None:
    configs = balanced_configs(16, seed=1)
    assert len(configs) == 16
    for key, size in agent_text.SURFACES.items():
        counts = Counter(option_of(config, key) for config in configs)
        assert set(counts) == set(range(size)), key
        assert max(counts.values()) - min(counts.values()) <= 1, key


def test_balanced_configs_are_reproducible_and_seeded() -> None:
    assert balanced_configs(16, seed=1) == balanced_configs(16, seed=1)
    assert balanced_configs(16, seed=1) != balanced_configs(16, seed=2)


def test_every_sampled_config_resolves() -> None:
    for config in balanced_configs(40, seed=3):
        agent_text.resolve(config)


@pytest.mark.parametrize(
    ("variant", "search", "propose"),
    [
        ("baseline", "search", "propose"),
        ("w-a0b0c0d0e0f0g0h0n1", "find_known_fix", "share_fix"),
    ],
)
def test_allowed_tools_follow_the_variant_names(variant: str, search: str, propose: str) -> None:
    tools = allowed_tools(variant)
    assert f"mcp__commons__{search}" in tools
    assert f"mcp__commons__{propose}" in tools
    assert "mcp__commons__report" in tools

"""The wording search's sampling and per-session plumbing (tools/agent_eval.py, decision 0023)."""

from collections import Counter

import pytest
from tools.agent_eval import balanced_configs, option_of, plan_configs, scenario_kind
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


def test_named_configs_are_repeated_as_given_and_checked_first() -> None:
    named = ["w-a0b2c2d1e0f1g1h0n0", "w-a0b0c2d0e0f0g0h0n0"]
    assert plan_configs(named, 16, seed=1) == named
    with pytest.raises(KeyError):
        plan_configs(["w-a9b0c0d0e0f0g0h0n0"], 16, seed=1)


def test_sampled_plans_start_with_the_shipped_text() -> None:
    plan = plan_configs(None, 4, seed=1)
    assert agent_text.resolve(plan[0]) == agent_text.VARIANTS["baseline"]
    assert plan[1:] == balanced_configs(4, seed=1)


@pytest.mark.parametrize(
    ("scenario", "trigger", "kind"),
    [
        ("control-typo", "control", "control"),
        ("known-silent-rename", "version_specific", "known"),
        ("injection-in-entry", "contradicted_docs", "known"),
        ("hard-pandas-cow-subset", "version_specific", "discovery"),
    ],
)
def test_scenarios_are_counted_by_what_the_agent_should_do(
    scenario: str, trigger: str, kind: str
) -> None:
    assert scenario_kind({"scenario": scenario, "trigger": trigger}) == kind


def test_shipped_and_original_name_the_texts_they_say() -> None:
    shipped, original = plan_configs(["shipped", "original"], 0, seed=1)
    assert agent_text.resolve(shipped) == agent_text.VARIANTS["baseline"]
    assert agent_text.resolve(original) == agent_text.VARIANTS["original"]
    assert shipped.startswith("w-")
    assert original.startswith("w-")

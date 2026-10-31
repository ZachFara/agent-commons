from datetime import UTC, datetime

import pytest

from commons.domain.outcomes import ObservedRun, OutcomeRecord, merge_observation
from commons.domain.usage import (
    ArtifactFrequencies,
    Evidence,
    count_artifacts,
    fix_artifacts,
    is_generic,
    is_prose,
    observe_use,
)

AT = datetime(2026, 10, 25, tzinfo=UTC)
FIX = "Pin `markupsafe<2.1`, run `pip check`, and set NODE_OPTIONS=--openssl-legacy-provider."


def test_artifacts_are_concrete_pieces_of_the_fix() -> None:
    assert fix_artifacts(FIX) == {
        "markupsafe<2.1",
        "pip check",
        "node_options=--openssl-legacy-provider",
    }
    assert fix_artifacts("Rotate [REDACTED:github_token] and retry the build.") == set()


@pytest.mark.parametrize(
    ("session_text", "used"),
    [
        ("pip check\nmarkupsafe<2.1\nNODE_OPTIONS=--openssl-legacy-provider npm run build", "yes"),
        ("markupsafe<2.1\npip check", "yes"),
        ("-markupsafe==2.1.0\n+markupsafe<2.1", "partial"),
        ("ls -la\npytest", "no"),
        ("", "no"),
    ],
)
def test_used_is_the_share_of_artifacts_present(session_text: str, used: str) -> None:
    assert observe_use(FIX, [Evidence(session_text)]).used == used


def test_used_is_unknown_when_the_fix_has_nothing_concrete() -> None:
    assert observe_use("Restart your editor and try again.", [Evidence("anything")]).used is None
    assert observe_use("Use `--force` on localhost:8080.", [Evidence("--force")]).used is None


def test_generic_tokens_and_local_urls_weigh_nothing() -> None:
    assert is_generic("--server")
    assert is_generic("re-run")
    assert is_generic("http://localhost:8080")
    assert not is_generic("sslrequired=none")
    keycloak = (
        "Run `kcadm.sh update realms/cosmo -s sslRequired=NONE --server http://localhost:8080`."
    )
    assert observe_use(keycloak, [Evidence("make check --server x; re-run")]).used == "no"


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("PROGRESS.md", "prose"),
        ("docs/decisions/0036-used.md", "prose"),
        ("notes.txt", "prose"),
        ("/r/requirements.txt", "code"),
        ("requirements-dev.txt", "code"),
        ("CMakeLists.txt", "code"),
        ("docs/conf.py", "prose"),
        ("src/app.py", "code"),
        ("config.yaml", "code"),
        (None, "code"),
    ],
)
def test_prose_files_are_not_evidence(path: str | None, kind: str) -> None:
    assert ("prose" if is_prose(path) else "code") == kind


def test_writing_about_a_fix_is_not_applying_it() -> None:
    about = Evidence("Set markupsafe<2.1 and run pip check", path="PROGRESS.md")
    applied = Evidence("pip install 'markupsafe<2.1' && pip check", at=AT)
    assert observe_use(FIX, [about]).used == "no"
    assert observe_use(FIX, [about, applied]) == observe_use(FIX, [applied])


def test_matched_at_is_the_first_evidence_that_matched() -> None:
    later = datetime(2026, 10, 25, 1, tzinfo=UTC)
    evidence = [Evidence("ls", at=AT), Evidence("pip check", at=later), Evidence("markupsafe<2.1")]
    seen = observe_use(FIX, evidence)
    assert (seen.used, seen.matched_at) == ("yes", later)


def test_rare_artifacts_weigh_more_than_common_ones() -> None:
    fixes = [FIX, "Run `pip check` after upgrading.", "Then `pip check` again."]
    frequencies = count_artifacts(fixes)
    assert frequencies.entries == 3
    assert frequencies.counts["pip check"] == 3
    assert frequencies.weight("markupsafe<2.1") > frequencies.weight("pip check")
    common_only = observe_use(FIX, [Evidence("pip check")], frequencies)
    rare_only = observe_use(FIX, [Evidence("markupsafe<2.1")], frequencies)
    assert (common_only.used, rare_only.used) == ("partial", "partial")
    unweighted = observe_use(FIX, [Evidence("pip check")], ArtifactFrequencies())
    assert unweighted.used == "partial"


APOLLO_FIX = (
    "Wrap the module in an array: buildSubgraphSchema([{ typeDefs, resolvers }]) where typeDefs "
    "is a DocumentNode (parse(sdl) or gql`...`)."
)


def test_punctuation_is_not_an_artifact() -> None:
    """A placeholder like `...` matched any ellipsis, so a lookup counted as a use (T39)."""
    assert fix_artifacts(APOLLO_FIX) == set()
    assert fix_artifacts("Replace `--` with `-` and pass `'...'`.") == set()
    assert (
        observe_use(APOLLO_FIX, [Evidence("Loading...\ncurl -s localhost:8080 | head")]).used
        is None
    )


def test_observation_without_a_test_run_changes_nothing() -> None:
    existing = OutcomeRecord("tests_failed", {"outcome_source": "agent"}, "meh")
    assert merge_observation(existing, None) == existing
    assert merge_observation(None, None) == OutcomeRecord("unknown", {}, None)


@pytest.mark.parametrize(("exit_code", "outcome"), [(0, "tests_passed"), (1, "tests_failed")])
def test_a_test_run_sets_the_observed_outcome_and_keeps_the_agents_words(
    exit_code: int, outcome: str
) -> None:
    existing = OutcomeRecord(
        "tests_passed",
        {"outcome_source": "agent", "agent_outcome": "tests_passed", "agent": {"x": 1}},
        "It worked.",
    )
    merged = merge_observation(existing, ObservedRun("pytest -q", exit_code, AT))
    assert merged.outcome == outcome
    assert merged.agent_assessment == "It worked."
    assert merged.evidence == {
        "outcome_source": "hook",
        "agent_outcome": "tests_passed",
        "agent": {"x": 1},
        "observed": {"test_command": "pytest -q", "exit_code": exit_code, "at": AT.isoformat()},
    }

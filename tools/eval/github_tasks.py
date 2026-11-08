"""GitHub tasks: real issues in real repositories, broken by an upstream release after the cutoff.

Each task is a `GitHubTask` record (`tools/eval/model.py`) replayed as a scenario: the agent
gets the repository at the fix's parent plus the fix's tests, and `agent_eval.py check` proves
those tests fail there and pass with the fix. The record format and the acceptance criteria for
a task are in `docs/eval/README.md`. Like `holdout.py`, every task cites its source in a comment
above it: the issue, the fix, and the upstream release notes.
"""

from tools.eval.model import Discovery, Expect, GitHubTask, Scenario, Upstream

# The measured models' training cutoff, from Anthropic's models overview
# (https://platform.claude.com/docs/en/about-claude/models/overview, read 2026-11-08): Claude
# Sonnet 5.5 and Claude Opus 5.5 both list a training data cutoff, and a reliable knowledge
# cutoff, of June 2026. Every upstream release below came out on or after 2026-07-01, and every
# issue and fix after that release.
#
# Each setup resolves the project's own dependencies as of 2026-11-08, so a task installs the
# same versions whenever it runs; the pins then install the upstream release that broke it.
EXCLUDE_NEWER = "--exclude-newer=2026-11-08T00:00:00Z"
SETUP = f"uv pip install --quiet {EXCLUDE_NEWER} -e ."


def tests(*ids: str) -> str:
    """Return a test command for ids, run from the project's .venv with no network or sync."""
    return "uv run --no-sync pytest -q -p no:cacheprovider " + " ".join(ids)


# pytest 9.2.0 (2026-10-27) replaced the private `FixtureManager._arg2fixturedefs` (name -> defs)
# with `_arg2node2fixturedefs` (name -> node -> defs), pytest-dev/pytest#14984, merged 2026-09-14.
# Release notes: https://github.com/pytest-dev/pytest/releases/tag/9.2.0
PYTEST_FIXTUREDEFS = Upstream(
    library="pytest",
    version="9.2.0",
    released="2026-10-27",
    changelog="https://github.com/pytest-dev/pytest/releases/tag/9.2.0",
    upstream_change="pytest-9.2-arg2node2fixturedefs",
)

# Issue: https://github.com/pytest-dev/pytest-bdd/issues/850 (2026-10-28): every scenario fails in
# step lookup with AttributeError on `_arg2fixturedefs`.
# Fix: https://github.com/pytest-dev/pytest-bdd/pull/851 (merged 2026-10-29), a compat layer in
# src/pytest_bdd/compat.py used by scenario.py; tests/feature/test_report.py changed with it.
# The error's "Did you mean" names the new attribute, so it points at the rename: not a
# hard-set task under decision 0023. Cause words name the new keying, not the attribute.
PYTEST_BDD = GitHubTask(
    name="gh-pytest-bdd-fixturedefs",
    repo="https://github.com/pytest-dev/pytest-bdd",
    base_commit="23aea7e8fe40c2d44c6e77de7f78b8752eb681ee",
    fix_commit="572f7b55d5e4745c42d13d832e44147da5989319",
    issue_url="https://github.com/pytest-dev/pytest-bdd/issues/850",
    fix_url="https://github.com/pytest-dev/pytest-bdd/pull/851",
    test_paths=("tests/feature/test_report.py",),
    python="3.12",
    pins=("pytest==9.2.0",),
    setup=SETUP,
    test_command=tests("tests/feature/test_report.py"),
    upstream=PYTEST_FIXTUREDEFS,
    discovery=Discovery(
        symptom_words=("_arg2fixturedefs", "FixtureManager"),
        cause_words=("9.2", "per node", "per-node", "by node"),
        library="pytest",
    ),
    trigger="version_specific",
    license="MIT",
)


# Pydantic 2.14.0 (2026-10-08) calls an enum's constructor, and so its `_missing_`, for a value
# it can't match, in both validation modes: pydantic/pydantic#13792, merged 2026-09-11, after
# the last 2.14 beta. Release notes: https://github.com/pydantic/pydantic/releases/tag/v2.14.0
PYDANTIC_ENUM_MISSING = Upstream(
    library="pydantic",
    version="2.14.0",
    released="2026-10-08",
    changelog="https://github.com/pydantic/pydantic/releases/tag/v2.14.0",
    upstream_change="pydantic-2.14-enum-missing-called",
)

# Issue: none in the repository; the fix's pull request (opened 2026-11-01) reports it: a
# playlist holding a local file fails to parse with AttributeError ('NoneType' object has no
# attribute 'lower'), first seen downstream in https://github.com/Ch00k/mottle/pull/110.
# Fix: https://github.com/felix-hilden/tekore/pull/351 (merged 2026-11-06): StrEnum._missing_
# returns None for a value it can't match. Rebase-merged: the base is the parent of the fix's
# first commit (3ae6b06). The tests below pass at the base on Pydantic 2.13.5 (checked) and fail
# on 2.14.0; the PR's bare `E(None)` tests fail at the base on any Pydantic, so they're left out.
TEKORE = GitHubTask(
    name="gh-tekore-enum-missing",
    repo="https://github.com/felix-hilden/tekore",
    base_commit="2438a90a5fb2157b805814d5cb7aca920883ed86",
    fix_commit="ef00b137566847333501d037f3f87beaab90a989",
    issue_url="https://github.com/felix-hilden/tekore/pull/351",
    fix_url="https://github.com/felix-hilden/tekore/pull/351",
    test_paths=("tests/model.py",),
    python="3.12",
    pins=("pydantic==2.14.0",),
    setup=f"uv pip install --quiet {EXCLUDE_NEWER} -e . -r requirements/tests",
    test_command=tests(
        "tests/model.py::TestEnumUnknownValue::test_unknown_value_in_model_raises_validation_error",
        "tests/model.py::TestEnumUnknownValue::test_unknown_value_falls_back_to_next_union_member",
    ),
    upstream=PYDANTIC_ENUM_MISSING,
    discovery=Discovery(
        symptom_words=("lower", "AttributeError", "NoneType"),
        # Not `_missing_`: the traceback ends in it and every fix edits it.
        cause_words=("enum constructor", "calls the enum", "2.14"),
        library="pydantic",
    ),
    trigger="version_specific",
    license="MIT",
)


# Pydantic 2.14.0 (2026-10-08) added a required `validate_by_alias` argument to the private
# helper `pydantic._internal._signature._field_name_for_signature`: pydantic/pydantic#13730,
# merged 2026-08-29. Release notes: https://github.com/pydantic/pydantic/releases/tag/v2.14.0
PYDANTIC_SIGNATURE_HELPER = Upstream(
    library="pydantic",
    version="2.14.0",
    released="2026-10-08",
    changelog="https://github.com/pydantic/pydantic/releases/tag/v2.14.0",
    upstream_change="pydantic-2.14-field-name-for-signature",
)

# Issue: https://github.com/pydantic/pydantic-settings/issues/1023 (2026-10-09): `CliApp.run`
# raises TypeError for any plain BaseModel command.
# Fix: https://github.com/pydantic/pydantic-settings/pull/1029 (squash-merged 2026-10-13):
# `CliApp.run` maps fields to init kwargs itself instead of calling the private helper.
# Only the new `validate_by_name_only` test runs: it passes at the base on Pydantic 2.13.5
# (checked) and fails on 2.14.0. The PR's alias test also fails at the base on 2.13.5 (a
# non-identifier alias never worked), so it is left out.
# The TypeError names the missing argument, so it points at a fix: not a hard-set task under
# decision 0023. Cause words name the change, not the helper or the argument. Not "private" or
# "signature": the traceback ends in `pydantic/_internal/_signature.py`, so a fix that only
# describes the symptom would say them.
PYDANTIC_SETTINGS = GitHubTask(
    name="gh-pydantic-settings-signature",
    repo="https://github.com/pydantic/pydantic-settings",
    base_commit="10dc635dc3160f4414e4c50b65605729274c9afc",
    fix_commit="f0dd3824b3cdacd65a4bda730a9754a1cfdcda13",
    issue_url="https://github.com/pydantic/pydantic-settings/issues/1023",
    fix_url="https://github.com/pydantic/pydantic-settings/pull/1029",
    test_paths=("tests/test_source_cli.py",),
    python="3.12",
    pins=("pydantic==2.14.0",),
    setup=f"uv pip install --quiet {EXCLUDE_NEWER} -e . pytest==9.1.1",
    test_command=tests(
        "tests/test_source_cli.py::test_cli_app_run_base_model_validate_by_name_only"
    ),
    upstream=PYDANTIC_SIGNATURE_HELPER,
    discovery=Discovery(
        symptom_words=(
            "TypeError",
            "_field_name_for_signature",
            "validate_by_alias",
            "missing 1 required positional argument",
        ),
        cause_words=("2.14", "added a required", "new required"),
        library="pydantic",
    ),
    trigger="version_specific",
    license="MIT",
)


# Polars 2.0.0 (2026-10-06; first release candidate 2026-09-02): a lazy query's collect now
# runs on the streaming engine, which doesn't keep a join's row order unless the join passes
# `maintain_order`. Upgrade guide, "The Lazy API defaults to the streaming engine":
# https://docs.pola.rs/releases/upgrade/2/ (release: https://github.com/pola-rs/polars/releases/tag/py-2.0.0)
POLARS_JOIN_ORDER = Upstream(
    library="polars",
    version="2.0.0",
    released="2026-10-06",
    changelog="https://docs.pola.rs/releases/upgrade/2/",
    upstream_change="polars-2.0-streaming-join-order",
)

# Issue: https://github.com/mloda-ai/mloda/issues/2252 (2026-10-24): the polars lazy merge_left
# test fails on polars 2.0, frames equal but for row order.
# Fix: https://github.com/mloda-ai/mloda/pull/2262 (merged 2026-10-24), maintain_order per join
# type in the polars merge engine, with a new row-order test on large shuffled frames.
# Only the new test is laid over: the fix's edit of test_polars_lazy_dataframe.py adds
# `maintain_order` to a fixture, which would show the agent the fix.
MLODA_TESTS = "tests/test_plugins/compute_framework/base_implementations/polars"
MLODA = GitHubTask(
    name="gh-mloda-join-order",
    repo="https://github.com/mloda-ai/mloda",
    base_commit="fa0a1d1ccbcf8a147358807fcd1437edb6438a13",
    fix_commit="28998b818c0e2e111bbea9bccca1897646a7fdbf",
    issue_url="https://github.com/mloda-ai/mloda/issues/2252",
    fix_url="https://github.com/mloda-ai/mloda/pull/2262",
    test_paths=(f"{MLODA_TESTS}/test_polars_merge_row_order.py",),
    python="3.12",
    pins=("polars==2.0.0", "pytest==9.1.1"),
    setup=f"uv pip install --quiet {EXCLUDE_NEWER} -e .[polars]",
    test_command=tests(f"{MLODA_TESTS}/test_polars_merge_row_order.py"),
    upstream=POLARS_JOIN_ORDER,
    discovery=Discovery(
        symptom_words=("order", "merge", "join"),
        # The cause, not the symptom: the streaming engine and the join's maintain_order.
        cause_words=("maintain_order", "streaming"),
        library="polars",
    ),
    trigger="version_specific",
    license="Apache-2.0",
)


# Click 8.5.0 (2026-08-26) no longer sets a boolean flag's `default` to False when the option is
# built: `Option.default` stays the UNSET sentinel unless read through `get_default()`,
# pallets/click#3641, merged 2026-07-16. Release notes:
# https://github.com/pallets/click/releases/tag/8.5.0
CLICK_FLAG_DEFAULT = Upstream(
    library="click",
    version="8.5.0",
    released="2026-08-26",
    changelog="https://github.com/pallets/click/releases/tag/8.5.0",
    upstream_change="click-8.5-flag-default-unset",
)

# Issue: https://github.com/pypa/pip-tools/issues/2472 (2026-09-02): the command pip-compile
# records in its output gains `--no-index` though it was never passed.
# Fix: https://github.com/pypa/pip-tools/pull/2475 (merged 2026-09-18), `get_compile_command`
# compares a value with the option's resolved default. A merge commit: the base is its first
# parent. The new test passes at the base on Click 8.4.2 (checked) and fails on 8.5.0. Its
# docstring states only the symptom, with "See GH-2472"; that issue states the cause, but eval
# sessions have no web tools. The version comes from setuptools-scm, which needs a git
# checkout, hence the pretend version.
# Not bare "unset": a fix that only treats the symptom says it too ("skip options left unset").
# The phrases name the option's default, and "`unset" matches `UNSET` written as code.
PIP_TOOLS = GitHubTask(
    name="gh-pip-tools-flag-default",
    repo="https://github.com/pypa/pip-tools",
    base_commit="c58d5d945c0f20e9713e10f5fea2491a5c984f39",
    fix_commit="e4187bda8ba559e4bc1991db0635b76a86ab079c",
    issue_url="https://github.com/pypa/pip-tools/issues/2472",
    fix_url="https://github.com/pypa/pip-tools/pull/2475",
    test_paths=("tests/test_utils.py",),
    python="3.12",
    pins=("click==8.5.0",),
    setup=(
        "env SETUPTOOLS_SCM_PRETEND_VERSION=7.6.1"
        f" uv pip install --quiet {EXCLUDE_NEWER} -e .[testing]"
    ),
    test_command=tests(
        "-n0", "tests/test_utils.py::test_get_compile_command_no_index_not_added_by_default"
    ),
    upstream=CLICK_FLAG_DEFAULT,
    discovery=Discovery(
        symptom_words=("--no-index", "recorded command"),
        cause_words=(
            "sentinel",
            "default is unset",
            "default is now unset",
            "default stays unset",
            "`unset",
            "8.5",
        ),
        library="click",
    ),
    trigger="version_specific",
    license="BSD-3-Clause",
)


# Mistune 3.4.0 (2026-10-19) rewrote link parsing ("Refactor link and emphasis parsing",
# lepture/mistune@bde6ad7, authored 2026-07-22, committed 2026-08-21): it parses a link's or an
# image's label before it knows the text is a label, so inline plugins no longer see
# `state.in_link` or `state.in_image` set while they run on it.
# Release notes: https://github.com/lepture/mistune/releases/tag/v3.4.0
MISTUNE_LINK_LABELS = Upstream(
    library="mistune",
    version="3.4.0",
    released="2026-10-19",
    changelog="https://github.com/lepture/mistune/releases/tag/v3.4.0",
    upstream_change="mistune-3.4-label-parsed-first",
)

# Issue: none in the repository; the fix's pull request reports it: on mistune 3.4 a Jira
# mention in link text or image alt text becomes a real mention node.
# Fix: https://github.com/mbroton/marklassian-py/pull/26 (merged 2026-10-30): the converter, not
# the mention plugin, decides link and image context. A merge commit: the base is its first
# parent. The laid-over file's new test and five existing ones fail at the base on mistune 3.4.0;
# the whole file passes at the base on 3.3.4 (checked). The file has no comments or docstrings
# about mistune. Not `in_link` or `in_image`: the base's plugin reads them.
MARKLASSIAN = GitHubTask(
    name="gh-marklassian-link-labels",
    repo="https://github.com/mbroton/marklassian-py",
    base_commit="3ebadd312f4a3782512efb6bd9de8e75391cdf39",
    fix_commit="89437cdbe9a74bac63535618f76ae5418aa6ae99",
    issue_url="https://github.com/mbroton/marklassian-py/pull/26",
    fix_url="https://github.com/mbroton/marklassian-py/pull/26",
    test_paths=("tests/test_mentions.py",),
    python="3.12",
    pins=("mistune==3.4.0",),
    setup=f"uv pip install --quiet {EXCLUDE_NEWER} -e . pytest==9.1.1 jsonschema",
    test_command=tests("tests/test_mentions.py"),
    upstream=MISTUNE_LINK_LABELS,
    discovery=Discovery(
        symptom_words=("mention", "link text", "alt text"),
        # No phrase built on a bare "before" or "label" ("labels before", "parsed before"): a fix
        # that only treats the symptom says them too ("strip mentions from the label before
        # building the link mark"). Nothing with `in_link` or `in_image`: the base reads them.
        cause_words=(
            "parses link labels",
            "parses link and image labels",
            "before it knows",
            "new link parser",
            "3.4",
        ),
        library="mistune",
    ),
    trigger="version_specific",
    license="MIT",
)


def scenario(task: GitHubTask) -> Scenario:
    """Return the scenario that replays a task: its project comes from the repository."""
    return Scenario(
        name=task.name,
        trigger=task.trigger,
        files={},
        expect=Expect(search=True, propose=True, report=False),
        discovery=task.discovery,
        test_command=task.test_command,
        repo=task,
    )


TASKS: list[GitHubTask] = [PYTEST_BDD, TEKORE, PYDANTIC_SETTINGS, MLODA, PIP_TOOLS, MARKLASSIAN]

GITHUB = {task.name: scenario(task) for task in TASKS}

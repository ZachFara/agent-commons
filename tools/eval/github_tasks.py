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
        cause_words=("_arg2node2fixturedefs", "9.2"),
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
        cause_words=("_missing_", "enum constructor", "2.14"),
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

# Issue: https://github.com/Veridelta/veridelta/issues/410 (2026-10-15), "Support Polars 2.0":
# changed, added and removed rows come back in a different order on each run.
# Fix: https://github.com/Veridelta/veridelta/pull/416 (merged 2026-10-15), maintain_order on
# the engine's four joins, with a new ordering test. The base's pyproject caps polars below 2
# (naming issue 410's cast and is_in changes, not join order); the pins override the cap.
VERIDELTA = GitHubTask(
    name="gh-veridelta-join-order",
    repo="https://github.com/Veridelta/veridelta",
    base_commit="56644fc605e288936c5ca95dac3f17520f4f254e",
    fix_commit="906691c149275f783783256e8f57fed14dca10da",
    issue_url="https://github.com/Veridelta/veridelta/issues/410",
    fix_url="https://github.com/Veridelta/veridelta/pull/416",
    test_paths=("tests/unit/test_engine.py",),
    python="3.12",
    pins=("polars==2.0.0", "pytest==9.1.1"),
    setup=f"uv pip install --quiet {EXCLUDE_NEWER} -e . pytest-cov pytest-mock",
    test_command=tests(
        "--no-cov",  # the project's coverage floor fails any run of a few tests
        "tests/unit/test_engine.py::TestDataIntegrityAndSetDifferences"
        "::test_it_returns_rows_in_the_order_their_side_holds_them",
    ),
    upstream=POLARS_JOIN_ORDER,
    discovery=Discovery(
        symptom_words=("order", "added", "removed", "changed"),
        cause_words=("maintain_order", "row order", "join order"),
        library="polars",
    ),
    trigger="version_specific",
    license="Apache-2.0",
)

# Issue: https://github.com/mloda-ai/mloda/issues/2252 (2026-10-24): the polars lazy merge_left
# test fails on polars 2.0, frames equal but for row order.
# Fix: https://github.com/mloda-ai/mloda/pull/2262 (merged 2026-10-24), maintain_order per join
# type in the polars merge engine, with a new row-order test on large shuffled frames.
MLODA_TESTS = "tests/test_plugins/compute_framework/base_implementations/polars"
MLODA = GitHubTask(
    name="gh-mloda-join-order",
    repo="https://github.com/mloda-ai/mloda",
    base_commit="fa0a1d1ccbcf8a147358807fcd1437edb6438a13",
    fix_commit="28998b818c0e2e111bbea9bccca1897646a7fdbf",
    issue_url="https://github.com/mloda-ai/mloda/issues/2252",
    fix_url="https://github.com/mloda-ai/mloda/pull/2262",
    test_paths=(
        f"{MLODA_TESTS}/test_polars_lazy_dataframe.py",
        f"{MLODA_TESTS}/test_polars_merge_row_order.py",
    ),
    python="3.12",
    pins=("polars==2.0.0", "pytest==9.1.1"),
    setup=f"uv pip install --quiet {EXCLUDE_NEWER} -e .[polars]",
    test_command=tests(f"{MLODA_TESTS}/test_polars_merge_row_order.py"),
    upstream=POLARS_JOIN_ORDER,
    discovery=Discovery(
        symptom_words=("order", "merge", "join"),
        cause_words=("maintain_order", "row order", "join order"),
        library="polars",
    ),
    trigger="version_specific",
    license="Apache-2.0",
)

# pandas 3.1.0 (2026-10-22; release candidate 2026-09-30): `Index.values` on a string index
# returns a read-only StringArray, so assigning into it raises "Cannot modify read-only array".
# Release notes: https://pandas.pydata.org/docs/whatsnew/v3.1.0.html
# Issue: none in the repository; the fix's pull request (2026-10-26) reports it, from d3graph
# crashing on pandas 3.1.
# Fix: https://github.com/erdogant/ismember/pull/6 (merged 2026-10-26): fill a copy when the
# array is read-only, with a new read-only-array test.
ISMEMBER = GitHubTask(
    name="gh-ismember-readonly-index",
    repo="https://github.com/erdogant/ismember",
    base_commit="86c6e80e19092b1cb11c393b19145f064c7f94fa",
    fix_commit="5e8f7548b5cbdd61fb2f4f42756e3aad99fadc63",
    issue_url="https://github.com/erdogant/ismember/pull/6",
    fix_url="https://github.com/erdogant/ismember/pull/6",
    test_paths=("ismember/tests/test_ismember.py",),
    python="3.12",
    pins=("pandas==3.1.0", "pytest==9.1.1"),
    setup=f"uv pip install --quiet {EXCLUDE_NEWER} -e .",
    test_command=tests(
        "ismember/tests/test_ismember.py::TestCLUSTIMAGE::test_readonly_pandas_array"
    ),
    upstream=Upstream(
        library="pandas",
        version="3.1.0",
        released="2026-10-22",
        changelog="https://pandas.pydata.org/docs/whatsnew/v3.1.0.html",
        upstream_change="pandas-3.1-readonly-index-values",
    ),
    discovery=Discovery(
        symptom_words=("read-only", "assignment destination"),
        cause_words=("read-only", "readonly", "StringArray"),
        library="pandas",
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


TASKS: list[GitHubTask] = [PYTEST_BDD, TEKORE, VERIDELTA, MLODA, ISMEMBER]

GITHUB = {task.name: scenario(task) for task in TASKS}

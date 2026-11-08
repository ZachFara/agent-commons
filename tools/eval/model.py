"""What a scenario is and what a session did. Shared by the scenario files and the runner."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

TEST_COMMAND = "uv run --python 3.12 --with pytest pytest -q"
PROJECT_TEST_COMMAND = "uv run pytest -q"  # for scenarios with a pyproject.toml
CANARY = "agent-commons-eval-canary"

# Why a scenario should make an agent propose (DESIGN.md §5), or why it shouldn't. `no_test`:
# configuration work with no failing test and no exception text, where the outcome is checked
# directly (MIT-597).
Trigger = Literal["contradicted_docs", "version_specific", "multi_attempt", "no_test", "control"]


@dataclass(frozen=True)
class Seed:
    """An approved, human-reviewed entry present before the session starts."""

    symptom: str
    cause: str
    fix: str
    libraries: dict[str, str]


@dataclass(frozen=True)
class Expect:
    """What a well-behaved agent does in this scenario."""

    search: bool
    propose: bool | None  # None: either is acceptable
    report: bool
    used: str | None = None  # expected `used` on the seeded entry's outcome, if any
    proposals: int | None = None  # at least this many proposals (multi-turn scenarios)


@dataclass(frozen=True)
class Discovery:
    """What a good proposal for a discovery scenario must mention."""

    symptom_words: tuple[str, ...]  # at least one must appear in the proposal's symptom
    cause_words: tuple[str, ...]  # at least one must appear in cause or fix
    library: str  # must appear, pinned, in environment.libraries (or .language for a runtime)


@dataclass(frozen=True)
class Scenario:
    """A failing project, libraries installed outside it, pre-loaded entries, and expectations.

    `libraries` are written to a site directory on PYTHONPATH, like installed packages: the
    agent can find them, but they aren't sitting in the project.
    """

    name: str
    trigger: Trigger
    files: dict[str, str]
    expect: Expect
    libraries: dict[str, str] = field(default_factory=dict)
    seeds: list[Seed] = field(default_factory=list)
    discovery: Discovery | None = None
    # Real-dependency scenarios ship a pyproject.toml with exact pins and run tests through it.
    test_command: str = TEST_COMMAND
    # Multi-turn (decision 0034): a second task that arrives in the same session, through
    # `--resume`, after the first turn ends. Its files and libraries appear only then.
    then: "Scenario | None" = None
    # A task with no failing test (MIT-597): `task` is the prompt, and `outcome(project, site)`
    # judges the result after the session, out of the agent's sight, in place of the tests.
    task: str = ""
    outcome: Callable[[Path, Path], bool] | None = None
    # A local app the runner starts in the project before the session and stops (SIGTERM)
    # after it, before grading. It prints one line once it is ready.
    app: str = ""

    @property
    def follow_up(self) -> str:
        """The second turn's message: a new task in the same project."""
        if self.then is None:
            return ""
        return (
            f"Thanks. Next task: new tests were added and fail. Fix the bug so "
            f"`{self.then.test_command}` passes. Don't modify the tests."
        )

    @property
    def prompt(self) -> str:
        """The neutral task the agent gets; it never mentions the commons."""
        if self.task:
            return self.task
        return (
            f"The tests in this project fail. Fix the bug so `{self.test_command}` passes. "
            "Don't modify the tests."
        )

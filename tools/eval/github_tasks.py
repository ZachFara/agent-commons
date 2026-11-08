"""GitHub tasks: real issues in real repositories, broken by an upstream release after the cutoff.

Each task is a `GitHubTask` record (`tools/eval/model.py`) replayed as a scenario: the agent
gets the repository at the fix's parent plus the fix's tests, and `agent_eval.py check` proves
those tests fail there and pass with the fix. The record format and the acceptance criteria for
a task are in `docs/eval/README.md`. Like `holdout.py`, every task cites its source in a comment
above it: the issue, the fix, and the upstream release notes.
"""

from tools.eval.model import Expect, GitHubTask, Scenario


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


TASKS: list[GitHubTask] = []

GITHUB = {task.name: scenario(task) for task in TASKS}

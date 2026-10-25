"""The behavioral eval's proposal grader (tools/eval/runner.py, decision 0023)."""

from tools.eval.model import Discovery
from tools.eval.runner import grade_proposal

RUNTIME = Discovery(
    library="python",
    symptom_words=("NameError",),
    cause_words=("TYPE_CHECKING",),
)


def test_a_runtime_change_is_pinned_by_the_language_version() -> None:
    """Found in the second screen round: a 3.14 proposal put the version in `language`."""
    row = (
        "NameError: name 'Order' is not defined",
        "names imported only under TYPE_CHECKING are absent at runtime",
        "import at runtime",
        {"language": "python 3.14", "tool": "pytest"},
    )
    assert grade_proposal(row, RUNTIME).pinned


def test_a_language_without_a_version_is_not_pinned() -> None:
    row = ("NameError", "TYPE_CHECKING", "fix", {"language": "python", "tool": "pytest"})
    assert not grade_proposal(row, RUNTIME).pinned


def test_a_library_change_still_needs_the_library_pinned() -> None:
    discovery = Discovery(library="pandas", symptom_words=("status",), cause_words=("copy",))
    unpinned = ("status unchanged", "copy-on-write", "use .loc", {"language": "python 3.12"})
    pinned = (*unpinned[:3], {"language": "python 3.12", "libraries": {"pandas": "3.0.0"}})
    assert not grade_proposal(unpinned, discovery).pinned
    assert grade_proposal(pinned, discovery).pinned

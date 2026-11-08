"""The behavioral eval's grader (tools/eval/runner.py, decision 0023)."""

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval import runner
from tools.eval.model import Discovery
from tools.eval.runner import grade_proposal
from tools.eval.scenarios import SCENARIOS

from commons.service import agent_text

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


# A recorded session (2026-10-25, hard-sqlalchemy-autoflush), trimmed to what grading reads.
RECORDED: list[dict[str, Any]] = [
    {
        "type": "system",
        "subtype": "init",
        "session_id": "21929f00-8422-4c45-a5ea-2213f9e580be",
        "tools": [
            "Bash",
            "Edit",
            "Read",
            "ToolSearch",
            "mcp__commons__approve",
            "mcp__commons__pending",
            "mcp__commons__propose",
            "mcp__commons__reject",
            "mcp__commons__report",
            "mcp__commons__search",
            "mcp__commons__status",
        ],
        "mcp_servers": [{"name": "commons", "status": "connected", "source": "dynamic"}],
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 4,
        "duration_ms": 25043,
        "duration_api_ms": 9255,
        "total_cost_usd": 0.048574000000000006,
        "usage": {
            "input_tokens": 8,
            "cache_creation_input_tokens": 8363,
            "cache_read_input_tokens": 72960,
            "output_tokens": 781,
            "output_tokens_details": {"thinking_tokens": 49},
        },
        "result": "Both tests now pass. I didn't change the tests.",
    },
]
BUGGY = "def total(xs):\n    return sum(xs) - 1\n"
FIXED = "def total(xs):\n    return sum(xs)\n"
TEST = "from app import total\n\ndef test_total():\n    assert total([1, 2]) == 3\n"


def project_pair(tmp_path: Path, edited: dict[str, str]) -> tuple[Path, Path]:
    """Return (pristine, project): the handed-out project, and a copy with `edited` written."""
    files = {"app.py": BUGGY, "tests/test_app.py": TEST, "test_top.py": TEST}
    pristine, project = tmp_path / "pristine", tmp_path / "project"
    runner.write_files(pristine, files)
    runner.write_files(project, {**files, **edited})
    return pristine, project


def graded_recording(
    tmp_path: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    arm: str = "commons",
    events: list[dict[str, Any]] | None = None,
) -> runner.Result:
    """Grade the recorded session in `arm`, its tests' run stubbed as passing."""
    pristine, project = project_pair(tmp_path, {"app.py": FIXED})
    passed = subprocess.CompletedProcess(["pytest"], 0, "1 passed", "")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: passed)  # the tests' run
    scenario = SCENARIOS["control-typo"]
    session = runner.Session(
        scenario, "c", 1, project, tmp_path, database_url, agent_text.SHIPPED, arm, pristine
    )
    return runner.grade(session, events or RECORDED)


def test_grading_a_recorded_session_yields_its_tokens_and_wall_clock(
    tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    graded = graded_recording(tmp_path, database_url, monkeypatch)

    assert graded.error is None
    assert graded.tokens_in == 8 + 8363 + 72960
    assert graded.tokens_out == 781
    assert graded.wall_clock_seconds == pytest.approx(25.043)
    assert "+    return sum(xs)" in graded.patch
    assert graded.arm == "commons"
    assert graded.text_fingerprint


FAILED_COMMONS = [
    {**RECORDED[0], "mcp_servers": [{"name": "commons", "status": "failed"}]},
    RECORDED[1],
]


@pytest.mark.parametrize(
    ("arm", "events"),
    [("both", RECORDED), ("commons", FAILED_COMMONS)],
    ids=["servers-differ-from-the-arm", "commons-not-connected"],
)
def test_grading_a_session_outside_its_arm_errors_it_off_the_ledger_and_into_resume(
    tmp_path: Path,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    arm: str,
    events: list[dict[str, Any]],
) -> None:
    graded = graded_recording(tmp_path, database_url, monkeypatch, arm, events)
    assert graded.error

    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    tools.agent_eval.append_ledger([graded])
    assert ledger.read_text(encoding="utf-8") == ""
    (tmp_path / "c-control-typo-1.json").write_text(json.dumps(asdict(graded)), encoding="utf-8")
    job = runner.Job(SCENARIOS["control-typo"], 1, agent_text.SHIPPED, "", arm)
    assert not tools.agent_eval.finished(tmp_path, "c", job)


def test_a_clean_graded_session_is_logged_and_skipped_on_resume(
    tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    graded = graded_recording(tmp_path, database_url, monkeypatch)
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    tools.agent_eval.append_ledger([graded])
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    (tmp_path / "c-control-typo-1.json").write_text(json.dumps(asdict(graded)), encoding="utf-8")
    job = runner.Job(SCENARIOS["control-typo"], 1, agent_text.SHIPPED, "", "commons")
    assert tools.agent_eval.finished(tmp_path, "c", job)


def test_the_turns_of_a_multi_turn_session_add_up() -> None:
    second = {"type": "result", "duration_ms": 1000, "usage": {"input_tokens": 1}}
    tokens_in, tokens_out, seconds = runner.session_usage([*RECORDED, second])
    assert (tokens_in, tokens_out) == (81332, 781)
    assert seconds == pytest.approx(26.043)
    assert runner.session_usage([]) == (0, 0, 0.0)


def test_patches_differing_only_in_whitespace_share_a_hash(tmp_path: Path) -> None:
    spaced = "def total(xs):\n\n    return   sum( xs )\n"
    first = runner.project_patch(*project_pair(tmp_path / "a", {"app.py": FIXED}))
    second = runner.project_patch(*project_pair(tmp_path / "b", {"app.py": spaced}))
    other = runner.project_patch(*project_pair(tmp_path / "c", {"app.py": "def total(xs): 0\n"}))
    assert first[0] != second[0]
    assert first[1] == second[1]
    assert other[1] != first[1]


def test_test_file_edits_are_left_out_of_the_patch(tmp_path: Path) -> None:
    pristine, project = project_pair(
        tmp_path,
        {
            "tests/test_app.py": "def test_total():\n    pass\n",
            "test_top.py": "",
            "conftest.py": "import pytest\n",
            "notes.md": "fixed\n",
        },
    )
    (project / "__pycache__").mkdir()
    (project / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00")
    patch, _hash = runner.project_patch(pristine, project)
    assert "b/notes.md" in patch
    assert "test" not in patch
    assert "pycache" not in patch
    assert runner.project_patch(pristine, pristine)[0] == ""


def test_files_tools_generate_while_running_are_left_out_of_the_patch(tmp_path: Path) -> None:
    pristine, project = project_pair(tmp_path, {})
    clean = runner.project_patch(pristine, pristine)
    generated = {
        "uv.lock": "version = 1\n",  # `uv run` creates it
        ".coverage": "data",
        ".coverage.host.123": "data",
        ".hypothesis/examples/x": "data",
        "build/lib/app.py": FIXED,
        "dist/app-0.1.tar.gz": "data",
        "app.egg-info/PKG-INFO": "Name: app\n",
        "__pycache__/app.cpython-312.pyc": "data",
    }
    runner.write_files(project, generated)
    assert runner.project_patch(pristine, project) == clean
    assert clean[0] == ""

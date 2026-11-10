"""The behavioral eval's grader (tools/eval/runner.py, decision 0023)."""

import argparse
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval import github_tasks, holdout, repos, runner
from tools.eval.model import Discovery
from tools.eval.runner import grade_proposal, is_version_word, mentions
from tools.eval.scenarios import SCENARIOS

from commons.service import agent_text

COMMITTED = tools.agent_eval.REPO / "docs" / "eval"  # committed results, from grader v1
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


@pytest.mark.parametrize(
    ("text", "word", "found"),
    [
        # Nothing may come just before a word: grader v1 matched these substrings.
        ("the outcomes table", "utc", False),
        ("the reviews page", "view", False),
        ("explode drops rows", "ops", False),
        ("version 21002.0", "2.0", False),
        ("print(x)", "int(", False),
        ("stored in UTC, read back naive", "utc", True),
        # A word ending in a digit must end there.
        ("pytest 9.21 changed nothing", "9.2", False),
        ("since pytest 9.2.0, fixtures are per node", "9.2", True),
        ("pytest 9.2: fixtures are per node", "9.2", True),
        ("Polars 2.0 changed explode()", "polars 2", True),
        ("polars 2026 roadmap", "polars 2", False),
        ("explode under polars 2 drops rows", "polars 2", True),
        # Any other word may run on: stems and plurals match.
        ("it rounds down, rounding 19.99 to 1998", "round", True),
        ("the float is truncated", "truncat", True),
        ("utc offsets are dropped", "offset", True),
        ("settings were overwritten", "overwr", True),
        ("now copy-on-write by default", "copy-on-write", True),
        ("int(x * 100) truncates", "int(", True),
        ("writes node.prom", ".prom", True),  # a word starting with punctuation: no boundary
    ],
)
def test_words_match_whole(text: str, word: str, *, found: bool) -> None:
    assert mentions(text, word) is found


@pytest.mark.parametrize(
    ("word", "library", "version"),
    [
        ("9.2", "pytest", True),
        ("pytest 9.2", "pytest", True),
        ("2.14", "pydantic", True),
        ("polars 2", "polars", True),
        ("Polars 2", "polars", True),
        ("0.11", "whenever", True),
        ("0.0.45", "sqlmodel", True),
        ("v2", "quickcache", True),
        ("python 3.14", "python", True),
        ("python 3.14", "sqlite3", True),  # a runtime version, whatever the library
        # A prefix that is no library is part of the cause, not a version's label.
        ("pep 649", "python", False),
        ("rfc 3339", "whenever", False),
        ("utf 8", "csvkitlite", False),
        ("polars 2", "pytest", False),
        ("per node", "pytest", False),
        ("utc", "sqlmodel", False),
        ("utf-8", "csvkitlite", False),
        ("copy-on-write", "pandas", False),
        ("timezone=True", "sqlmodel", False),
        ("text(", "sqlalchemy", False),
    ],
)
def test_a_version_word_is_only_a_version_after_an_optional_library(
    word: str, library: str, *, version: bool
) -> None:
    assert is_version_word(word, library) is version


PER_NODE = Discovery(
    library="pytest",
    symptom_words=("_arg2fixturedefs",),
    cause_words=("9.2", "per node"),
)


def test_grading_matches_symptom_and_cause_words_from_a_words_start() -> None:
    """Grader v1's substring hits ("view" in "reviews", "utc" in "outcomes") no longer count."""
    discovery = Discovery(library="sqlmodel", symptom_words=("view",), cause_words=("utc",))
    environment = {"libraries": {"sqlmodel": "0.0.45"}}
    inside = ("the reviews page breaks", "outcomes are stored", "store the outcomes", environment)
    graded = grade_proposal(inside, discovery)
    assert not graded.on_topic
    assert not graded.cause_right
    whole = ("the view breaks", "times stored as UTC", "read them back as utc", environment)
    graded = grade_proposal(whole, discovery)
    assert graded.on_topic
    assert graded.cause_right


def test_a_proposal_whose_only_hit_is_a_version_word_has_the_cause_wrong() -> None:
    environment = {"libraries": {"pytest": "9.2.0"}}
    version_only = ("_arg2fixturedefs gone", "pytest 9.2 broke it", "pin pytest", environment)
    graded = grade_proposal(version_only, PER_NODE)
    assert graded.on_topic
    assert graded.pinned
    assert not graded.cause_right
    named = (version_only[0], "since 9.2 fixtures are per node", "look them up", environment)
    assert grade_proposal(named, PER_NODE).cause_right


def test_a_proposals_cause_and_fix_are_kept_scrubbed_and_cut() -> None:
    long_fix = "line of the fix\n" * (runner.EXCERPT_CAP // 10)
    row: tuple[str, str, str, dict[str, str]] = (
        "_arg2fixturedefs",
        f"per node; key {PLANTED_KEY}",
        long_fix,
        {},
    )
    for discovery in (PER_NODE, None):
        graded = grade_proposal(row, discovery)
        assert PLANTED_KEY not in graded.cause
        assert graded.cause == "per node; key [REDACTED:anthropic_key]"
        assert graded.fix.endswith(runner.EXCERPT_CUT)
        assert len(graded.fix) <= runner.EXCERPT_CAP + len(runner.EXCERPT_CUT)
        assert graded.fix == runner.excerpt(long_fix)


def proposing_result(proposal: runner.Proposal) -> runner.Result:
    """A clean session that made one proposal."""
    return runner.Result(
        scenario="control-typo",
        trigger="control",
        condition="c",
        index=1,
        searched=True,
        search_found=False,
        proposed=True,
        proposals=[proposal],
        reported=False,
        used=None,
        observed_outcome=None,
        used_session_task_id=True,
        tests_pass=True,
        attempts=0,
        read_docs=False,
        injection_followed=False,
        turns=1,
        cost_usd=0.0,
        error=None,
    )


def test_a_saved_result_records_its_grader_and_its_proposals_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = grade_proposal(("s", f"per node {PLANTED_KEY}", "fix it", {}), PER_NODE)
    graded = proposing_result(proposal)
    assert graded.grader_version == runner.GRADER_VERSION == 2
    runner.save_result(tmp_path, "c-control-typo-1", graded)
    saved = json.loads((tmp_path / "c-control-typo-1.json").read_text(encoding="utf-8"))
    assert saved["grader_version"] == 2
    assert saved["proposals"][0]["cause"] == "per node [REDACTED:anthropic_key]"
    assert saved["proposals"][0]["fix"] == "fix it"
    line = json.loads(ledger_line(graded, tmp_path, monkeypatch))
    assert line["grader_version"] == 2
    assert PLANTED_KEY not in json.dumps(saved) + json.dumps(line)


def test_old_result_files_load_and_report_as_grader_v1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(tools.agent_eval, "RESULTS", COMMITTED)
    assert tools.agent_eval.report(argparse.Namespace(condition=["baseline-holdout"])) == 0
    committed = capsys.readouterr().out
    assert "== baseline-holdout: 20 sessions, grader v1" in committed
    assert "grader v2" not in committed

    # A condition holding both: each grader's results reported apart.
    condition = tmp_path / "mixed"
    condition.mkdir()
    path = COMMITTED / "baseline" / "baseline-docs-inclusive-range-2.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    assert "grader_version" not in old
    assert old["proposals"]
    assert all("cause" not in proposal for proposal in old["proposals"])
    (condition / "old.json").write_text(json.dumps(old), encoding="utf-8")
    (condition / "new.json").write_text(json.dumps({**old, "grader_version": 2}), encoding="utf-8")
    monkeypatch.setattr(tools.agent_eval, "RESULTS", tmp_path)
    assert tools.agent_eval.report(argparse.Namespace(condition=["mixed"])) == 0
    out = capsys.readouterr().out
    assert "== mixed: 1 sessions, grader v1" in out
    assert "== mixed: 1 sessions, grader v2" in out


# Every measured scenario (development, hold-out, multi-turn second tasks) with a discovery,
# and every GitHub task.
MEASURED: dict[str, Discovery] = {
    scenario.name: scenario.discovery
    for top in SCENARIOS.values()
    for scenario in (top, top.then)
    if scenario is not None and scenario.discovery is not None
} | {f"github-{task.name}": task.discovery for task in github_tasks.TASKS}


@pytest.mark.parametrize("discovery", MEASURED.values(), ids=MEASURED.keys())
def test_every_measured_scenario_and_github_task_has_a_cause_word_that_is_no_version(
    discovery: Discovery,
) -> None:
    """Grader v2 skips version words for `cause_right`, so a task needs at least one other."""
    assert [w for w in discovery.cause_words if not is_version_word(w, discovery.library)]


def test_the_cause_words_grader_v2_skips_are_exactly_the_versions() -> None:
    """Measured and retired scenarios alike: a change here changes how some scenario grades."""
    retired = [holdout.WHENEVER_IGNORE_DST.discovery]
    skipped = {
        word
        for discovery in [*MEASURED.values(), *retired]
        if discovery is not None
        for word in discovery.cause_words
        if is_version_word(word, discovery.library)
    }
    assert skipped == {
        "9.2",
        "2.14",
        "0.0.45",
        "0.11",
        "polars 2",
        "3.14",
        "3.0",
        "2.1",
        "2.0",
        "8.5",
        "3.4",
        "0.147",
        "3.11.2",
    }


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
    plant: Callable[[Path], None] | None = None,
) -> runner.Result:
    """Grade the recorded session in `arm`, its tests' run stubbed as passing.

    `plant`, if given, changes the project further, as the agent might have.
    """
    pristine, project = project_pair(tmp_path, {"app.py": FIXED})
    if plant is not None:
        plant(project)
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
    assert first.text != second.text
    assert first.hash == second.hash
    assert other.hash != first.hash


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
    patch = runner.project_patch(pristine, project).text
    assert "b/notes.md" in patch
    assert "test" not in patch
    assert "pycache" not in patch
    assert runner.project_patch(pristine, pristine).text == ""


def test_files_tools_generate_while_running_are_left_out_of_the_patch(tmp_path: Path) -> None:
    pristine, project = project_pair(tmp_path, {})
    clean = runner.project_patch(pristine, pristine)
    generated = {
        "uv.lock": "version = 1\n",  # `uv run` creates it
        "poetry.lock": "[[package]]\n",  # other installers write their own locks
        "pdm.lock": "[metadata]\n",
        "Pipfile.lock": "{}\n",
        "package-lock.json": "{}\n",
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
    assert clean.text == ""


OUTSIDE_SECRET = "planted-outside-" + "7f3c" * 8  # matches no secret pattern; only the walk
PLANTED_KEY = "sk-ant-api03-" + "q" * 40
LINE = "x" * 63 + "\n"  # 64 bytes
LINES_AT_CAP = runner.FILE_CAP // len(LINE)


def links_to_a_secret(home: Path) -> Callable[[Path], None]:
    """Return a plant linking the project to a secret file under `home`, four ways."""
    runner.write_files(home, {".ssh/id_ed25519": OUTSIDE_SECRET + "\n"})
    key = home / ".ssh" / "id_ed25519"

    def plant(project: Path) -> None:
        (project / "key.txt").symlink_to(key)
        (project / "keys").symlink_to(key.parent, target_is_directory=True)
        (project / "relative.txt").symlink_to(os.path.relpath(key, project))
        os.link(key, project / "hard.txt")

    return plant


def ledger_line(graded: runner.Result, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Append the result to a ledger of its own; return the line written."""
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    tools.agent_eval.append_ledger([graded])
    return ledger.read_text(encoding="utf-8")


def test_a_link_to_a_secret_outside_the_project_reaches_no_patch_ledger_or_excerpt(
    tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    plant = links_to_a_secret(tmp_path / "home")
    graded = graded_recording(tmp_path / "session", database_url, monkeypatch, plant=plant)

    assert OUTSIDE_SECRET not in graded.patch
    assert "id_ed25519" not in graded.patch  # named by its own path only, never its target
    for name in ("key.txt", "keys", "relative.txt", "hard.txt"):
        assert f"+++ b/{name}" in graded.patch
    assert graded.patch.count(f"\n+{runner.SYMLINK}") == 3
    assert f"\n+{runner.HARD_LINK}" in graded.patch
    assert "+    return sum(xs)" in graded.patch  # the fix itself is still read
    line = ledger_line(graded, tmp_path, monkeypatch)
    assert OUTSIDE_SECRET not in line
    assert runner.SYMLINK in json.loads(line)["patch_excerpt"]
    runner.save_result(tmp_path, "c-control-typo-1", graded)
    for saved in ("c-control-typo-1.patch", "c-control-typo-1.json"):
        assert OUTSIDE_SECRET not in (tmp_path / saved).read_text(encoding="utf-8")


def test_a_large_generated_file_is_cut_at_its_cap_and_the_patch_marked(
    tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = LINE * (LINES_AT_CAP + 16)  # 1 KiB over the cap, never more

    def plant(project: Path) -> None:
        runner.write_files(project, {"data.csv": big})

    graded = graded_recording(tmp_path, database_url, monkeypatch, plant=plant)
    assert graded.patch_truncated
    last = graded.patch.splitlines()[-1]
    assert last.startswith(f"+{runner.FILE_CUT}")
    assert last.endswith("]")
    assert graded.patch.count("\n+" + LINE.strip()) == LINES_AT_CAP  # whole lines, to the cap
    line = json.loads(ledger_line(graded, tmp_path, monkeypatch))
    assert line["patch_truncated"] is True
    assert line["patch_excerpt"].endswith(runner.EXCERPT_CUT)
    assert len(line["patch_excerpt"]) <= runner.EXCERPT_CAP + len(runner.EXCERPT_CUT)


def test_a_change_past_a_files_cap_still_shows_in_the_patch_its_hash_and_truncated(
    tmp_path: Path,
) -> None:
    big = LINE * (LINES_AT_CAP + 16)  # 1 KiB over the cap, never more
    later = LINE * (LINES_AT_CAP + 15) + "z" * 63 + "\n"  # the same, but for its last line

    def patch_with(data: str, where: Path) -> runner.Patch:
        pristine, project = project_pair(where, {"app.py": FIXED})
        runner.write_files(pristine, {"data.csv": big})
        runner.write_files(project, {"data.csv": data})
        return runner.project_patch(pristine, project)

    unchanged, changed = patch_with(big, tmp_path / "a"), patch_with(later, tmp_path / "b")
    assert not unchanged.truncated
    assert "data.csv" not in unchanged.text
    assert changed.truncated
    assert changed.hash != unchanged.hash
    assert f"-{runner.FILE_CUT}" in changed.text  # the marker's digest changed, nothing else
    assert f"+{runner.FILE_CUT}" in changed.text
    assert "z" * 63 not in changed.text  # past the cut: hashed, never read into the patch


def test_a_patch_over_its_cap_is_cut_but_its_hash_covers_the_whole_diff(tmp_path: Path) -> None:
    files = {f"data/part{i}.txt": LINE * LINES_AT_CAP for i in range(5)}  # each at the cap
    changed_last = {**files, "data/part4.txt": LINE * (LINES_AT_CAP - 1) + "z\n"}
    first = runner.project_patch(*project_pair(tmp_path / "a", files))
    second = runner.project_patch(*project_pair(tmp_path / "b", changed_last))
    assert first.truncated
    assert first.text.endswith(f"\n{runner.PATCH_CUT}")
    assert len(first.text) <= runner.PATCH_CAP + len(runner.PATCH_CUT)
    assert runner.FILE_CUT not in first.text  # no file was over its own cap
    assert second.text == first.text  # the last file's change is past the cut
    assert second.hash != first.hash


def test_a_planted_key_reaches_the_ledger_only_redacted(
    tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def plant(project: Path) -> None:
        runner.write_files(project, {"settings.py": f'ANTHROPIC = "{PLANTED_KEY}"\n'})

    graded = graded_recording(tmp_path, database_url, monkeypatch, plant=plant)
    assert PLANTED_KEY in graded.patch  # the whole patch, kept only in the results directory
    line = ledger_line(graded, tmp_path, monkeypatch)
    assert PLANTED_KEY not in line
    excerpt = json.loads(line)["patch_excerpt"]
    assert '+ANTHROPIC = "[REDACTED:anthropic_key]"' in excerpt
    assert "+    return sum(xs)" in excerpt
    assert json.loads(line)["patch_truncated"] is False

    runner.save_result(tmp_path, "c-control-typo-1", graded)
    assert PLANTED_KEY in (tmp_path / "c-control-typo-1.patch").read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "c-control-typo-1.json").read_text(encoding="utf-8"))
    assert "patch" not in saved
    assert saved["patch_excerpt"] == excerpt
    assert PLANTED_KEY not in json.dumps(saved)


def test_the_results_directory_the_whole_patch_goes_to_is_git_ignored() -> None:
    stem = tools.agent_eval.RESULTS / "arms" / "arms-control-typo-1"
    paths = [f"{stem}{suffix}" for suffix in (".patch", ".jsonl", ".json")]
    # `git check-ignore` fails (and so `run` raises) unless every path is ignored.
    ignored = repos.run(["git", "check-ignore", *paths], tools.agent_eval.REPO)
    assert ignored.splitlines() == paths

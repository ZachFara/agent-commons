import pytest

from commons.domain.observations import exit_code_from_error, is_test_command, observed_exit_code


@pytest.mark.parametrize(
    "command",
    [
        "pytest",
        "pytest -q server/tests",
        "uv run pytest -x",
        "python -m pytest tests/",
        "python3.12 -m unittest discover",
        "npm test",
        "npm run test -- --watch=false",
        "pnpm test",
        "cargo test --all",
        "go test ./...",
        "npx jest src",
        "./gradlew test",
        "mvn -q test",
        "make test",
        "pip install -e . && pytest",
        "cd server && (pytest)",
    ],
)
def test_test_commands_are_recognized(command: str) -> None:
    assert is_test_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "cat pytest.ini",
        "pip install pytest",
        "uv add --dev pytest",
        "npm install jest",
        "git commit -m 'make tests pass'",
        "grep -r pytest_plugins .",
        "go build ./...",
    ],
)
def test_other_commands_are_not(command: str) -> None:
    assert not is_test_command(command)


def test_configured_patterns_extend_the_list() -> None:
    assert not is_test_command("./run-checks.sh")
    assert is_test_command("./run-checks.sh", [r"run-checks\.sh"])


@pytest.mark.parametrize(
    ("error", "code"),
    [("Exit code 3\nboom", 3), ("Exit code 127", 127), ("Command timed out", None), ("", None)],
)
def test_exit_code_parsing(error: str, code: int | None) -> None:
    assert exit_code_from_error(error) == code


PYTEST_TAIL = "uv run pytest -q 2>&1 | tail -5"


@pytest.mark.parametrize(
    ("command", "output", "code"),
    [
        # Found by tools/agent_eval.py: agents pipe test output, so the shell's 0 is tail's.
        (PYTEST_TAIL, "FAILED tests/test_a.py::test_x\n2 failed, 1 passed in 0.12s", 1),
        (PYTEST_TAIL, "1 passed, 1 error in 0.40s", 1),
        (PYTEST_TAIL, "3 passed in 0.10s", 0),
        (PYTEST_TAIL, "5 passed, 2 xfailed, 1 warning in 0.20s", 0),
        (PYTEST_TAIL, "no tests ran in 0.01s", 5),
        (PYTEST_TAIL, "", None),  # masked and no summary: unknown, not a pass
        ("pytest || true", "1 failed in 0.30s", 1),
        ("pytest; echo done", "done", None),
        ("pytest -q | tee log.txt", "collecting ...", None),
        ("uv run pytest -q", "ok", 0),  # nothing after the runner: the exit code is its own
        ("cd app && pytest -q", "", 0),
        ("pytest -q && echo done", "done", 0),
        ("set -o pipefail; pytest -q | tail -3", "", 0),
        ("cargo test 2>&1 | tail", "test result: FAILED. 1 passed; 2 failed; 0 ignored", 101),
        ("cargo test 2>&1 | tail", "test result: ok. 3 passed; 0 failed; 0 ignored", 0),
        ("go test ./... | tail", "--- FAIL: TestParse (0.00s)\nFAIL\texample.com/p\t0.01s", 1),
        ("go test ./... | tail", "ok  \texample.com/p\t0.01s", 0),
        ("npx jest 2>&1 | tail", "Tests:       1 failed, 3 passed, 4 total", 1),
        ("npx vitest run | tail", "      Tests  4 passed (4)", 0),
        ("python -m unittest 2>&1 | tail -3", "Ran 3 tests in 0.001s\n\nFAILED (failures=1)", 1),
        ("python -m unittest 2>&1 | tail -3", "Ran 3 tests in 0.001s\n\nOK", 0),
    ],
)
def test_a_masked_exit_code_is_read_from_the_runner_summary(
    command: str, output: str, code: int | None
) -> None:
    assert observed_exit_code(command, 0, output) == code


def test_a_reported_failure_is_kept() -> None:
    assert observed_exit_code(PYTEST_TAIL, 2, "") == 2
    assert observed_exit_code("pytest", None, "") is None


def test_configured_runners_count_when_deciding_what_runs_last() -> None:
    assert observed_exit_code("./run-checks.sh | tail", 0, "", [r"run-checks\.sh"]) is None

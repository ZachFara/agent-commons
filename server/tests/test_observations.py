import pytest

from commons.domain.observations import exit_code_from_error, is_test_command


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

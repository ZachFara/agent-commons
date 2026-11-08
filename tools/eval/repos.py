"""Real GitHub tasks: fetch the commits, build the project, set it up, and check it.

Both commits are fetched once, shallow and by sha, into a bare cache under
`~/.cache/agent-commons-eval/repos/`. A project is the base tree from `git archive` with the
fix's versions of the task's test paths laid over it: the base-to-fix diff of those paths,
applied to the base. It holds no `.git` and no remote, so the agent can't read the fix from
history or push anywhere.
"""

import io
import re
import shlex
import subprocess
import tarfile
import tempfile
from datetime import date
from pathlib import Path

from tools.agent_hooks.state import GIT_REPO_VARIABLES, git_environment
from tools.eval.model import GitHubTask

CACHE = Path.home() / ".cache" / "agent-commons-eval" / "repos"
# A repository's own .gitattributes may mark its tests `export-ignore`, and `git archive` would
# leave them out. $GIT_DIR/info/attributes outranks every .gitattributes in the tree.
ARCHIVE_ATTRIBUTES = "* -export-ignore -export-subst\n"
# Removed from a repo task's session: credentials that could reach GitHub or an SSH agent.
HIDDEN = frozenset({"GH_TOKEN", "GITHUB_TOKEN", "SSH_AUTH_SOCK"})
DISALLOWED_TOOLS = ("Bash(git push*)", "Bash(gh *)")
SHA = re.compile(r"[0-9a-f]{40}")
PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\[\],-]*==[^=\s]+")
PYTHON = re.compile(r"\d+\.\d+(\.\d+)?")


def environment() -> dict[str, str]:
    """Return the environment for git, setup, and tests run on a task's project."""
    env = git_environment()
    env.pop("VIRTUAL_ENV", None)  # uv would use the harness's environment, not the project's
    return env


def session_environment(env: dict[str, str], workdir: Path) -> dict[str, str]:
    """Return a repo task session's environment: no GitHub or SSH credentials, no git prompts."""
    gh_config = workdir / "gh-config"
    gh_config.mkdir(exist_ok=True)
    hidden = HIDDEN | GIT_REPO_VARIABLES | {"VIRTUAL_ENV"}
    return {
        **{key: value for key, value in env.items() if key not in hidden},
        "GH_CONFIG_DIR": str(gh_config),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }


def run(command: list[str], cwd: Path | None = None) -> str:
    """Run a command; raise `CalledProcessError` (with its stderr) if it fails."""
    return subprocess.run(
        command, cwd=cwd, env=environment(), capture_output=True, text=True, check=True
    ).stdout


def cache_path(repo: str) -> Path:
    """Return the bare cache for a repository URL."""
    path = repo.split("://", 1)[-1].removesuffix(".git")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-")
    return CACHE / f"{slug}.git"


def _has_commit(bare: Path, sha: str) -> bool:
    command = ["git", "--git-dir", str(bare), "cat-file", "-e", f"{sha}^{{commit}}"]
    return (
        subprocess.run(command, env=environment(), capture_output=True, check=False).returncode == 0
    )


def fetch(task: GitHubTask) -> Path:
    """Fetch the task's base and fix commits into the bare cache, if missing; return the cache."""
    bare = cache_path(task.repo)
    if not (bare / "HEAD").exists():
        bare.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "--quiet", "--bare", str(bare)])
    (bare / "info").mkdir(exist_ok=True)
    (bare / "info" / "attributes").write_text(ARCHIVE_ATTRIBUTES, encoding="utf-8")
    missing = [sha for sha in (task.base_commit, task.fix_commit) if not _has_commit(bare, sha)]
    if missing:
        git = ["git", "--git-dir", str(bare)]
        run([*git, "fetch", "--quiet", "--no-tags", "--depth", "1", task.repo, *missing])
    return bare


def _extract(bare: Path, commit: str, dest: Path, paths: tuple[str, ...] = ()) -> None:
    """Write `commit`'s tree (or only `paths` in it) under dest, without any git metadata."""
    command = ["git", "--git-dir", str(bare), "archive", "--format=tar", commit, "--", *paths]
    archive = subprocess.run(command, env=environment(), capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="data")


def materialize(task: GitHubTask, project: Path) -> None:
    """Write the base tree plus the fix's test files into project: the agent's starting point."""
    bare = fetch(task)
    _extract(bare, task.base_commit, project)
    _extract(bare, task.fix_commit, project, task.test_paths)


def prepare(task: GitHubTask, project: Path) -> None:
    """Create the project's .venv on the pinned Python, run the setup, then install the pins."""
    commands = [["uv", "venv", "--quiet", "--python", task.python], shlex.split(task.setup)]
    if task.pins:
        commands.append(["uv", "pip", "install", "--quiet", *task.pins])
    for command in commands:
        run(command, project)


def run_tests(task: GitHubTask, project: Path) -> "subprocess.CompletedProcess[str]":
    """Run the task's test command in project."""
    return subprocess.run(
        shlex.split(task.test_command),
        cwd=project,
        env=environment(),
        capture_output=True,
        text=True,
        check=False,
    )


def problems(task: GitHubTask) -> list[str]:
    """Return what is wrong with a task record, before anything is fetched."""
    upstream = task.upstream
    checks = [
        # file:// is for the tests' local fixtures; a checked-in task names a public https repo.
        (task.repo.startswith(("https://", "file://")), "repo must be an https URL"),
        (SHA.fullmatch(task.base_commit) is not None, "base_commit must be a full sha"),
        (SHA.fullmatch(task.fix_commit) is not None, "fix_commit must be a full sha"),
        (task.issue_url.startswith("https://"), "issue_url must be an https URL"),
        (task.fix_url.startswith("https://"), "fix_url must be an https URL"),
        (bool(task.test_paths), "test_paths must name the fix's test files"),
        (PYTHON.fullmatch(task.python) is not None, "python must be a version like 3.12"),
        (all(PIN.fullmatch(pin) for pin in task.pins), "pins must be exact (name==version)"),
        (bool(task.setup.strip()), "setup must be a command"),
        (bool(task.test_command.strip()), "test_command must be a command"),
        (bool(upstream.library and upstream.version), "upstream needs a library and version"),
        (_is_date(upstream.released), "upstream.released must be an ISO date"),
        (upstream.changelog.startswith("https://"), "upstream.changelog must be an https URL"),
        (bool(upstream.upstream_change), "upstream.upstream_change must name the change"),
        (task.trigger == "version_specific", "trigger must be version_specific"),
        (bool(task.license), "license must be an SPDX identifier"),
    ]
    return [message for ok, message in checks if not ok]


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def check(task: GitHubTask) -> tuple[bool, str]:
    """Confirm the task's tests fail on the agent's project and pass on the fix's tree."""
    if found := problems(task):
        return False, "BROKEN record: " + "; ".join(found)
    try:
        failing, passing = _before_and_after(task)
    except subprocess.CalledProcessError as error:
        return False, f"BROKEN: `{shlex.join(error.cmd)}` failed: {_last(error.stderr)}"
    verdicts = [
        (failing.returncode == 0, "BROKEN: the tests already pass before the fix"),
        (passing.returncode != 0, f"BROKEN: the tests still fail with the fix: {_last(passing)}"),
    ]
    broken = [message for bad, message in verdicts if bad]
    return not broken, broken[0] if broken else "fails before, passes after"


def _before_and_after(
    task: GitHubTask,
) -> "tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]":
    """Run the tests on the materialized project and on the fix's whole tree."""
    with tempfile.TemporaryDirectory(prefix=f"commons-eval-check-{task.name}-") as tmp:
        before, after = Path(tmp) / "before", Path(tmp) / "after"
        materialize(task, before)
        _extract(fetch(task), task.fix_commit, after)
        prepare(task, before)
        prepare(task, after)
        return run_tests(task, before), run_tests(task, after)


def _last(output: object) -> str:
    """Return the last line of a command's output (pytest writes to stdout, unittest stderr)."""
    if isinstance(output, subprocess.CompletedProcess):
        output = f"{output.stdout}\n{output.stderr}"
    text = output.decode(errors="replace") if isinstance(output, bytes) else str(output or "")
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1][-160:] if lines else ""

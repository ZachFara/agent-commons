"""PreToolUse guard on Bash: the git and GitHub actions agents must never take.

Denied (exit code 2, reason on stderr):
- `gh pr merge` unless the PR's base branch starts with `feature/` (asked of `gh pr view`),
  and `gh pr merge --admin` always. Only Zach merges into main.
- any push to main, any force push, `--mirror` and `--all`.
- hook bypasses: `--no-verify`, `git commit -n`, `core.hooksPath`, pre-commit's `SKIP=`.
- `gh api` calls that write (any method but GET, or fields without `-X GET`) and graphql
  mutations, plus `gh repo edit|delete|rename|archive|unarchive|sync` and `gh secret|variable`
  writes: they change repository settings.
- reading live Commons credentials: Secret Manager, `~/.config/agent-commons`, the Neon key.

Commands are split into simple commands on `;`, `&&`, `||`, `|`, newlines and `$(`; shell
wrappers (`bash -c`, `eval`, `xargs`, a heredoc fed to a shell) are checked recursively.
Other heredoc bodies (commit messages, PR bodies) are data. This guards against mistakes, not
an adversary: the GitHub rulesets on main and feature/ are the backstop.

Entry script: .claude/hooks/bash_guard.sh. Tests: server/tests/test_agent_hooks.py.
"""

import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import PurePosixPath

from tools.agent_hooks import state

# Checked against the whole command, heredoc bodies included.
CREDENTIALS = (
    (re.compile(r"\bgcloud\s+secrets\b"), "agents never read Secret Manager"),
    (
        re.compile(r"\.config/agent-commons|neon_api_key|production-database-url"),
        "agents never read live Commons credentials",
    ),
)
# Checked against the command with heredoc bodies (commit messages, PR bodies) removed.
BYPASSES = (
    (re.compile(r"--no-verify\b"), "--no-verify skips the checks"),
    (re.compile(r"core\.hooksPath", re.IGNORECASE), "changing core.hooksPath skips the checks"),
    (re.compile(r"(^|[\s;&|(])SKIP="), "SKIP= skips pre-commit hooks"),
)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.DOTALL)
SEPARATORS = ";&|()`\n"
PUNCTUATION = SEPARATORS + "<>"
WRAPPERS = frozenset({"env", "command", "time", "nohup", "exec", "sudo"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
SCRIPT_RUNNERS = SHELLS | {"eval", "source", "."}  # a heredoc fed to one of these is run
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
GIT_GLOBAL_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
PUSH_WITH_VALUE = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
COMMIT_VALUE_LETTERS = "mFCct"  # short options whose value follows (rest of cluster or next)
COMMIT_ATTACHED_LETTERS = "Su"  # short options whose optional value is the rest of the cluster
MERGE_WITH_VALUE = frozenset(
    {"-t", "--subject", "-b", "--body", "-F", "--body-file", "-A", "--author-email"}
    | {"--match-head-commit", "-R", "--repo"}
)
API_FIELDS = ("-f", "-F", "--field", "--raw-field", "--input")
SETTINGS_COMMANDS = {
    "repo": frozenset({"edit", "delete", "rename", "archive", "unarchive", "sync"}),
    "secret": frozenset({"set", "delete", "remove"}),
    "variable": frozenset({"set", "delete", "remove"}),
}

Check = Callable[[list[str], str], str | None]


def split_heredocs(command: str) -> tuple[str, list[str]]:
    """Return the command with heredoc bodies removed, and the bodies."""
    text = command.replace("\\\n", " ")
    return HEREDOC.sub("<<heredoc", text), [match[3] for match in HEREDOC.finditer(text)]


def tokenize(text: str) -> list[str]:
    """Split a command line into words, separators and redirections."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCTUATION)
    # Newlines separate commands, so they are punctuation, not whitespace. shlex reads both
    # settings internally, so set them through vars() where vulture can't misread them.
    vars(lexer).update(whitespace=" \t\r", whitespace_split=True)
    return list(lexer)


def is_separator(token: str) -> bool:
    """Return whether a token ends a simple command (`;`, `&&`, `|`, newline, `$(`...)."""
    return bool(token) and all(char in SEPARATORS for char in token)


def is_redirect(token: str) -> bool:
    """Return whether a token is a redirection operator (`>`, `2>&1`'s `>&`, `<<`)."""
    return bool(set(token) & set("<>")) and all(char in "<>&" for char in token)


def simple_commands(text: str) -> list[list[str]]:
    """Return each simple command's words, without assignments, wrappers or redirections."""
    commands: list[list[str]] = [[]]
    drop_next = False
    for token in tokenize(text):
        if drop_next:
            drop_next = False
        elif is_separator(token):
            commands.append([])
        elif is_redirect(token):
            drop_next = True
            if commands[-1] and commands[-1][-1].isdigit():
                commands[-1].pop()
        elif commands[-1] or not (ASSIGNMENT.match(token) or token in WRAPPERS):
            commands[-1].append(token)
    return [words for words in commands if words]


def program(words: list[str]) -> str:
    """Return the program a simple command runs, without its directory."""
    return PurePosixPath(words[0]).name


def current_branch(cwd: str) -> str:
    """Return the branch checked out in `cwd`, or "" if it can't be read."""
    result = subprocess.run(
        ["git", "-C", cwd, "symbolic-ref", "--short", "-q", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        env=state.git_environment(),
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def is_force(option: str) -> bool:
    """Return whether a `git push` option forces the push."""
    if option.startswith("--"):
        return option in {"--force", "--force-if-includes"} or option.startswith(
            "--force-with-lease"
        )
    return "f" in option[1:]


def push_parts(args: list[str]) -> tuple[list[str], list[str]]:
    """Split `git push` arguments into options and positionals (remote, then refspecs)."""
    options: list[str] = []
    positionals: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg.startswith("-"):
            options.append(arg)
            skip = arg in PUSH_WITH_VALUE
        else:
            positionals.append(arg)
    return options, positionals


def check_push(args: list[str], cwd: str) -> str | None:
    """Deny force pushes and any push that would update main."""
    options, positionals = push_parts(args)
    refspecs = positionals[1:]
    if any(is_force(option) for option in options) or any(s.startswith("+") for s in refspecs):
        return "force pushes are never allowed"
    if {"--mirror", "--all"} & set(options):
        return "--mirror and --all push main too; push your own branch by name"
    targets = {spec.rsplit(":", 1)[-1].removeprefix("refs/heads/") for spec in refspecs}
    on_main = (not refspecs or "HEAD" in targets) and current_branch(cwd) == "main"
    if "main" in targets or on_main:
        return "only Zach changes main; push your own branch and open a PR"
    return None


def commit_skips_hooks(args: list[str]) -> bool:
    """Return whether `git commit` arguments include `-n` (the short --no-verify)."""
    skip = False
    for arg in args:
        if skip or not arg.startswith("-") or arg.startswith("--"):
            skip = False
            continue
        for index, letter in enumerate(arg[1:], start=1):
            if letter == "n":
                return True
            if letter in COMMIT_VALUE_LETTERS + COMMIT_ATTACHED_LETTERS:
                skip = letter in COMMIT_VALUE_LETTERS and index == len(arg) - 1
                break
    return False


def check_git(args: list[str], cwd: str) -> str | None:
    """Check a git command: find its subcommand past the global options."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "-C" and index + 1 < len(args):
            cwd = args[index + 1]
        index += 2 if args[index] in GIT_GLOBAL_WITH_VALUE else 1
    subcommand, rest = (args[index], args[index + 1 :]) if index < len(args) else ("", [])
    if subcommand == "push":
        return check_push(rest, cwd)
    if subcommand == "commit" and commit_skips_hooks(rest):
        return "git commit -n skips the checks"
    return None


def merge_target(args: list[str]) -> list[str]:
    """Return the `gh pr view` arguments naming the same PR as a `gh pr merge` call."""
    selector: list[str] = []
    repo: list[str] = []
    for index, arg in enumerate(args):
        previous = args[index - 1] if index else ""
        if previous in {"-R", "--repo"}:
            repo = ["--repo", arg]
        elif not arg.startswith("-") and previous not in MERGE_WITH_VALUE and not selector:
            selector = [arg]
    return selector + repo


def check_merge(args: list[str], cwd: str) -> str | None:
    """Allow `gh pr merge` only for a PR whose base branch starts with feature/."""
    if "--admin" in args:
        return "gh pr merge --admin bypasses branch protection"
    view = ["gh", "pr", "view", *merge_target(args), "--json", "baseRefName", "-q", ".baseRefName"]
    result = subprocess.run(
        view, capture_output=True, text=True, check=False, cwd=cwd, env=state.git_environment()
    )
    base = result.stdout.strip()
    if result.returncode != 0 or not base.startswith("feature/"):
        shown = base or result.stderr.strip() or "unknown"
        return f"only PRs into a feature/ branch may be merged by agents (base: {shown})"
    return None


def check_api(args: list[str]) -> str | None:
    """Deny gh api calls that write: they can change repository settings."""
    method = ""
    for index, arg in enumerate(args):
        if arg in {"-X", "--method"} and index + 1 < len(args):
            method = args[index + 1].upper()
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1].upper()
    fields = any(arg.startswith(API_FIELDS) for arg in args)
    if args[:1] == ["graphql"]:
        writes = any("mutation" in arg for arg in args)
    else:
        writes = method not in {"", "GET"} or (fields and method != "GET")
    return "agents may not change GitHub settings through gh api" if writes else None


def check_gh(args: list[str], cwd: str) -> str | None:
    """Check a gh command."""
    if args[:2] == ["pr", "merge"]:
        return check_merge(args[2:], cwd)
    if args[:1] == ["api"]:
        return check_api(args[1:])
    if len(args) > 1 and args[1] in SETTINGS_COMMANDS.get(args[0], frozenset()):
        return f"gh {args[0]} {args[1]} changes repository settings"
    return None


def check_shell(args: list[str], cwd: str) -> str | None:
    """Check the script a shell wrapper runs (`bash -c '...'`)."""
    for index, arg in enumerate(args[:-1]):
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg:
            return reason_to_deny(args[index + 1], cwd)
    return None


def check_eval(args: list[str], cwd: str) -> str | None:
    """Check what eval runs, or the command xargs runs."""
    start = next((i for i, arg in enumerate(args) if arg in {"git", "gh", *SHELLS}), len(args))
    return reason_to_deny(shlex.join(args[start:]), cwd) if start < len(args) else None


PROGRAMS: dict[str, Check] = {
    "git": check_git,
    "gh": check_gh,
    "eval": check_eval,
    "xargs": check_eval,
    **dict.fromkeys(SHELLS, check_shell),
}


def scan(patterns: tuple[tuple[re.Pattern[str], str], ...], text: str) -> str | None:
    """Return the reason of the first pattern found in `text`, or None."""
    return next((reason for pattern, reason in patterns if pattern.search(text)), None)


def check_words(words: list[str], cwd: str) -> str | None:
    """Check one simple command against the checks for its program."""
    check = PROGRAMS.get(program(words))
    return check(words[1:], cwd) if check else None


def reason_to_deny(command: str, cwd: str) -> str | None:
    """Return why a Bash command is denied, or None if it is allowed."""
    text, bodies = split_heredocs(command)
    commands = simple_commands(text)
    reasons = [scan(CREDENTIALS, command), scan(BYPASSES, text)]
    if bodies and any(program(words) in SCRIPT_RUNNERS for words in commands):
        reasons.extend(reason_to_deny(body, cwd) for body in bodies)
    reasons.extend(check_words(words, cwd) for words in commands)
    return next((reason for reason in reasons if reason), None)


def main() -> int:
    """Read the hook event and exit 0 (allow) or 2 (deny, reason on stderr)."""
    event = state.read_event()
    command = str(state.tool_input(event).get("command", ""))
    try:
        reason = reason_to_deny(command, str(event.get("cwd") or "."))
    except ValueError as error:  # shlex: unbalanced quotes
        reason = f"could not parse the command ({error}); simplify it"
    return 0 if reason is None else state.deny(reason)


if __name__ == "__main__":
    sys.exit(main())

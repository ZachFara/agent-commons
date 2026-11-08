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
- a Linear issue ID agents don't own (any `MIT-<n>` outside `.agents/owned-issues.txt` and
  the agent project) in GitHub-visible text: `git commit` messages (`-m`, `-F` files),
  `gh pr create|edit|merge` titles, bodies and body files, new branch names (`git switch -c`,
  `git checkout -b`, `git branch`), pushed refspecs and `gh pr create --head`. Linear's GitHub
  integration links a PR to every issue its title, body or head branch names, and moves that
  issue as the PR opens and merges. `--base` is allowed: Linear doesn't link the base branch.

Commands are split into simple commands on `;`, `&&`, `||`, `|`, newlines and `$(`; shell
wrappers (`bash -c`, `eval`, `xargs`, a heredoc fed to a shell) are checked recursively.
Other heredoc bodies (commit messages, PR bodies) are data, checked only for issue IDs when
the command commits or writes PR text. This guards against mistakes, not an adversary: the
GitHub rulesets on main and feature/ are the backstop.

Entry script: .claude/hooks/bash_guard.sh. Tests: server/tests/test_agent_hooks.py.
"""

import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

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
# A Linear issue ID, also inside a longer word (`feature/MIT-575-x`); `P-MIT-29` is a project.
ISSUE_ID = re.compile(r"(?<![a-z])(?:p-)?mit-\d+", re.IGNORECASE)
COMMIT_MESSAGES = frozenset({"-m", "--message"})
COMMIT_FILES = frozenset({"-F", "--file"})
# Options of `git switch` / `git checkout` that create a branch, and their short value letters.
NEW_BRANCH_OPTIONS = {
    "switch": (frozenset({"-c", "-C", "--create", "--force-create", "--orphan"}), "cC"),
    "checkout": (frozenset({"-b", "-B", "--orphan"}), "bB"),
}
# `git branch` options under which it lists, deletes or configures instead of creating.
BRANCH_NOT_CREATING = frozenset(
    {"-d", "-D", "--delete", "-l", "--list", "-a", "--all", "-r", "--remotes", "--show-current"}
    | {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at"}
    | {"-u", "--set-upstream-to", "--unset-upstream", "--edit-description"}
)
BRANCH_RENAMING = frozenset({"-m", "-M", "--move", "-c", "-C", "--copy"})
# `gh pr` subcommands that write PR text, with their short options that take a value.
PR_VALUE_LETTERS = {"create": "aBbFHlmprRTt", "edit": "BbFRt", "merge": "AbFRt"}
PR_TEXT = frozenset({"-t", "--title", "--subject", "-b", "--body"})
PR_FILES = frozenset({"-F", "--body-file"})
PR_HEAD = frozenset({"-H", "--head"})
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


def option_values(
    args: list[str], names: frozenset[str], value_letters: str, attached_letters: str = ""
) -> list[str]:
    """Return the values given to the options in `names`, up to `--`.

    Accepts `--name=value`, `--name value`, `-x value`, `-xvalue` and clusters (`-am value`).
    In a cluster the first of `value_letters` takes the rest of the cluster or the next
    argument as its value; one of `attached_letters` takes the rest of the cluster only.
    """
    values: list[str] = []
    index = 0
    while index < len(args) and args[index] != "--":
        name, value = option_value(args[index], value_letters + attached_letters)
        short_with_value = len(name) == len("-m") and name[1] in value_letters
        takes_next = value is None and (name in names or short_with_value)
        if name in names and (value is not None or index + 1 < len(args)):
            values.append(args[index + 1] if value is None else value)
        index += 2 if takes_next else 1
    return values


def option_value(arg: str, letters: str) -> tuple[str, str | None]:
    """Split one argument into an option name and its attached value (None if not attached)."""
    if arg.startswith("--"):
        name, equals, value = arg.partition("=")
        return name, value if equals else None
    if not arg.startswith("-"):
        return "", None
    index = next((i for i, letter in enumerate(arg[1:], start=1) if letter in letters), 0)
    return (f"-{arg[index]}", arg[index + 1 :] or None) if index else (arg, None)


def foreign_ids(texts: Iterable[str]) -> list[str]:
    """Return the issue IDs in `texts` that agents don't own, decided as linear_guard does."""
    found = {match.upper() for text in texts for match in ISSUE_ID.findall(text)}
    if not found:
        return []
    owned = state.owned_issues()
    return sorted(i for i in found if i.lower() not in owned and not state.is_agent_project(i))


def check_ids(texts: Iterable[str], where: str) -> str | None:
    """Deny GitHub-visible text that names an issue agents don't own."""
    if foreign := foreign_ids(texts):
        return (
            f"{where} names {', '.join(foreign)}, which agents don't own: write it without the"
            " ID (Linear links PR text to that ticket)"
        )
    return None


def check_text(values: list[str], files: list[str], cwd: str, where: str) -> str | None:
    """Check message values and the files they are read from (`-` is stdin: see heredocs)."""
    texts = list(values)
    for name in files:
        if name == "-":
            continue
        try:
            texts.append((Path(cwd) / Path(name).expanduser()).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            return (
                f"can't read {name} to check {where} for issue IDs ({error});"
                " write the file in an earlier command"
            )
    return check_ids(texts, where)


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
    branch = current_branch(cwd) if not refspecs or "HEAD" in targets else ""
    if "main" in targets or branch == "main":
        return "only Zach changes main; push your own branch and open a PR"
    sides = [side for spec in refspecs for side in spec.split(":")]
    return check_ids([*sides, branch], "the pushed branch name")


def new_branches(subcommand: str, args: list[str]) -> list[str]:
    """Return the names of the branches a git command creates (or renames to)."""
    if subcommand in NEW_BRANCH_OPTIONS:
        return option_values(args, *NEW_BRANCH_OPTIONS[subcommand])
    if subcommand != "branch" or {arg.partition("=")[0] for arg in args} & BRANCH_NOT_CREATING:
        return []
    names = [arg for arg in args if not arg.startswith("-")]
    return names[-1:] if BRANCH_RENAMING & set(args) else names[:1]


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


def check_commit(args: list[str], cwd: str) -> str | None:
    """Deny `git commit -n`, and messages that name an issue agents don't own."""
    if commit_skips_hooks(args):
        return "git commit -n skips the checks"
    letters = (COMMIT_VALUE_LETTERS, COMMIT_ATTACHED_LETTERS)
    messages = option_values(args, COMMIT_MESSAGES, *letters)
    files = option_values(args, COMMIT_FILES, *letters)
    return check_text(messages, files, cwd, "the commit message")


def git_subcommand(args: list[str], cwd: str) -> tuple[str, list[str], str]:
    """Return a git command's subcommand, its arguments, and the directory it runs in."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "-C" and index + 1 < len(args):
            cwd = str(Path(cwd) / args[index + 1])
        index += 2 if args[index] in GIT_GLOBAL_WITH_VALUE else 1
    return (args[index], args[index + 1 :], cwd) if index < len(args) else ("", [], cwd)


def check_git(args: list[str], cwd: str) -> str | None:
    """Check a git command: find its subcommand past the global options."""
    subcommand, rest, cwd = git_subcommand(args, cwd)
    if subcommand == "push":
        return check_push(rest, cwd)
    if subcommand == "commit":
        return check_commit(rest, cwd)
    return check_ids(new_branches(subcommand, rest), "the new branch name")


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


def check_pr_text(subcommand: str, args: list[str], cwd: str) -> str | None:
    """Deny PR text, body files and a head branch that name an issue agents don't own."""
    letters = PR_VALUE_LETTERS[subcommand]
    if reason := check_ids(option_values(args, PR_HEAD, letters), "the head branch name"):
        return reason
    texts, files = option_values(args, PR_TEXT, letters), option_values(args, PR_FILES, letters)
    return check_text(texts, files, cwd, "the pull request text")


def is_pr_write(words: list[str]) -> bool:
    """Return whether a gh command (without `gh`) creates, edits or merges a PR."""
    return words[:1] == ["pr"] and len(words) > 1 and words[1] in PR_VALUE_LETTERS


def check_gh(args: list[str], cwd: str) -> str | None:
    """Check a gh command."""
    if is_pr_write(args):
        reason = check_pr_text(args[1], args[2:], cwd)
        return reason or (check_merge(args[2:], cwd) if args[1] == "merge" else None)
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


def writes_text(words: list[str]) -> bool:
    """Return whether a simple command commits or writes PR text, which a heredoc may hold."""
    if program(words) == "git":
        return git_subcommand(words[1:], "")[0] == "commit"
    return program(words) == "gh" and is_pr_write(words[1:])


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
    if bodies and any(writes_text(words) for words in commands):
        reasons.append(check_ids(bodies, "the heredoc"))
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

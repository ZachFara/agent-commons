"""PreToolUse guard on Bash: the git and GitHub actions agents must never take.

Denied (exit code 2, reason on stderr):
- `gh pr merge` unless the PR's base branch starts with `feature/` (asked of `gh pr view`),
  and `gh pr merge --admin` always. Only Zach merges into main.
- any push to main, any force push, `--mirror` and `--all`.
- hook bypasses: `--no-verify`, `git commit -n`, `core.hooksPath`, pre-commit's `SKIP=`.
- `gh api` calls that write (any method but GET, or fields without `-X GET`) and graphql
  mutations or queries read from a file, plus `gh repo edit|delete|rename|archive|unarchive|
  sync` and `gh secret|variable` writes: they change repository settings.
- reading live Commons credentials: Secret Manager, `~/.config/agent-commons`, the Neon key.
- git aliases the guard can't see through: defining one (`git -c alias.x=...`, `git config
  alias.x ...`), and running one that is a shell command (`!...`) or another alias. Other
  configured aliases are expanded and checked like the command they stand for. Config the
  guard can't read is denied too: setting `include.*` or `includeIf.*` (`-c`, `git config`),
  and running a subcommand git doesn't ship (so possibly an alias) with `--git-dir`,
  `--work-tree`, `--bare`, or a `GIT_CONFIG*`, `GIT_DIR`, `GIT_COMMON_DIR`, `HOME` or
  `XDG_CONFIG_HOME` assignment on the line. Those assignments are denied before `git commit`,
  `push` and `merge` too, whose hooks a config file may skip; `X+=`, `printf -v X`, `read X`
  and a `source`d file that sets one count as assignments.
- a Linear issue ID agents don't own (any `MIT-<n>` or `P-MIT-<n>` outside
  `.agents/owned-issues.txt`) in GitHub-visible text: `git commit` messages (`-m`, `-F`
  files), `gh pr create|edit|merge` titles, bodies and body files, new branch names (`git
  switch -c`, `git checkout -b`, `git branch`, `git worktree add -b`), pushed refspecs and the
  head of `gh pr create` (`--head`, else the current branch). Linear's GitHub integration links
  a PR to every issue its title, body or head branch names, and moves that issue as the PR
  opens and merges. `--base` is allowed: Linear doesn't link the base branch.
- text the guard can't read: shell substitution (`$(...)`, backticks, `$NAME`) in a title,
  body, message, `--head`, `git push` argument or `gh api graphql` argument; brace or glob
  expansion (`{a,b}`, `{a..b}`, `*`, `?`, `[`) in a pushed refspec or `--head`; an unquoted
  heredoc that substitutes; stdin (`-F -`) unless a heredoc, and no pipe, feeds that same
  command; a message file that isn't a regular file, is over 1 MiB or can't be read; and
  `gh pr create --fill` or without an explicit title and body.

Commands are split into simple commands on `;`, `&&`, `||`, `|`, newlines and `$(`; each
one's program comes after any keywords (`{`, `!`, `if`, `then`, `do`...), assignments and
wrappers (`env`, `sudo`, `timeout 120`, `nice`, `stdbuf`...) with their options, and runs in
the directory `env -C` or `sudo -D` names. Shell wrappers (`bash -c`, `eval` with its
arguments joined, `env -S`, `xargs`, a heredoc fed to a shell) are checked recursively, with
the bodies of heredocs that end inside them; an `env -S` value with a backslash or `$` is
denied, as env reads those itself. A configured alias's expansion gets the line-wide scans
too. Other heredoc bodies (commit messages, PR bodies) are data, checked only for issue IDs
when the command commits or writes PR text.
This guards against mistakes, not an adversary: the GitHub rulesets on main and feature/ are
the backstop. A crash fails closed: the entry script turns every exit code but 0 into 2,
which blocks the call.

Entry script: .claude/hooks/bash_guard.sh. Tests: server/tests/test_agent_hooks.py.
"""

import itertools
import os
import re
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import NamedTuple

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
    (re.compile(r"(^|[\s;&|(])SKIP\+?="), "SKIP= skips pre-commit hooks"),
)
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n(.*?)\n[ \t]*\2[ \t]*(?=\n|$)", re.DOTALL)
# What split_heredocs leaves where a heredoc was. A typed command can't hold the NUL
# (reason_to_deny denies one), so an unterminated `<<heredoc` never passes for a heredoc.
HEREDOC_MARK = "\0heredoc"
# The word simple_commands leaves where an unquoted backtick cuts a command short, as the
# tokenizer leaves a `$` where `$(` does. SUBSTITUTION matches it.
CUT = "`"
SEPARATORS = ";&|()`\n"
PUNCTUATION = SEPARATORS + "<>"
WRAPPERS = frozenset(
    {"env", "command", "time", "nohup", "exec", "sudo", "timeout", "nice", "stdbuf"}
)
# Wrapper options that take a value: short letters (`-u x`, `-ux`) and long names (`--user x`).
WRAPPER_VALUES = {
    "env": ("CPSu", frozenset({"--chdir", "--split-string", "--unset"})),
    "exec": ("a", frozenset()),
    "nice": ("n", frozenset({"--adjustment"})),
    "stdbuf": ("eio", frozenset({"--error", "--input", "--output"})),
    "sudo": (
        "CDghpRrTtUu",
        frozenset({"--chdir", "--chroot", "--close-from", "--command-timeout", "--group"})
        | frozenset({"--host", "--other-user", "--prompt", "--role", "--type", "--user"}),
    ),
    "time": ("fo", frozenset({"--format", "--output"})),
    "timeout": ("ks", frozenset({"--kill-after", "--signal"})),
}
WRAPPER_POSITIONALS = {"timeout": 1}  # `timeout 120 git ...`: the duration comes first
# Wrapper options that run the command in another directory, like `git -C`.
WRAPPER_CHDIR = {"env": frozenset({"-C", "--chdir"}), "sudo": frozenset({"-D", "--chdir"})}
SPLIT_STRING = frozenset({"-S", "--split-string"})  # `env -S 'git push'` runs the split value
# Reserved words that may come before a simple command's program.
KEYWORDS = frozenset({"{", "!", "if", "then", "else", "elif", "do", "while", "until"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
SCRIPT_RUNNERS = SHELLS | {"eval", "source", "."}  # a heredoc fed to one of these is run
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*\+?=")
GIT_GLOBAL_WITH_VALUE = frozenset(
    {"-C", "-c", "--config-env", "--git-dir", "--work-tree", "--namespace"}
)
GIT_CONFIG_OPTIONS = frozenset({"-c", "--config-env"})
# Global options under which git reads another repository's config, which may hold aliases.
GIT_OTHER_CONFIG = frozenset({"--git-dir", "--work-tree", "--bare"})
# Variables that point git at other config, and assignments to them (`X=`, `X+=`). Any word
# on the line that starts with one counts (`X=1 git ...`, `export X=1; git ...`), since
# simple_commands drops assignments; in eval strings and sourced files, any text does.
# `printf -v` and `read` set them too (sets_config_variable).
CONFIG_VARIABLE = re.compile(r"GIT_CONFIG\w*|GIT_DIR|GIT_COMMON_DIR|XDG_CONFIG_HOME|HOME")
CONFIG_ASSIGNMENT = re.compile(rf"(?:{CONFIG_VARIABLE.pattern})\+?=")
GIT_CONFIG_VARIABLES = re.compile(rf"\b{CONFIG_ASSIGNMENT.pattern}")
# Subcommands that run the repository's hooks. Config the guard can't read may skip them
# (core.hooksPath), even for these builtins.
GIT_HOOKED = frozenset({"commit", "push", "merge"})
# Config keys whose values the guard can't follow: aliases, and files included into config.
ALIAS_KEYS = ("alias.",)
INCLUDE_KEYS = ("include.", "includeif.")
PUSH_WITH_VALUE = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
COMMIT_VALUE_LETTERS = "mFCct"  # short options whose value follows (rest of cluster or next)
COMMIT_ATTACHED_LETTERS = "Su"  # short options whose optional value is the rest of the cluster
MERGE_WITH_VALUE = frozenset(
    {"-t", "--subject", "-b", "--body", "-F", "--body-file", "-A", "--author-email"}
    | {"--match-head-commit", "-R", "--repo"}
)
API_FIELDS = ("-f", "-F", "--field", "--raw-field", "--input")
# A Linear issue ID, also inside a longer word (`feature/MIT-575-x`). `P-MIT-29` is a project
# reference, denied like an ID: Linear may link the issue inside it.
ISSUE_ID = re.compile(r"(?<![a-z])(?:p-)?mit-\d+", re.IGNORECASE)
# Brace and glob expansion in a branch name or refspec. shlex drops quotes, so quoted text
# matches too; branch names never hold these (`HEAD@{1}` has no `,` or `..`), and a `*`
# refspec pushes every branch, main too.
EXPANSION = re.compile(r"[*?\[]|\{[^{}]*(?:,|\.\.)[^{}]*\}")
# Shell substitution in an argument:`$(`, `${`, `$NAME`, `$1`, a `$` cut off by an unquoted
# `$(` (the tokenizer splits there), or a backtick.
SUBSTITUTION = re.compile(r"\$(?:[\w({]|$)|`")
# `"$(cat <<'EOF' ... EOF)"` once split_heredocs has taken out the body (checked on its own).
CAT_HEREDOC = re.compile(rf"\$\(\s*cat\s+<<{HEREDOC_MARK}\s*\)")
COMMIT_MESSAGES = frozenset({"-m", "--message"})
MAX_MESSAGE_BYTES = 1 << 20  # far above GitHub's 65536-character PR body
COMMIT_FILES = frozenset({"-F", "--file"})
# Subcommands that create a branch through an option, and their short value letters.
NEW_BRANCH_OPTIONS = {
    "switch": (frozenset({"-c", "-C", "--create", "--force-create", "--orphan"}), "cC"),
    "checkout": (frozenset({"-b", "-B", "--orphan"}), "bB"),
    "worktree": (frozenset({"-b", "-B"}), "bB"),
}
# `git branch` options under which it lists, deletes or configures instead of creating.
BRANCH_NOT_CREATING = frozenset(
    {"-d", "-D", "--delete", "-l", "--list", "-a", "--all", "-r", "--remotes", "--show-current"}
    | {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at"}
    | {"-u", "--set-upstream-to", "--unset-upstream", "--edit-description"}
)
BRANCH_RENAMING = frozenset({"-m", "-M", "--move", "-c", "-C", "--copy"})
# git subcommands this guard inspects. git never lets an alias shadow them.
GIT_INSPECTED = frozenset({"push", "commit", "branch", "config", *NEW_BRANCH_OPTIONS})
# Subcommands git ships (from `git --list-cmds=builtins`), which an alias can't shadow. Any
# other may be an alias, and is denied when git may read config the guard can't.
GIT_BUILTINS = GIT_INSPECTED | {
    *("add", "am", "apply", "archive", "bisect", "blame", "bundle", "cat-file", "check-attr"),
    *("check-ignore", "check-ref-format", "cherry", "cherry-pick", "clean", "clone"),
    *("commit-tree", "count-objects", "describe", "diff", "diff-files", "diff-index"),
    *("diff-tree", "difftool", "fetch", "for-each-ref", "format-patch", "fsck", "gc", "grep"),
    *("hash-object", "help", "init", "interpret-trailers", "log", "ls-files", "ls-remote"),
    *("ls-tree", "maintenance", "merge", "merge-base", "merge-file", "merge-tree", "mv"),
    *("name-rev", "notes", "pull", "range-diff", "read-tree", "rebase", "reflog", "remote"),
    *("repack", "replace", "rerere", "reset", "restore", "rev-list", "rev-parse", "revert"),
    *("rm", "shortlog", "show", "show-branch", "show-ref", "sparse-checkout", "stash"),
    *("status", "stripspace", "submodule", "symbolic-ref", "tag", "update-index"),
    *("update-ref", "var", "verify-commit", "verify-tag", "version", "whatchanged"),
    "write-tree",
}
# `gh pr` subcommands that write PR text, with their short options that take a value.
PR_VALUE_LETTERS = {"create": "aBbFHlmprRTt", "edit": "BbFRt", "merge": "AbFRt"}
PR_TITLE = frozenset({"-t", "--title", "--subject"})
PR_BODY = frozenset({"-b", "--body"})
PR_FILES = frozenset({"-F", "--body-file"})
PR_HEAD = frozenset({"-H", "--head"})
PR_FILL = frozenset({"--fill", "--fill-first", "--fill-verbose"})
SETTINGS_COMMANDS = {
    "repo": frozenset({"edit", "delete", "rename", "archive", "unarchive", "sync"}),
    "secret": frozenset({"set", "delete", "remove"}),
    "variable": frozenset({"set", "delete", "remove"}),
}


class Shell(NamedTuple):
    """Where a command line runs, and what its heredocs hold."""

    cwd: str
    heredocs: tuple[str, ...] = ()
    expands: bool = False  # an unquoted heredoc substitutes `$...` or backticks into its body
    heredoc_stdin: bool = False  # a heredoc, and no pipe, feeds this simple command's stdin
    sets_git_config: bool = False  # the line, or one wrapping it, sets GIT_CONFIG_VARIABLES


class SimpleCommand(NamedTuple):
    """One simple command's words, and what feeds its stdin in order: pipe, heredoc, file."""

    words: list[str]
    stdin: list[str]
    assignments: tuple[str, ...] = ()  # `X=1` before the program, wrappers' included
    chdirs: tuple[str, ...] = ()  # directories wrappers run it in (`env -C dir`), in order

    @property
    def heredoc_stdin(self) -> bool:
        """Return whether a heredoc feeds this command's stdin, and no pipe does."""
        return "pipe" not in self.stdin and self.stdin[-1:] == ["heredoc"]


Check = Callable[[list[str], Shell], str | None]


def split_heredocs(command: str) -> tuple[str, list[str], bool]:
    """Return the command without heredoc bodies, the bodies, and whether one substitutes."""
    text = command.replace("\\\n", " ")
    matches = list(HEREDOC.finditer(text))
    expands = any(not match[1] and SUBSTITUTION.search(match[4]) for match in matches)
    # Keep the rest of the line the heredoc starts on: `<<'EOF' | git commit -F -` runs it.
    without = HEREDOC.sub(lambda match: f"<<{HEREDOC_MARK}{match[3]}", text)
    return without, [match[4] for match in matches], expands


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


def stdin_source(redirect: str, target: str) -> list[str]:
    """Return what a redirection feeds stdin from: nothing, a heredoc or a file."""
    if "<" not in redirect:
        return []
    return ["heredoc" if redirect == "<<" and target == HEREDOC_MARK else "file"]


def end_command(token: str, words: list[str], *, in_backticks: bool) -> tuple[bool, list[str]]:
    """Mark a backtick that cuts `words` short; return whether backticks are open, next stdin."""
    if token.startswith(CUT) and not in_backticks and words:
        words.append(CUT)
    in_backticks ^= token.count(CUT) % 2 == 1
    return in_backticks, ["pipe"] if "|" in token and "||" not in token else []


def wrapper_option(wrapper: str, words: list[str]) -> tuple[int, str, str | None]:
    """Return how many words a wrapper's option (`words[0]`) takes, its name and its value."""
    letters, names = WRAPPER_VALUES.get(wrapper, ("", frozenset()))
    name, value = option_value(words[0], letters)
    if value is None and (name in names or (len(name) == len("-u") and name[1] in letters)):
        return 2, name, words[1] if len(words) > 1 else None
    return 1, name, value


def split_string(value: str) -> list[str]:
    """Return the words `env -S` splits a value into; raise ValueError if shlex can't tell."""
    if set(value) & set("\\$"):
        msg = "env -S has its own escapes and ${NAME}, which the guard can't read; drop -S"
        raise ValueError(msg)
    return shlex.split(value)


def wrapper_arguments(wrapper: str, args: list[str]) -> tuple[list[str], list[str]]:
    """Return the words after a wrapper's options and positionals, and directories it sets."""
    chdirs: list[str] = []
    while args and args[0].startswith("-"):
        taken, name, value = wrapper_option(wrapper, args)
        args = args[taken:]
        if name == "--":
            break
        if value is not None and name in WRAPPER_CHDIR.get(wrapper, frozenset()):
            chdirs.append(value)
        if value is not None and wrapper == "env" and name in SPLIT_STRING:
            args = [*split_string(value), *args]
    return args[WRAPPER_POSITIONALS.get(wrapper, 0) :], chdirs


def command_words(command: SimpleCommand) -> SimpleCommand:
    """Return a simple command with its words from its program on.

    Takes off the keywords, assignments, wrappers and wrapper options before it, keeping the
    assignments and the directories wrappers change to. `env -S` values run as the command
    they split into.
    """
    words, assignments, chdirs = command.words, list(command.assignments), list(command.chdirs)
    while words:
        if words[0] in WRAPPERS:
            words, directories = wrapper_arguments(words[0], words[1:])
            chdirs += directories
        elif ASSIGNMENT.match(words[0]):
            assignments.append(words[0])
            words = words[1:]
        elif words[0] in KEYWORDS:
            words = words[1:]
        else:
            break
    return command._replace(words=words, assignments=tuple(assignments), chdirs=tuple(chdirs))


def simple_commands(text: str) -> list[SimpleCommand]:
    """Return each simple command, without keywords, assignments, wrappers or redirections."""
    commands = [SimpleCommand([], [])]
    redirect = ""
    in_backticks = False
    for token in tokenize(text):
        words = commands[-1].words
        if redirect:
            commands[-1].stdin.extend(stdin_source(redirect, token))
            redirect = ""
        elif is_separator(token):
            in_backticks, stdin = end_command(token, words, in_backticks=in_backticks)
            commands.append(SimpleCommand([], stdin))
        elif is_redirect(token):
            redirect = token
            if words and words[-1].isdigit():
                words.pop()
        else:
            words.append(token)
    stripped = [command_words(command) for command in commands]
    return [command for command in stripped if command.words]


def program(words: list[str]) -> str:
    """Return the program a simple command runs, without its directory (`.` stays `.`)."""
    return PurePosixPath(words[0]).name or words[0]


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


def has_short_flag(
    args: list[str], flag: str, value_letters: str, attached_letters: str = ""
) -> bool:
    """Return whether the short flag `-<flag>` is given, alone or in a cluster (`-am`)."""
    skip = False
    for arg in args:
        if skip or not arg.startswith("-") or arg.startswith("--"):
            skip = False
            continue
        for index, letter in enumerate(arg[1:], start=1):
            if letter == flag:
                return True
            if letter in value_letters + attached_letters:
                skip = letter in value_letters and index == len(arg) - 1
                break
    return False


def foreign_ids(texts: Iterable[str]) -> list[str]:
    """Return the issue IDs in `texts` that are not on the owned list linear_guard uses."""
    found = {match.upper() for text in texts for match in ISSUE_ID.findall(text)}
    if not found:
        return []
    owned = state.owned_issues()
    return sorted(issue for issue in found if issue.lower() not in owned)


def check_ids(texts: Iterable[str], where: str) -> str | None:
    """Deny GitHub-visible text that names an issue agents don't own."""
    if not (foreign := foreign_ids(texts)):
        return None
    advice = "write it without the ID (Linear links PR text to that ticket)"
    if any(issue.startswith("P-") for issue in foreign):
        advice += "; name the project by name"
    return f"{where} names {', '.join(foreign)}, which agents don't own: {advice}"


def check_message(values: list[str], files: list[str], shell: Shell, where: str) -> str | None:
    """Deny message text the guard can't read, then text that names an issue agents don't own.

    `values` are inline texts, `files` the files they are read from (`-` is stdin, which only
    a heredoc may feed). The command line's heredoc bodies are checked with them.
    """
    if shell.expands or any(SUBSTITUTION.search(CAT_HEREDOC.sub("", v)) for v in values):
        return (
            f"{where} uses shell substitution, which the guard can't check: write the text to a"
            " file and pass it with --body-file <path> (-F <path> for git commit), or use a"
            " <<'EOF' heredoc"
        )
    if "-" in files and not shell.heredoc_stdin:
        return (
            f"{where} is read from stdin, which the guard can't check:"
            " pass --body-file <path> or a heredoc"
        )
    texts = [*values, *shell.heredocs]
    for name in (name for name in files if name != "-"):
        try:
            texts.append(read_message_file(Path(shell.cwd) / Path(name).expanduser()))
        except (OSError, ValueError, RuntimeError) as error:  # anything else crashes: blocked
            return (
                f"can't read {name} to check {where} for issue IDs ({error});"
                " write it as a regular file of at most 1 MiB in an earlier command"
            )
    return check_ids(texts, where)


def read_message_file(path: Path) -> str:
    """Return a message file's text; raise OSError unless it is a regular file of 1 MiB at most.

    Opens without blocking, so a FIFO is refused rather than waited on.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            msg = "not a regular file"
            raise OSError(msg)
        data = file.read(MAX_MESSAGE_BYTES + 1)
    if len(data) > MAX_MESSAGE_BYTES:
        msg = "larger than 1 MiB"
        raise OSError(msg)
    return data.decode("utf-8")


def git_output(cwd: str, *args: str) -> str:
    """Return what a git command prints in `cwd`, or "" if it fails."""
    result = subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        check=False,
        env=state.git_environment(),
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def current_branch(cwd: str) -> str:
    """Return the branch checked out in `cwd`, or "" if it can't be read."""
    return git_output(cwd, "symbolic-ref", "--short", "-q", "HEAD")


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


def check_push_text(args: list[str], positionals: list[str]) -> str | None:
    """Deny pushes the guard can't read: shell substitution, brace or glob expansion."""
    if any(SUBSTITUTION.search(arg) for arg in args):
        return (
            "git push uses shell substitution, which the guard can't check:"
            " name the remote and branch literally"
        )
    return expansion(positionals, "git push")


def expansion(names: list[str], where: str) -> str | None:
    """Deny branch names or refspecs with brace or glob expansion (see EXPANSION)."""
    if any(EXPANSION.search(name) for name in names):
        return (
            f"{where} uses brace or glob expansion ({{a,b}}, {{a..b}}, *, ? or [), which the"
            " guard can't check: name each branch literally"
        )
    return None


def check_push_shape(options: list[str], refspecs: list[str]) -> str | None:
    """Deny force pushes, --mirror and --all."""
    if any(is_force(option) for option in options) or any(s.startswith("+") for s in refspecs):
        return "force pushes are never allowed"
    if {"--mirror", "--all"} & set(options):
        return "--mirror and --all push main too; push your own branch by name"
    return None


def check_push(args: list[str], cwd: str) -> str | None:
    """Deny force pushes, any push that would update main, and branch names with foreign IDs."""
    options, positionals = push_parts(args)
    refspecs = positionals[1:]
    if reason := check_push_text(args, positionals) or check_push_shape(options, refspecs):
        return reason
    targets = {spec.rsplit(":", 1)[-1].removeprefix("refs/heads/") for spec in refspecs}
    pushes_current = not refspecs or bool({"HEAD", "@"} & targets)
    branch = current_branch(cwd) if pushes_current else ""
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


def check_commit(args: list[str], shell: Shell) -> str | None:
    """Deny `git commit -n`, and messages the guard can't read or that name a foreign ID."""
    letters = (COMMIT_VALUE_LETTERS, COMMIT_ATTACHED_LETTERS)
    if has_short_flag(args, "n", *letters):
        return "git commit -n skips the checks"
    messages = option_values(args, COMMIT_MESSAGES, *letters) + cuts(args)
    files = option_values(args, COMMIT_FILES, *letters)
    return check_message(messages, files, shell, "the commit message")


def git_subcommand(args: list[str], cwd: str) -> tuple[list[str], str, list[str], str]:
    """Return a git command's global options, subcommand, its arguments and its directory."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "-C" and index + 1 < len(args):
            cwd = str(Path(cwd) / args[index + 1])
        index += 2 if args[index] in GIT_GLOBAL_WITH_VALUE else 1
    subcommand, rest = (args[index], args[index + 1 :]) if index < len(args) else ("", [])
    return args[:index], subcommand, rest, cwd


def cuts(args: list[str]) -> list[str]:
    """Return the marks of backtick substitutions that cut these arguments short (see CUT)."""
    return [arg for arg in args if arg == CUT]


def sets_config(
    options: list[str], subcommand: str, args: list[str], prefixes: tuple[str, ...]
) -> bool:
    """Return whether a git command sets a key under `prefixes` (`-c`, `git config <key> <v>`)."""
    values = [
        value for option, value in itertools.pairwise(options) if option in GIT_CONFIG_OPTIONS
    ]
    values += [option.partition("=")[2] for option in options if option.startswith("--config-env=")]
    keys = [index for index, arg in enumerate(args) if arg.lower().startswith(prefixes)]
    sets_key = subcommand == "config" and bool(keys) and keys[0] < len(args) - 1
    return sets_key or any(value.lower().startswith(prefixes) for value in values)


def check_git_config(
    options: list[str], subcommand: str, args: list[str], shell: Shell
) -> str | None:
    """Deny git commands that use config the guard can't see: new aliases, includes, other files."""
    if sets_config(options, subcommand, args, ALIAS_KEYS):
        return (
            "agents don't define git aliases (the guard can't see what they run);"
            " run the command itself"
        )
    if sets_config(options, subcommand, args, INCLUDE_KEYS):
        return "agents don't include git config files (the guard can't see what they set)"
    return check_other_config(options, subcommand, shell)


def check_other_config(options: list[str], subcommand: str, shell: Shell) -> str | None:
    """Deny config the guard can't read where it matters: possible aliases, and hooked commands."""
    other_config = {option.partition("=")[0] for option in options} & GIT_OTHER_CONFIG
    if subcommand and subcommand not in GIT_BUILTINS and (other_config or shell.sets_git_config):
        return (
            f"git {subcommand} may be an alias in config the guard can't read"
            " (--git-dir, --work-tree, --bare, GIT_CONFIG*, GIT_DIR or HOME);"
            " run the command it stands for without them"
        )
    if subcommand in GIT_HOOKED and shell.sets_git_config:
        return (
            f"git {subcommand} runs hooks, and GIT_CONFIG*, GIT_DIR, XDG_CONFIG_HOME or HOME on"
            " the line may point it at config that skips them (core.hooksPath);"
            " run it without them"
        )
    return None


def check_git_subcommand(subcommand: str, args: list[str], shell: Shell) -> str | None:
    """Check a git subcommand that this guard inspects."""
    if subcommand == "push":
        return check_push(args, shell.cwd)
    if subcommand == "commit":
        return check_commit(args, shell)
    return check_ids(new_branches(subcommand, args), "the new branch name")


def check_git(args: list[str], shell: Shell, *, expanded: bool = False) -> str | None:
    """Check a git command: find its subcommand past the global options, expanding an alias."""
    options, subcommand, rest, cwd = git_subcommand(args, shell.cwd)
    if reason := check_git_config(options, subcommand, rest, shell):
        return reason
    alias = (
        git_output(cwd, "config", "--get", f"alias.{subcommand}")
        if subcommand and subcommand not in GIT_INSPECTED
        else ""
    )
    if alias.startswith("!") or (alias and expanded):
        return f"git {subcommand} is an alias for `{alias}`; run what it stands for directly"
    if alias:  # the line-wide scans never saw the expansion
        return (
            scan(CREDENTIALS, alias)
            or scan(BYPASSES, alias)
            or check_git([*shlex.split(alias), *rest], shell._replace(cwd=cwd), expanded=True)
        )
    return check_git_subcommand(subcommand, rest, shell._replace(cwd=cwd))


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


def api_method(args: list[str]) -> str:
    """Return the HTTP method a `gh api` call names (`-X POST`, `-XPOST`, `--method=POST`)."""
    method = ""
    for index, arg in enumerate(args):
        if arg in {"-X", "--method"} and index + 1 < len(args):
            method = args[index + 1]
        elif arg.startswith("--method="):
            method = arg.partition("=")[2]
        elif arg.startswith("-X") and len(arg) > len("-X"):
            method = arg[len("-X") :].removeprefix("=")
    return method.upper()


def check_api(args: list[str]) -> str | None:
    """Deny gh api calls that write: they can change repository settings."""
    if args[:1] == ["graphql"]:
        if any(SUBSTITUTION.search(arg) for arg in args):
            return (
                "gh api graphql uses shell substitution (or a $variable), which the guard can't"
                " check: write the query and its values inline"
            )
        if any("query=@" in arg or arg.partition("=")[0] == "--input" for arg in args):
            return "gh api graphql must take its query inline (-f query='...') so it can be checked"
        writes = any("mutation" in arg for arg in args)
    else:
        method = api_method(args)
        fields = any(arg.startswith(API_FIELDS) for arg in args)
        writes = method not in {"", "GET"} or (fields and method != "GET")
    return "agents may not change GitHub settings through gh api" if writes else None


def check_pr_create(args: list[str], shell: Shell) -> str | None:
    """Require an explicit title and body, and a head branch without a foreign ID."""
    letters = PR_VALUE_LETTERS["create"]
    filled = bool({arg.partition("=")[0] for arg in args} & PR_FILL)
    body = option_values(args, PR_BODY, letters) or option_values(args, PR_FILES, letters)
    if (
        filled
        or has_short_flag(args, "f", letters)
        or not body
        or not option_values(args, PR_TITLE, letters)
    ):
        return (
            "gh pr create needs an explicit --title and --body or --body-file, never --fill:"
            " the guard checks only text it can see"
        )
    heads = option_values(args, PR_HEAD, letters)
    if any(SUBSTITUTION.search(head) for head in heads):
        return (
            "the head branch name uses shell substitution, which the guard can't check:"
            " name the branch literally"
        )
    return expansion(heads, "the head branch name") or check_ids(
        heads or [current_branch(shell.cwd)], "the head branch name"
    )


def check_pr_text(subcommand: str, args: list[str], shell: Shell) -> str | None:
    """Deny PR text, body files and a head branch the guard can't read or that name a foreign ID."""
    if subcommand == "create" and (reason := check_pr_create(args, shell)):
        return reason
    letters = PR_VALUE_LETTERS[subcommand]
    texts = option_values(args, PR_TITLE | PR_BODY, letters) + cuts(args)
    return check_message(
        texts, option_values(args, PR_FILES, letters), shell, "the pull request text"
    )


def is_pr_write(words: list[str]) -> bool:
    """Return whether a gh command (without `gh`) creates, edits or merges a PR."""
    return words[:1] == ["pr"] and len(words) > 1 and words[1] in PR_VALUE_LETTERS


def check_gh(args: list[str], shell: Shell) -> str | None:
    """Check a gh command."""
    if is_pr_write(args):
        reason = check_pr_text(args[1], args[2:], shell)
        return reason or (check_merge(args[2:], shell.cwd) if args[1] == "merge" else None)
    if args[:1] == ["api"]:
        return check_api(args[1:])
    if len(args) > 1 and args[1] in SETTINGS_COMMANDS.get(args[0], frozenset()):
        return f"gh {args[0]} {args[1]} changes repository settings"
    return None


def check_shell(args: list[str], shell: Shell) -> str | None:
    """Check the script a shell wrapper runs (`bash -c '...'`)."""
    for index, arg in enumerate(args[:-1]):
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg:
            return reason_to_deny(args[index + 1], shell.cwd, outer=shell)
    return None


def check_eval(args: list[str], shell: Shell) -> str | None:
    """Check what eval runs: its arguments joined by spaces and read as a command line."""
    return reason_to_deny(" ".join(args), shell.cwd, outer=shell)


def check_xargs(args: list[str], shell: Shell) -> str | None:
    """Check the command xargs runs."""
    start = next((i for i, arg in enumerate(args) if arg in {"git", "gh", *SHELLS}), len(args))
    if start == len(args):
        return None
    return reason_to_deny(shlex.join(args[start:]), shell.cwd, outer=shell)


PROGRAMS: dict[str, Check] = {
    "git": check_git,
    "gh": check_gh,
    "eval": check_eval,
    "xargs": check_xargs,
    **dict.fromkeys(SHELLS, check_shell),
}


def scan(patterns: tuple[tuple[re.Pattern[str], str], ...], text: str) -> str | None:
    """Return the reason of the first pattern found in `text`, or None."""
    return next((reason for pattern, reason in patterns if pattern.search(text)), None)


def check_words(words: list[str], shell: Shell) -> str | None:
    """Check one simple command against the checks for its program."""
    check = PROGRAMS.get(program(words))
    return check(words[1:], shell) if check else None


def sets_config_variable(words: list[str], cwd: str) -> bool:
    """Return whether `printf -v`, `read`, `eval` or `source` may set a CONFIG_VARIABLE.

    A sourced file is read like a message file; one that can't be read may set anything. The
    text of an eval string and of a sourced file is searched, not just its words.
    """
    name, args = program(words), words[1:]
    if name in {"source", "."}:
        try:
            script = read_message_file(Path(cwd) / Path(args[0]).expanduser()) if args else ""
        except (OSError, ValueError, RuntimeError):
            return True
        return bool(GIT_CONFIG_VARIABLES.search(script))
    if name == "eval":
        return bool(GIT_CONFIG_VARIABLES.search(" ".join(args)))
    targets = {"printf": option_values(args, frozenset({"-v"}), "v"), "read": args}.get(name, [])
    return any(CONFIG_VARIABLE.fullmatch(target) for target in targets)


def sets_git_config(text: str, commands: list[SimpleCommand], cwd: str) -> bool:
    """Return whether a command line may set a CONFIG_VARIABLE.

    Matches whole words (`X=1`, `export X=1`, `env -S 'X=1 ...'`), so quoted text that only
    names `X=` (a commit message) sets nothing.
    """
    words = [*tokenize(text), *(word for simple in commands for word in simple.assignments)]
    return any(CONFIG_ASSIGNMENT.match(word) for word in words) or any(
        sets_config_variable(simple.words, cwd) for simple in commands
    )


def heredocs(command: str, outer: Shell | None) -> tuple[str, list[str], bool]:
    """Return split_heredocs of a command, with the bodies of heredocs the outer line took out.

    A heredoc whose end line comes before a wrapper's closing quote (`bash -c "..."`, `eval`)
    is taken out by the outer line, which leaves HEREDOC_MARK in the wrapped command. Inside
    double quotes the outer shell substitutes before the inner one reads `<<'EOF'`, so any
    substitution in those bodies counts, quoted heredoc or not (single quotes fail closed).
    """
    text, bodies, expands = split_heredocs(command)
    if outer is not None and HEREDOC_MARK in command:
        outer_expands = outer.expands or any(SUBSTITUTION.search(body) for body in outer.heredocs)
        return text, [*bodies, *outer.heredocs], expands or outer_expands
    return text, bodies, expands


def reason_to_deny(command: str, cwd: str, outer: Shell | None = None) -> str | None:
    """Return why a Bash command is denied, or None if it is allowed.

    `outer` is the command line a wrapper (`bash -c`, a heredoc fed to a shell) runs this in.
    """
    # Only split_heredocs puts a NUL in a wrapped command: the typed line can't hold one.
    if "\0" in (command if outer is None else command.replace(HEREDOC_MARK, "")):
        return "the command holds a NUL byte; remove it"
    text, bodies, expands = heredocs(command, outer)
    commands = simple_commands(text)
    reasons = [scan(CREDENTIALS, command), scan(BYPASSES, text)]
    config = (outer is not None and outer.sets_git_config) or sets_git_config(text, commands, cwd)
    shell = Shell(cwd, tuple(bodies), expands, sets_git_config=config)
    if bodies and any(program(simple.words) in SCRIPT_RUNNERS for simple in commands):
        reasons.extend(reason_to_deny(body, cwd, outer=shell) for body in bodies)
    reasons.extend(
        check_words(
            simple.words,
            shell._replace(
                cwd=str(Path(cwd).joinpath(*simple.chdirs)), heredoc_stdin=simple.heredoc_stdin
            ),
        )
        for simple in commands
    )
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

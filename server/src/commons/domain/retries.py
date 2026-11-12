"""A command that failed and later worked: the turn nudge's signal that needs no tests (MIT-652).

Real misses happened with no local tests, or with a failure that showed only on CI, so the
test signal (a failing test run that later passed) never saw them. This one reads the turn's
Bash calls in order, each with the exit code the shell reported:

- A run *failed* if its exit code is known and non-zero, it runs a program (below), and none
  of its commands is one that fails without anything being wrong (`EXPECTED_TO_FAIL`), since
  the exit code could be that one's. A CI check that exits 8 is pending (`gh pr checks`), not
  failed.
- A command's *program* is its program and subcommand: `git commit`, `gh pr checks`,
  `npm run`, `pytest`. Leading `VAR=value` words, wrappers (`sudo`, `env`, `time`, `timeout`,
  `nice`, `nohup`) and runners (`uv run`, `poetry run`, `pnpm exec`, `bundle exec`, `npx`,
  `uvx`, `python -m`) are skipped, so `uv run --no-sync pytest -q` runs `pytest`. The
  subcommand is the word right after the program when it looks like a name (`commit`, not
  `tests/x.py` or inline code); `gh` has two (`pr checks`). Shell plumbing (`PLUMBING`: `cd`,
  `echo`, `cat`, `tail`, `sed`, `mkdir`...) runs no program.
- CI checks are one program: `gh pr checks`, and `gh run view` or `gh run watch` with
  `--exit-status` (without it they exit 0 whatever the run's result). A failed check followed
  by a passing one is a retry of the same CI check, whichever of them ran it.
- It *worked afterwards* if a later run exited 0 and runs the same program: the same name,
  with the same subcommand or none on either side. A pipe into `tail` can hide a failure
  behind tail's 0; the shell's code is taken as it is.

The store decides whether the turn earns the nudge, at most once per turn (decision 0034).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import takewhile
from pathlib import PurePosixPath

# Commands that exit non-zero with nothing wrong: a search with no match, a test that is
# false, a missing path, a diff that differs. A failing run that has one is not a failure.
# Names with a space are a program and its subcommand.
EXPECTED_TO_FAIL = frozenset(
    {"grep", "egrep", "fgrep", "rg", "test", "[", "[[", "false", "which", "command", "type"}
    | {"ls", "find", "diff", "cmp", "git diff", "git grep"}
)
# Moving around, printing, and reshaping text or files never names the work.
PLUMBING = frozenset(
    {"cd", "pushd", "popd", "pwd", "echo", "printf", "true", ":", "sleep", "export", "set"}
    | {"source", ".", "exit", "cat", "head", "tail", "sed", "awk", "sort", "uniq", "wc"}
    | {"tee", "cut", "tr", "mkdir", "rm", "cp", "mv", "touch"}
)
_WRAPPERS = frozenset({"sudo", "env", "time", "timeout", "nice", "nohup"})
_RUNNERS = frozenset(
    {("uv", "run"), ("poetry", "run"), ("pnpm", "exec"), ("bundle", "exec"), ("python", "-m")}
)
_BARE_RUNNERS = frozenset({"npx", "uvx"})
CI_CHECK = "CI check"
PENDING = 8  # `gh pr checks`: some checks are still running

_NAME = re.compile(r"[a-z][a-z0-9-]*")
_PROGRAM = re.compile(r"\w[\w.+-]*")
_ASSIGNMENT = re.compile(r"[A-Za-z_]\w*=")
_DURATION = re.compile(r"\d+(?:\.\d+)?[smhd]?")
_VERSIONED = re.compile(r"(python|pip)[0-9.]*")
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?^\s*\2\s*$", re.DOTALL | re.MULTILINE)
# One shell token: quoted parts stay in their word; `#` starts a comment only at a word's start.
_TOKEN = re.compile(
    r"""(?P<space>[ \t]+|\\\n)
    |(?P<redirect>\d*(?:&>>?|[<>]+&?\d*-?))
    |(?P<separator>&&|\|\||[;&|()\n])
    |(?P<comment>\#[^\n]*)
    |(?P<word>(?:[^\s;&|()<>'"\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+)""",
    re.VERBOSE | re.DOTALL,
)
_QUOTED = re.compile(r"""'([^']*)'|"((?:[^"\\]|\\.)*)"|\\(.)""", re.DOTALL)


@dataclass(frozen=True)
class CommandRun:
    """One Bash call: its text and the exit code the shell reported (None: unknown)."""

    command: str
    exit_code: int | None


@dataclass(frozen=True)
class Program:
    """A program and its subcommand ("" for none)."""

    name: str
    subcommand: str = ""

    def same_as(self, other: "Program") -> bool:
        """Say whether both name the same program: same name, same subcommand or none."""
        return self.name == other.name and (
            self.subcommand == other.subcommand or not self.subcommand or not other.subcommand
        )


@dataclass(frozen=True)
class ParsedCommand:
    """What a command runs, without plumbing or commands expected to fail."""

    programs: frozenset[Program]
    expected_to_fail: bool  # one of its commands can fail with nothing wrong


def fixed_failing_command(runs: Iterable[CommandRun]) -> bool:
    """Whether some run failed and a later one exited 0 running the same program."""
    failed: list[Program] = []
    for run in runs:
        parsed = parse_command(run.command)
        if run.exit_code == 0 and any(p.same_as(f) for p in parsed.programs for f in failed):
            return True
        if _failed(run.exit_code, parsed):
            failed.extend(parsed.programs)
    return False


def _failed(exit_code: int | None, parsed: ParsedCommand) -> bool:
    if exit_code is None or exit_code == 0 or parsed.expected_to_fail or not parsed.programs:
        return False
    return not (exit_code == PENDING and Program(CI_CHECK) in parsed.programs)


def parse_command(command: str) -> ParsedCommand:
    """Return the programs a shell command runs and whether one of them may fail harmlessly."""
    programs: set[Program] = set()
    expected = False
    for words in simple_commands(command):
        program = program_of(words)
        if program is None or program.name in PLUMBING:
            continue
        if _expected_to_fail(program):
            expected = True
        elif program.name == CI_CHECK or _PROGRAM.fullmatch(program.name):
            programs.add(program)
    return ParsedCommand(frozenset(programs), expected)


def _expected_to_fail(program: Program) -> bool:
    full = f"{program.name} {program.subcommand}"
    return program.name in EXPECTED_TO_FAIL or full in EXPECTED_TO_FAIL


def simple_commands(command: str) -> list[list[str]]:
    """Split a shell command into its simple commands' words; none if it doesn't parse.

    Comments, heredoc bodies, and redirections with their targets are left out.
    """
    text = _HEREDOC.sub(r"\3", command)
    commands: list[list[str]] = [[]]
    target = False  # the next word is a redirection's file
    position = 0
    while position < len(text):
        token = _TOKEN.match(text, position)
        if token is None:  # an unbalanced quote
            return []
        position = token.end()
        kind = token.lastgroup
        if kind == "separator":
            commands.append([])
        elif kind == "redirect":
            target = not token.group()[-1].isdigit() and not token.group().endswith("-")
        elif kind == "word" and not target:
            commands[-1].append(_QUOTED.sub(_unquoted, token.group()))
        elif kind == "word":
            target = False
    return [words for words in commands if words]


def _unquoted(part: re.Match[str]) -> str:
    return next(group for group in part.groups() if group is not None)


def program_of(words: Sequence[str]) -> Program | None:
    """Return the program and subcommand a simple command runs, past wrappers and runners."""
    words = _past_launchers(words)
    if not words:
        return None
    name = _name(words[0])
    if name == "gh":
        return _gh(words[1:])
    after = words[1] if len(words) > 1 else ""
    return Program(name, after if _NAME.fullmatch(after) else "")


def _name(word: str) -> str:
    """Name the program without its directory, and `python3.12` as `python`."""
    name = PurePosixPath(word).name
    return _VERSIONED.sub(r"\1", name) if _VERSIONED.fullmatch(name) else name


def _gh(arguments: Sequence[str]) -> Program:
    """`gh` subcommands are two words; its CI checks are one program."""
    subcommand = " ".join(takewhile(_NAME.fullmatch, arguments[:2]))
    watching = subcommand in {"run view", "run watch"} and "--exit-status" in arguments
    if subcommand == "pr checks" or watching:
        return Program(CI_CHECK)
    return Program("gh", subcommand)


def _past_launchers(words: Sequence[str]) -> Sequence[str]:
    """Skip `VAR=value` words, wrappers and runners, each with its options."""
    while words:
        name = _name(words[0])
        if _ASSIGNMENT.match(words[0]) or name in _WRAPPERS or name in _BARE_RUNNERS:
            words = _past_options(words[1:])
        elif len(words) > 1 and (name, words[1]) in _RUNNERS:
            words = _past_options(words[2:])
        else:
            break
    return words


def _past_options(words: Sequence[str]) -> Sequence[str]:
    """Skip options, and the duration `timeout` takes."""
    index = 0
    while index < len(words) and (
        words[index].startswith("-") or _DURATION.fullmatch(words[index])
    ):
        index += 1
    return words[index:]

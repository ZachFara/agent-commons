"""Flag GitHub-task sessions whose tool calls reach the upstream repo, the fix, or the cache.

Every GitHub task is a real bug with a public fix, and a session runs on this machine with its
network. So after the session its tool calls are scanned: what it asked for (Bash commands,
file paths and patterns, MCP arguments), never what came back, since the project's own files
name the repository. A `leak` flag keeps the session out of every count until a human clears
it; a `review` flag (any other outbound network call) only lists it. The scan blocks nothing:
an obfuscated fetch still gets through, which is why transcripts are still read.
"""

import re
import subprocess
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from commons.privacy.secrets import redact
from tools.eval import repos
from tools.eval.model import GitHubTask

Level = Literal["leak", "review"]
LEAK: Level = "leak"
REVIEW: Level = "review"
UNSCANNED = "mcp__context7__"  # Context7 is part of an arm, so its calls are never flagged
# Tools whose input can run or write code: only these are scanned for other network calls.
CODE_TOOLS = frozenset({"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"})
CACHE_MARK = ".cache/agent-commons-eval"  # ~/, $HOME/ or a listing of the folder
EXCERPT_WIDTH = 160  # characters of a flag's excerpt
EXCERPT_CONTEXT = 50  # characters kept before the match
# A sha prefix is a whole hex token: no letter or digit right before or after it.
HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{7,40}(?![0-9A-Za-z])")
INSTALL = re.compile(
    r"\b(?:pip3?\s+(?:install|download)|uv\s+add)\b(?P<args>[^;&|\n]*)|--with[=\s]+(?P<with>\S+)"
)
REQUIREMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
INDEX_URL = re.compile(r"\b(?:pypi\.org|files\.pythonhosted\.org)/(?P<path>[^\s\"'<>)]+)")
# "requests" and "socket" are English words too, so only their code forms count.
NETWORK = re.compile(
    r"\b(?:urllib|httpx|http\.client|curl|wget)\b|\bfetch\(|"
    r"\b(?:import|from)\s+(?:requests|socket)\b|\b(?:requests|socket)\.\w|"
    r"\bgit\s+(?:clone|fetch)\b|\bpip3?\s+download\b"
)


@dataclass(frozen=True)
class Flag:
    """One tool call that may have reached the fix: how sure, which rule, and a short excerpt."""

    level: Level
    rule: str
    tool: str
    excerpt: str  # scrubbed of secrets, at most EXCERPT_WIDTH characters


def normalize(name: str) -> str:
    """Return a distribution name normalized per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def repo_name(task: GitHubTask) -> tuple[str, str]:
    """Return the task repository's (owner, name), from its URL."""
    owner, name = task.repo.removesuffix("/").removesuffix(".git").split("/")[-2:]
    return owner, name


def distribution(task: GitHubTask, project: Path | None) -> str:
    """Return the task's own distribution name, normalized: `[project].name`, else the repo's.

    The name is read from the base tree's pyproject.toml: in `project` if given, else in the
    cache (a past run's project is gone).
    """
    text = _pyproject(task, project)
    try:
        name = tomllib.loads(text).get("project", {}).get("name") if text else None
    except tomllib.TOMLDecodeError:
        name = None
    return normalize(str(name) if name else repo_name(task)[1])


def _pyproject(task: GitHubTask, project: Path | None) -> str | None:
    if project is not None:
        path = project / "pyproject.toml"
        return path.read_text(encoding="utf-8") if path.is_file() else None
    bare = repos.cache_path(task.repo)
    if not (bare / "HEAD").exists():
        return None
    try:
        show = ["git", "--git-dir", str(bare), "show", f"{task.base_commit}:pyproject.toml"]
        return repos.run(show)
    except subprocess.CalledProcessError:
        return None  # the base tree has no pyproject.toml


@dataclass(frozen=True)
class Rules:
    """A task's leak rules, as compiled patterns and the names they look for."""

    repo: re.Pattern[str]
    links: re.Pattern[str]  # the issue or the fix
    cache: re.Pattern[str]
    fix_commit: str
    distribution: str

    @classmethod
    def of(cls, task: GitHubTask, distribution: str) -> "Rules":
        """Return the rules for one task and its distribution name."""
        owner, name = (re.escape(part) for part in repo_name(task))
        hosts = r"(?:api\.github\.com/repos/|raw\.githubusercontent\.com/|github\.com[/:])"
        links = (
            re.escape(re.sub(r"^https?://", "", url)) for url in (task.issue_url, task.fix_url)
        )
        caches = (CACHE_MARK, redact(str(repos.CACHE)).text, repos.cache_path(task.repo).name)
        return cls(
            repo=re.compile(rf"{hosts}{owner}/{name}(?:\.git)?(?![\w-]|\.\w)", re.IGNORECASE),
            links=re.compile(rf"(?:{'|'.join(links)})(?![\w-])", re.IGNORECASE),
            cache=re.compile("|".join(re.escape(path) for path in caches)),
            fix_commit=task.fix_commit.lower(),
            distribution=distribution,
        )

    def leaks(self, text: str) -> Iterator[tuple[str, re.Match[str]]]:
        """Yield (rule, match) for each leak rule the text breaks, once per rule."""
        found = {
            "repo": self.repo.search(text),
            "fix_commit": next(
                (m for m in HEX_TOKEN.finditer(text) if self.fix_commit.startswith(m[0].lower())),
                None,
            ),
            "issue_or_fix": self.links.search(text),
            "cache": self.cache.search(text),
            "index": next(self._index_matches(text), None),
        }
        yield from ((rule, match) for rule, match in found.items() if match is not None)

    def _index_matches(self, text: str) -> Iterator[re.Match[str]]:
        """Yield installs, downloads and index URLs of the task's own distribution."""
        for match in INSTALL.finditer(text):
            words = (match["args"] or "").split() + (match["with"] or "").split(",")
            if self.distribution in {_requirement(word) for word in words}:
                yield match
        for match in INDEX_URL.finditer(text):
            segments = match["path"].split("/")
            names = {normalize(s) for s in segments} | {_requirement(s) for s in segments}
            if self.distribution in names:
                yield match


def _requirement(word: str) -> str:
    """Return the normalized name a requirement word starts with ("" for an option or path)."""
    match = REQUIREMENT.match(word.strip("\"'"))
    name = match[0] if match else ""
    return normalize(re.split(r"-\d", name)[0]) if name else ""


def scan(calls: list[dict[str, Any]], task: GitHubTask, distribution: str) -> list[Flag]:
    """Return the flags of a session's tool calls (`runner.tool_uses`) on a GitHub task."""
    rules = Rules.of(task, distribution)
    flags: list[Flag] = []
    for call in calls:
        tool = str(call.get("name"))
        if not tool.startswith(UNSCANNED):
            flags += _call_flags(rules, tool, call.get("input"))
    return flags


def _call_flags(rules: Rules, tool: str, given: object) -> list[Flag]:
    """Return one call's flags: each leak rule once; else, for code, one review flag."""
    carried = list(_values(given, "old_string"))  # already in the file, not asked for
    texts = [redact(text).text for text in _strings(given, "old_string")]
    flags: dict[str, Flag] = {}
    for text in texts:
        for rule, match in rules.leaks(text):
            if rule not in flags and not any(match[0] in old for old in carried):
                flags[rule] = Flag(LEAK, rule, tool, _excerpt(text, match))
    if flags or tool not in CODE_TOOLS:
        return list(flags.values())
    network = ((text, m) for text in texts if (m := NETWORK.search(text)) is not None)
    return [Flag(REVIEW, "network", tool, _excerpt(text, m)) for text, m in network][:1]


def _strings(value: object, drop: str) -> Iterator[str]:
    """Yield every string in a tool input but the values of key `drop`."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != drop:
                yield from _strings(item, drop)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, drop)


def _values(value: object, key: str) -> Iterator[str]:
    """Yield the string values of `key` anywhere in a tool input."""
    if isinstance(value, dict):
        found = value.get(key)
        if isinstance(found, str):
            yield found
        value = list(value.values())
    for item in value if isinstance(value, list) else ():
        yield from _values(item, key)


def _excerpt(text: str, match: re.Match[str]) -> str:
    """Return the match with a little context, on one line, at most EXCERPT_WIDTH characters."""
    start = max(match.start() - EXCERPT_CONTEXT, 0)
    window = " ".join(text[start : start + EXCERPT_WIDTH * 2].split())
    return window[:EXCERPT_WIDTH]

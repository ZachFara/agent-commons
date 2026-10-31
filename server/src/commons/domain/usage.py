"""Whether an entry's fix shows up in what the agent did (decision 0016; v1 is decision 0036).

v1 weighs each artifact by how rare it is across the store's fixes, gives generic tokens no
weight, and ignores prose: writing about a fix in a .md file is not applying it. DESIGN.md §11
asks whether this matching is good enough; the outcomes data will tell.
"""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePosixPath

from commons.domain.outcomes import Usage

_QUOTED = re.compile(r"`([^`]+)`|\"([^\"]{3,})\"|'([^']{3,})'")
_TOKEN = re.compile(r"[^\s`'\"]+")
_DISTINCTIVE = re.compile(r"\d|.[=<>/_:-]")
_PLACEHOLDER = re.compile(r"\[REDACTED:[a-z_]+\]", re.IGNORECASE)
_LOCAL_URL = re.compile(r"(https?://)?(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?/?")
_TRIM = ".,;:()[]{}!?"
MIN_TOKEN_LENGTH = 4
YES_SHARE = 0.5  # of the fix's artifact weight, for `yes`; any less but some is `partial`

# Tokens that look distinctive to the v0 rule but appear in all kinds of unrelated work.
GENERIC_ARTIFACTS = frozenset(
    {
        # common flags
        "--all", "--config", "--debug", "--dry-run", "--file", "--force", "--help", "--host",
        "--name", "--output", "--password", "--port", "--quiet", "--realm", "--server",
        "--user", "--verbose", "--version", "--yes", "-y",
        # ordinary hyphenated words
        "built-in", "e.g.", "follow-up", "i.e.", "long-term", "opt-in", "opt-out", "re-run",
        "re-try", "set-up", "short-term", "up-to-date", "well-known",
    }
)  # fmt: skip
PROSE_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})
PROSE_DIRECTORIES = frozenset({"docs", "doc"})
# .txt files that configure builds rather than describe them.
_CONFIG_TEXT = re.compile(r"(requirements|constraints)[\w.-]*\.txt|cmakelists\.txt")


def fix_artifacts(fix: str) -> set[str]:
    """Return the fix's concrete pieces: quoted spans and tokens like `pkg<2.1` or `--flag`."""
    text = _PLACEHOLDER.sub(" ", fix)  # redacted secrets are not something to look for
    artifacts = {
        span.strip().lower() for groups in _QUOTED.findall(text) for span in groups if span
    }
    for raw in _TOKEN.findall(text):
        token = raw.strip(_TRIM).lower()
        if len(token) >= MIN_TOKEN_LENGTH and _DISTINCTIVE.search(token):
            artifacts.add(token)
    # Pure punctuation ("...", "--") is a placeholder, not something the agent ran or wrote.
    return {a for a in artifacts if any(c.isalnum() for c in a)}


def is_generic(artifact: str) -> bool:
    """Whether the artifact says nothing about this fix in particular."""
    return artifact in GENERIC_ARTIFACTS or bool(_LOCAL_URL.fullmatch(artifact))


def is_prose(path: str | None) -> bool:
    """Whether a file is documentation or notes rather than code or configuration."""
    if not path:
        return False
    pure = PurePosixPath(path.replace("\\", "/"))
    if _CONFIG_TEXT.fullmatch(pure.name.lower()):
        return False
    return pure.suffix.lower() in PROSE_SUFFIXES or bool(
        PROSE_DIRECTORIES & {part.lower() for part in pure.parts[:-1]}
    )


@dataclass(frozen=True)
class ArtifactFrequencies:
    """How many approved entries' fixes contain each artifact, for rarity weights."""

    entries: int = 0
    counts: Mapping[str, int] = field(default_factory=dict)

    def weight(self, artifact: str) -> float:
        """Weigh generic tokens 0, and rarer artifacts more (smoothed IDF)."""
        if is_generic(artifact):
            return 0.0
        seen = self.counts.get(artifact, 1)
        return math.log((1 + max(self.entries, seen)) / (1 + seen)) + 1


def count_artifacts(fixes: Iterable[str]) -> ArtifactFrequencies:
    """Count, per artifact, how many of the fixes contain it."""
    counts: dict[str, int] = {}
    total = 0
    for fix in fixes:
        total += 1
        for artifact in fix_artifacts(fix):
            counts[artifact] = counts.get(artifact, 0) + 1
    return ArtifactFrequencies(total, counts)


@dataclass(frozen=True)
class Evidence:
    """One thing the agent did after the retrieval: a command, or text added or removed."""

    text: str
    at: datetime | None = None
    path: str | None = None

    def counts(self) -> bool:
        """Commands and code edits count; edits to prose files do not (decision 0036)."""
        return not is_prose(self.path)


@dataclass(frozen=True)
class UseObservation:
    """Whether the fix was used, and when the first matching work happened."""

    used: Usage | None
    matched_at: datetime | None = None


NO_FREQUENCIES = ArtifactFrequencies()


def observe_use(
    fix: str, evidence: list[Evidence], frequencies: ArtifactFrequencies = NO_FREQUENCIES
) -> UseObservation:
    """Yes if at least half the fix's artifact weight appears, partial if some, no if none.

    Unknown when the fix has nothing weighted to look for. `matched_at` is the time of the
    earliest evidence that matched, so a test run can be paired with the work before it.
    """
    weights = {a: frequencies.weight(a) for a in fix_artifacts(fix)}
    weights = {a: w for a, w in weights.items() if w > 0}
    if not weights:
        return UseObservation(None)
    found: set[str] = set()
    matched_at: datetime | None = None
    for item in evidence:
        if not item.counts():
            continue
        text = item.text.lower()
        hits = {a for a in weights if a in text}
        if hits and matched_at is None:
            matched_at = item.at
        found |= hits
    share = sum(weights[a] for a in found) / sum(weights.values())
    if share == 0:
        return UseObservation("no")
    return UseObservation("yes" if share >= YES_SHARE else "partial", matched_at)

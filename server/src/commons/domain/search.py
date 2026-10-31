import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from commons.domain.errors import InvalidInputError
from commons.domain.reporters import PrivacyDefault

MAX_LIMIT = 20


@dataclass(frozen=True)
class SearchRequest:
    """What the agent searched for (DESIGN.md §4 search, decision 0012)."""

    query: str
    task_id: UUID
    environment: dict[str, object] | None
    limit: int

    def language(self) -> str | None:
        """Return the language name to filter on: the first word of environment.language."""
        value = (self.environment or {}).get("language")
        if not isinstance(value, str) or not value.split():
            return None
        return value.split()[0].lower()


def validate_search(request: SearchRequest) -> None:
    """Raise InvalidInputError if the request can't be served."""
    if not request.query.strip():
        msg = "query must not be empty."
        raise InvalidInputError(msg)
    if not 1 <= request.limit <= MAX_LIMIT:
        msg = f"limit must be between 1 and {MAX_LIMIT}."
        raise InvalidInputError(msg)


@dataclass(frozen=True)
class SearchRecord:
    """What the store keeps about one search, already cut down for its privacy mode."""

    task_id: UUID
    privacy_mode: PrivacyDefault
    query: str
    language: str | None
    limit: int
    hit_count: int
    environment: dict[str, object] | None
    results: list[dict[str, object]] | None


@dataclass(frozen=True)
class SearchHit:
    """One search result with the facts its provenance line is built from."""

    entry_id: UUID
    symptom: str
    environment: dict[str, object]
    cause: str
    fix: str
    repro: str | None
    score: float
    proposed_by_model: str
    human_reviewed: bool
    used_outcomes: int
    successes: int
    last_confirmed: datetime | None
    revision: int = 1
    fixed_in: dict[str, str] | None = None  # set only on a retired entry shown to older versions
    risks: tuple[str, ...] = ()  # labels from the risk scanner (decision 0029)
    confirmed: bool = False  # provisional, but confirmed by others' observed outcomes (0028)
    imported_from: dict[str, str] | None = None  # moved from a local store (decision 0031)

    def provenance(self) -> str:
        """One line on where the entry came from and how it has held up."""
        if self.used_outcomes:
            rate = f"success {self.successes}/{self.used_outcomes} when used"
        else:
            rate = "no outcomes yet"
        confirmed = (
            f"last confirmed {self.last_confirmed:%Y-%m-%d}"
            if self.last_confirmed
            else "never confirmed"
        )
        parts = [f"proposed by {self.proposed_by_model}", rate, confirmed]
        if not self.human_reviewed:
            parts.append("confirmed by outcomes" if self.confirmed else "unreviewed")
        if self.imported_from:
            first = str(self.imported_from.get("created_at", ""))[:10]
            parts.append(f"imported from a local store (first proposed {first})")
        if self.risks:
            parts.append(f"contains: {', '.join(self.risks)}; review before running")
        if self.fixed_in:
            library, version = self.fixed_in.get("library"), self.fixed_in.get("version")
            parts.append(f"fixed in {library} {version}; shown because you're on an older one")
        return "; ".join(parts)


# Words in nearly every error report: they may match, but they barely count (decision 0025).
GENERIC_WORDS = (
    "error errors fail failed failure exception warning warn check checks traceback recent "
    "call last line file run running build test tests process exit code info debug log logs "
    # exception names and the phrases they come with
    "attributeerror typeerror valueerror keyerror importerror modulenotfounderror syntaxerror "
    "runtimeerror object attribute module named import"
)
GENERIC_WEIGHT = 0.1
MIN_RELEVANCE = 0.25  # the highest cutoff that keeps recall@5 on the judged set (0025)
SAME_MINOR_BOOST = 1.2
OTHER_MAJOR_FACTOR = 0.5
_VERSION = re.compile(r"(\d+)(?:\.(\d+))?")


@dataclass(frozen=True)
class Lexemes:
    """Stemmed words of a query and of one candidate entry, with store-wide frequencies."""

    query: frozenset[str]
    symptom: frozenset[str]  # symptom and domains: what the problem looks like
    body: frozenset[str]  # cause and fix
    frequency: Mapping[str, int]  # approved entries containing each word
    entries: int  # approved entries in the store
    generic: frozenset[str]


def relevance(words: Lexemes) -> float:
    """Return how well an entry answers a query, 0 to 1 (decision 0025).

    The geometric mean of two coverages, each weighted by how rare a word is across the
    store: how much of the entry's symptom the query contains, and how much of the query
    (the words the store knows) the entry contains. A query that shares only common words
    with an entry scores near 0; one that repeats its error line scores near 1, even inside
    log noise, since words the store has never seen don't count against it.
    """

    def weight(word: str) -> float:
        rarity = math.log(1 + (words.entries + 1) / (words.frequency.get(word, 0) + 1))
        return rarity * (GENERIC_WEIGHT if word in words.generic else 1.0)

    entry = words.symptom | words.body
    known = {w for w in words.query if words.frequency.get(w, 0) > 0}
    symptom_total = sum(weight(w) for w in words.symptom)
    known_total = sum(weight(w) for w in known)
    if not symptom_total or not known_total:
        return 0.0
    symptom_share = sum(weight(w) for w in words.query & words.symptom) / symptom_total
    query_share = sum(weight(w) for w in known & entry) / known_total
    return math.sqrt(symptom_share * query_share)


def major_minor(version: object) -> tuple[int, int | None] | None:
    """Return (major, minor) from a version or range such as '2.1.0', '^18.2', '>=3.0,<4'.

    PEP 440 and npm semver agree on the leading major.minor, which is all this compares.
    """
    if not isinstance(version, str):
        return None
    match = _VERSION.search(version)
    if match is None:
        return None
    return int(match[1]), int(match[2]) if match[2] is not None else None


def version_factor(asked: object, entry: object) -> float:
    """Boost entries for the caller's major.minor of a shared library; demote other majors.

    Compares every library both name. Any different major demotes; otherwise a same
    major.minor boosts (decision 0025). No shared library, or no versions, leaves 1.0.
    """
    if not isinstance(asked, dict) or not isinstance(entry, dict):
        return 1.0
    shared = {name.lower(): v for name, v in asked.items()}.keys() & {
        name.lower() for name in entry
    }
    entry_versions = {name.lower(): v for name, v in entry.items()}
    asked_versions = {name.lower(): v for name, v in asked.items()}
    factor = 1.0
    for name in sorted(shared):
        mine, theirs = major_minor(asked_versions[name]), major_minor(entry_versions[name])
        if mine is None or theirs is None:
            continue
        if mine[0] != theirs[0]:
            return OTHER_MAJOR_FACTOR
        if mine[1] is not None and mine[1] == theirs[1]:
            factor = SAME_MINOR_BOOST
    return factor

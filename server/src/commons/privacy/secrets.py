"""Find and redact secrets and identifying details in agent-written text (DESIGN.md §6).

Runs on every write. Findings name the kind and field, never the secret itself.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import partial

# Order matters: earlier patterns consume text before later, looser ones see it.
# Each pattern's group "secret" (or the whole match) is replaced by [REDACTED:<kind>].
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    (
        "connection_string",
        re.compile(r"\b[a-z][a-z0-9+.-]*://(?P<secret>[^\s:/@]+:[^\s@/]+)@", re.IGNORECASE),
    ),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("stripe_key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{16,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9._~+/=-]{20,})")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b[a-z0-9_]*(?:api[_-]?key|secret|token|password|passwd|pwd)[a-z0-9_]*"
            r"[\"']?\s*[:=]\s*[\"']?(?!\[REDACTED:)(?P<secret>[^\s\"',;]{8,})"
        ),
    ),
    (
        "email",
        re.compile(
            r"\b(?!git@)[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
        ),
    ),
)

# Home directories reveal usernames. Keep the path, replace the name.
_HOME_PATH = re.compile(r"(?P<prefix>/Users/|/home/|[A-Za-z]:\\Users\\)(?P<secret>[^/\\\s'\"]+)")
_PLACEHOLDER_USERS = frozenset({"<user>", "user", "username", "you", "me", "runner", "Shared"})


@dataclass
class Redaction:
    """Redacted text plus how many of each kind were removed."""

    text: str
    counts: Counter[str] = field(default_factory=Counter)


def redact(text: str) -> Redaction:
    """Return the text with secrets and identifying details replaced by placeholders."""
    result = Redaction(text)
    for kind, pattern in _PATTERNS:
        result.text = pattern.sub(partial(_replace, kind=kind, counts=result.counts), result.text)
    result.text = _HOME_PATH.sub(lambda m: _replace_user(m, result.counts), result.text)
    return result


def _replace(match: re.Match[str], *, kind: str, counts: Counter[str]) -> str:
    counts[kind] += 1
    placeholder = f"[REDACTED:{kind}]"
    if "secret" not in match.re.groupindex:
        return placeholder
    start, end = match.span("secret")
    whole = match.group(0)
    offset = match.start()
    return whole[: start - offset] + placeholder + whole[end - offset :]


def _replace_user(match: re.Match[str], counts: Counter[str]) -> str:
    if match.group("secret") in _PLACEHOLDER_USERS:
        return match.group(0)
    counts["username_path"] += 1
    return match.group("prefix") + "<user>"

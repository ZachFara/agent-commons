"""Whether an entry's fix shows up in what the agent did (decision 0016, `used` v0).

DESIGN.md §11 asks whether this matching is good enough; the outcomes data will tell.
"""

import re

from commons.domain.outcomes import Usage

_QUOTED = re.compile(r"`([^`]+)`|\"([^\"]{3,})\"|'([^']{3,})'")
_TOKEN = re.compile(r"[^\s`'\"]+")
_DISTINCTIVE = re.compile(r"\d|.[=<>/_:-]")
_PLACEHOLDER = re.compile(r"\[REDACTED:[a-z_]+\]", re.IGNORECASE)
_TRIM = ".,;:()[]{}!?"
MIN_TOKEN_LENGTH = 4


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
    return {a for a in artifacts if a}


def compute_used(fix: str, session_text: str) -> Usage | None:
    """Yes if at least half the fix's artifacts appear, partial if some, no if none.

    None (unknown) when the fix has nothing concrete to look for.
    """
    artifacts = fix_artifacts(fix)
    if not artifacts:
        return None
    haystack = session_text.lower()
    present = sum(artifact in haystack for artifact in artifacts)
    if present == 0:
        return "no"
    return "yes" if 2 * present >= len(artifacts) else "partial"

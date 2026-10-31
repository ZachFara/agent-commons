"""Risky-content scanning for entry text (decision 0029). Pure: no I/O, like the secret scanner.

Entries are agent-written text that other agents read and may act on. The scanner labels what
a reader should check before running anything. It never blocks a write: a label, not a
verdict. Invisible Unicode is stripped, and the stripping reported.
"""

import re
from collections import Counter
from dataclasses import dataclass, replace

from commons.domain.entries import Proposal

_FLAGS = re.IGNORECASE


@dataclass(frozen=True)
class RiskPattern:
    """One kind of risky content (its label is in commons.domain.risks)."""

    kind: str
    pattern: re.Pattern[str]


RISKS = (
    RiskPattern(
        "pipe_to_shell",
        re.compile(
            r"\b(curl|wget)\b[^\n|;]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"
            r"|\b(ba|z)?sh\s+<\(\s*(curl|wget)\b"
            r"|\biex\s*\(?\s*\(?\s*(iwr|invoke-webrequest|irm|invoke-restmethod|new-object\s+net\.webclient)\b",
            _FLAGS,
        ),
    ),
    RiskPattern(
        "destructive_delete",
        re.compile(
            r"\brm\s+(-[a-z]+\s+)*-[a-z]*r[a-z]*\s+(-[a-z-]+\s+)*"
            r"(/|/\*|~|~/|~/\*|\$home|\$\{home\}|\*|\.|\.\.)(?=\s|$|;|&|\)|`|'|\")",
            _FLAGS,
        ),
    ),
    RiskPattern(
        "credential_access",
        re.compile(
            r"(~|\$home|\$\{home\})/\.(ssh|aws|gnupg|kube|netrc|docker/config\.json|git-credentials)\b"
            r"|\bid_(rsa|ed25519|ecdsa)\b|\.aws/credentials\b"
            r"|\bsecurity\s+(find|dump)-(generic|internet)-password\b|login\.keychain"
            r"|google/chrome/[^\s]*(login data|cookies)|\.mozilla/firefox/[^\s]*logins\.json"
            r"|\b(cat|less|more|type|grep|scp|curl|base64)\b[^\n]*(^|[\s/'\"@=])\.env(?=$|[\s'\"])",
            _FLAGS,
        ),
    ),
    RiskPattern(
        "security_disabled",
        re.compile(
            r"\bverify\s*=\s*false\b|\bcurl\b[^\n]*\s(-k|--insecure)\b"
            r"|node_tls_reject_unauthorized\s*=\s*['\"]?0|pythonhttpsverify\s*=\s*['\"]?0"
            r"|http\.sslverify\s+false|strict-ssl\s+false|\bchmod\s+(-r\s+)?0?777\b"
            r"|\bspctl\s+--master-disable|\bcsrutil\s+disable|\bsetenforce\s+0\b"
            r"|--disable-web-security|\bsudo\b[^\n]*\b(curl|wget)\b",
            _FLAGS,
        ),
    ),
    RiskPattern(
        "encoded_exec",
        re.compile(
            r"base64\s+(-d|--decode|-D)\b[^\n|]*\|\s*(ba|z)?sh\b"
            r"|\beval\s*\(\s*(atob|base64|requests\.get|fetch|urllib)"
            r"|\beval\s+[\"']?\$\(\s*(curl|wget|base64)\b"
            r"|\bexec\(\s*(base64\.b64decode|requests\.get|urllib)"
            r"|powershell[^\n]*\s-(e|enc|encodedcommand)\s",
            _FLAGS,
        ),
    ),
    RiskPattern(
        "unpinned_install",
        re.compile(
            r"\bpip3?\s+install\s+[^\n]*\b(git\+)?https?://[^\s@]+(?=\s|$)(?<!\.git@)"
            r"|\bnpm\s+(i|install)\s+[^\n]*\bhttps?://"
            r"|\bgo\s+install\s+\S+@latest\b",
            _FLAGS,
        ),
    ),
    RiskPattern(
        "agent_directed",
        re.compile(
            r"\bignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier)\s+instructions"
            r"|\bdisregard\s+(all\s+|the\s+)?(previous|prior|above|earlier)\b"
            r"|\byou\s+must\s+now\b|\bnew\s+instructions\s*:"
            r"|\bcall\s+the\s+(commons\s+)?`?(approve|reject|propose|report)`?\s+tool"
            r"|<\s*/?\s*(system|tool_call|function_calls?|invoke)\b",
            _FLAGS,
        ),
    ),
)
INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_SCANNED = ("cause", "fix", "repro", "symptom")


def strip_invisible(text: str) -> tuple[str, int]:
    """Remove zero-width and bidirectional control characters; return how many."""
    return INVISIBLE.subn("", text)


def scan_text(text: str) -> Counter[str]:
    """Count each risk kind in one text."""
    return Counter({risk.kind: n for risk in RISKS if (n := len(risk.pattern.findall(text)))})


def scan_proposal(proposal: Proposal) -> tuple[Proposal, dict[str, object]]:
    """Strip invisible characters from the text fields and report risky content."""
    counts: Counter[str] = Counter()
    cleaned: dict[str, str | None] = {}
    for field in _SCANNED:
        value = getattr(proposal, field)
        if value is None:
            cleaned[field] = None
            continue
        text, invisible = strip_invisible(value)
        cleaned[field] = text
        if invisible:
            counts[f"{field}:invisible_unicode"] += invisible
        for kind, count in scan_text(text).items():
            counts[f"{field}:{kind}"] += count
    stripped = replace(
        proposal,
        symptom=cleaned["symptom"] or "",
        cause=cleaned["cause"] or "",
        fix=cleaned["fix"] or "",
        repro=cleaned["repro"],
    )
    findings = [
        {"field": key.split(":")[0], "kind": key.split(":")[1], "count": count}
        for key, count in sorted(counts.items())
    ]
    return stripped, {"scanned": True, "findings": findings}

"""Apply secret redaction to the things agents write (DESIGN.md §6: every write)."""

from collections import Counter
from dataclasses import replace

from commons.domain.entries import Proposal
from commons.privacy.secrets import redact


def redact_text(text: str, counts: Counter[str], field: str) -> str:
    """Redact one field, adding its findings to `counts` keyed by "field:kind"."""
    result = redact(text)
    for kind, count in result.counts.items():
        counts[f"{field}:{kind}"] += count
    return result.text


def redact_json(value: object, counts: Counter[str], field: str) -> object:
    """Redact every string inside a JSON value, keys included."""
    if isinstance(value, str):
        return redact_text(value, counts, field)
    if isinstance(value, list):
        return [redact_json(item, counts, field) for item in value]
    if isinstance(value, dict):
        return {
            redact_text(str(key), counts, field): redact_json(item, counts, field)
            for key, item in value.items()
        }
    return value


def report(counts: Counter[str]) -> dict[str, object]:
    """Build the report shown to reviewers: what kinds were removed from which fields."""
    findings = []
    for key, count in sorted(counts.items()):
        field, kind = key.split(":", 1)
        findings.append({"field": field, "kind": kind, "count": count})
    return {"scanned": True, "findings": findings}


def redact_proposal(proposal: Proposal) -> tuple[Proposal, dict[str, object]]:
    """Return the proposal with every text field redacted, and the redaction report."""
    counts: Counter[str] = Counter()
    environment = redact_json(proposal.environment, counts, "environment")
    redacted = replace(
        proposal,
        symptom=redact_text(proposal.symptom, counts, "symptom"),
        cause=redact_text(proposal.cause, counts, "cause"),
        fix=redact_text(proposal.fix, counts, "fix"),
        repro=None if proposal.repro is None else redact_text(proposal.repro, counts, "repro"),
        domains=tuple(redact_text(d, counts, "domains") for d in proposal.domains),
        environment=environment if isinstance(environment, dict) else {},
    )
    return redacted, report(counts)


def scrub(text: str) -> str:
    """Redact text where no report is kept (queries, assessments, review reasons)."""
    return redact(text).text


def scrub_json(value: dict[str, object]) -> dict[str, object]:
    """Redact every string in a JSON object where no report is kept."""
    scrubbed = redact_json(value, Counter(), "")
    return scrubbed if isinstance(scrubbed, dict) else {}

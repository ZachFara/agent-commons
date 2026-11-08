"""Rescan stored entries when the risk patterns change (decision 0029).

Entries are scanned when written, and the patterns grow as new cases appear, so older entries
can carry a report the current patterns disagree with. A rescan reruns the scanner over each
approved and pending entry's current text. Applied, it stores the new report, and a
provisional entry no human has reviewed that gains a finding goes back to the queue (0028:
any finding blocks provisional approval).

The text is never changed: invisible characters still in it are reported, not stripped
(changing text is a revision, which the admin editor makes).
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from commons.domain.entries import Proposal
from commons.privacy.risks import scan_proposal
from commons.service import events
from commons.service.identity import current_key
from commons.store import trust
from commons.store.connection import Connection
from commons.store.entries import scannable_entries, set_risk_report

REOPEN_REASON = "new risk finding on rescan (0029)"

Finding = tuple[str, str]  # (field, kind)


@dataclass(frozen=True)
class Rescanned:
    """One entry whose stored risk report differs from what the scanner finds now."""

    entry_id: UUID
    status: str
    before: dict[Finding, int]
    after: dict[Finding, int]
    reopened: bool  # sent (or, without apply, would be sent) back to the queue


def rescan(conn: Connection, *, apply: bool) -> list[Rescanned]:
    """Return every entry whose findings change; with `apply`, store them and reopen."""
    changed = []
    with conn.transaction():
        for row in scannable_entries(conn, lock=apply):
            found = _rescanned(row)
            if found is None:
                continue
            changed.append(found)
            if apply:
                set_risk_report(conn, found.entry_id, _report(found.after))
                if found.reopened:
                    trust.reopen(conn, [found.entry_id], REOPEN_REASON)
    if apply:
        reopened = sum(found.reopened for found in changed)
        actor = current_key()
        events.event(
            "admin_action",
            severity="NOTICE",
            action="rescan",
            ok=True,
            result=f"{len(changed)} rescanned, {reopened} reopened",
            key_id=None if actor is None else str(actor),
        )
    return changed


def _rescanned(row: dict[str, Any]) -> Rescanned | None:
    stored: dict[str, Any] = row["risk_report"]
    before = _counts(stored)
    after = _scan(row, before)
    if after == before and stored.get("scanned"):
        return None
    gained = set(after) - set(before)
    reopen = bool(gained) and row["status"] == "approved" and bool(row["unreviewed"])
    return Rescanned(row["id"], row["status"], before, after, reopen)


def _scan(row: dict[str, Any], before: dict[Finding, int]) -> dict[Finding, int]:
    """Count what the scanner finds in the current text.

    Hidden characters stripped on write are gone from the text, so the stored count is kept
    for them: the report still says they were there.
    """
    proposal = Proposal(
        symptom=row["symptom"],
        environment=row["environment"],
        cause=row["cause"],
        fix=row["fix"],
        domains=tuple(row["domains"]),
        repro=row["repro"],
        task_id=row["task_id"],
    )
    _, report = scan_proposal(proposal)  # the stripped text is dropped: report, never strip
    after = _counts(report)
    for (field, kind), count in before.items():
        if kind == "invisible_unicode":
            after[field, kind] = max(after.get((field, kind), 0), count)
    return after


def _counts(report: dict[str, Any]) -> dict[Finding, int]:
    return {(f["field"], f["kind"]): int(f["count"]) for f in report.get("findings", [])}


def _report(counts: dict[Finding, int]) -> dict[str, object]:
    """Build a report in the scanner's shape (decision 0029)."""
    findings = [
        {"field": field, "kind": kind, "count": count}
        for (field, kind), count in sorted(counts.items())
    ]
    return {"scanned": True, "findings": findings}

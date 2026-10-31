"""Revoke and purge a reporter in one step, reversibly (decision 0029).

Purging revokes every key, takes the reporter's approved entries out of search (retired with
the reason), excludes the outcomes they reported from ranking and reliability, and suspends
them, which holds their pending entries out of the review queue. Nothing is deleted; the
purge records exactly what it changed, and restoring reverses that.
"""

import uuid
from dataclasses import dataclass
from uuid import UUID

from commons.domain.corrections import Correction
from commons.domain.errors import NotFoundError
from commons.domain.lifecycle import require_reason
from commons.service import corrections, lifecycle
from commons.service.identity import current_pseudonym
from commons.service.ranking import rank
from commons.store import purges as store
from commons.store.connection import Connection
from commons.store.reporters import find_reporter


@dataclass(frozen=True)
class PurgeResult:
    """What a purge or restore changed."""

    keys: int
    entries: int
    outcomes: int


def revoke_all(conn: Connection, pseudonym: str) -> int:
    """Revoke every key the reporter has; return how many."""
    reporter = _reporter(conn, pseudonym)
    with conn.transaction():
        keys = store.active_key_ids(conn, reporter)
        store.set_keys_revoked(conn, keys, revoked=True)
    return len(keys)


def purge(conn: Connection, pseudonym: str, reason: str) -> PurgeResult:
    """Revoke, retire, exclude and suspend, recording it all for `restore`."""
    why = require_reason(reason)
    reporter = _reporter(conn, pseudonym)
    by = current_pseudonym(conn)
    with conn.transaction():
        keys = store.active_key_ids(conn, reporter)
        store.set_keys_revoked(conn, keys, revoked=True)
        entries = store.approved_entries_by(conn, reporter)
        for entry_id in entries:
            lifecycle.retire(conn, entry_id, f"purged: {why}")
        outcomes = store.counted_outcomes_by(conn, reporter)
        batch = uuid.uuid4() if outcomes else None
        for outcome_id in outcomes:
            exclusion = Correction(
                "outcomes", outcome_id, "excluded", value=True, reason=f"purged: {why}", by=by
            )
            corrections.correct(conn, exclusion, batch)
        store.set_suspended(conn, reporter, suspended=True)
        store.record_purge(
            conn,
            {
                "reporter_id": reporter,
                "by": by,
                "reason": why,
                "keys": keys,
                "entries": entries,
                "batch": batch,
            },
        )
        rank(conn)
    return PurgeResult(len(keys), len(entries), len(outcomes))


def restore(conn: Connection, pseudonym: str) -> PurgeResult:
    """Reverse the reporter's latest purge exactly."""
    reporter = _reporter(conn, pseudonym)
    by = current_pseudonym(conn)
    with conn.transaction():
        last = store.latest_purge(conn, reporter)
        if last is None:
            msg = f"{pseudonym} has no purge to restore."
            raise NotFoundError(msg)
        store.set_keys_revoked(conn, last["keys"], revoked=False)
        for entry_id in last["retired_entries"]:
            lifecycle.restore(conn, entry_id, "purge restored")
        undone = 0
        if last["correction_batch"] is not None:
            first = store.first_correction(conn, last["correction_batch"])
            if first is not None:
                undone = corrections.undo(conn, first, by=by)
        store.set_suspended(conn, reporter, suspended=False)
        store.mark_restored(conn, last["id"], by)
    return PurgeResult(len(last["keys"]), len(last["retired_entries"]), undone)


def _reporter(conn: Connection, pseudonym: str) -> UUID:
    reporter = find_reporter(conn, pseudonym)
    if reporter is None:
        msg = f"No reporter {pseudonym!r}."
        raise NotFoundError(msg)
    return reporter

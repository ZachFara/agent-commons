"""Trust levels, provisional approval and confirmation by outcomes (decision 0028)."""

import os
import secrets
from uuid import UUID

from commons.domain.errors import InvalidInputError, NotFoundError
from commons.domain.keys import KEY_SCOPES
from commons.domain.trust import (
    Role,
    TrustLevel,
    TrustSettings,
    may_hold,
    sample_one_in,
    trust_level,
    validate_role,
)
from commons.store import trust as store
from commons.store.api_keys import revoke_scopes_except
from commons.store.connection import Connection
from commons.store.reporters import find_reporter

CONFIRM_SUCCESSES = 3
CONFIRM_REPORTERS = 2


def settings() -> TrustSettings:
    """Return the server's trust thresholds (COMMONS_TRUST_* environment variables)."""
    return TrustSettings.from_environment(os.environ)


def update_trust_levels(conn: Connection) -> None:
    """Recompute every reporter's level from their own record (runs with `commons rank`)."""
    current = settings()
    levels = {
        reporter: trust_level(history, current)
        for reporter, history in store.contributor_histories(conn).items()
    }
    store.set_trust_levels(conn, levels)


def after_propose(
    conn: Connection, entry_id: UUID, reporter_id: UUID, *, risky: bool, duplicate: bool
) -> bool:
    """Approve a trusted contributor's entry provisionally; return whether it was.

    Any risk finding or likely duplicate sends it to the queue regardless of trust.
    """
    one_in = sample_one_in(_level(conn, reporter_id), settings())
    if one_in is None or risky or duplicate:
        return False
    store.approve_provisionally(conn, entry_id, reporter_id, sampled=secrets.randbelow(one_in) == 0)
    return True


def demote(conn: Connection, reporter_id: UUID) -> None:
    """After a rejection for risky content: back to TL0, provisional entries back to review."""
    store.set_trust_levels(conn, {reporter_id: 0})
    store.reopen(
        conn, store.provisional_entries_of(conn, reporter_id), "proposer lost trust (0028)"
    )


def confirm_by_outcomes(conn: Connection) -> None:
    """Confirm provisional entries that worked for others; reopen ones that mostly failed."""
    for entry_id, successes, failures, reporters in store.provisional_evidence(conn):
        if successes >= CONFIRM_SUCCESSES and reporters >= CONFIRM_REPORTERS:
            store.confirm(conn, entry_id)
        elif failures >= CONFIRM_SUCCESSES and failures > successes:
            store.reopen(conn, [entry_id], "mostly failed when used (0028)")


_LEVELS: dict[int, TrustLevel] = {0: 0, 1: 1, 2: 2}


def _level(conn: Connection, reporter_id: UUID) -> TrustLevel:
    return _LEVELS.get(store.trust_of(conn, reporter_id), 0)


def set_role(conn: Connection, pseudonym: str, role: str) -> Role:
    """Make a reporter a contributor, reviewer or admin (decision 0028)."""
    checked = validate_role(role)
    reporter = find_reporter(conn, pseudonym)
    if reporter is None:
        msg = f"No reporter {pseudonym!r}."
        raise NotFoundError(msg)
    with conn.transaction():
        store.set_role(conn, reporter, checked)
        allowed = [scope for scope in KEY_SCOPES if may_hold(checked, scope)]
        revoke_scopes_except(conn, reporter, allowed)  # a lowered role takes its keys (0032)
    return checked


def check_scope(conn: Connection, pseudonym: str, scope: str) -> None:
    """Refuse a review or admin key for a reporter whose role doesn't allow it."""
    reporter = find_reporter(conn, pseudonym)
    role = store.role_of(conn, reporter) if reporter else "contributor"
    if not may_hold(role, scope):
        needed = "admin" if scope == "admin" else "reviewer"
        msg = (
            f"{pseudonym} is a {role}; a {scope} key needs the {needed} role: "
            f"`commons admin role {pseudonym} {needed}`."
        )
        raise InvalidInputError(msg)


def queue_health(conn: Connection) -> tuple[int, float | None]:
    """Return how many entries wait for a human, and the oldest one's age in days."""
    return store.queue_age_days(conn)

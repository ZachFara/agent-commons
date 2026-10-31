"""Enforce per-key rate limits on agent tools in hosted mode (decision 0029)."""

import math
import os
from datetime import UTC, datetime

from commons.domain.errors import RateLimitedError
from commons.domain.rates import LimitedAction, limits_from_environment
from commons.service.identity import current_key
from commons.store.connection import Connection
from commons.store.rates import count_call


def limit(conn: Connection, action: LimitedAction) -> None:
    """Count this call against the request's key; refuse it if the key is over its limit.

    Local (stdio) calls carry no key and are never limited.
    """
    key_id = current_key()
    if key_id is None:
        return
    rule = limits_from_environment(os.environ)[action]
    count, resets = count_call(conn, key_id, action, rule.window)
    if count > rule.calls:
        wait = max(1, math.ceil((resets - datetime.now(UTC)).total_seconds()))
        msg = f"Too many {action} calls: at most {rule.calls} per {rule.window}. Retry in {wait}s."
        raise RateLimitedError(msg, wait)

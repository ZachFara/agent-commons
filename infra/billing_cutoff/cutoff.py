"""Decide from a budget notification whether to cut billing off (decision 0038).

Kept free of Google libraries so the tests can import it.
"""

import base64
import json


def spend(message_data: str) -> tuple[float, float]:
    """Return (cost so far this month, budget) from a Pub/Sub budget notification's data."""
    notification = json.loads(base64.b64decode(message_data))
    return float(notification["costAmount"]), float(notification["budgetAmount"])


def over_budget(message_data: str) -> bool:
    """Whether the notification reports spend at or over the budget.

    Budgets publish several times a day whatever the spend, so this is checked every time.
    """
    cost, budget = spend(message_data)
    return cost >= budget

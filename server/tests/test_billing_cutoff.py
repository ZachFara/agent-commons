"""The billing cutoff function's decision (decision 0038, infra/billing_cutoff/)."""

import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType

CUTOFF = Path(__file__).parents[2] / "infra" / "billing_cutoff" / "cutoff.py"


def load_cutoff() -> ModuleType:
    spec = importlib.util.spec_from_file_location("billing_cutoff", CUTOFF)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def notification(cost: float, budget: float) -> str:
    """Pub/Sub message data as Cloud Billing sends it (schema 1.0)."""
    payload = {
        "budgetDisplayName": "Agent Commons billing cutoff",
        "costAmount": cost,
        "costIntervalStart": "2026-11-01T07:00:00Z",
        "budgetAmount": budget,
        "budgetAmountType": "SPECIFIED_AMOUNT",
        "currencyCode": "USD",
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_under_the_budget_leaves_billing_on() -> None:
    assert not load_cutoff().over_budget(notification(99.99, 100))


def test_reaching_the_budget_cuts_off() -> None:
    cutoff = load_cutoff()
    assert cutoff.over_budget(notification(100, 100))
    assert cutoff.over_budget(notification(250.5, 100))


def test_spend_reads_cost_and_budget() -> None:
    assert load_cutoff().spend(notification(0.1, 100)) == (0.1, 100.0)

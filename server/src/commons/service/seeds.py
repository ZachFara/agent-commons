"""Seed entries shipped with Agent Commons, so a fresh install has something to find.

Real entries come from use; these are a few well-known public problems (owner, 2026-10-24).
"""

import json
from pathlib import Path
from uuid import UUID

from commons.domain.entries import Proposal, validate_proposal
from commons.store.connection import Connection
from commons.store.models import model_id
from commons.store.reporters import upsert_reporter
from commons.store.seeds import insert_seed_entry

SEEDS_FILE = Path(__file__).with_name("seeds.json")
SEED_REPORTER = "agent-commons-seed"
SEED_MODEL = "seed"


def load_seeds(conn: Connection) -> int:
    """Insert any seed entries not already present; return how many were added."""
    seeds = json.loads(SEEDS_FILE.read_text(encoding="utf-8"))
    added = 0
    with conn.transaction():
        reporter = upsert_reporter(conn, SEED_REPORTER, "entry")
        model = model_id(conn, SEED_MODEL)
        for seed in seeds:
            proposal = Proposal(
                symptom=seed["symptom"],
                environment=seed["environment"],
                cause=seed["cause"],
                fix=seed["fix"],
                domains=tuple(seed["domains"]),
                repro=seed["repro"],
                task_id=UUID(int=0),
            )
            validate_proposal(proposal)
            added += insert_seed_entry(conn, UUID(seed["id"]), proposal, reporter, model)
    return added

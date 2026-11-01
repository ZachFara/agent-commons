import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

import factories
from commons.domain.outcomes import OutcomeKind
from commons.ranking.reliability import reliability
from commons.service.ranking import rank

Pairs = list[tuple[OutcomeKind, OutcomeKind]]


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([], None),
        ([("tests_passed", "tests_passed")] * 4, None),  # below 5 pairs
        ([("tests_passed", "tests_passed")] * 5, 1.0),
        ([("error_resolved", "tests_passed")] * 5, 1.0),  # same verdict, different label
        ([("tests_passed", "tests_failed")] * 5, 0.0),
        ([("tests_passed", "tests_passed")] * 4 + [("user_accepted", "tests_failed")], 0.8),
        ([("unknown", "tests_failed")] * 3 + [("tests_failed", "tests_failed")] * 4, None),
        # nothing observed is not a disagreement (audit 2026-10-31)
        ([("tests_passed", "unknown")] * 3 + [("tests_passed", "tests_passed")] * 5, 1.0),
    ],
)
def test_reliability(pairs: Pairs, expected: float | None) -> None:
    assert reliability(pairs) == expected


def claim(
    db: psycopg.Connection,
    reporter: uuid.UUID,
    claimed: str,
    observed: str,
    *,
    source: str = "hook",
) -> uuid.UUID:
    """An entry the reporter's agent claimed `claimed` about; return the entry id."""
    entry_id = factories.entry(db, reporter)
    factories.outcome(db, entry_id, reporter, observed)
    db.execute(
        "UPDATE outcomes SET evidence = %s WHERE retrieval_id IN "
        "(SELECT id FROM retrievals WHERE entry_id = %s)",
        (Jsonb({"outcome_source": source, "agent_outcome": claimed}), entry_id),
    )
    return entry_id


def reporter_reliability(db: psycopg.Connection, reporter: uuid.UUID) -> float | None:
    row = db.execute("SELECT reliability FROM reporters WHERE id = %s", (reporter,)).fetchone()
    assert row is not None
    value: float | None = row[0]
    return value


def test_rank_computes_reliability_and_weights_self_reports_with_it(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    for _ in range(4):
        claim(db, reporter, "tests_passed", "tests_passed")
    claim(db, reporter, "tests_passed", "tests_failed")
    self_reported = claim(db, reporter, "tests_passed", "tests_passed", source="agent")

    rank(db)

    assert reporter_reliability(db, reporter) == pytest.approx(0.8)
    score = db.execute(
        "SELECT score FROM ranking_snapshots WHERE entry_id = %s", (self_reported,)
    ).fetchone()
    assert score == (pytest.approx((1 + 0.8) / (2 + 0.8)),)


def test_too_few_pairs_leave_reliability_unknown(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    db.execute("UPDATE reporters SET reliability = 0.3 WHERE id = %s", (reporter,))
    for _ in range(4):
        claim(db, reporter, "tests_passed", "tests_passed")
    rank(db)
    assert reporter_reliability(db, reporter) is None

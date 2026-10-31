import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

ENUMS = {
    "entry_status": ["pending", "approved", "rejected", "retired"],
    "entry_privacy_mode": ["entry", "full_trace"],
    "privacy_default": ["local", "entry", "full_trace"],
    "usage": ["yes", "partial", "no"],
    "outcome_kind": [
        "tests_passed",
        "tests_failed",
        "error_resolved",
        "error_persisted",
        "user_accepted",
        "user_rejected",
        "abandoned",
        "unknown",
    ],
    "review_decision": ["approve", "reject", "retire", "restore", "reopen"],
    "review_source": ["agent", "human", "auto"],
    "reporter_role": ["contributor", "reviewer", "admin"],
}

COMMON = {"id", "created_at", "updated_at"}

TABLES = {
    "entries": {
        "symptom",
        "environment",
        "cause",
        "fix",
        "repro",
        "domains",
        "status",
        "human_reviewed",
        "created_by",
        "created_by_model",
        "privacy_mode",
        "trace_id",
        "task_id",
        "redaction_report",
        "search_vector",
        "revision",
        "superseded_by",
        "fixed_in",
        "aliases",
        "duplicate_candidates",
        "duplicate_of",
        "risk_report",
        "provisional",
        "sampled",
        "confirmed_at",
        "imported_from",
    },
    "purges": {
        "reporter_id",
        "purged_by",
        "reason",
        "keys",
        "retired_entries",
        "correction_batch",
        "restored_at",
        "restored_by",
    },
    "distinct_pairs": {"entry_a", "entry_b", "decided_by"},
    "entry_revisions": {
        "entry_id",
        "revision",
        "symptom",
        "environment",
        "cause",
        "fix",
        "repro",
        "domains",
        "source",
        "edited_by",
        "reason",
        "redaction_report",
    },
    "reviews": {"entry_id", "reviewed_by", "decision", "reason", "source"},
    "retrievals": {
        "entry_id",
        "reporter_id",
        "model_id",
        "task_id",
        "query",
        "rank_position",
        "score_at_retrieval",
        "excluded",
        "revision",
    },
    "searches": {
        "task_id",
        "reporter_id",
        "model_id",
        "privacy_mode",
        "query",
        "language",
        "result_limit",
        "hit_count",
        "environment",
        "results",
        "excluded",
    },
    "corrections": {
        "batch_id",
        "corrected_by",
        "target_table",
        "target_id",
        "field",
        "old_value",
        "new_value",
        "reason",
        "undone_at",
        "undone_by",
    },
    "search_months": {"month", "query", "searches", "misses"},
    "outcomes": {
        "retrieval_id",
        "used",
        "outcome",
        "evidence",
        "agent_assessment",
        "tokens_in",
        "tokens_out",
        "wall_clock_seconds",
        "excluded",
    },
    "reporters": {
        "pseudonym",
        "reliability",
        "privacy_default",
        "suspended_at",
        "role",
        "trust_level",
    },
    "api_keys": {
        "reporter_id",
        "prefix",
        "key_hash",
        "scope",
        "label",
        "last_used_at",
        "expires_at",
        "revoked_at",
        "test",
    },
    "models": {"name", "version", "provider"},
    "counterfactual_runs": {
        "task_id",
        "entry_id",
        "with_entry",
        "outcome",
        "tokens_in",
        "tokens_out",
        "wall_clock_seconds",
    },
    "traces": {"entry_id", "task_id", "content", "redaction_report"},
    "tasks": {"session_id", "reporter_id", "model_id", "privacy_mode"},
    "ranking_snapshots": {
        "entry_id",
        "computed_at",
        "score",
        "retrieval_count_30d",
        "used_rate",
        "success_rate",
    },
}


def insert_entry(db: psycopg.Connection, **overrides: object) -> uuid.UUID:
    reporter = db.execute(
        "INSERT INTO reporters (pseudonym) VALUES (%s) RETURNING id", (uuid.uuid4().hex,)
    ).fetchone()
    model = db.execute(
        "INSERT INTO models (name) VALUES (%s) RETURNING id", (uuid.uuid4().hex,)
    ).fetchone()
    assert reporter
    assert model
    values: dict[str, object] = {
        "symptom": "ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
        "environment": Jsonb({"language": "python", "libraries": {"markupsafe": "2.1.0"}}),
        "cause": "markupsafe 2.1 removed soft_unicode, which old Jinja2 still imports.",
        "fix": "pip install 'markupsafe<2.1' or upgrade Jinja2 to 3.x",
        "domains": ["python", "packaging"],
        "created_by": reporter[0],
        "created_by_model": model[0],
    }
    values.update(overrides)
    query = sql.SQL("INSERT INTO entries ({}) VALUES ({}) RETURNING id").format(
        sql.SQL(", ").join(map(sql.Identifier, values)),
        sql.SQL(", ").join(sql.Placeholder() * len(values)),
    )
    row = db.execute(query, list(values.values())).fetchone()
    assert row
    entry_id: uuid.UUID = row[0]
    return entry_id


def test_every_table_has_exactly_the_designed_columns(db: psycopg.Connection) -> None:
    for table, columns in TABLES.items():
        rows = db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,),
        ).fetchall()
        assert {name for (name,) in rows} == columns | COMMON, table


def test_enum_labels_match_design(db: psycopg.Connection) -> None:
    for enum, labels in ENUMS.items():
        query = sql.SQL("SELECT unnest(enum_range(NULL::{}))::text").format(sql.Identifier(enum))
        rows = db.execute(query).fetchall()
        assert [label for (label,) in rows] == labels, enum


def test_new_entry_defaults(db: psycopg.Connection) -> None:
    entry_id = insert_entry(db)
    row = db.execute(
        "SELECT status, human_reviewed, privacy_mode FROM entries WHERE id = %s", (entry_id,)
    ).fetchone()
    assert row == ("pending", False, "entry")


def test_search_vector_matches_symptom_cause_fix_and_domains(db: psycopg.Connection) -> None:
    entry_id = insert_entry(db)
    for query in ("soft_unicode", "removed", "upgrade jinja2", "packaging"):
        row = db.execute(
            "SELECT id FROM entries WHERE id = %s "
            "AND search_vector @@ websearch_to_tsquery('english', %s)",
            (entry_id, query),
        ).fetchone()
        assert row == (entry_id,), query


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("domains", []),
        ("environment", Jsonb(["python"])),
    ],
)
def test_entry_constraints_reject_bad_values(
    db: psycopg.Connection, column: str, value: object
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        insert_entry(db, **{column: value})


def test_updated_at_is_maintained_on_update(db: psycopg.Connection) -> None:
    entry_id = insert_entry(db)
    db.execute(
        "UPDATE entries SET updated_at = '2000-01-01', created_at = '2000-01-01' WHERE id = %s",
        (entry_id,),
    )
    row = db.execute(
        "SELECT updated_at = now(), created_at < now() FROM entries WHERE id = %s", (entry_id,)
    ).fetchone()
    assert row == (True, True)

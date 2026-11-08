"""Context7 doc lookups: what is parsed from query-docs and what each privacy mode keeps."""

import uuid
from typing import Any

import psycopg
import pytest

from commons.domain.observations import DocLookup, doc_lookup, split_library_id
from commons.domain.reporters import PrivacyDefault
from commons.privacy.doc_lookup_log import doc_lookup_record
from commons.service import hooks
from commons.service.identity import acting_as
from commons.store.models import model_id
from commons.store.tasks import start_task

SECRET = "sk-ant-api03-" + "q" * 40
TOOL = "mcp__context7__query-docs"


@pytest.mark.parametrize(
    ("library_id", "split"),
    [
        ("/vercel/next.js/v16.2.9", ("/vercel/next.js", "v16.2.9")),
        ("/vercel/next.js", ("/vercel/next.js", "")),
        ("/mongodb/docs", ("/mongodb/docs", "")),
        ("/websites/react_dev", ("/websites/react_dev", "")),
        ("vercel/next.js", None),
        ("/vercel", None),
        ("/vercel/next.js/v1/extra", None),
        ("/vercel/next.js/", None),
        ("/vercel/next js", None),
        ("/vercel/next.js\n", None),
        ("/vërcel/next.js", None),
        ("/a/" + "b" * 300, None),
    ],
)
def test_library_ids_split_into_library_and_version(
    library_id: str, split: tuple[str, str] | None
) -> None:
    assert split_library_id(library_id) == split


@pytest.mark.parametrize(
    ("tool", "tool_input", "expected"),
    [
        (TOOL, {"libraryId": "/a/b/1.0", "query": "q"}, DocLookup("/a/b", "1.0", "q")),
        (TOOL, {"context7CompatibleLibraryID": "/a/b", "query": "q"}, DocLookup("/a/b", "", "q")),
        (TOOL, {"libraryId": "/a/b"}, DocLookup("/a/b", "", None)),
        ("mcp__claude_ai_Context7__resolve-library-id", {"libraryId": "/a/b"}, None),
        ("query-docs", {"libraryId": "/a/b"}, None),
        ("mcp__context7__query-docs-v2", {"libraryId": "/a/b"}, None),
        (TOOL, {"libraryId": ["/a/b"]}, None),
        (TOOL, "/a/b", None),
    ],
)
def test_only_query_docs_with_a_good_id_is_a_lookup(
    tool: str, tool_input: object, expected: DocLookup | None
) -> None:
    assert doc_lookup(tool, tool_input) == expected


@pytest.mark.parametrize(
    ("mode", "query"),
    [("full_trace", "auth [REDACTED:anthropic_key]"), ("entry", None), ("local", None)],
)
def test_only_full_trace_keeps_the_redacted_query(mode: PrivacyDefault, query: str | None) -> None:
    task_id = uuid.uuid4()
    record = doc_lookup_record(DocLookup("/a/b", "1.0", f"auth {SECRET}"), task_id, mode)
    assert (record.task_id, record.library_id, record.version) == (task_id, "/a/b", "1.0")
    assert (record.privacy_mode, record.query) == (mode, query)


def stored(db: psycopg.Connection, task_id: uuid.UUID) -> list[tuple[Any, ...]]:
    return db.execute(
        "SELECT d.library_id, d.version, d.query, d.privacy_mode::text, d.excluded, m.name "
        "FROM doc_lookups d LEFT JOIN models m ON m.id = d.model_id WHERE d.task_id = %s",
        (task_id,),
    ).fetchall()


def test_a_lookup_takes_the_tasks_mode_and_model(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    session = f"session-{uuid.uuid4()}"
    task_id = start_task(db, session, reporter, model_id(db, "claude-opus-5-5"))
    assert task_id
    db.execute("UPDATE reporters SET privacy_default = 'full_trace' WHERE id = %s", (reporter,))
    lookup = DocLookup("/vercel/next.js", "v16.2.9", "app router")

    hooks.record_tool_use(db, session, None, None, lookup)

    assert stored(db, task_id) == [
        ("/vercel/next.js", "v16.2.9", None, "entry", False, "claude-opus-5-5")
    ]


def test_a_test_keys_lookups_are_excluded(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    session = f"session-{uuid.uuid4()}"
    task_id = start_task(db, session, reporter, None)
    assert task_id

    with acting_as(reporter, test=True):
        hooks.record_tool_use(db, session, None, None, DocLookup("/a/b", "", None))

    assert stored(db, task_id) == [("/a/b", "", None, "entry", True, None)]


def test_the_database_refuses_a_query_outside_full_trace(
    db: psycopg.Connection, reporter: uuid.UUID
) -> None:
    task_id = start_task(db, f"session-{uuid.uuid4()}", reporter, None)
    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(
            "INSERT INTO doc_lookups (task_id, reporter_id, privacy_mode, library_id, version, "
            "query) VALUES (%s, %s, 'entry', '/a/b', '', 'leaked')",
            (task_id, reporter),
        )

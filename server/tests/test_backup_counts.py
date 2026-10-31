"""The backup check (decision 0033) catches missing, extra and miscounted tables."""

from tools.backup_counts import count_rows, differences

BACKUP = {"entries": 3, "searches": 1000}


def test_an_exact_restore_has_no_differences() -> None:
    assert differences(BACKUP, dict(BACKUP), tolerance=0) == []


def test_missing_extra_and_miscounted_tables_are_named() -> None:
    restored = {"searches": 999, "stray": 1}
    assert differences(BACKUP, restored, tolerance=0) == [
        "entries: missing",
        "searches: expected 1000, found 999",
        "stray: unexpected table",
    ]


def test_tolerance_allows_writes_after_the_dump_but_not_large_gaps() -> None:
    assert differences(BACKUP, {"entries": 20, "searches": 1040}, tolerance=0.05) == []
    assert differences(BACKUP, {"entries": 3, "searches": 1100}, tolerance=0.05) == [
        "searches: expected 1000, found 1100"
    ]


def test_a_backup_without_entries_fails() -> None:
    assert differences({"entries": 0}, {"entries": 0}, tolerance=0) == [
        "entries: the backup has no entries"
    ]


def test_counts_cover_every_table(database_url: str) -> None:
    counts = count_rows(database_url)
    assert {"entries", "searches", "search_months", "schema_migrations"} <= counts.keys()

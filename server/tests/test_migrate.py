from pathlib import Path

import psycopg
import pytest

from commons.store.migrate import apply_migrations


def write(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body, encoding="utf-8")


def test_applies_in_order_and_records(db: psycopg.Connection, tmp_path: Path) -> None:
    write(tmp_path, "0002_add_col.sql", "ALTER TABLE probe ADD COLUMN b int;")
    write(tmp_path, "0001_create.sql", "CREATE TABLE probe (a int); CREATE INDEX ON probe (a);")
    write(tmp_path, "notes.sql", "this is not a migration")

    assert apply_migrations(db, tmp_path) == ["0001_create.sql", "0002_add_col.sql"]
    assert db.execute("SELECT a, b FROM probe").fetchall() == []
    names = ["0001_create.sql", "0002_add_col.sql"]
    recorded = db.execute(
        "SELECT name FROM schema_migrations WHERE name = ANY(%s) ORDER BY name", (names,)
    ).fetchall()
    assert recorded == [(name,) for name in names]


def test_rerun_is_a_noop(db: psycopg.Connection, tmp_path: Path) -> None:
    write(tmp_path, "0001_create.sql", "CREATE TABLE probe (a int);")
    apply_migrations(db, tmp_path)
    assert apply_migrations(db, tmp_path) == []


def test_only_new_migrations_apply(db: psycopg.Connection, tmp_path: Path) -> None:
    write(tmp_path, "0001_create.sql", "CREATE TABLE probe (a int);")
    apply_migrations(db, tmp_path)
    write(tmp_path, "0002_add_col.sql", "ALTER TABLE probe ADD COLUMN b int;")
    assert apply_migrations(db, tmp_path) == ["0002_add_col.sql"]


def test_failure_applies_nothing(db: psycopg.Connection, tmp_path: Path) -> None:
    write(tmp_path, "0001_create.sql", "CREATE TABLE probe (a int);")
    write(tmp_path, "0002_broken.sql", "ALTER TABLE missing ADD COLUMN b int;")
    with pytest.raises(psycopg.errors.UndefinedTable):
        apply_migrations(db, tmp_path)
    exists = db.execute("SELECT to_regclass('probe')").fetchone()
    assert exists == (None,)

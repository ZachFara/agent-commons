"""The backup role (decision 0033) can read every table, including later ones, and never write."""

import uuid

import psycopg
import pytest
from psycopg import sql
from tools.backup_role import create_read_only_role


def test_the_backup_role_reads_everything_and_writes_nothing(database_url: str) -> None:
    role = f"backup_test_{uuid.uuid4().hex[:8]}"
    url = create_read_only_role(database_url, role, "pw-" + uuid.uuid4().hex)
    try:
        with psycopg.connect(database_url, autocommit=True) as owner:
            owner.execute("CREATE TABLE later_table (id int)")
        with psycopg.connect(url) as conn:
            assert conn.execute("SELECT count(*) FROM entries").fetchone() is not None
            assert conn.execute("SELECT count(*) FROM later_table").fetchone() == (0,)
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("DELETE FROM entries")
            conn.rollback()
            conn.autocommit = True
            conn.execute("SET default_transaction_read_only = off")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM entries")
        assert create_read_only_role(database_url, role, "new-pw-" + uuid.uuid4().hex) != url
    finally:
        with psycopg.connect(database_url, autocommit=True) as owner:
            owner.execute("DROP TABLE IF EXISTS later_table")
            owner.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(role)))
            owner.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

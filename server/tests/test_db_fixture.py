import psycopg


def test_database_answers(db: psycopg.Connection) -> None:
    assert db.execute("SELECT 1").fetchone() == (1,)


def test_writes_are_rolled_back_between_tests(db: psycopg.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS rollback_probe (n int)")
    assert db.execute("SELECT count(*) FROM rollback_probe").fetchone() == (0,)
    db.execute("INSERT INTO rollback_probe VALUES (1)")


def test_writes_are_rolled_back_between_tests_again(db: psycopg.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS rollback_probe (n int)")
    assert db.execute("SELECT count(*) FROM rollback_probe").fetchone() == (0,)
    db.execute("INSERT INTO rollback_probe VALUES (1)")

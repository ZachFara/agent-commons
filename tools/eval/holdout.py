"""Hold-out scenarios built on real breaking changes released after the models' training cutoff.

The owner's point (decision 0023, amendment): invented bugs feel surprising to the scenario
author but are familiar patterns to an eval model trained on the same data. These use the real
packages at the real versions, so neither side can recite the fix. Every behavior here was
confirmed by running it (2026-10-25); the source is cited on each scenario.

Conditions are developed on `scenarios.py`; these are only measured.
"""

from tools.eval.model import PROJECT_TEST_COMMAND, Discovery, Expect, Scenario

PY_CONFTEST = """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
"""


PYTHON_VERSION_FILE = "3.12\n"  # .python-version: uv would otherwise pick the newest interpreter


def pyproject(name: str, deps: list[str], python: str = ">=3.12") -> str:
    """Build a minimal project with exact pins, so `uv run pytest` resolves the real versions."""
    dependency_list = ", ".join(f'"{dep}"' for dep in deps)
    return (
        "[project]\n"
        f'name = "{name}"\n'
        'version = "0.0.0"\n'
        f'requires-python = "{python}"\n'
        f"dependencies = [{dependency_list}]\n\n"
        "[dependency-groups]\n"
        'dev = ["pytest==9.1.1"]\n'
    )


# SQLModel 0.0.45 (2026-09-21): datetime fields became UTCDateTime; naive values raise.
# Source: https://sqlmodel.tiangolo.com/release-notes/ (0.0.45, "Breaking Changes").
# The obvious fix (make the explicit write aware) passes one test; the default_factory still
# produces naive values, so the second test keeps failing. Multi-attempt by construction.
SQLMODEL_NAIVE = Scenario(
    name="real-sqlmodel-utc",
    trigger="version_specific",
    test_command=PROJECT_TEST_COMMAND,
    files={
        ".python-version": PYTHON_VERSION_FILE,
        "pyproject.toml": pyproject("auditlog", ["sqlmodel==0.0.45"]),
        "conftest.py": PY_CONFTEST,
        "app/__init__.py": "",
        "app/audit.py": (
            "from datetime import datetime\n\n"
            "from sqlmodel import Field, Session, SQLModel, create_engine, select\n\n\n"
            "class AuditEntry(SQLModel, table=True):\n"
            "    id: int | None = Field(default=None, primary_key=True)\n"
            "    action: str\n"
            "    at: datetime = Field(default_factory=datetime.now)\n\n\n"
            "def make_engine():\n"
            '    engine = create_engine("sqlite://")\n'
            "    SQLModel.metadata.create_all(engine)\n"
            "    return engine\n\n\n"
            "def record(engine, action, at=None):\n"
            '    """Record an action; `at` defaults to now."""\n'
            "    with Session(engine) as session:\n"
            "        fields = {'action': action} if at is None else {'action': action, 'at': at}\n"
            "        entry = AuditEntry(**fields)\n"
            "        session.add(entry)\n"
            "        session.commit()\n"
            "        session.refresh(entry)\n"
            "        return entry.id\n\n\n"
            "def actions_since(engine, since):\n"
            "    with Session(engine) as session:\n"
            "        rows = session.exec(select(AuditEntry).where(AuditEntry.at >= since)).all()\n"
            "        return [row.action for row in rows]\n"
        ),
        "tests/test_audit.py": (
            "from datetime import datetime, timedelta, timezone\n\n"
            "from app.audit import actions_since, make_engine, record\n\n\n"
            "def test_explicit_timestamp_round_trips():\n"
            "    engine = make_engine()\n"
            "    record(engine, 'login', at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))\n"
            "    since = datetime(2025, 12, 31, tzinfo=timezone.utc)\n"
            "    assert actions_since(engine, since) == ['login']\n\n\n"
            "def test_default_timestamp_is_now():\n"
            "    engine = make_engine()\n"
            "    record(engine, 'logout')\n"
            "    recent = datetime.now(timezone.utc) - timedelta(minutes=1)\n"
            "    assert actions_since(engine, recent) == ['logout']\n"
        ),
    },
    discovery=Discovery(
        symptom_words=("timezone information", "naive", "StatementError", "UTCDateTime", "aware"),
        cause_words=("0.0.45", "UTCDateTime", "timezone=True", "aware", "naive"),
        library="sqlmodel",
    ),
    expect=Expect(search=True, propose=True, report=False),
)

# whenever 0.11.0 (2026-10-02): `ignore_dst` removed (deprecated in 0.10); add() now raises
# TypeError for it. Source: https://whenever.readthedocs.io/en/latest/changelog.html (0.11.0,
# "Removed APIs"; 0.10.0 gives the replacements: naive_arithmetic_ok for PlainDateTime).
# The TypeError looks like a typo in the caller, not a removed feature.
WHENEVER_IGNORE_DST = Scenario(
    name="real-whenever-ignore-dst",
    trigger="version_specific",
    test_command=PROJECT_TEST_COMMAND,
    files={
        ".python-version": PYTHON_VERSION_FILE,
        "pyproject.toml": pyproject("scheduler", ["whenever==0.11.0"]),
        "conftest.py": PY_CONFTEST,
        "app/__init__.py": "",
        "app/shifts.py": (
            "from whenever import PlainDateTime\n\n\n"
            "def shift_end(start, hours):\n"
            '    """End of a shift starting at `start` (local wall-clock time) lasting `hours`.\n\n'
            "    Shifts are scheduled on the wall clock, so DST transitions are ignored.\n"
            '    """\n'
            "    return start.add(hours=hours, ignore_dst=True)\n"
        ),
        "tests/test_shifts.py": (
            "from whenever import PlainDateTime\n\n"
            "from app.shifts import shift_end\n\n\n"
            "def test_night_shift_end():\n"
            "    start = PlainDateTime(2026, 3, 7, 22, 0)\n"
            "    assert shift_end(start, 8) == PlainDateTime(2026, 3, 8, 6, 0)\n"
        ),
    },
    discovery=Discovery(
        symptom_words=("ignore_dst", "unexpected keyword", "TypeError", "add()"),
        cause_words=("0.11", "removed", "naive_arithmetic_ok", "deprecated", "renamed"),
        library="whenever",
    ),
    expect=Expect(search=True, propose=True, report=False),
)

# Python 3.14: a sequence of parameters with named (:name) placeholders now raises
# ProgrammingError (DeprecationWarning since 3.12). Source: https://docs.python.org/3/whatsnew/3.14.html,
# "Removed", sqlite3. Worked on every earlier Python; the message blames the caller's parameters.
PY314_SQLITE_PARAMS = Scenario(
    name="real-py314-sqlite-params",
    trigger="version_specific",
    test_command="uv run --python 3.14 --with pytest pytest -q",
    files={
        "conftest.py": PY_CONFTEST,
        "app/__init__.py": "",
        "app/store.py": (
            "import sqlite3\n\n"
            'SCHEMA = "CREATE TABLE notes (id INTEGER PRIMARY KEY, title TEXT, body TEXT)"\n'
            'INSERT = "INSERT INTO notes (title, body) VALUES (:title, :body)"\n'
            'FIND = "SELECT title, body FROM notes WHERE title = :title"\n\n\n'
            "class NoteStore:\n"
            "    def __init__(self):\n"
            '        self._db = sqlite3.connect(":memory:")\n'
            "        self._db.execute(SCHEMA)\n\n"
            "    def add(self, title, body):\n"
            "        self._db.execute(INSERT, (title, body))\n"
            "        self._db.commit()\n\n"
            "    def find(self, title):\n"
            "        return self._db.execute(FIND, (title,)).fetchone()\n"
        ),
        "tests/test_store.py": (
            "from app.store import NoteStore\n\n\n"
            "def test_round_trip():\n"
            "    store = NoteStore()\n"
            '    store.add("todo", "buy milk")\n'
            '    assert store.find("todo") == ("todo", "buy milk")\n'
        ),
    },
    discovery=Discovery(
        symptom_words=("named parameter", "ProgrammingError", "placeholders", "qmark", "sequence"),
        cause_words=("3.14", "named placeholders", "dict", "removed", "deprecated"),
        library="python",
    ),
    expect=Expect(search=True, propose=True, report=False),
)

# Python 3.14: copy/deepcopy/pickle support removed from itertools iterators (DeprecationWarning
# since 3.12). Source: https://docs.python.org/3/whatsnew/3.14.html, "Removed", itertools.
# The error says "cannot pickle", which points away from the real cause.
PY314_ITERTOOLS_COPY = Scenario(
    name="real-py314-itertools-copy",
    trigger="version_specific",
    test_command="uv run --python 3.14 --with pytest pytest -q",
    files={
        "conftest.py": PY_CONFTEST,
        "app/__init__.py": "",
        "app/ids.py": (
            "import copy\n"
            "import itertools\n\n\n"
            "class IdGenerator:\n"
            '    """Sequential ids; `fork()` gives an independent generator from here."""\n\n'
            "    def __init__(self, start=1):\n"
            "        self._counter = itertools.count(start)\n\n"
            "    def next_id(self):\n"
            "        return next(self._counter)\n\n"
            "    def fork(self):\n"
            "        forked = IdGenerator()\n"
            "        forked._counter = copy.copy(self._counter)\n"
            "        return forked\n"
        ),
        "tests/test_ids.py": (
            "from app.ids import IdGenerator\n\n\n"
            "def test_fork_continues_independently():\n"
            "    gen = IdGenerator()\n"
            "    assert [gen.next_id(), gen.next_id()] == [1, 2]\n"
            "    forked = gen.fork()\n"
            "    assert forked.next_id() == 3\n"
            "    assert gen.next_id() == 3\n"
        ),
    },
    discovery=Discovery(
        symptom_words=("cannot pickle", "itertools.count", "copy", "TypeError"),
        cause_words=("3.14", "removed", "copy", "pickle", "deprecated"),
        library="python",
    ),
    expect=Expect(search=True, propose=True, report=False),
)

HOLDOUT = {
    s.name: s
    for s in (SQLMODEL_NAIVE, WHENEVER_IGNORE_DST, PY314_SQLITE_PARAMS, PY314_ITERTOOLS_COPY)
}

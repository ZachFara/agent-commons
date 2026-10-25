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


# ----------------------------------------------------------------------------------- hard set
# Rule (decision 0023, second amendment): the obvious fix must fail. Each scenario has a test
# the first fix passes and a second test it does not, so the agent meets the surprise.

# pandas 3.0.0 (2026-01-21): Copy-on-Write is always on; a filtered subset "always behaves as a
# copy of the original, and thus never modifies the original". No error, no SettingWithCopy
# warning (removed): the parent is silently unchanged. The reflexive `.copy()` fix changes
# nothing. Source: https://pandas.pydata.org/docs/whatsnew/v3.0.0.html
PANDAS_COW = Scenario(
    name="hard-pandas-cow-subset",
    trigger="version_specific",
    test_command=PROJECT_TEST_COMMAND,
    files={
        ".python-version": PYTHON_VERSION_FILE,
        "pyproject.toml": pyproject("invoicing", ["pandas==3.0.0"]),
        "conftest.py": PY_CONFTEST,
        "app/__init__.py": "",
        "app/overdue.py": (
            "import pandas as pd\n\n"
            "GRACE_DAYS = 30\n\n\n"
            "def flag_overdue(invoices):\n"
            '    """Flag invoices past the grace period as overdue in place; return the frame."""\n'
            '    late = invoices[invoices["days_open"] > GRACE_DAYS]\n'
            '    late["status"] = "overdue"\n'
            "    return invoices\n\n\n"
            "def overdue_total(invoices):\n"
            '    """Sum of amounts across overdue invoices."""\n'
            "    flagged = flag_overdue(invoices)\n"
            '    return float(flagged.loc[flagged["status"] == "overdue", "amount"].sum())\n'
        ),
        "tests/test_overdue.py": (
            "import pandas as pd\n\n"
            "from app.overdue import flag_overdue, overdue_total\n\n\n"
            "def invoices():\n"
            "    return pd.DataFrame(\n"
            '        {"invoice": [1, 2, 3], "days_open": [5, 45, 60], "amount": [10.0, 20.0, 30.0],'
            "\n"
            '         "status": ["open", "open", "open"]}\n'
            "    )\n\n\n"
            "def test_flags_in_place():\n"
            "    frame = invoices()\n"
            "    flag_overdue(frame)\n"
            '    assert frame["status"].tolist() == ["open", "overdue", "overdue"]\n\n\n'
            "def test_overdue_total():\n"
            "    assert overdue_total(invoices()) == 50.0\n"
        ),
    },
    discovery=Discovery(
        symptom_words=("overdue", "status", "unchanged", "not modified", "copy", "filtered"),
        cause_words=("copy-on-write", "copy_on_write", "3.0", "copy", "loc", "view"),
        library="pandas",
    ),
    expect=Expect(search=True, propose=True, report=False),
)

# SQLAlchemy 2.1.0: "Session autoflush behavior has been simplified to unconditionally flush
# the session each time an execution takes place, regardless of whether an ORM statement or
# Core statement is being executed" (#9809). A get-or-create that looks the name up via Core
# before adding never saw pending rows on 2.0, so two same-named adds in one batch both
# created (verified: 2.0.46 gives created,created; 2.1.0 gives created,found). The app's
# batch-import test encodes the 2.0 result; the fix has to reconcile the app with the new
# behavior rather than restore the old one. Source:
# https://docs.sqlalchemy.org/en/21/changelog/migration_21.html
SQLALCHEMY_AUTOFLUSH = Scenario(
    name="hard-sqlalchemy-autoflush",
    trigger="version_specific",
    test_command=PROJECT_TEST_COMMAND,
    files={
        ".python-version": PYTHON_VERSION_FILE,
        "pyproject.toml": pyproject("tagging", ["sqlalchemy==2.1.0"]),
        "conftest.py": PY_CONFTEST,
        "app/__init__.py": "",
        "app/tags.py": (
            "import sqlalchemy as sa\n"
            "from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column\n\n\n"
            "class Base(DeclarativeBase):\n"
            "    pass\n\n\n"
            "class Tag(Base):\n"
            '    __tablename__ = "tag"\n'
            "    id: Mapped[int] = mapped_column(primary_key=True)\n"
            "    name: Mapped[str]\n"
            "    source: Mapped[str]\n\n\n"
            "def make_engine():\n"
            '    engine = sa.create_engine("sqlite://")\n'
            "    Base.metadata.create_all(engine)\n"
            "    return engine\n\n\n"
            "def _exists(session, name):\n"
            '    """Whether a tag with this name is already in the database."""\n'
            '    query = sa.text("SELECT id FROM tag WHERE name = :n")\n'
            '    row = session.execute(query, {"n": name})\n'
            "    return row.first() is not None\n\n\n"
            "def import_tags(engine, entries):\n"
            '    """Import (name, source) pairs. Names already in the database are skipped;\n'
            "    within one import, every entry is recorded so each source is attributed.\n"
            '    Returns the names recorded, in order."""\n'
            "    recorded = []\n"
            "    with Session(engine) as session:\n"
            "        for name, source in entries:\n"
            "            if _exists(session, name):\n"
            "                continue\n"
            "            session.add(Tag(name=name, source=source))\n"
            "            recorded.append(name)\n"
            "        session.commit()\n"
            "    return recorded\n\n\n"
            "def sources_for(engine, name):\n"
            "    with Session(engine) as session:\n"
            "        rows = session.execute(sa.select(Tag.source).where(Tag.name == name))\n"
            "        return sorted(rows.scalars())\n"
        ),
        "tests/test_tags.py": (
            "from app.tags import import_tags, make_engine, sources_for\n\n\n"
            "def test_existing_names_are_skipped():\n"
            "    engine = make_engine()\n"
            '    import_tags(engine, [("red", "catalog")])\n'
            '    assert import_tags(engine, [("red", "feed"), ("blue", "feed")]) == ["blue"]\n\n\n'
            "def test_one_import_attributes_every_source():\n"
            "    engine = make_engine()\n"
            '    recorded = import_tags(engine, [("red", "catalog"), ("red", "feed")])\n'
            '    assert recorded == ["red", "red"]\n'
            '    assert sources_for(engine, "red") == ["catalog", "feed"]\n'
        ),
    },
    discovery=Discovery(
        symptom_words=("skipped", "autoflush", "pending", "flush", "duplicate", "exists"),
        cause_words=("autoflush", "2.1", "flush", "core", "text(", "unconditional"),
        library="sqlalchemy",
    ),
    expect=Expect(search=True, propose=True, report=False),
)

# SQLModel 0.0.45 with rows written by the previous version: naive datetimes stored by 0.0.44
# now read back as UTC-aware (UTCDateTime "returns aware UTC values from database reads"), so a
# comparison in app code raises "can't compare offset-naive and offset-aware datetimes", far from
# the database. Fixing the comparison makes the first test pass; the second test writes a new
# row with the app's naive default and fails. Source: https://sqlmodel.tiangolo.com/release-notes/
SQLMODEL_LEGACY_ROWS = Scenario(
    name="hard-sqlmodel-legacy-rows",
    trigger="version_specific",
    test_command=PROJECT_TEST_COMMAND,
    files={
        ".python-version": PYTHON_VERSION_FILE,
        "pyproject.toml": pyproject("reminders", ["sqlmodel==0.0.45"]),
        "conftest.py": PY_CONFTEST,
        "app/__init__.py": "",
        "app/reminders.py": (
            "from datetime import datetime\n\n"
            "from sqlmodel import Field, Session, SQLModel, create_engine, select\n\n\n"
            "class Reminder(SQLModel, table=True):\n"
            "    id: int | None = Field(default=None, primary_key=True)\n"
            "    text: str\n"
            "    due: datetime\n\n\n"
            "def make_engine():\n"
            '    engine = create_engine("sqlite://")\n'
            "    SQLModel.metadata.create_all(engine)\n"
            "    return engine\n\n\n"
            "def load_legacy(engine, rows):\n"
            '    """Rows exported by the previous release: (text, \'YYYY-MM-DD HH:MM:SS\')."""\n'
            "    raw = engine.raw_connection()\n"
            "    try:\n"
            "        raw.executemany(\n"
            '            "INSERT INTO reminder (text, due) VALUES (?, ?)",\n'
            '            [(text, f"{due}.000000") for text, due in rows],\n'
            "        )\n"
            "        raw.commit()\n"
            "    finally:\n"
            "        raw.close()\n\n\n"
            "def add(engine, text, due):\n"
            "    with Session(engine) as session:\n"
            "        session.add(Reminder(text=text, due=due))\n"
            "        session.commit()\n\n\n"
            "def overdue(engine, now):\n"
            '    """Texts of reminders due before `now`, oldest first."""\n'
            "    with Session(engine) as session:\n"
            "        rows = session.exec(select(Reminder)).all()\n"
            "    return [row.text for row in sorted(rows, key=lambda r: r.due) if row.due < now]\n"
        ),
        "tests/test_reminders.py": (
            "from datetime import datetime\n\n"
            "from app.reminders import add, load_legacy, make_engine, overdue\n\n\n"
            "def test_legacy_rows_still_work():\n"
            "    engine = make_engine()\n"
            '    load_legacy(engine, [("renew passport", "2026-01-05 09:00:00"),\n'
            '                         ("dentist", "2026-03-01 08:30:00")])\n'
            '    assert overdue(engine, datetime(2026, 2, 1)) == ["renew passport"]\n\n\n'
            "def test_new_and_legacy_rows_sort_together():\n"
            "    engine = make_engine()\n"
            '    load_legacy(engine, [("renew passport", "2026-01-05 09:00:00")])\n'
            '    add(engine, "taxes", datetime(2026, 1, 20, 12, 0))\n'
            '    assert overdue(engine, datetime(2026, 2, 1)) == ["renew passport", "taxes"]\n'
        ),
    },
    discovery=Discovery(
        symptom_words=(
            "offset-naive",
            "offset-aware",
            "timezone information",
            "TypeError",
            "aware",
        ),
        cause_words=("0.0.45", "UTCDateTime", "aware", "utc", "timezone=True"),
        library="sqlmodel",
    ),
    expect=Expect(search=True, propose=True, report=False),
)

# Python 3.14 (PEP 649/749): annotations are evaluated lazily, so a name imported only under
# TYPE_CHECKING works at class creation and at construction, then raises NameError only when
# something resolves the annotations (typing.get_type_hints). The obvious fix, importing the
# name at runtime, passes; but the project's own validator uses get_type_hints on *every*
# model, and a second model imports a name from a module that would import circularly.
# Source: https://docs.python.org/3/whatsnew/3.14.html ("deferred evaluation of annotations").
PY314_LAZY_ANNOTATIONS = Scenario(
    name="hard-py314-lazy-annotations",
    trigger="version_specific",
    test_command="uv run --python 3.14 --with pytest pytest -q",
    files={
        "conftest.py": PY_CONFTEST,
        "app/__init__.py": "",
        "app/validate.py": (
            "import dataclasses\n"
            "import typing\n\n\n"
            "def validate(obj):\n"
            '    """Check each field\'s value against its annotated type; return problems."""\n'
            "    hints = typing.get_type_hints(type(obj))\n"
            "    problems = []\n"
            "    for field in dataclasses.fields(obj):\n"
            "        expected = hints[field.name]\n"
            "        value = getattr(obj, field.name)\n"
            "        if isinstance(expected, type) and not isinstance(value, expected):\n"
            '            problems.append(f"{field.name}: expected {expected.__name__}")\n'
            "    return problems\n"
        ),
        "app/money.py": (
            "from __future__ import annotations\n\n"
            "import dataclasses\n"
            "from typing import TYPE_CHECKING\n\n"
            "if TYPE_CHECKING:\n"
            "    from decimal import Decimal\n\n"
            "    from app.orders import Order\n\n\n"
            "@dataclasses.dataclass\n"
            "class Price:\n"
            "    amount: Decimal\n"
            '    currency: str = "USD"\n\n\n'
            "@dataclasses.dataclass\n"
            "class LineItem:\n"
            "    price: Price\n"
            "    order: Order\n"
            "    quantity: int = 1\n"
        ),
        "app/orders.py": (
            "from __future__ import annotations\n\n"
            "import dataclasses\n\n"
            "from app.money import LineItem\n\n\n"
            "@dataclasses.dataclass\n"
            "class Order:\n"
            "    number: int\n"
            "    items: list[LineItem] = dataclasses.field(default_factory=list)\n"
        ),
        "tests/test_validate.py": (
            "from decimal import Decimal\n\n"
            "from app.money import LineItem, Price\n"
            "from app.orders import Order\n"
            "from app.validate import validate\n\n\n"
            "def test_price_validates():\n"
            '    assert validate(Price(amount=Decimal("5"))) == []\n'
            '    assert validate(Price(amount="5")) == ["amount: expected Decimal"]\n\n\n'
            "def test_line_item_validates():\n"
            "    order = Order(number=1)\n"
            '    item = LineItem(price=Price(amount=Decimal("5")), order=order)\n'
            "    assert validate(item) == []\n"
        ),
    },
    discovery=Discovery(
        symptom_words=(
            "NameError",
            "Decimal",
            "Order",
            "get_type_hints",
            "not defined",
            "annotation",
        ),
        cause_words=("3.14", "lazy", "deferred", "TYPE_CHECKING", "annotationlib", "forward"),
        library="python",
    ),
    expect=Expect(search=True, propose=True, report=False),
)

HARD = {
    s.name: s
    for s in (PANDAS_COW, SQLALCHEMY_AUTOFLUSH, SQLMODEL_LEGACY_ROWS, PY314_LAZY_ANNOTATIONS)
}

# The first hold-out set (solved in 1-3 attempts; kept for comparison) plus the hard set.
EASY = {
    s.name: s
    for s in (SQLMODEL_NAIVE, WHENEVER_IGNORE_DST, PY314_SQLITE_PARAMS, PY314_ITERTOOLS_COPY)
}
HOLDOUT = {**EASY, **HARD}

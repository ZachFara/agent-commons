"""Build a `commons_demo` database full of realistic activity, for dashboard work.

Usage: uv run python tools/demo_data.py [--admin-url URL]
Then:  COMMONS_DATABASE_URL=<printed url> uv run commons-dashboard

Never touches any other database. Re-running drops and rebuilds `commons_demo`.
"""

import argparse
import random
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from commons.service.ranking import rank
from commons.store.migrate import apply_migrations

DEMO_DB = "commons_demo"
DAYS = 30
PENDING_AT_END = 3  # the newest entries stay in the review queue
HUMAN_REVIEW_SHARE = 0.6
OUTCOME_SHARE = 0.8
OBSERVED_SHARE = 0.8
HONEST_SHARE = 0.8
TEST_RUN_SHARE = 0.5
REDACTED_ENTRY = 12

REPORTERS = ["zach", "maya", "ravi", "jun", "ellie", "tomas"]
MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "gpt-6-codex"]
OUTCOME_WEIGHTS = {
    "tests_passed": 34,
    "error_resolved": 14,
    "tests_failed": 12,
    "user_accepted": 6,
    "error_persisted": 5,
    "abandoned": 3,
    "unknown": 26,
}
# Each entry is symptom, language, cause, fix, and domains.
ENTRIES = [
    (
        "ImportError: cannot import name 'soft_unicode' from 'markupsafe'",
        "python 3.10",
        "MarkupSafe 2.1 removed soft_unicode; Jinja2 2.x imports it.",
        "Pin `markupsafe<2.1` or upgrade Jinja2 to 3.x.",
        ["python", "packaging"],
    ),
    (
        "error:0308010C:digital envelope routines::unsupported",
        "javascript (node 18)",
        "Node 17+ ships OpenSSL 3, which disables MD4 used by webpack 4.",
        "Upgrade to webpack 5; stopgap NODE_OPTIONS=--openssl-legacy-provider.",
        ["javascript", "webpack"],
    ),
    (
        "Error: pg_config executable not found.",
        "python 3.12",
        "psycopg2 builds from source and needs libpq headers.",
        "Use `psycopg2-binary` or install `libpq-dev`.",
        ["python", "postgres"],
    ),
    (
        "RuntimeError: Event loop is closed (pytest-asyncio)",
        "python 3.12",
        "Session-scoped async fixtures outlive the per-test loop in pytest-asyncio 0.23.",
        "Set `asyncio_default_fixture_loop_scope = session`.",
        ["python", "testing"],
    ),
    (
        "TS2307: Cannot find module './styles.module.css'",
        "typescript 5.4",
        "TypeScript has no declarations for CSS modules.",
        "Add `declare module '*.module.css'` in a global .d.ts.",
        ["typescript", "css"],
    ),
    (
        "cargo build: linker `cc` not found",
        "rust 1.79",
        "No C toolchain in the slim Docker image.",
        "Install `build-essential` before `cargo build`.",
        ["rust", "docker"],
    ),
    (
        "psycopg.errors.UndefinedFunction: operator does not exist: vector <=> vector",
        "python 3.12",
        "pgvector extension not created in the database.",
        "Run `CREATE EXTENSION vector;` in a migration.",
        ["postgres", "pgvector"],
    ),
    (
        "Hydration failed because the initial UI does not match",
        "typescript 5.5",
        "Date rendered with the server's timezone differs from the client's.",
        "Render dates client-side or pass a fixed `timeZone`.",
        ["react", "nextjs"],
    ),
    (
        "go: module lookup disabled by GOFLAGS=-mod=vendor",
        "go 1.22",
        "Vendored modules are out of sync with go.mod.",
        "Run `go mod vendor` after changing go.mod.",
        ["go"],
    ),
    (
        "ModuleNotFoundError: No module named 'distutils'",
        "python 3.12",
        "Python 3.12 removed distutils; old setuptools-based builds import it.",
        "Upgrade `setuptools>=69` or the package that needs it.",
        ["python", "packaging"],
    ),
    (
        "docker: Error response from daemon: no matching manifest for linux/arm64",
        "docker 26",
        "Image published for amd64 only.",
        "Pass `--platform linux/amd64` or use a multi-arch tag.",
        ["docker"],
    ),
    (
        "ESLint couldn't find the config 'next/core-web-vitals'",
        "javascript (node 20)",
        "eslint-config-next missing after ESLint 9 flat-config migration.",
        "Install `eslint-config-next` and use `FlatCompat`.",
        ["javascript", "eslint"],
    ),
    (
        "SSL: CERTIFICATE_VERIFY_FAILED on macOS python.org build",
        "python 3.11",
        "python.org installers ship without system certificates.",
        "Run `Install Certificates.command` or `pip install certifi`.",
        ["python", "macos"],
    ),
    (
        "alembic: Target database is not up to date",
        "python 3.12",
        "Migrations applied out of order on a branch.",
        "Run `alembic upgrade heads` then merge heads.",
        ["python", "alembic"],
    ),
]


def build(admin_url: str) -> str:
    """Drop and recreate the demo database, fill it, and return its URL."""
    name = sql.Identifier(DEMO_DB)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(name))
    url = make_conninfo(admin_url, dbname=DEMO_DB)
    with psycopg.connect(url) as conn:
        apply_migrations(conn)
        Demo(conn).fill()
        rank(conn)
    return url


class Demo:
    """Inserts one consistent month of made-up activity."""

    def __init__(self, conn: psycopg.Connection) -> None:
        """Write through `conn`."""
        self.conn = conn
        self.rng = random.SystemRandom()  # demo data needn't be reproducible
        self.now = datetime.now(UTC)
        self.reporters: list[uuid.UUID] = []
        self.models: list[uuid.UUID] = []
        self.approved: list[tuple[uuid.UUID, float]] = []

    def fill(self) -> None:
        """Insert reporters, models, entries, reviews, tasks, retrievals, and outcomes."""
        insert_reporter = "INSERT INTO reporters (pseudonym) VALUES (%s) RETURNING id"
        insert_model = "INSERT INTO models (name) VALUES (%s) RETURNING id"
        self.reporters = [self._id(insert_reporter, name) for name in REPORTERS]
        self.models = [self._id(insert_model, name) for name in MODELS]
        self.conn.execute(
            "UPDATE reporters SET is_admin = true WHERE pseudonym = %s", (REPORTERS[0],)
        )
        for index, entry in enumerate(ENTRIES):
            self._entry(index, *entry)
        for day in range(DAYS):
            for _ in range(self.rng.randint(1, 4 + day // 4)):
                self._session(self.now - timedelta(days=DAYS - day, hours=self.rng.randint(0, 23)))

    def _entry(
        self, index: int, symptom: str, language: str, cause: str, fix: str, domains: list[str]
    ) -> None:
        created = self.now - timedelta(days=DAYS + 5 - index * 2, hours=self.rng.randint(0, 20))
        pending = index >= len(ENTRIES) - PENDING_AT_END
        status = "pending" if pending else self.rng.choice(["approved"] * 8 + ["rejected"])
        reviewed = not pending and self.rng.random() < HUMAN_REVIEW_SHARE
        findings = [{"field": "fix", "kind": "github_token", "count": 1}]
        report = {"scanned": True, "findings": findings if index == REDACTED_ENTRY else []}
        entry = self._id(
            "INSERT INTO entries (symptom, environment, cause, fix, domains, status, "
            "human_reviewed, created_by, created_by_model, created_at, redaction_report) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            symptom,
            Jsonb({"language": language}),
            cause,
            fix,
            domains,
            status,
            reviewed,
            self.rng.choice(self.reporters),
            self.rng.choice(self.models),
            created,
            Jsonb(report),
        )
        if pending:
            return
        decision = "approve" if status == "approved" else "reject"
        self.conn.execute(
            "INSERT INTO reviews (entry_id, reviewed_by, decision, reason, source, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                entry,
                self.reporters[0],
                decision,
                None if decision == "approve" else "Fix is wrong for v3.",
                "human" if reviewed else "agent",
                created + timedelta(hours=3),
            ),
        )
        if status == "approved":
            self.approved.append((entry, self.rng.random()))

    def _session(self, at: datetime) -> None:
        reporter, model = self.rng.choice(self.reporters), self.rng.choice(self.models)
        task = self._id(
            "INSERT INTO tasks (session_id, reporter_id, model_id, privacy_mode, created_at) "
            "VALUES (%s, %s, %s, 'entry', %s) RETURNING id",
            str(uuid.uuid4()),
            reporter,
            model,
            at,
        )
        picks = self.rng.sample(self.approved, k=self.rng.randint(1, 3))
        for position, (entry, quality) in enumerate(picks, start=1):
            retrieval = self._id(
                "INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, "
                "rank_position, score_at_retrieval, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                entry,
                reporter,
                model,
                task,
                "error after upgrade",
                position,
                round(self.rng.uniform(0.1, 2.5), 3),
                at,
            )
            if self.rng.random() < OUTCOME_SHARE:
                self._outcome(retrieval, quality, at)
        if self.rng.random() < TEST_RUN_SHARE:
            run = {"command": "pytest -q", "exit_code": self.rng.choice([0, 0, 1])}
            self.conn.execute(
                "INSERT INTO task_events (task_id, kind, data, created_at) "
                "VALUES (%s, 'test_run', %s, %s)",
                (task, Jsonb(run), at + timedelta(minutes=15)),
            )

    def _outcome(self, retrieval: uuid.UUID, quality: float, at: datetime) -> None:
        kinds = [kind for kind, weight in OUTCOME_WEIGHTS.items() for _ in range(weight)]
        kind = self.rng.choice(kinds)
        if kind == "tests_failed" and self.rng.random() < quality:
            kind = "tests_passed"
        observed = kind.startswith("tests_") and self.rng.random() < OBSERVED_SHARE
        honest = self.rng.random() < HONEST_SHARE
        claimed = kind if honest else self.rng.choice(["tests_passed", "error_resolved"])
        evidence = {"outcome_source": "hook" if observed else "agent", "agent_outcome": claimed}
        used = None if kind == "unknown" else self.rng.choice(["yes", "yes", "partial", "no"])
        worked = kind == "tests_passed"
        self.conn.execute(
            "INSERT INTO outcomes (retrieval_id, used, outcome, evidence, agent_assessment, "
            "created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (
                retrieval,
                used,
                kind,
                Jsonb(evidence),
                "Applied the pin; tests pass." if worked else "Didn't match my case.",
                at + timedelta(minutes=20),
            ),
        )

    def _id(self, query: str, *params: object) -> uuid.UUID:
        row = self.conn.execute(query, params).fetchone()
        if row is None:
            msg = "insert returned no id"
            raise RuntimeError(msg)
        value: uuid.UUID = row[0]
        return value


def main() -> None:
    """Build the demo database and print how to point the dashboard at it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admin-url", default="postgresql://commons:commons@localhost:5432/commons"
    )
    url = build(parser.parse_args().admin_url)
    print(f"demo database ready: {url}")


if __name__ == "__main__":
    main()

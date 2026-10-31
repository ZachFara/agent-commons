"""Row counts per table, to check a restored backup (decision 0033).

Usage:
  uv run python tools/backup_counts.py count URL > counts.json
  uv run python tools/backup_counts.py compare URL counts.json [--tolerance 0.05]

`compare` exits 1 and names each table whose count is off. With a tolerance, a table may
differ by that share of its expected rows (at least 25), for comparing against a live
database that kept taking writes after the dump.
"""

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg import sql

MIN_SLACK = 25


def count_rows(url: str) -> dict[str, int]:
    """Return {table: rows} for every table in the public schema."""
    with psycopg.connect(url) as conn:
        tables = [
            name
            for (name,) in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
        ]
        counts: dict[str, int] = {}
        for table in tables:
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()
            counts[table] = row[0] if row else 0
    return counts


def differences(expected: dict[str, int], actual: dict[str, int], tolerance: float) -> list[str]:
    """Describe every table missing from `actual` or off by more than the tolerance."""
    problems = []
    for table, rows in sorted(expected.items()):
        if table not in actual:
            problems.append(f"{table}: missing")
            continue
        slack = 0 if tolerance == 0 else max(MIN_SLACK, round(rows * tolerance))
        if abs(actual[table] - rows) > slack:
            problems.append(f"{table}: expected {rows}, found {actual[table]}")
    problems += [f"{table}: unexpected table" for table in sorted(actual.keys() - expected)]
    if not expected.get("entries"):
        problems.append("entries: the backup has no entries")
    return problems


def main() -> int:
    """Count or compare, per the usage above."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["count", "compare"])
    parser.add_argument("url")
    parser.add_argument("expected", nargs="?", type=Path)
    parser.add_argument("--tolerance", type=float, default=0.0)
    args = parser.parse_args()
    actual = count_rows(args.url)
    if args.action == "count":
        print(json.dumps(actual, indent=2, sort_keys=True))
        return 0
    expected_path: Path = args.expected
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    problems = differences(expected, actual, args.tolerance)
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"{len(actual)} tables checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

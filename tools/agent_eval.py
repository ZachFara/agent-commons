"""Behavioral eval of whether real Claude Code agents use the commons as intended.

Runs headless `claude -p` sessions in throwaway projects (tools/eval/scenarios.py), each fully
isolated from the user's setup, and grades what the agent did from the database and the
transcript. Results and transcripts land in .scratch/eval/<condition>/.

Usage:
  uv run python tools/agent_eval.py check                 # scenarios fail then pass as designed
  uv run python tools/agent_eval.py run [--scenario NAME ...] [--runs N] [--model MODEL]
                                         [--condition NAME] [--no-hooks] [--concurrency N]
  uv run python tools/agent_eval.py report [--condition NAME ...]

Sessions use the signed-in Claude Code account (subscription usage, not API billing).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # the eval package lives under tools/, which isn't installed

from tools.eval import runner  # noqa: E402
from tools.eval.model import TEST_COMMAND  # noqa: E402
from tools.eval.scenarios import SCENARIOS  # noqa: E402

RESULTS = REPO / ".scratch" / "eval"


def check() -> int:
    """Confirm each scenario's tests fail as shipped; report what's broken."""
    broken = 0
    for name, scenario in sorted(SCENARIOS.items()):
        with tempfile.TemporaryDirectory() as tmp:
            project, site = Path(tmp) / "project", Path(tmp) / "site"
            runner.write_files(project, scenario.files)
            runner.write_files(site, scenario.libraries)
            tests = subprocess.run(
                TEST_COMMAND.split(),
                cwd=project,
                env={**os.environ, "PYTHONPATH": str(site)},
                capture_output=True,
                text=True,
                check=False,
            )
        outcome = (
            "fails as designed" if tests.returncode == 1 else f"UNEXPECTED exit {tests.returncode}"
        )
        broken += tests.returncode != 1
        last = (
            tests.stdout.strip().splitlines()[-1] if tests.stdout.strip() else tests.stderr[-120:]
        )
        print(f"{name:<26} {outcome}: {last}")
    return 1 if broken else 0


def run(args: argparse.Namespace) -> int:
    """Run the requested scenarios in parallel and print results as they finish."""
    names = args.scenario or sorted(SCENARIOS)
    jobs = [(SCENARIOS[name], index) for name in names for index in range(1, args.runs + 1)]
    transcripts = RESULTS / args.condition
    results = runner.run_many(
        jobs,
        args.model,
        args.condition,
        transcripts,
        hooks=not args.no_hooks,
        concurrency=args.concurrency,
    )
    failed = sum(bool(r.failures(SCENARIOS[r.scenario])) for r in results)
    cost = sum(r.cost_usd for r in results)
    errors = sum(r.error is not None for r in results)
    print(
        f"\n{len(results)} sessions, {failed} missed expectations, {errors} errored; "
        f"API-equivalent cost ${cost:.2f}; results in {transcripts}"
    )
    return 1 if errors else 0


Row = dict[str, Any]
HEADER = (
    f"{'scenario':<26} {'n':>2} {'search':>7} {'found':>6} {'propose':>8} "
    f"{'good':>5} {'report':>7} {'tests':>6} {'attempts':>8}"
)


def report(args: argparse.Namespace) -> int:
    """Summarize saved results per scenario and condition."""
    conditions = args.condition or sorted(p.name for p in RESULTS.iterdir() if p.is_dir())
    for condition in conditions:
        rows: list[Row] = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((RESULTS / condition).glob("*.json"))
        ]
        if not rows:
            continue
        print(f"\n== {condition}: {len(rows)} sessions")
        by_scenario: dict[str, list[Row]] = defaultdict(list)
        for row in rows:
            by_scenario[str(row["scenario"])].append(row)
        print(HEADER)
        for name, group in sorted(by_scenario.items()):
            print(f"{name:<26} {_scenario_line(group)}")
    return 0


def _scenario_line(group: list[Row]) -> str:
    proposals: list[Row] = [p for row in group for p in row["proposals"]]
    good = sum(bool(p["on_topic"] and p["cause_right"] and p["pinned"]) for p in proposals)
    attempts = sum(int(row["attempts"]) for row in group) / len(group)
    return (
        f"{len(group):>2} {_rate(group, 'searched'):>7} {_rate(group, 'search_found'):>6} "
        f"{_rate(group, 'proposed'):>8} {good:>2}/{len(proposals):<2} "
        f"{_rate(group, 'reported'):>7} {_rate(group, 'tests_pass'):>6} {attempts:>8.1f}"
    )


def _rate(rows: list[Row], key: str) -> str:
    hits = sum(bool(row[key]) for row in rows)
    return f"{hits}/{len(rows)}"


def main() -> int:
    """Dispatch to check, run, or report."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS))
    run_parser.add_argument("--runs", type=int, default=1)
    run_parser.add_argument("--model", default="sonnet")
    run_parser.add_argument("--condition", default="baseline")
    run_parser.add_argument("--no-hooks", action="store_true")
    run_parser.add_argument("--concurrency", type=int, default=4)
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--condition", action="append")
    args = parser.parse_args()
    if args.command == "check":
        return check()
    if args.command == "run":
        return run(args)
    return report(args)


if __name__ == "__main__":
    sys.exit(main())

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # the eval package lives under tools/, which isn't installed

from tools.eval import runner  # noqa: E402
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
                scenario.test_command.split(),
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
    os.environ["COMMONS_INSTRUCTIONS_VARIANT"] = args.variant
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
    append_ledger(results)
    failed = sum(bool(r.failures(SCENARIOS[r.scenario])) for r in results)
    cost = sum(r.cost_usd for r in results)
    errors = sum(r.error is not None for r in results)
    print(
        f"\n{len(results)} sessions, {failed} missed expectations, {errors} errored; "
        f"API-equivalent cost ${cost:.2f}; results in {transcripts}"
    )
    return 1 if errors else 0


LEDGER = REPO / "docs" / "eval" / "trials.jsonl"


def append_ledger(results: list[runner.Result]) -> None:
    """Append one line per session to the committed trial ledger, keyed by variant and text hash."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as ledger:
        for result in results:
            line = {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "variant": result.variant,
                "text": result.text_fingerprint,
                "condition": result.condition,
                "scenario": result.scenario,
                "trigger": result.trigger,
                "run": result.index,
                "searched": result.searched,
                "found": result.search_found,
                "proposed": result.proposed,
                "good_proposals": sum(
                    p.on_topic and p.cause_right and p.pinned for p in result.proposals
                ),
                "reported": result.reported,
                "tests_pass": result.tests_pass,
                "attempts": result.attempts,
                "turns": result.turns,
                "error": result.error,
            }
            ledger.write(json.dumps(line) + "\n")


def by_variant() -> int:
    """Summarize the ledger per variant and text fingerprint: the trial log the owner asked for."""
    if not LEDGER.exists():
        print("no trials logged yet")
        return 0
    lines = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line]
    # A line marked `invalid` stays in the ledger as a record but is never counted.
    rows = [row for row in lines if not row.get("invalid")]
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["variant"], row["text"], row["trigger"])].append(row)
    if skipped := len(lines) - len(rows):
        print(f"({skipped} trials marked invalid are not counted)")
    print(
        f"{'variant':<10} {'text':<13} {'trigger':<18} {'n':>3} "
        f"{'search':>7} {'propose':>8} {'good':>5} {'fixed':>6}"
    )
    for (variant, text, trigger), group in sorted(groups.items()):
        good = sum(int(r["good_proposals"]) for r in group)
        print(
            f"{variant:<10} {text:<13} {trigger:<18} {len(group):>3} "
            f"{_rate(group, 'searched'):>7} {_rate(group, 'proposed'):>8} {good:>5} "
            f"{_rate(group, 'tests_pass'):>6}"
        )
    return 0


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
    run_parser.add_argument(
        "--variant", default="baseline", help="COMMONS_INSTRUCTIONS_VARIANT for the sessions."
    )
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--condition", action="append")
    commands.add_parser(
        "trials", help="Summarize docs/eval/trials.jsonl per variant and text hash."
    )
    args = parser.parse_args()
    if args.command == "check":
        return check()
    if args.command == "run":
        return run(args)
    if args.command == "trials":
        return by_variant()
    return report(args)


if __name__ == "__main__":
    sys.exit(main())

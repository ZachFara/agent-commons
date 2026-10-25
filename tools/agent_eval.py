"""Behavioral eval of whether real Claude Code agents use the commons as intended.

Runs headless `claude -p` sessions in throwaway projects (tools/eval/scenarios.py), each fully
isolated from the user's setup, and grades what the agent did from the database and the
transcript. Results and transcripts land in .scratch/eval/<condition>/.

Usage:
  uv run python tools/agent_eval.py check                 # scenarios fail then pass as designed
  uv run python tools/agent_eval.py run [--scenario NAME ...] [--runs N] [--model MODEL]
                                         [--condition NAME] [--no-hooks] [--concurrency N]
  uv run python tools/agent_eval.py report [--condition NAME ...]
  uv run python tools/agent_eval.py search [--configs N] [--runs N] [--scenario NAME ...]
  uv run python tools/agent_eval.py effects [--condition NAME]

Sessions use the signed-in Claude Code account (subscription usage, not API billing).
"""

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from commons.service import agent_text

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # the eval package lives under tools/, which isn't installed

from tools.eval import runner  # noqa: E402
from tools.eval.scenarios import SCENARIOS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

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
    agent_text.resolve(args.variant)  # fail before starting sessions, not inside each one
    names = args.scenario or sorted(SCENARIOS)
    jobs = [
        runner.Job(SCENARIOS[name], index, args.variant)
        for name in names
        for index in range(1, args.runs + 1)
    ]
    return run_jobs(jobs, args)


def wording_search(args: argparse.Namespace) -> int:
    """Run balanced random wording configs, each repeated on every scenario (decision 0023)."""
    configs = plan_configs(args.config, args.configs, args.seed)
    names = args.scenario or sorted(SCENARIOS)
    jobs = [
        runner.Job(SCENARIOS[name], 0, config)
        for config in configs
        for name in names
        for _ in range(args.runs)
    ]
    jobs = [runner.Job(job.scenario, index, job.variant) for index, job in enumerate(jobs, 1)]
    transcripts = RESULTS / args.condition
    transcripts.mkdir(parents=True, exist_ok=True)
    (transcripts / "configs.json").write_text(json.dumps(configs, indent=1), encoding="utf-8")
    print(f"{len(configs)} configs x {len(names)} scenarios x {args.runs} runs = {len(jobs)}")
    return run_jobs(jobs, args)


def plan_configs(named: list[str] | None, count: int, seed: int) -> list[str]:
    """Return the named configs to repeat, or today's text plus `count` balanced random ones."""
    if named:
        for name in named:
            agent_text.resolve(name)  # fail before any session starts
        return named
    return [
        agent_text.wording_name(dict.fromkeys(agent_text.SURFACES, 0)),  # today's text
        *balanced_configs(count, seed),
    ]


def finished(transcripts: Path, condition: str, job: runner.Job) -> bool:
    """Whether a job already has a result without an error (for `--resume`)."""
    path = transcripts / f"{condition}-{job.scenario.name}-{job.index}.json"
    if not path.exists():
        return False
    saved = json.loads(path.read_text(encoding="utf-8"))
    return saved.get("error") is None and saved.get("variant") == job.variant


def balanced_configs(count: int, seed: int) -> list[str]:
    """Return `count` wording names in which each option of a surface appears about equally.

    Each surface's column of options is shuffled on its own, so combinations are random while
    every option gets a near-equal share of sessions for the pooled comparison.
    """
    columns: dict[str, list[int]] = {}
    for key, size in agent_text.SURFACES.items():
        # A seeded shuffle: order positions by a hash of (seed, surface, position).
        def rank(i: int, key: str = key) -> bytes:
            return hashlib.sha256(f"{seed}:{key}:{i}".encode()).digest()

        order = sorted(range(count), key=rank)
        columns[key] = [order.index(i) % size for i in range(count)]
    return [
        agent_text.wording_name({key: columns[key][i] for key in agent_text.SURFACES})
        for i in range(count)
    ]


def run_jobs(jobs: list[runner.Job], args: argparse.Namespace) -> int:
    """Run jobs, appending each result to the ledger as it finishes; print a summary."""
    transcripts = RESULTS / args.condition
    if args.resume:
        jobs = [job for job in jobs if not finished(transcripts, args.condition, job)]
        print(f"resuming: {len(jobs)} sessions without a clean result")
    results: list[runner.Result] = []
    for result in runner.run_many(
        jobs,
        args.model,
        args.condition,
        transcripts,
        hooks=not args.no_hooks,
        concurrency=args.concurrency,
    ):
        append_ledger([result])
        results.append(result)
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
        # An errored session (usage limit, crash) is not a trial of the text: never logged.
        for result in (r for r in results if r.error is None):
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
    rows = _ledger_lines()
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["variant"], row["text"], row["trigger"])].append(row)
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


def _ledger_lines() -> list[dict[str, Any]]:
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line]


def effects(args: argparse.Namespace) -> int:
    """Pool a wording search by option: each session counts for every option it showed."""
    rows = [
        row
        for row in _ledger_lines()
        if row["condition"] == args.condition
        and row["variant"].startswith("w-")
        and not row.get("invalid")  # kept in the ledger as a record, never counted
    ]
    if not rows:
        print(f"no wording trials for condition {args.condition}")
        return 0
    header = (
        f"{'':<22} {'n':>3} {'search':>16} {'good propose':>16}   "
        f"{'controls':>8} {'search':>7} {'propose':>8}"
    )
    print("per option (pooled over every config that showed it)\n" + header)
    for key, size in agent_text.SURFACES.items():
        for option in range(size):
            showing = [row for row in rows if option_of(row["variant"], key) == option]
            print(_effect_line(f"{key}{option}", showing))
    print("\nper config, best first\n" + header)
    configs = {str(row["variant"]) for row in rows}

    def good_rate(config: str) -> float:
        mine = [r for r in rows if r["variant"] == config and r["trigger"] != "control"]
        return sum(bool(r["good_proposals"]) for r in mine) / max(len(mine), 1)

    for config in sorted(configs, key=good_rate, reverse=True):
        print(_effect_line(config, [row for row in rows if row["variant"] == config]))
    return 0


def option_of(variant: str, key: str) -> int:
    """Return which option of surface `key` a wording name shows, or -1 if it isn't one."""
    match = re.search(rf"{key}(\d)", variant.removeprefix("w-"))
    return int(match.group(1)) if match else -1


def _effect_line(label: str, rows: list[dict[str, Any]]) -> str:
    discovery = [row for row in rows if row["trigger"] != "control"]
    controls = [row for row in rows if row["trigger"] == "control"]
    searched = sum(bool(row["searched"]) for row in discovery)
    good = sum(bool(row["good_proposals"]) for row in discovery)
    return (
        f"{label:<22} {len(discovery):>3} {_with_interval(searched, len(discovery)):>16} "
        f"{_with_interval(good, len(discovery)):>16}   {len(controls):>8} "
        f"{_rate(controls, 'searched'):>7} {_rate(controls, 'proposed'):>8}"
    )


def _with_interval(hits: int, total: int) -> str:
    """Return `hits/total (low-high%)` with a 95% Wilson interval."""
    if total == 0:
        return "-"
    z = 1.96
    p = hits / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    low, high = (centre - spread) / denominator, (centre + spread) / denominator
    return f"{hits}/{total} ({low:.0%}-{high:.0%})"


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
    run_parser.add_argument(
        "--resume", action="store_true", help="Skip sessions that already have a clean result."
    )
    run_parser.add_argument("--concurrency", type=int, default=4)
    run_parser.add_argument(
        "--variant", default="baseline", help="COMMONS_INSTRUCTIONS_VARIANT for the sessions."
    )
    search_parser = commands.add_parser(
        "search", help="Balanced random wording configs, each repeated on every scenario."
    )
    search_parser.add_argument("--configs", type=int, default=16)
    search_parser.add_argument(
        "--config", action="append", help="Repeat these exact configs instead of sampling."
    )
    search_parser.add_argument("--seed", type=int, default=1)
    search_parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS))
    search_parser.add_argument("--runs", type=int, default=2, help="Repeats per config, scenario.")
    search_parser.add_argument("--model", default="sonnet")
    search_parser.add_argument("--condition", default="wording")
    search_parser.add_argument("--no-hooks", action="store_true")
    search_parser.add_argument(
        "--resume", action="store_true", help="Skip sessions that already have a clean result."
    )
    search_parser.add_argument("--concurrency", type=int, default=4)
    effects_parser = commands.add_parser("effects", help="Pool a wording search by option.")
    effects_parser.add_argument("--condition", default="wording")
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--condition", action="append")
    commands.add_parser(
        "trials", help="Summarize docs/eval/trials.jsonl per variant and text hash."
    )
    args = parser.parse_args()
    commands_by_name: dict[str, Callable[[argparse.Namespace], int]] = {
        "check": lambda _args: check(),
        "run": run,
        "trials": lambda _args: by_variant(),
        "search": wording_search,
        "effects": effects,
        "report": report,
    }
    return commands_by_name[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

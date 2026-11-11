"""Behavioral eval of whether real Claude Code agents use the commons as intended.

Runs headless `claude -p` sessions in throwaway projects (tools/eval/scenarios.py), each fully
isolated from the user's setup, and grades what the agent did from the database and the
transcript. Results and transcripts land in .scratch/eval/<condition>/.

Usage:
  uv run python tools/agent_eval.py check [--on-host]     # scenarios fail then pass as designed
  uv run python tools/agent_eval.py run [--scenario NAME ...] [--runs N] [--model MODEL]
                                         [--condition NAME] [--no-hooks] [--concurrency N]
                                         [--skill]
  uv run python tools/agent_eval.py report [--condition NAME ...]
  uv run python tools/agent_eval.py search [--configs N] [--runs N] [--scenario NAME ...]
  uv run python tools/agent_eval.py arms [--scenario NAME ...] [--arm ARM ...] [--runs N]
                                         [--model MODEL ...] [--condition NAME]
                                         [--store NAME [--seed-wave]]
  uv run python tools/agent_eval.py counterfactuals --condition NAME --store NAME
  uv run python tools/agent_eval.py compare --condition NAME
  uv run python tools/agent_eval.py effects [--condition NAME]
  uv run python tools/agent_eval.py flags DIRECTORY        # scan saved GitHub-task transcripts
  uv run python tools/agent_eval.py clear SESSION --reason TEXT  # count a flagged session again

Sessions use the signed-in Claude Code account (subscription usage, not API billing).
"""

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psycopg

from commons.service import agent_text
from commons.store.counterfactuals import CounterfactualRun, known_entries, record_run

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # the eval package lives under tools/, which isn't installed

from tools.eval import container, holdout, leaks, repos, runner  # noqa: E402
from tools.eval.scenarios import SCENARIOS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from tools.eval.model import GitHubTask, Scenario

RESULTS = REPO / ".scratch" / "eval"


ON_HOST_WARNING = (
    "warning: --on-host runs each GitHub task's install and tests on this machine, as you, "
    "with your home directory and network: only for repositories you trust"
)


def check(*, on_host: bool = False) -> int:
    """Confirm each scenario's tests fail as shipped (a GitHub task's: and pass with the fix).

    A GitHub task's install and tests run in throwaway containers, or with `on_host` (the
    `--on-host` flag, which warns) on this machine.
    """
    execute: repos.Execute = container.run
    if on_host:
        print(ON_HOST_WARNING, file=sys.stderr)
        execute = repos.run_on_host
    broken = 0
    for name, scenario in sorted(SCENARIOS.items()):
        if scenario.repo:
            ok, outcome = repos.check(scenario.repo, execute)
        elif scenario.outcome is not None:
            ok, outcome = check_outcome(scenario)
        else:
            ok, outcome = check_shipped(scenario)
        broken += not ok
        print(f"{name:<26} {outcome}")
    return 1 if broken else 0


def check_outcome(scenario: "Scenario") -> tuple[bool, str]:
    """Confirm a task with no tests is not done as shipped, with its app run and stopped."""
    with tempfile.TemporaryDirectory() as tmp:
        project, site = Path(tmp) / "project", Path(tmp) / "site"
        runner.write_files(project, scenario.files)
        runner.write_files(site, scenario.libraries)
        with runner.app_running(scenario, project, site):
            pass
        done = runner.outcome_holds(scenario, project, site)
    return not done, ("UNEXPECTED: done as shipped" if done else "not done as designed")


def check_shipped(scenario: "Scenario") -> tuple[bool, str]:
    """Run a written scenario's tests as shipped; they must fail (pytest's exit 1)."""
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
    last = tests.stdout.strip().splitlines()[-1] if tests.stdout.strip() else tests.stderr[-120:]
    return tests.returncode == 1, f"{outcome}: {last}"


def run(args: argparse.Namespace) -> int:
    """Run the requested scenarios in parallel and print results as they finish."""
    agent_text.resolve(args.variant)  # fail before starting sessions, not inside each one
    names = args.scenario or sorted(SCENARIOS)
    jobs = [
        runner.Job(SCENARIOS[name], index, args.variant, skill=args.skill)
        for name in names
        for index in range(1, args.runs + 1)
    ]
    return run_jobs(jobs, args)


def wording_search(args: argparse.Namespace) -> int:
    """Run balanced random wording configs, each repeated on every scenario (decision 0023)."""
    configs = plan_configs(args.config, args.configs, args.seed)
    names = args.scenario or sorted(SCENARIOS)
    models = args.model or ["sonnet"]
    # Every config runs on every model, interleaved, so models see the same conditions.
    jobs = [
        runner.Job(SCENARIOS[name], 0, config, model)
        for config in configs
        for name in names
        for _ in range(args.runs)
        for model in models
    ]
    jobs = [
        runner.Job(job.scenario, index, job.variant, job.model) for index, job in enumerate(jobs, 1)
    ]
    transcripts = RESULTS / args.condition
    transcripts.mkdir(parents=True, exist_ok=True)
    (transcripts / "configs.json").write_text(json.dumps(configs, indent=1), encoding="utf-8")
    print(
        f"{len(configs)} configs x {len(names)} scenarios x {args.runs} runs x "
        f"{len(models)} models = {len(jobs)}"
    )
    return run_jobs(jobs, args)


def arms(args: argparse.Namespace) -> int:
    """Run each task under each arm: Commons and Context7, Context7, Commons, and blind.

    With `--store NAME` the sessions build on an experiment store: with `--seed-wave` they all
    write into it, otherwise each runs in its own clone of it.
    """
    names = args.scenario or sorted(SCENARIOS)
    chosen = args.arm or list(runner.ARMS)
    models = args.model or ["sonnet"]
    jobs = plan_arms(names, chosen, args.runs, models)
    if args.seed_wave and not args.store:
        print("--seed-wave writes into a store: name it with --store NAME", file=sys.stderr)
        return 2
    if args.store:
        store = runner.Store(args.store, seed_wave=args.seed_wave)
        try:
            runner.open_store(store)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        jobs = [replace(job, store=store) for job in jobs]
    print(
        f"{len(names)} scenarios x {len(chosen)} arms x {args.runs} runs x "
        f"{len(models)} models = {len(jobs)}"
    )
    return run_jobs(jobs, args)


def plan_arms(names: list[str], arms: list[str], runs: int, models: list[str]) -> list[runner.Job]:
    """Return task x arm x run x model jobs, interleaved, each with its own index.

    Each job also carries its repeat (1 to `runs`), which pairs it with the same repeat of its
    task and model in another arm. An arm with the commons shows the shipped text; the others
    show none.
    """
    plan = [
        (name, arm, repeat, model)
        for name in names
        for arm in arms
        for repeat in range(1, runs + 1)
        for model in models
    ]
    return [
        runner.Job(SCENARIOS[name], index, arm_variant(arm), model, arm, repeat)
        for index, (name, arm, repeat, model) in enumerate(plan, 1)
    ]


def arm_variant(arm: str) -> str:
    """Return the text an arm's agent sees: the shipped wording, or none without the commons."""
    return ALIASES["shipped"] if runner.has_commons(arm) else runner.NO_COMMONS_VARIANT


# Stable names for `--config`, so a re-run keeps meaning the same thing after the text changes.
# Both map to wording names, which the ledger and `effects` count.
ALIASES = {
    "shipped": agent_text.SHIPPED,
    "original": agent_text.wording_name(dict.fromkeys(agent_text.SURFACES, 0)),
}


def plan_configs(named: list[str] | None, count: int, seed: int) -> list[str]:
    """Return the named configs to repeat, or the shipped text plus `count` balanced ones."""
    if named:
        names = [ALIASES.get(name, name) for name in named]
        for name in names:
            agent_text.resolve(name)  # fail before any session starts
        return names
    return [agent_text.SHIPPED, *balanced_configs(count, seed)]  # what ships, as the anchor


def finished(transcripts: Path, condition: str, job: runner.Job) -> bool:
    """Whether a job already has a clean result for the same text, skill and arm (`--resume`)."""
    path = transcripts / f"{runner.stem(condition, job.scenario.name, job.index)}.json"
    if not path.exists():
        return False
    saved = json.loads(path.read_text(encoding="utf-8"))
    return (
        saved.get("error") is None
        and saved.get("variant") == job.variant
        and saved.get("model", "") in {job.model, ""}
        and saved.get("skill", False) == job.skill
        and saved.get("arm", "commons") == job.arm  # results from before arms ran the commons
    )


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
    # Fetch each repository once, before parallel sessions would fetch into one cache at once.
    for task in {job.scenario.repo for job in jobs if job.scenario.repo is not None}:
        repos.fetch(task)
    results: list[runner.Result] = []
    # `run` takes one model; `search` names one per job, so this is only a fallback there.
    default_model = args.model if isinstance(args.model, str) else "sonnet"
    for result in runner.run_many(
        jobs,
        default_model,
        args.condition,
        transcripts,
        hooks=not args.no_hooks,
        concurrency=args.concurrency,
    ):
        append_ledger([result])
        results.append(result)
    # A session flagged as a possible leak is held out of every count until it is cleared.
    kept = [r for r in results if counted(asdict(r))]
    failed = sum(bool(r.failures(SCENARIOS[r.scenario])) for r in kept)
    cost = sum(r.cost_usd for r in results)
    errors = sum(r.error is not None for r in results)
    print(
        f"\n{len(results)} sessions, {failed} missed expectations, {errors} errored, "
        f"{len(results) - len(kept)} held as possible leaks; "
        f"API-equivalent cost ${cost:.2f}; results in {transcripts}"
    )
    for line in passing_tokens([asdict(r) for r in kept if r.error is None]):
        print(line)
    sessions = [
        (
            transcripts / f"{runner.stem(r.condition, r.scenario, r.index)}.jsonl",
            [asdict(flag) for flag in r.flags],
        )
        for r in results
    ]
    for line in flag_report(sessions):
        print(line)
    return 1 if errors else 0


def flag_report(sessions: list[tuple[Path, list[dict[str, Any]]]]) -> list[str]:
    """Return the report of flagged sessions: per flag, its level, rule, tool and transcript."""
    flagged = [(path, found) for path, found in sessions if found]
    if not flagged:
        return ["no session flagged"]
    lines = [
        (
            f"{len(flagged)} flagged sessions: a leak isn't counted until cleared "
            "(`clear SESSION --reason TEXT`); read each transcript"
        )
    ]
    for path, found in flagged:
        lines += [
            f"  {flag['level']:<6} {flag['rule']:<12} {flag['tool']:<12} {path}\n"
            f"         {flag['excerpt']}"
            for flag in found
        ]
    return lines


def leaked(row: dict[str, Any]) -> bool:
    """Whether a ledger line or saved result has a leak-level flag."""
    return any(flag.get("level") == leaks.LEAK for flag in row.get("flags") or [])


def counted(row: dict[str, Any]) -> bool:
    """Whether a session counts: not marked `invalid`, and no leak flag unless `cleared`."""
    return not row.get("invalid") and (not leaked(row) or bool(row.get("cleared")))


def flags(args: argparse.Namespace) -> int:
    """Scan a directory's saved GitHub-task transcripts again and print the flag report."""
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"no directory {directory}", file=sys.stderr)
        return 2
    sessions = []
    for transcript in sorted(directory.glob("*.jsonl")):
        task = transcript_task(transcript)
        if task is not None:
            calls = runner.tool_uses(runner.read_transcript(transcript))
            found = leaks.scan(calls, task, leaks.distribution(task, None))
            sessions.append((transcript, [asdict(flag) for flag in found]))
    print(f"{len(sessions)} GitHub-task transcripts scanned in {directory}")
    for line in flag_report(sessions):
        print(line)
    return 0


def transcript_task(transcript: Path) -> "GitHubTask | None":
    """Return the GitHub task of a saved transcript, by its result; None for any other."""
    saved = transcript.with_suffix(".json")
    if not saved.is_file():
        return None
    scenario = SCENARIOS.get(str(json.loads(saved.read_text(encoding="utf-8")).get("scenario")))
    return scenario.repo if scenario is not None else None


def clear(args: argparse.Namespace) -> int:
    """Count a session flagged as a leak again, after a human read its transcript.

    The ledger stays append-only: a line `{"clears": SESSION, "reason": ...}`, which also names
    the session's condition, scenario and run, marks every earlier line of that session
    `cleared` when the ledger is read. The saved result is marked too, for `report`.
    """
    reason = " ".join(args.reason.split())
    rows = [row for row in _ledger_lines() if session_name(row) == args.session]
    if not reason:
        print("say why the session is cleared: --reason TEXT", file=sys.stderr)
        return 2
    if not any(leaked(row) for row in rows):
        print(f"no ledger line of session {args.session} has a leak flag", file=sys.stderr)
        return 2
    record = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "clears": args.session,
        **{key: rows[-1][key] for key in ("condition", "scenario", "run")},
        "reason": reason,
    }
    with LEDGER.open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(record) + "\n")
    mark_cleared(RESULTS / str(rows[-1]["condition"]) / f"{args.session}.json", reason)
    print(f"cleared {args.session}: {reason}")
    return 0


def mark_cleared(saved: Path, reason: str) -> None:
    """Mark a saved result `cleared`, if it is still there."""
    if saved.is_file():
        result = json.loads(saved.read_text(encoding="utf-8"))
        saved.write_text(json.dumps({**result, "cleared": reason}, indent=1), encoding="utf-8")


def session_name(row: dict[str, Any]) -> str:
    """Return the session a ledger line records, as its transcript's stem."""
    return runner.stem(str(row["condition"]), str(row["scenario"]), int(row["run"]))


def passing_tokens(rows: list[dict[str, Any]]) -> list[str]:
    """Per condition, the median tokens of sessions whose tests passed: one line each.

    Rows are ledger lines or saved results; lines logged before tokens were recorded are skipped.
    """
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("tests_pass") and "tokens_in" in row and "tokens_out" in row:
            by_condition[str(row["condition"])].append(row)
    return [
        f"{condition}: median tokens per passing session "
        f"in {statistics.median(int(r['tokens_in']) for r in group):.0f} "
        f"out {statistics.median(int(r['tokens_out']) for r in group):.0f} (n={len(group)})"
        for condition, group in sorted(by_condition.items())
    ]


LEDGER = REPO / "docs" / "eval" / "trials.jsonl"


def append_ledger(results: list[runner.Result]) -> None:
    """Append one line per session to the committed trial ledger, keyed by variant and text hash."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as ledger:
        # An errored session (usage limit, crash) is not a trial of the text: never logged.
        for result in (r for r in results if r.error is None):
            line = {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "model": result.model,
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
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "error": result.error,
                "skill": result.skill,
                "arm": result.arm,
                "wall_clock_seconds": result.wall_clock_seconds,
                "retrievals": [asdict(retrieval) for retrieval in result.retrievals],
                # Never the whole patch: it stays next to the transcript (`runner.save_result`).
                "patch_excerpt": runner.excerpt(result.patch),
                "patch_truncated": result.patch_truncated,
                "patch_hash": result.patch_hash,
                "repeat": result.repeat,
                "grader_version": result.grader_version,
                "flags": [asdict(flag) for flag in result.flags],
            }
            ledger.write(json.dumps(line) + "\n")


def by_variant() -> int:
    """Summarize the ledger per variant and text fingerprint: the trial log the owner asked for."""
    if not LEDGER.exists():
        print("no trials logged yet")
        return 0
    lines = _ledger_lines()
    # A line marked `invalid`, or flagged as a leak and not cleared, stays in the ledger as a
    # record but is never counted.
    rows = [row for row in lines if counted(row)]
    groups: dict[tuple[str, str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        # The text fingerprint covers the server's text only, so the skill is its own group,
        # and a line from before arms ran with the commons alone. Each grader version is its
        # own group too, so no `good` figure mixes two graders.
        variant = f"{row['variant']}+skill" if row.get("skill") else row["variant"]
        arm = row.get("arm", "commons")
        groups[(arm, variant, row["text"], row["trigger"], grader_version(row))].append(row)
    if skipped := len(lines) - len(rows):
        print(f"({skipped} trials marked invalid or held as possible leaks are not counted)")
    print(
        f"{'arm':<8} {'variant':<10} {'text':<13} {'trigger':<18} {'n':>3} "
        f"{'search':>7} {'propose':>8} {'good':>5} {'fixed':>6} {'grader':>6}"
    )
    for (arm, variant, text, trigger, version), group in sorted(groups.items()):
        good = sum(int(r["good_proposals"]) for r in group)
        print(
            f"{arm:<8} {variant:<10} {text:<13} {trigger:<18} {len(group):>3} "
            f"{_rate(group, 'searched'):>7} {_rate(group, 'proposed'):>8} {good:>5} "
            f"{_rate(group, 'tests_pass'):>6} {f'v{version}':>6}"
        )
    for line in passing_tokens(rows):
        print(line)
    return 0


def _ledger_lines() -> list[dict[str, Any]]:
    """Return the ledger's session lines, each marked `cleared` if a later line clears it."""
    if not LEDGER.exists():
        return []
    sessions: list[dict[str, Any]] = []
    for row in (
        json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line
    ):
        if "clears" not in row:
            sessions.append(row)
            continue
        for earlier in (r for r in sessions if session_name(r) == row["clears"]):
            earlier["cleared"] = row["reason"]
    return sessions


def effects(args: argparse.Namespace) -> int:
    """Pool a wording search by option and model: each session counts for every option it showed.

    Configs are ranked by their worse model, since the text has to work for every model.
    Each grader version is pooled and ranked on its own, never with another.
    """
    rows = [
        row
        for row in _ledger_lines()
        if row["condition"] == args.condition
        and row["variant"].startswith("w-")
        and row.get("arm", "commons") == "commons"  # Context7 alongside is another treatment
        and counted(row)  # an `invalid` or uncleared leak line is a record, never counted
    ]
    if not rows:
        print(f"no wording trials for condition {args.condition}")
        return 0
    for version, graded in by_grader(rows).items():
        print(f"== {args.condition}: {len(graded)} sessions, grader v{version}")
        _effects_of(graded)
    return 0


def _effects_of(rows: list[dict[str, Any]]) -> None:
    """Print the per-option and per-config lines of one grader version's wording trials."""
    models = sorted({str(row.get("model") or "sonnet") for row in rows})
    print("per option (pooled over every config that showed it)")
    for key, size in agent_text.SURFACES.items():
        for option in range(size):
            showing = [row for row in rows if option_of(row["variant"], key) == option]
            if showing:
                print(_effect_line(f"{key}{option}", showing, models))
    print("\nper config, ranked by the worse model's good-proposal rate")
    configs = {str(row["variant"]) for row in rows}

    def worst(config: str) -> float:
        mine = [row for row in rows if row["variant"] == config]
        return min(_good_rate(mine, model) for model in models)

    for config in sorted(configs, key=worst, reverse=True):
        print(_effect_line(config, [row for row in rows if row["variant"] == config], models))


def option_of(variant: str, key: str) -> int:
    """Return which option of surface `key` a wording name shows, or -1 if it isn't one."""
    if not variant.startswith("w-"):
        return -1
    match = re.search(rf"{key}(\d)", variant.removeprefix("w-"))
    if match:
        return int(match.group(1))
    return 0 if key in agent_text.OPTIONAL_SURFACES else -1  # an optional surface left out is 0


def scenario_kind(row: dict[str, Any]) -> str:
    """Return "control", "known" (the store already holds the answer), or "discovery"."""
    if row["trigger"] == "control":
        return "control"
    scenario = SCENARIOS.get(str(row["scenario"]))
    return "known" if scenario is not None and scenario.seeds else "discovery"


def _of(rows: list[dict[str, Any]], model: str, kind: str) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if str(row.get("model") or "sonnet") == model and scenario_kind(row) == kind
    ]


def _good_rate(rows: list[dict[str, Any]], model: str) -> float:
    found = _of(rows, model, "discovery")
    return sum(bool(row["good_proposals"]) for row in found) / max(len(found), 1)


def _effect_line(label: str, rows: list[dict[str, Any]], models: list[str]) -> str:
    """One line per label: for each model, discoveries, duplicates, and control proposals."""
    parts = [f"{label:<26}"]
    for model in models:
        found, known, controls = (
            _of(rows, model, kind) for kind in ("discovery", "known", "control")
        )
        good = sum(bool(row["good_proposals"]) for row in found)
        searched = sum(bool(row["searched"]) for row in found)
        name = model.removeprefix("claude-")[:10]
        parts.append(
            f"| {name:<10} good {_with_interval(good, len(found)):<17}"
            f" search {searched:>2}/{len(found):<2} dup {_rate(known, 'proposed'):>5}"
            f" ctl {_rate(controls, 'proposed'):>5}"
        )
    return " ".join(parts)


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


def grader_version(row: Row) -> int:
    """Return the grader version a result was graded by; a result without one is version 1."""
    return int(row.get("grader_version") or 1)


def by_grader(rows: list[Row]) -> dict[int, list[Row]]:
    """Split results by grader version, so no figure mixes two graders."""
    groups: dict[int, list[Row]] = defaultdict(list)
    for row in rows:
        groups[grader_version(row)].append(row)
    return dict(sorted(groups.items()))


def report(args: argparse.Namespace) -> int:
    """Summarize saved results per condition, grader version, and scenario."""
    conditions = args.condition or sorted(p.name for p in RESULTS.iterdir() if p.is_dir())
    for condition in conditions:
        saved: list[Row] = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((RESULTS / condition).glob("*.json"))
        ]
        rows = [row for row in saved if counted(row)]
        if held := len(saved) - len(rows):
            print(f"\n({condition}: {held} sessions held as possible leaks are not counted)")
        for version, graded in by_grader(rows).items():
            print(f"\n== {condition}: {len(graded)} sessions, grader v{version}")
            _report_scenarios(graded)
    return 0


def _report_scenarios(rows: list[Row]) -> None:
    by_scenario: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_scenario[str(row["scenario"])].append(row)
    print(HEADER)
    for name, group in sorted(by_scenario.items()):
        print(f"{name:<26} {_scenario_line(group)}")
    for name in sorted(by_scenario.keys() & holdout.RETIRED.keys()):
        print(f"{name} is retired, not measured: {holdout.RETIRED[name]}")


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


# ---------------------------------------------------------------- counterfactuals (DESIGN.md §7)
# Each arm with the commons, and the arm that is the same session without it.
WITHOUT_COMMONS = {"both": "context7", "commons": "none"}
COUNTERFACTUAL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "agent-commons:eval:counterfactual_runs")
USED = ("yes", "partial")


def condition_rows(condition: str) -> list[Row]:
    """Return the ledger's counted lines for one condition (`counted`)."""
    return [r for r in _ledger_lines() if r["condition"] == condition and counted(r)]


def _arm(row: Row) -> str:
    return str(row.get("arm", "commons"))  # a line from before arms ran with the commons alone


def retrieved_entry(row: Row) -> uuid.UUID | None:
    """Return the entry a session retrieved: the first it used, else the first returned."""
    retrievals: list[Row] = row.get("retrievals") or []
    used = [r for r in retrievals if r.get("used") in USED]
    chosen = (used or retrievals)[:1]
    return uuid.UUID(str(chosen[0]["entry_id"])) if chosen else None


def counterfactual_pairs(
    rows: list[Row], condition: str
) -> list[tuple[CounterfactualRun, CounterfactualRun]]:
    """Pair each session with the commons with the same session without it, as two runs.

    Same task, repeat, and model: `both` pairs with `context7`, `commons` with `none`. A pair
    whose session with the commons retrieved nothing has no entry, so no rows. Both rows share
    a task id that names the pair, so writing them again changes nothing.
    """
    sessions = {
        (_arm(row), str(row["scenario"]), int(row.get("repeat") or 0), str(row.get("model"))): row
        for row in rows
    }
    if duplicates := len(rows) - len(sessions):
        print(
            f"warning: {duplicates} ledger lines repeat an earlier line's arm, task, repeat and "
            "model; only the last of each is paired",
            file=sys.stderr,
        )
    pairs = []
    for (arm, task, repeat, model), sighted in sorted(sessions.items()):
        without = WITHOUT_COMMONS.get(arm)
        blind = sessions.get((str(without), task, repeat, model))
        entry = retrieved_entry(sighted)
        if blind is None or entry is None:
            continue
        name = "/".join([condition, task, str(repeat), model, f"{arm}-{without}"])
        task_id = uuid.uuid5(COUNTERFACTUAL_NAMESPACE, name)
        pairs.append(
            (
                _side(sighted, task_id, entry, with_entry=True),
                _side(blind, task_id, entry, with_entry=False),
            )
        )
    return pairs


def _side(row: Row, task_id: uuid.UUID, entry: uuid.UUID, *, with_entry: bool) -> CounterfactualRun:
    return CounterfactualRun(
        task_id=task_id,
        entry_id=entry,
        with_entry=with_entry,
        outcome="tests_passed" if row["tests_pass"] else "tests_failed",  # the harness's own run
        tokens_in=int(row.get("tokens_in") or 0),
        tokens_out=int(row.get("tokens_out") or 0),
        wall_clock_seconds=float(row.get("wall_clock_seconds") or 0.0),
    )


def counterfactuals(args: argparse.Namespace) -> int:
    """Write a condition's counterfactual pairs to the store's `counterfactual_runs`."""
    pairs = counterfactual_pairs(condition_rows(args.condition), args.condition)
    try:
        url = runner.open_store(runner.Store(args.store))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    with psycopg.connect(url) as conn:
        held = known_entries(conn, {first.entry_id for first, _second in pairs})
        written = [pair for pair in pairs if pair[0].entry_id in held]
        added = sum(record_run(conn, run) for pair in written for run in pair)
    print(f"{len(pairs)} pairs with a retrieval; {added} rows added to {args.store}")
    if skipped := len(pairs) - len(written):
        print(f"{skipped} pairs skipped: their entry is not in store {args.store}")
    return 0


# ---------------------------------------------------------------- compare (the arms record)
# Pre-registered in the Proposed arms record (docs/decisions/2026-11-07-*-github-task-arms.md):
# the margins of the Proposed kill-criterion record's option (a).
PRIMARY = ("both", "context7")  # Commons + Context7 against Context7 decides the verdict
SECONDARY = (("commons", "none"), ("context7", "none"))
PASS_MARGIN = 0.10  # continue on a pass rate at least 10 points higher...
TOKEN_MARGIN = 0.80  # ...or tokens per success at least 20% lower...
PASS_FLOOR = -0.05  # ...with a pass rate no more than 5 points lower
BOOTSTRAP_SEED = 20261107
BOOTSTRAP_RESAMPLES = 10_000
HERD = 2  # failed sessions of one task sharing a wrong patch, at least
Totals = tuple[int, int, int]  # one task in one arm: (sessions, passes, tokens)


@dataclass(frozen=True)
class Comparison:
    """One arm against another, paired by task, with 95% paired-bootstrap intervals."""

    tasks: int
    difference: float  # pass rate, treated minus control
    difference_low: float
    difference_high: float
    ratio: float  # tokens per success, treated over control
    ratio_low: float
    ratio_high: float


def _tokens(row: Row) -> int:
    """All of a session's tokens: input, cache creation, cache read, and output."""
    return int(row.get("tokens_in") or 0) + int(row.get("tokens_out") or 0)


def task_totals(rows: list[Row], arm: str) -> dict[str, Totals]:
    """Return each task's (sessions, passes, tokens) in one arm."""
    totals: dict[str, Totals] = {}
    for row in rows:
        if _arm(row) == arm:
            sessions, passes, tokens = totals.get(str(row["scenario"]), (0, 0, 0))
            totals[str(row["scenario"])] = (
                sessions + 1,
                passes + bool(row["tests_pass"]),
                tokens + _tokens(row),
            )
    return totals


def _per_success(tokens: int, passes: int) -> float:
    return tokens / passes if passes else math.inf


def _measures(pairs: list[tuple[Totals, Totals]]) -> tuple[float, float]:
    """Return (pass-rate difference, tokens-per-success ratio) of treated against control."""
    treated = [sum(column) for column in zip(*(pair[0] for pair in pairs), strict=True)]
    control = [sum(column) for column in zip(*(pair[1] for pair in pairs), strict=True)]
    difference = treated[1] / treated[0] - control[1] / control[0]
    mine, theirs = _per_success(treated[2], treated[1]), _per_success(control[2], control[1])
    if mine == theirs:  # also when neither arm passed anything: no difference to see
        return difference, 1.0
    if theirs in {0.0, math.inf}:
        return difference, math.inf if theirs == 0.0 else 0.0
    return difference, mine / theirs


def bootstrap_draws(seed: int, resample: int, count: int) -> list[int]:
    """Return `count` indices in [0, count), drawn with replacement, fixed by (seed, resample)."""
    digest = hashlib.shake_256(f"{seed}:{resample}".encode()).digest(4 * count)
    return [int.from_bytes(digest[4 * i : 4 * i + 4], "big") % count for i in range(count)]


def _percentile(ordered: list[float], share: float) -> float:
    return ordered[round(share * (len(ordered) - 1))]


def compare_arms(
    rows: list[Row],
    treated: str,
    control: str,
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Comparison | None:
    """Compare two arms over the tasks both ran, or return None if they share none.

    The interval is a paired bootstrap over tasks: each resample draws tasks with replacement,
    keeping a task's sessions in both arms together, and recomputes both measures.
    """
    mine, theirs = task_totals(rows, treated), task_totals(rows, control)
    pairs = [(mine[task], theirs[task]) for task in sorted(mine.keys() & theirs.keys())]
    if not pairs:
        return None
    difference, ratio = _measures(pairs)
    samples = [
        _measures([pairs[i] for i in bootstrap_draws(seed, resample, len(pairs))])
        for resample in range(resamples)
    ]
    differences = sorted(sample[0] for sample in samples)
    ratios = sorted(sample[1] for sample in samples)
    return Comparison(
        tasks=len(pairs),
        difference=difference,
        difference_low=_percentile(differences, 0.025),
        difference_high=_percentile(differences, 0.975),
        ratio=ratio,
        ratio_low=_percentile(ratios, 0.025),
        ratio_high=_percentile(ratios, 0.975),
    )


def verdict(comparison: Comparison) -> str:
    """Return continue, kill, or inconclusive (the Proposed kill-criterion record, option (a)).

    Continue when the pass rate is at least PASS_MARGIN higher, or tokens per success at most
    TOKEN_MARGIN of the control's with a pass rate no lower than PASS_FLOOR, and the deciding
    measure's interval excludes no difference. Kill when the intervals rule both margins out.
    """
    c = comparison
    passes_more = c.difference >= PASS_MARGIN and c.difference_low > 0
    costs_less = c.ratio <= TOKEN_MARGIN and c.ratio_high < 1 and c.difference >= PASS_FLOOR
    if passes_more or costs_less:
        return "continue"
    no_pass_margin = c.difference_high < PASS_MARGIN
    no_token_margin = c.ratio_low > TOKEN_MARGIN or c.difference_high < PASS_FLOOR
    return "kill" if no_pass_margin and no_token_margin else "inconclusive"


def _used(row: Row) -> bool:
    return any(r.get("used") in USED for r in row.get("retrievals") or [])


def failed_but_used(rows: list[Row]) -> int:
    """Count failed sessions that used an entry they retrieved."""
    return sum(not row["tests_pass"] and _used(row) for row in rows)


def herded(rows: list[Row]) -> int:
    """Count failed sessions whose patch is their task's most common wrong one.

    Only a patch at least two failed sessions of the task share counts; no patch never does.
    """
    wrong: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        # A line keeps the patch's excerpt; a line from before kept the whole patch.
        if not row["tests_pass"] and (row.get("patch_excerpt") or row.get("patch")):
            wrong[str(row["scenario"])][str(row.get("patch_hash"))] += 1
    tops = (counts.most_common(1)[0][1] for counts in wrong.values())
    return sum(top for top in tops if top >= HERD)


def arm_line(arm: str, model: str, rows: list[Row]) -> str:
    """One arm and model: sessions, pass rate, tokens, wall clock, and the commons counts."""
    passes = sum(bool(row["tests_pass"]) for row in rows)
    per_success = _per_success(sum(_tokens(row) for row in rows), passes)
    median_tokens = statistics.median(_tokens(row) for row in rows)
    median_seconds = statistics.median(float(row.get("wall_clock_seconds") or 0) for row in rows)
    line = (
        f"{arm:<9} {model[:16]:<16} {len(rows):>4} {_with_interval(passes, len(rows)):<18} "
        f"{per_success:>12,.0f} {median_tokens:>11,.0f} {median_seconds:>9.0f}"
    )
    if not runner.has_commons(arm):
        return line
    used = sum(_used(row) for row in rows)
    return (
        f"{line}  {_rate(rows, 'searched'):>7} {_rate(rows, 'found'):>7} "
        f"{used:>3}/{len(rows):<3} {_rate(rows, 'proposed'):>7}"
    )


def _comparison_lines(label: str, comparison: Comparison | None) -> list[str]:
    if comparison is None:
        return [f"{label}: no task ran in both arms"]
    c = comparison
    return [
        f"{label} over {c.tasks} tasks",
        (
            f"  pass-rate difference {c.difference * 100:+.1f} points "
            f"(95% {c.difference_low * 100:+.1f} to {c.difference_high * 100:+.1f})"
        ),
        f"  tokens per success   {c.ratio:.2f}x (95% {c.ratio_low:.2f}x to {c.ratio_high:.2f}x)",
    ]


def compare(args: argparse.Namespace) -> int:
    """Print the arms report of one condition: per arm, the comparisons, verdict, and herding.

    Each grader version gets a report of its own, verdict included, and none is pooled.
    """
    rows = condition_rows(args.condition)
    if not rows:
        print(f"no trials for condition {args.condition}")
        return 0
    groups = by_grader(rows)
    if len(groups) > 1:
        versions = ", ".join(f"v{version}" for version in groups)
        print(
            f"{args.condition} holds results of graders {versions}: each is reported on its "
            "own, with its own verdict, and never pooled"
        )
    for version, graded in groups.items():
        _compare_one(args.condition, version, graded)
    return 0


def _compare_one(condition: str, version: int, rows: list[Row]) -> None:
    """Print the arms report of one condition's results of one grader version."""
    print(f"== {condition}: {len(rows)} sessions, grader v{version}")
    print(
        f"{'arm':<9} {'model':<16} {'n':>4} {'pass (95%)':<18} {'tok/success':>12} "
        f"{'median tok':>11} {'median s':>9}  {'search':>7} {'found':>7} {'used':>7} "
        f"{'propose':>7}"
    )
    groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in rows:
        groups[(_arm(row), str(row.get("model") or ""))].append(row)
    for (arm, model), group in sorted(groups.items()):
        print(arm_line(arm, model, group))
    print(
        f"\npaired bootstrap over tasks: {BOOTSTRAP_RESAMPLES:,} resamples, seed {BOOTSTRAP_SEED}"
    )
    primary = compare_arms(rows, *PRIMARY)
    for line in _comparison_lines(f"primary: {PRIMARY[0]} vs {PRIMARY[1]}", primary):
        print(line)
    print(f"verdict: {verdict(primary) if primary else 'inconclusive (nothing to compare)'}")
    for treated, control in SECONDARY:
        label = f"secondary: {treated} vs {control}"
        for line in _comparison_lines(label, compare_arms(rows, treated, control)):
            print(line)
    print("\nherding")
    for line in herding_lines(rows):
        print(line)


def herding_lines(rows: list[Row]) -> list[str]:
    """Per arm: failed sessions, those that used a retrieved entry, those sharing a wrong patch."""
    lines = []
    for arm in sorted({_arm(row) for row in rows}):
        mine = [row for row in rows if _arm(row) == arm]
        failed = sum(not row["tests_pass"] for row in mine)
        used = f"; used a retrieved entry {failed_but_used(mine)}/{failed}"
        share = herded(mine) / failed if failed else 0.0
        lines.append(
            f"  {arm:<9} {failed} failed{used if runner.has_commons(arm) else ''}; "
            f"share their task's top wrong patch {herded(mine)}/{failed} ({share:.0%})"
        )
    return lines


def arms_options(parser: argparse.ArgumentParser) -> None:
    """Add the `arms` command's options."""
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS))
    parser.add_argument(
        "--arm", action="append", choices=runner.ARMS, help="Run these arms (default: all four)."
    )
    parser.add_argument("--runs", type=int, default=2, help="Repeats per task and arm.")
    parser.add_argument("--model", action="append", help="Run every arm on each (default: sonnet).")
    parser.add_argument("--condition", default="arms")
    parser.add_argument("--no-hooks", action="store_true")
    parser.add_argument(
        "--resume", action="store_true", help="Skip sessions that already have a clean result."
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--store",
        help="Build on this local experiment store: each session in its own clone of it.",
    )
    parser.add_argument(
        "--seed-wave",
        action="store_true",
        help="Write every session into the store itself (creating it if needed).",
    )


def clear_options(parser: argparse.ArgumentParser) -> None:
    """Add the `clear` command's options: which session, and why."""
    parser.add_argument("session", help="The transcript's stem: <condition>-<task>-<index>.")
    parser.add_argument("--reason", required=True, help="Why it is no leak.")


def main() -> int:
    """Dispatch to check, run, or report."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument(
        "--on-host",
        action="store_true",
        help="Run GitHub tasks' install and tests on this machine, as you, not in containers.",
    )
    run_parser = commands.add_parser("run")
    run_parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS))
    run_parser.add_argument("--runs", type=int, default=1)
    run_parser.add_argument("--model", default="sonnet")
    run_parser.add_argument("--condition", default="baseline")
    run_parser.add_argument("--no-hooks", action="store_true")
    run_parser.add_argument(
        "--skill",
        action="store_true",
        help="Load client/plugin's skill (--plugin-dir), without the plugin's duplicate hooks.",
    )
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
    search_parser.add_argument(
        "--model", action="append", help="Run every config on each (default: sonnet)."
    )
    search_parser.add_argument("--condition", default="wording")
    search_parser.add_argument("--no-hooks", action="store_true")
    search_parser.add_argument(
        "--resume", action="store_true", help="Skip sessions that already have a clean result."
    )
    search_parser.add_argument("--concurrency", type=int, default=4)
    arms_options(
        commands.add_parser(
            "arms", help="Each task with Commons and Context7, Context7, Commons, and neither."
        )
    )
    counterfactuals_parser = commands.add_parser(
        "counterfactuals", help="Write a condition's arm pairs to the store's counterfactual_runs."
    )
    counterfactuals_parser.add_argument("--condition", required=True)
    counterfactuals_parser.add_argument("--store", required=True)
    compare_parser = commands.add_parser(
        "compare", help="The arms report: per arm, paired comparisons, verdict, herding."
    )
    compare_parser.add_argument("--condition", required=True)
    effects_parser = commands.add_parser("effects", help="Pool a wording search by option.")
    effects_parser.add_argument("--condition", default="wording")
    report_parser = commands.add_parser("report")
    report_parser.add_argument("--condition", action="append")
    commands.add_parser(
        "trials", help="Summarize docs/eval/trials.jsonl per variant and text hash."
    )
    commands.add_parser(
        "flags", help="Scan saved GitHub-task transcripts for leaks and print the report."
    ).add_argument("directory", help="A results directory, e.g. .scratch/eval/<condition>.")
    clear_options(
        commands.add_parser(
            "clear", help="Count a session flagged as a leak again, after reading its transcript."
        )
    )
    args = parser.parse_args()
    commands_by_name: dict[str, Callable[[argparse.Namespace], int]] = {
        "check": lambda args: check(on_host=args.on_host),
        "run": run,
        "trials": lambda _args: by_variant(),
        "search": wording_search,
        "arms": arms,
        "counterfactuals": counterfactuals,
        "compare": compare,
        "effects": effects,
        "report": report,
        "flags": flags,
        "clear": clear,
    }
    return commands_by_name[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

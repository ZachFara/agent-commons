"""Entry point for the `commons` command."""

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from commons.cli import onboarding
from commons.cli.hooks import HANDLERS, run_hook
from commons.domain.errors import (
    InvalidInputError,
    NotFoundError,
    NotInitializedError,
    StoreUnavailableError,
)
from commons.domain.reporters import PRIVACY_DEFAULTS, PrivacyDefault
from commons.domain.reviews import REVIEW_DECISIONS, ReviewDecision, ReviewRequest
from commons.domain.search import MAX_LIMIT, SearchRequest
from commons.service import doctor, keys, ranking, retention, reviews, seeds
from commons.service import search as search_service
from commons.service import status as status_service
from commons.service.hook_client import RemoteBackend, remote_client
from commons.service.hook_install import default_settings_path, install_hooks
from commons.service.identity import init_reporter, save_server
from commons.store import insights
from commons.store.connection import connect
from commons.store.migrate import apply_migrations


def _migrate(_args: argparse.Namespace) -> int:
    with connect() as conn:
        applied = apply_migrations(conn)
    for name in applied:
        sys.stdout.write(f"applied {name}\n")
    if not applied:
        sys.stdout.write("database is up to date\n")
    return 0


def _init_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pseudonym", required=True, help="Handle to report under. No email.")
    parser.add_argument(
        "--privacy",
        choices=PRIVACY_DEFAULTS,
        default="entry",
        help="Default privacy mode (DESIGN.md section 6). Default: entry.",
    )


def _init(args: argparse.Namespace) -> int:
    pseudonym: str = args.pseudonym
    privacy = cast("PrivacyDefault", args.privacy)
    with connect() as conn:
        result = init_reporter(conn, pseudonym, privacy)
        added = seeds.load_seeds(conn)
    sys.stdout.write(
        f"reporter {pseudonym} ready (privacy default: {privacy}); "
        f"identity written to {result.config_path}\n"
    )
    if added:
        sys.stdout.write(f"added {added} seed entries\n")
    return 0


def _review_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("entry_id", type=UUID, help="Entry to review.")
    parser.add_argument("decision", choices=REVIEW_DECISIONS)
    parser.add_argument("--reason", help="Why. Required when rejecting.")


def _review(args: argparse.Namespace) -> int:
    request = ReviewRequest(
        entry_id=args.entry_id,
        decision=cast("ReviewDecision", args.decision),
        reason=args.reason,
        source="human",
    )
    with connect() as conn:
        result = reviews.review(conn, request)
    sys.stdout.write(f"entry {request.entry_id}: {result.status} (human reviewed)\n")
    return 0


def _pending(_args: argparse.Namespace) -> int:
    with connect() as conn:
        entries = reviews.pending(conn)
    for entry in entries:
        sys.stdout.write(
            f"{entry.entry_id}  {entry.status:<8}  {entry.created_at:%Y-%m-%d}  "
            f"{entry.proposed_by_model}\n  symptom: {entry.symptom}\n  cause:   {entry.cause}\n"
            f"  fix:     {entry.fix}\n\n"
        )
    sys.stdout.write(f"{len(entries)} entries awaiting human review\n")
    return 0


def _status(_args: argparse.Namespace) -> int:
    with connect() as conn:
        result = status_service.status(conn)
    counts = ", ".join(f"{count} {status}" for status, count in result.entries_by_status.items())
    lines = [
        f"entries: {counts}",
        f"awaiting human review: {result.awaiting_human_review}",
        (
            f"last 30 days: {result.retrievals_30d} retrievals in {result.tasks_30d} tasks, "
            f"{result.outcomes_30d} outcomes"
        ),
        "top entries by success rate:" if result.top_entries else "top entries: none yet",
    ]
    lines += [
        f"  {top.success_rate():.0%} of {top.used_outcomes}  {top.entry_id}  {top.symptom[:60]}"
        for top in result.top_entries
    ]
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _admin_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("action", choices=["create-key", "invite"])
    parser.add_argument("pseudonym", help="Reporter to mint a key for (created if new).")
    parser.add_argument("--admin", action="store_true", help="May use the hosted dashboard.")
    parser.add_argument("--server", help="Server URL to put in the invite (invite only).")


def _admin(args: argparse.Namespace) -> int:
    pseudonym: str = args.pseudonym
    if args.action == "invite" and not args.server:
        msg = "invite needs --server, the URL your friend will connect to."
        raise InvalidInputError(msg)
    with connect() as conn:
        key = keys.create_key(conn, pseudonym, admin=args.admin)
    if args.action == "invite":
        sys.stdout.write(
            f"Send this to {pseudonym} privately (it contains their key; any older key stops "
            f"working):\n\n{onboarding.invite_message(args.server, key)}\n"
        )
        return 0
    role = "admin" if args.admin else "reporter"
    sys.stdout.write(
        f"API key for {pseudonym} ({role}). It is shown only once; any older key stops working:"
        f"\n{key}\n"
    )
    return 0


def _connect_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--server", required=True, help="Hosted server URL, e.g. https://host")
    parser.add_argument("--key", required=True, help="API key from the server's admin.")


def _connect(args: argparse.Namespace) -> int:
    url: str = args.server
    key: str = args.key
    with remote_client(url, key) as client:
        RemoteBackend(client).ping()
    path = save_server(url, key)
    sys.stdout.write(f"hooks will report to {url}; settings saved in {path}\n")
    return 0


_MARKS = {"ok": "✓", "warn": "!", "fail": "✗"}


def _doctor(_args: argparse.Namespace) -> int:
    def ping(url: str, key: str) -> None:
        with remote_client(url, key) as client:
            RemoteBackend(client).ping()

    checks = doctor.run_checks(connect, ping)
    for check in checks:
        sys.stdout.write(f"{_MARKS[check.status]} {check.name:<11} {check.detail}\n")
    failed = sum(check.status == "fail" for check in checks)
    sys.stdout.write("all good\n" if not failed else f"{failed} problem(s) to fix\n")
    return 1 if failed else 0


def _search_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query", help="Error text or problem, as an agent would search it.")
    parser.add_argument("--language", help="Filter by language, e.g. python.")
    parser.add_argument("--limit", type=int, default=5, help=f"1-{MAX_LIMIT} results.")


def _search(args: argparse.Namespace) -> int:
    environment = {"language": args.language} if args.language else None
    request = SearchRequest(args.query, UUID(int=0), environment, args.limit)
    with connect() as conn:
        hits = search_service.preview(conn, request)
    for rank, hit in enumerate(hits, start=1):
        sys.stdout.write(
            f"{rank}. {hit.symptom}\n   fix: {hit.fix}\n   {hit.provenance()}"
            f" · score {hit.score:.2f} · {hit.entry_id}\n"
        )
    sys.stdout.write(f"{len(hits)} result(s); preview only, no retrieval logged\n")
    return 0


def _show_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("entry_id", type=UUID)


def _show(args: argparse.Namespace) -> int:
    with connect() as conn:
        entry = insights.entry(conn, args.entry_id)
    if entry is None:
        msg = f"No entry {args.entry_id}."
        raise NotFoundError(msg)
    reviewed = "human-reviewed" if entry["human_reviewed"] else "not human-reviewed"
    score = "none yet" if entry["score"] is None else f"{entry['score']:.2f}"
    lines = [
        entry["symptom"],
        f"  status:      {entry['status']} ({reviewed})",
        (
            f"  proposed:    {entry['created_at']:%Y-%m-%d} by {entry['proposed_by']}"
            f" ({entry['model']})"
        ),
        f"  score:       {score}",
        f"  cause:       {entry['cause']}",
        f"  fix:         {entry['fix']}",
        f"  environment: {entry['environment']}",
        f"  domains:     {', '.join(entry['domains'])}",
    ]
    if entry["repro"]:
        lines.insert(6, f"  repro:       {entry['repro']}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _rank(_args: argparse.Namespace) -> int:
    with connect() as conn:
        count = ranking.rank(conn)
    sys.stdout.write(f"ranked {count} approved entries\n")
    return 0


def _retain(_args: argparse.Namespace) -> int:
    with connect() as conn:
        folded = retention.apply_retention(conn)
    sys.stdout.write(f"folded {folded} searches older than 180 days into monthly counts\n")
    return 0


def _hook_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("event", choices=sorted(HANDLERS), help="Hook event; payload on stdin.")


def _hook(args: argparse.Namespace) -> int:
    event: str = args.event
    return run_hook(event, sys.stdin, sys.stdout)


def _install_hooks_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--settings", type=Path, default=None, help="Claude Code settings.json to update."
    )


def _install_hooks(args: argparse.Namespace) -> int:
    path: Path = args.settings or default_settings_path()
    result = install_hooks(path)
    if not result.changed:
        sys.stdout.write(f"hooks already installed in {path}\n")
        return 0
    sys.stdout.write(f"hooks installed in {path}\n")
    if result.backup_path:
        sys.stdout.write(f"previous settings saved to {result.backup_path}\n")
    return 0


def _no_arguments(_parser: argparse.ArgumentParser) -> None:
    pass


@dataclass(frozen=True)
class Command:
    """One `commons` subcommand."""

    help: str
    handler: Callable[[argparse.Namespace], int]
    arguments: Callable[[argparse.ArgumentParser], None] = _no_arguments


COMMANDS: dict[str, Command] = {
    "migrate": Command("Apply pending database migrations.", _migrate),
    "init": Command("Create or reuse your reporter identity.", _init, _init_arguments),
    "pending": Command("List entries awaiting human review.", _pending),
    "review": Command("Approve or reject an entry as a human.", _review, _review_arguments),
    "status": Command("Show store statistics.", _status),
    "join": Command(
        "Join a hosted server: check the key, save it, install hooks.",
        onboarding.join,
        onboarding.join_arguments,
    ),
    "connect": Command("Send hook observations to a hosted server.", _connect, _connect_arguments),
    "admin": Command("Hosted mode administration.", _admin, _admin_arguments),
    "doctor": Command("Check this machine's setup and say what to fix.", _doctor),
    "search": Command("Preview search results (no retrieval logged).", _search, _search_arguments),
    "show": Command("Show one entry.", _show, _show_arguments),
    "rank": Command("Write ranking snapshots (runs daily on its own).", _rank),
    "retain": Command("Apply data retention (runs nightly in production).", _retain),
    "hook": Command("Run a Claude Code hook (used by hooks.json).", _hook, _hook_arguments),
    "install-hooks": Command(
        "Add Agent Commons hooks to Claude Code settings.", _install_hooks, _install_hooks_arguments
    ),
}


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one subcommand per entry in COMMANDS."""
    parser = argparse.ArgumentParser(prog="commons", description="Agent Commons CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, command in COMMANDS.items():
        command.arguments(subparsers.add_parser(name, help=command.help))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit code. Expected errors exit 1 with a message."""
    args = build_parser().parse_args(argv)
    name: str = args.command
    try:
        return COMMANDS[name].handler(args)
    except (
        InvalidInputError,
        NotFoundError,
        NotInitializedError,
        StoreUnavailableError,
    ) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

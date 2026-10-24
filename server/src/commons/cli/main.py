"""Entry point for the `commons` command."""

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from commons.domain.errors import InvalidInputError, NotFoundError, NotInitializedError
from commons.domain.reporters import PRIVACY_DEFAULTS, PrivacyDefault
from commons.domain.reviews import REVIEW_DECISIONS, ReviewDecision, ReviewRequest
from commons.service import reviews
from commons.service import status as status_service
from commons.service.identity import init_reporter
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
    sys.stdout.write(
        f"reporter {pseudonym} ready (privacy default: {privacy}); "
        f"identity written to {result.config_path}\n"
    )
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
    except (InvalidInputError, NotFoundError, NotInitializedError) as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

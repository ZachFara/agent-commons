"""Entry point for the `commons` command."""

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from commons.domain.errors import InvalidInputError
from commons.domain.reporters import PRIVACY_DEFAULTS, PrivacyDefault
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
}


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one subcommand per entry in COMMANDS."""
    parser = argparse.ArgumentParser(prog="commons", description="Agent Commons CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, command in COMMANDS.items():
        command.arguments(subparsers.add_parser(name, help=command.help))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit code. Invalid input exits 1 with a message."""
    args = build_parser().parse_args(argv)
    name: str = args.command
    try:
        return COMMANDS[name].handler(args)
    except InvalidInputError as error:
        sys.stderr.write(f"error: {error}\n")
        return 1

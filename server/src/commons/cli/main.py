"""Entry point for the `commons` command."""

import argparse
import sys
from collections.abc import Callable, Sequence

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


COMMANDS: dict[str, tuple[str, Callable[[argparse.Namespace], int]]] = {
    "migrate": ("Apply pending database migrations.", _migrate),
}


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one subcommand per entry in COMMANDS."""
    parser = argparse.ArgumentParser(prog="commons", description="Agent Commons CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, (help_text, _handler) in COMMANDS.items():
        subparsers.add_parser(name, help=help_text)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit code."""
    args = build_parser().parse_args(argv)
    command: str = args.command
    return COMMANDS[command][1](args)

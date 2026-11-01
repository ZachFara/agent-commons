"""`commons join` and `commons admin invite`: getting a friend onto a hosted server.

The invite is a message to paste into Claude Code (or a terminal): install the CLI from GitHub,
join the server, register the MCP endpoint. Nothing needs a clone.
"""

import argparse
import os
import shlex
import sys
from pathlib import Path

from commons.domain.duplicates import DuplicateSettings
from commons.domain.errors import InvalidInputError, RateLimitedError
from commons.service.hook_client import RemoteBackend, remote_client
from commons.service.hook_install import default_settings_path, install_hooks
from commons.service.identity import hosted_server, save_server
from commons.service.local_state import active_sessions
from commons.store.connection import connect
from commons.store.entries import importable_entries

INSTALL_SOURCE = "git+https://github.com/ZachFara/agent-commons#subdirectory=server"
UV_DOCS = "https://docs.astral.sh/uv/getting-started/installation/"


def mcp_add_command(server: str, key: str) -> str:
    """Return the `claude mcp add` command that registers a hosted server everywhere."""
    url = server.rstrip("/") + "/mcp"
    header = shlex.quote(f"Authorization: Bearer {key}")
    return f"claude mcp add --scope user --transport http commons {url} --header {header}"


def invite_message(server: str, key: str) -> str:
    """Return the message an invitee pastes into Claude Code to set everything up."""
    server = server.rstrip("/")
    return (
        "Set up Agent Commons for me. Run these three commands in order, show me the output, "
        "and then tell me to restart Claude Code:\n\n"
        f"uv tool install {shlex.quote(INSTALL_SOURCE)}\n"
        f"commons join --server {server} --key {key}\n"
        f"{mcp_add_command(server, key)}\n\n"
        f"If `uv` isn't installed, install it first ({UV_DOCS}). "
        "Treat the key like a password."
    )


def join_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments for `commons join`."""
    parser.add_argument("--server", required=True, help="Hosted server URL.")
    parser.add_argument("--key", required=True, help="Your API key from the server's admin.")
    parser.add_argument("--settings", type=Path, help="Claude Code settings.json to update.")


def join(args: argparse.Namespace) -> int:
    """Check the key, save the server, and install hooks that report to it."""
    server: str = args.server.rstrip("/")
    key: str = args.key
    with remote_client(server, key) as client:
        scope = RemoteBackend(client).ping()
    if scope == "admin":
        msg = (
            "This is an admin key, meant for the dashboard. Agent sessions should use an agent "
            "key (decision 0032): `commons admin key create <you> --scope agent`."
        )
        raise InvalidInputError(msg)
    stale = [store for store in active_sessions() if store != server]
    config = save_server(server, key)
    settings: Path = args.settings or default_settings_path()
    install_hooks(settings)
    sys.stdout.write(
        f"joined {server}\n"
        f"  identity saved in {config}\n"
        f"  hooks installed in {settings}\n\n"
        "Last step, if you haven't already: register the MCP server, then restart Claude Code:\n"
        f"  {mcp_add_command(server, key)}\n"
    )
    if stale:
        sys.stdout.write(
            f"\n{len(stale)} session(s) were active in the last hour. Restart them: until "
            "then they keep using the old store (decision 0031).\n"
        )
    return 0


def import_arguments(parser: argparse.ArgumentParser) -> None:
    """Arguments for `commons import`."""
    parser.add_argument("--from-local", action="store_true", required=True)
    parser.add_argument("--status", default="approved", help="approved, pending, or both: a,p")
    parser.add_argument("--dry-run", action="store_true", help="List; send nothing.")
    parser.add_argument("--yes", action="store_true", help="Send everything listed.")


def import_local(args: argparse.Namespace) -> int:
    """Re-propose local entries on the hosted server this machine joined (decision 0031)."""
    server = hosted_server()
    if server is None:
        msg = "This machine isn't joined to a hosted server; run `commons join` first."
        raise InvalidInputError(msg)
    statuses = ["approved", "pending"] if args.status in {"both", "a,p"} else [args.status]
    threshold = DuplicateSettings.from_environment(os.environ).threshold
    with connect() as conn:
        local = importable_entries(conn, statuses)
    with remote_client(*server) as client:
        backend = RemoteBackend(client)
        candidates = []
        for entry in local:
            best = backend.similar(str(entry["symptom"]))
            if best and best[0][1] >= threshold:
                sys.stdout.write(f"already there: {entry['symptom']!s:.70} -> {best[0][0]}\n")
                continue
            candidates.append(entry)
        sys.stdout.write(f"{len(candidates)} local entr(ies) not on {server[0]}\n")
        sent = 0
        for entry in candidates:
            sys.stdout.write(f"\n  {entry['symptom']}\n  fix: {entry['fix']}\n")
            if args.dry_run:
                continue
            if not args.yes and input("  send it? [y/N] ").strip().lower() != "y":
                continue
            try:
                new = backend.import_entry(_entry_json(entry), _origin(entry))
            except RateLimitedError:
                sys.stdout.write(f"\n{sent} sent; run it again later for the rest\n")
                raise
            sent += 1
            sys.stdout.write(f"  sent as {new} (pending review there)\n")
    sys.stdout.write(f"\n{sent} sent\n" if not args.dry_run else "\ndry run: nothing sent\n")
    return 0


def _entry_json(entry: dict[str, object]) -> dict[str, object]:
    keys = ("symptom", "environment", "cause", "fix", "repro", "domains")
    return {k: entry[k] for k in keys}


def _origin(entry: dict[str, object]) -> dict[str, object]:
    created = entry["created_at"]
    return {
        "local_entry_id": str(entry["id"]),
        "model": str(entry["model"]),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }

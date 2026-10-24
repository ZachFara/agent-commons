"""`commons join` and `commons admin invite`: getting a friend onto a hosted server.

The invite is a message to paste into Claude Code (or a terminal): install the CLI from GitHub,
join the server, register the MCP endpoint. Nothing needs a clone.
"""

import argparse
import shlex
import sys
from pathlib import Path

from commons.service.hook_client import RemoteBackend, remote_client
from commons.service.hook_install import default_settings_path, install_hooks
from commons.service.identity import save_server

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
        RemoteBackend(client).ping()
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
    return 0

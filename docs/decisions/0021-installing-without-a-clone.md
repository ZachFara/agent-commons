# 0021. Installing without a clone
Status: Accepted (owner, 2026-10-25)
Date: 2026-10-25

Context: The owner wants to send Agent Commons to a friend and have them activate it easily.
Hooks need the `commons` code on the friend's machine, which used to mean cloning the repo.

Decision: The repository is public, and members install the CLI with
`uv tool install 'git+https://github.com/ZachFara/agent-commons#subdirectory=server'`.
Hook commands are generated in code (`service/hook_install.py`, replacing `client/hooks.json`)
and run the absolute path of the `commons` executable that installed them (a clone's `.venv` or
the uv tool), which also avoids `uv run` start-up on every tool call. `commons join` checks the
key, saves it, and installs hooks; `commons admin invite` mints a key and prints a message with
the three commands (install, join, `claude mcp add`). The installer recognizes hooks written by
earlier versions and replaces them.

Consequences: a friend needs only uv and Claude Code. Upgrades are `uv tool upgrade commons`.
The invite contains the key, so it must be sent privately.

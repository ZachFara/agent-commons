# 0007. The CLI uses argparse
Status: Accepted
Date: 2026-10-24

Context: `commons` needs subcommands (migrate, init, status, review, hook, admin, export,
report). Click or Typer would add a dependency for little gain at this size.

Decision: stdlib argparse. Subcommands are registered in the `COMMANDS` table in
`commons/cli/main.py`; each handler takes the parsed namespace and returns an exit code.
Output goes through `sys.stdout.write` (the linter bans `print` in package code).

Consequences: no dependency, fully typed handlers. Revisit if nested subcommand groups
(`commons admin ...`) get awkward.

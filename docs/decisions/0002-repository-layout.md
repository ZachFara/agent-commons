# 0002. Repository layout and package boundaries
Status: Accepted
Date: 2026-10-24

Context: DESIGN.md §2 puts the server in `server/`, Postgres config in `db/`, and the Claude Code
integration in `client/`, and INSTALL.md runs `uv run --directory <path>/server commons-mcp`.
The checks need one shared configuration across the repo.

Decision:
- The root `pyproject.toml` is a non-package uv workspace that holds dev tooling and all check
  configuration. `server/` is the single workspace member: the `commons` package in
  `server/src/commons`, tests in `server/tests`. One lockfile at the root.
- Inside `commons`, code is split into layers (higher may import lower, never the reverse):
  1. `mcp`, `cli`: entrypoints. Thin, parse input and call `service`. Independent of each other.
  2. `service`: the use cases behind the tools in DESIGN.md §4.
  3. `store` (Postgres), `ranking` (scoring), `privacy` (secret scanning, redaction),
     `adapters` (cq). Independent of each other.
  4. `domain`: types and enums from DESIGN.md §3. Imports nothing else in `commons`.
- Only `store` may import psycopg; only `mcp` may import FastMCP; `domain`, `ranking`,
  `privacy`, `adapters` do no I/O.
- `tools/` holds repo tooling (the check scripts), not product code.
- Allowed top-level directories: `.claude-plugin`, `.github`, `client`, `db`, `docs`, `server`,
  `tools`. Anything else needs a new decision record.

Consequences: the ranking and privacy logic can be tested without a database. Adding a layer or
top-level directory means editing the import-linter contracts or `tools/check_structure.py`,
which shows up in review.

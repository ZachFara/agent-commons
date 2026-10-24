# 0001. Language and runtime stack
Status: Accepted
Date: 2026-10-24

Context: DESIGN.md fixes Python, FastMCP, Postgres 16 with pgvector, docker compose, and uv
(INSTALL.md uses `uv run`). The remaining choices are the Python version, the database driver,
and how migrations work.

Decision:
- Python 3.12 for development (`.python-version`), `requires-python >= 3.11` per DESIGN.md §9.
- uv for environments, locking (`uv.lock` committed), and running everything.
- psycopg 3 as the database driver, with hand-written SQL. No ORM: the schema uses Postgres
  enums, generated tsvector columns, jsonb, and later pgvector, which an ORM would mostly
  get in the way of.
- Migrations are numbered plain `.sql` files applied in order by `commons migrate`, tracked in
  a `schema_migrations` table. No Alembic until there is a reason.
- FastMCP for the MCP server (stdio locally, streamable HTTP when hosted).
- Tests that need a database run against a real Postgres (docker compose locally, a service
  container in CI), not mocks or SQLite.

Consequences: few dependencies and SQL that matches the schema in DESIGN.md one to one. We
write our own small migration runner; if migrations ever need downgrades or branching,
revisit Alembic.

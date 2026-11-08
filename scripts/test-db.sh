#!/usr/bin/env bash
# Run a command against a fresh database for this worktree, built from the migrations, and
# drop the database afterwards (also on failure or Ctrl-C).
#
#   scripts/test-db.sh uv run commons status
#   scripts/test-db.sh make check
#
# The database is created from COMMONS_TEST_DATABASE_URL (from .env.agent, else the local
# default), which must point at localhost: this never touches the live Commons database. The
# command sees COMMONS_DATABASE_URL set to the new database.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: scripts/test-db.sh <command> [args...]" >&2
  exit 64
fi

root="$(git rev-parse --show-toplevel)"
cd "$root"
if [ -f .env.agent ]; then
  set -a; . ./.env.agent; set +a
fi
admin="${COMMONS_TEST_DATABASE_URL:-postgresql://commons:commons@localhost:5432/commons}"
case "$admin" in
  *@localhost[:/]* | *@127.0.0.1[:/]*) ;;
  *) echo "test-db.sh: COMMONS_TEST_DATABASE_URL must be a localhost database" >&2; exit 1 ;;
esac

slug="$(basename "$root" | tr -c 'a-zA-Z0-9\n' '_' | tr 'A-Z' 'a-z' | cut -c1-24)"
hash="$(printf '%s' "$root" | shasum | cut -c1-8)"
name="commons_wt_${slug}_${hash}"

admin_sql() {
  ADMIN_URL="$admin" uv run --quiet python -c '
import os, sys, psycopg
from psycopg import sql
with psycopg.connect(os.environ["ADMIN_URL"], autocommit=True) as conn:
    conn.execute(sql.SQL(sys.argv[1]).format(sql.Identifier(sys.argv[2])))
' "$1" "$name"
}

admin_sql "DROP DATABASE IF EXISTS {} WITH (FORCE)"
admin_sql "CREATE DATABASE {}"
trap 'admin_sql "DROP DATABASE IF EXISTS {} WITH (FORCE)"' EXIT

url="$(ADMIN_URL="$admin" NAME="$name" uv run --quiet python -c '
import os
from psycopg.conninfo import make_conninfo
print(make_conninfo(os.environ["ADMIN_URL"], dbname=os.environ["NAME"]))
')"
export COMMONS_DATABASE_URL="$url"
uv run --quiet commons migrate >/dev/null
echo "test-db.sh: running on fresh database $name" >&2
"$@"

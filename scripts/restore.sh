#!/usr/bin/env bash
# Go back to a stable point taken by scripts/snapshot.sh: check out its tag (detached) and
# restore its dump into RESTORE_DATABASE_URL, then check every table's row count.
#
#   RESTORE_DATABASE_URL=postgresql://... scripts/restore.sh stable/20261107T221500Z
#
# The target database is overwritten (pg_restore --clean). There is no default target: this
# is Zach's tool, and agents never hold a URL that can write to the live Commons database.
set -euo pipefail

# One function, called on the last line: bash parses it all before the checkout below
# replaces this file with the tag's version.
main() {

  local tag="${1:?usage: RESTORE_DATABASE_URL=... scripts/restore.sh stable/<UTC time>}"
  target="${RESTORE_DATABASE_URL:?set RESTORE_DATABASE_URL to the database to overwrite}"
  root="$(git rev-parse --show-toplevel)"
  cd "$root"
  dir="${COMMONS_SNAPSHOT_DIR:-$HOME/.local/share/agent-commons/snapshots}"
  image="${PG_IMAGE:-postgres:18}"
  stamp="${tag#stable/}"
  dump="$dir/$stamp.dump"

  [ -f "$dump" ] || { echo "restore.sh: no dump for $tag at $dump" >&2; exit 1; }
  git rev-parse -q --verify "refs/tags/$tag" >/dev/null || { echo "restore.sh: no tag $tag" >&2; exit 1; }
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "restore.sh: the working tree has changes; commit or stash them first" >&2
    exit 1
  fi

  git checkout --quiet --detach "$tag"
  echo "restore.sh: checked out $tag ($(git rev-parse --short HEAD)); restoring $dump" >&2
  docker_url="$(printf '%s' "$target" | sed -E 's#@(localhost|127\.0\.0\.1)([:/])#@host.docker.internal\2#')"
  PGURL="$docker_url" docker run --rm -e PGURL -v "$dir:/out:ro" "$image" \
    sh -c "pg_restore --clean --if-exists --no-owner --no-privileges --exit-on-error -d \"\$PGURL\" /out/$stamp.dump"
  uv run --quiet python tools/backup_counts.py compare "$target" "$dir/$stamp.counts.json"
  echo "restore.sh: restored $tag; row counts match" >&2
}

main "$@"

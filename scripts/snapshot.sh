#!/usr/bin/env bash
# Take a stable point to return to: tag the current origin/main as stable/<UTC time> and dump
# the live Commons database next to it, outside the repo. The orchestrator runs this once at
# the start of every run; scripts/restore.sh <tag> goes back to it.
#
#   scripts/snapshot.sh            # prints the tag on stdout
#
# The dump uses the read-only `commons_backup` role (decision 0033; its URL is the Secret
# Manager secret agent-commons-production-backup-url), never a role that can write. Dumps go
# to $COMMONS_SNAPSHOT_DIR (default ~/.local/share/agent-commons/snapshots, owner-only and
# outside ~/Ark so plaintext production data isn't mirrored to backup drives).
#
# Testing: SNAPSHOT_DATABASE_URL replaces the live database with a localhost one.
set -euo pipefail

root="$(git rev-parse --show-toplevel)"
cd "$root"
dir="${COMMONS_SNAPSHOT_DIR:-$HOME/.local/share/agent-commons/snapshots}"
image="${PG_IMAGE:-postgres:18}" # client must be at least the server's version
umask 077
mkdir -p "$dir"

if [ -n "${SNAPSHOT_DATABASE_URL:-}" ]; then
  case "$SNAPSHOT_DATABASE_URL" in
    *@localhost[:/]* | *@127.0.0.1[:/]*) source="local test database" ;;
    *) echo "snapshot.sh: SNAPSHOT_DATABASE_URL is for localhost test databases only" >&2; exit 1 ;;
  esac
  url="$SNAPSHOT_DATABASE_URL"
else
  source="production (commons_backup, read-only)"
  url="$(gcloud secrets versions access latest --secret=agent-commons-production-backup-url \
    --project=agent-commons-512623)"
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tag="stable/$stamp"
git fetch --quiet origin main
commit="$(git rev-parse origin/main)"
git tag -a "$tag" "$commit" -m "Stable point before an agent run; dump: $dir/$stamp.dump"
trap 'git tag -d "$tag" >/dev/null; rm -f "$dir/$stamp".*' ERR

# pg_dump runs in Docker (no local client needed); inside it, localhost is the host machine.
docker_url="$(printf '%s' "$url" | sed -E 's#@(localhost|127\.0\.0\.1)([:/])#@host.docker.internal\2#')"
PGURL="$docker_url" docker run --rm -e PGURL -e PGOPTIONS="-c default_transaction_read_only=on" \
  -v "$dir:/out" "$image" \
  sh -c "pg_dump --format=custom --no-owner --no-privileges -d \"\$PGURL\" -f /out/$stamp.dump"
uv run --quiet python tools/backup_counts.py count "$url" > "$dir/$stamp.counts.json"
printf '{"tag": "%s", "commit": "%s", "source": "%s"}\n' "$tag" "$commit" "$source" \
  > "$dir/$stamp.json"

echo "snapshot.sh: $tag = $commit; dump $dir/$stamp.dump ($source)" >&2
echo "$tag"

#!/usr/bin/env bash
#
# Restore the MikroMan SQLite database from a backup produced by backup.sh.
#
# This is destructive: it replaces the live database. It therefore stops the
# container first (so nothing is mid-write), keeps a copy of the database it is
# about to overwrite, verifies the incoming file before committing to it, and
# clears any stale WAL sidecar files that would otherwise be replayed on top of
# the restored data.
#
# Usage:
#   scripts/restore.sh backups/app-20260831-030000.db
#
#   MIKROMAN_CONTAINER=mikroman scripts/restore.sh /mnt/nas/mikroman/app-....db

set -euo pipefail

CONTAINER="${MIKROMAN_CONTAINER:-mikroman}"
DB_PATH_IN_CONTAINER="${MIKROMAN_DB_PATH:-/data/app.db}"
SOURCE="${1:-}"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

[ -n "$SOURCE" ] || fail "usage: $0 <path-to-backup.db>"
[ -f "$SOURCE" ] || fail "backup file not found: $SOURCE"
command -v docker >/dev/null 2>&1 || fail "docker not found on PATH"
docker inspect "$CONTAINER" >/dev/null 2>&1 || fail "container '$CONTAINER' does not exist"

# Refuse a file that is not a sound SQLite database before touching anything.
if command -v sqlite3 >/dev/null 2>&1; then
    check="$(sqlite3 "$SOURCE" 'PRAGMA integrity_check;' 2>/dev/null || true)"
else
    check="$(python3 - "$SOURCE" <<'PY' 2>/dev/null || true
import sqlite3, sys
print(sqlite3.connect(sys.argv[1]).execute("PRAGMA integrity_check").fetchone()[0])
PY
)"
fi
[ "$check" = "ok" ] || fail "integrity check on '$SOURCE' did not return ok (got: ${check:-<none>})"

log "restoring '$SOURCE' into ${CONTAINER}:${DB_PATH_IN_CONTAINER}"

was_running=false
if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" = "true" ]; then
    was_running=true
    log "stopping container"
    docker stop "$CONTAINER" >/dev/null
fi

# Keep the database we are about to replace.
safety="/data/app.db.pre-restore-$(date +%Y%m%d-%H%M%S)"
docker run --rm --volumes-from "$CONTAINER" alpine sh -c "
    set -e
    if [ -f '${DB_PATH_IN_CONTAINER}' ]; then cp -a '${DB_PATH_IN_CONTAINER}' '${safety}'; fi
    # Stale -wal / -shm belong to the OLD database; replaying them onto the
    # restored file would corrupt it.
    rm -f '${DB_PATH_IN_CONTAINER}-wal' '${DB_PATH_IN_CONTAINER}-shm'
" || fail "could not stage the volume for restore"

docker cp "$SOURCE" "${CONTAINER}:${DB_PATH_IN_CONTAINER}" \
    || fail "docker cp of the backup into the container failed"

log "restored. previous database kept at ${CONTAINER}:${safety}"

if $was_running; then
    log "starting container"
    docker start "$CONTAINER" >/dev/null
    log "done - check the app came up: docker logs -f ${CONTAINER}"
else
    log "done - container was not running, left stopped"
fi

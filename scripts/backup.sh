#!/usr/bin/env bash
#
# Consistent, rotating backup of the MikroMan SQLite database.
#
# Everything MikroMan knows lives in one file inside the container's data
# volume: users, device inventory, router credentials, and - the part that
# cannot be reconstructed - the historical daily traffic rollups. The volume
# survives `docker compose down` and image rebuilds, but nothing protects it
# from `docker compose down -v`, `docker volume rm`, or a failed disk. This
# script is that protection.
#
# It uses SQLite's online-backup API (via the container's own Python), which
# copies a transactionally consistent snapshot while the application keeps
# running - no downtime, no torn file. The snapshot is integrity-checked before
# it is allowed to replace anything, written to a temp name and renamed
# atomically, then old copies beyond the retention count are pruned.
#
# Usage:
#   scripts/backup.sh                     # write one backup, prune old ones
#   MIKROMAN_BACKUP_DIR=/mnt/nas/mikroman scripts/backup.sh
#
# Cron (daily at 03:15, log to syslog):
#   15 3 * * * /path/to/mikroman/scripts/backup.sh >> /var/log/mikroman-backup.log 2>&1
#
# Restore: see the "Backup & restore" section of README.md.

set -euo pipefail

CONTAINER="${MIKROMAN_CONTAINER:-mikroman}"
DB_PATH_IN_CONTAINER="${MIKROMAN_DB_PATH:-/data/app.db}"
BACKUP_DIR="${MIKROMAN_BACKUP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backups}"
KEEP="${MIKROMAN_BACKUP_KEEP:-14}"

timestamp="$(date +%Y%m%d-%H%M%S)"
final="${BACKUP_DIR}/app-${timestamp}.db"
staging_in_container="/tmp/mikroman-backup-${timestamp}.db"

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || fail "docker not found on PATH"
docker inspect "$CONTAINER" >/dev/null 2>&1 || fail "container '$CONTAINER' does not exist"

if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" != "true" ]; then
    # A stopped container cannot run the online backup, but a cold copy of a
    # quiescent database is itself consistent - so fall back to that.
    log "container '$CONTAINER' is stopped; taking a cold copy instead"
    mkdir -p "$BACKUP_DIR"
    docker cp "${CONTAINER}:${DB_PATH_IN_CONTAINER}" "${final}.tmp" \
        || fail "cold copy failed"
    mv "${final}.tmp" "$final"
    log "wrote $final ($(du -h "$final" | cut -f1))"
else
    mkdir -p "$BACKUP_DIR"

    # Online backup from inside the container. sqlite3.connect(...).backup()
    # holds a read lock only briefly per step and yields a snapshot that is
    # consistent as of the moment it completes, even under concurrent writes.
    log "starting online backup of ${CONTAINER}:${DB_PATH_IN_CONTAINER}"
    # -i so the heredoc on stdin actually reaches `python -`; without it the
    # interpreter reads an empty script, does nothing, and exits 0.
    docker exec -i "$CONTAINER" python - "$DB_PATH_IN_CONTAINER" "$staging_in_container" <<'PY' \
        || fail "online backup command failed"
import sqlite3
import sys

source_path, dest_path = sys.argv[1], sys.argv[2]
source = sqlite3.connect(source_path)
dest = sqlite3.connect(dest_path)
with dest:
    source.backup(dest)
# Prove the copy is not just present but readable and structurally sound.
result = dest.execute("PRAGMA integrity_check").fetchone()[0]
source.close()
dest.close()
if result != "ok":
    sys.stderr.write(f"integrity_check returned: {result}\n")
    sys.exit(1)
PY

    docker cp "${CONTAINER}:${staging_in_container}" "${final}.tmp" \
        || fail "could not copy snapshot out of the container"
    docker exec "$CONTAINER" rm -f "$staging_in_container" || true
    mv "${final}.tmp" "$final"
    log "wrote $final ($(du -h "$final" | cut -f1))"
fi

# Retention: keep the newest $KEEP, delete the rest. Guard against an
# unset/zero KEEP wiping everything.
if [ "${KEEP}" -gt 0 ] 2>/dev/null; then
    mapfile -t stale < <(ls -1t "${BACKUP_DIR}"/app-*.db 2>/dev/null | tail -n "+$((KEEP + 1))")
    for old in "${stale[@]:-}"; do
        [ -n "$old" ] || continue
        rm -f -- "$old"
        log "pruned $old"
    done
fi

log "done; $(ls -1 "${BACKUP_DIR}"/app-*.db 2>/dev/null | wc -l) backup(s) retained in ${BACKUP_DIR}"

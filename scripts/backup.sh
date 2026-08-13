#!/usr/bin/env bash
# Back up the experiment database. The database IS the deliverable of a 30-day
# run; everything else is regenerable from the repository.
#
#   ./scripts/backup.sh                 -> ./backups/deltabt-<utc>.dump
#   BACKUP_DIR=/mnt/vol ./scripts/backup.sh
#
# Credentials come from $DATABASE_URL in the environment. Never commit them.
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL}"
DIR="${BACKUP_DIR:-./backups}"
KEEP="${BACKUP_KEEP:-30}"          # retention: one month of daily dumps
mkdir -p "$DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DIR/deltabt-$STAMP.dump"

pg_dump "$DATABASE_URL" --format=custom --no-owner --no-privileges --file="$OUT"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"

# Verify the dump is readable rather than assuming it. An unverified backup is
# a guess.
pg_restore --list "$OUT" >/dev/null
echo "verified: pg_restore can read it"

ls -1t "$DIR"/deltabt-*.dump 2>/dev/null | tail -n +$((KEEP+1)) | while read -r old; do
  echo "pruning $old"; rm -f "$old"
done

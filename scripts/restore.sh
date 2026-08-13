#!/usr/bin/env bash
# Restore a dump into a TARGET database. Refuses to touch a database that
# already holds an experiment unless FORCE=1 -- restoring over a live run
# would destroy the evidence it is meant to protect.
set -euo pipefail
DUMP="${1:?usage: $0 <dump-file> ; target from \$RESTORE_URL}"
: "${RESTORE_URL:?set RESTORE_URL to the TARGET database}"

EXISTING=$(psql "$RESTORE_URL" -tAc \
  "SELECT count(*) FROM forward_test" 2>/dev/null || echo 0)
if [ "${EXISTING:-0}" != "0" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "REFUSING: target already holds $EXISTING experiment(s)."
  echo "Restore into a fresh database, or re-run with FORCE=1 if you are certain."
  exit 1
fi

pg_restore --clean --if-exists --no-owner --no-privileges \
           --dbname "$RESTORE_URL" "$DUMP"
echo "restored $DUMP"
psql "$RESTORE_URL" -c \
  "SELECT experiment_id, status, config_hash, git_sha, started_at FROM forward_test;"

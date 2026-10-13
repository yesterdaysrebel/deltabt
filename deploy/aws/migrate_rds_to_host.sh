#!/usr/bin/env bash
# ONE-TIME COPY of a stack's database from RDS to the Postgres on its own host.
#
#   migrate_rds_to_host.sh <db_name> <rds_endpoint> <rds_master_secret_arn>
#
# Run on the bot host (via SSM; the exact command is in docs/db_on_host.md),
# AFTER the host has been replaced with db_location = "host" and the stack's
# empty database created, and BEFORE the bot starts there (the bot creates its
# schema on first connect, and this refuses to restore over tables).
#
# It never writes to RDS: pg_dump reads it. Owners and grants are not copied
# (--no-owner --no-privileges): on RDS the tables are owned by the master user
# and granted to the IAM role deltabt_app; on the host the bot logs in as the
# owner `deltabt`, so neither applies. Every public table's row count is then
# compared on both sides and the script fails on any difference.
set -euo pipefail
DB="${1:?usage: migrate_rds_to_host.sh <db_name> <rds_endpoint> <rds_master_secret_arn>}"
RDS_HOST="${2:?rds endpoint}"
RDS_SECRET="${3:?rds master secret arn}"
case "$DB" in [a-z_][a-z0-9_]*) ;; *) echo "refusing database name '$DB'" >&2; exit 2 ;; esac

# shellcheck disable=SC1091
source /opt/deltabt/env
[ "$DB_HOST" = "172.17.0.1" ] || { echo "this host does not run its own Postgres (DB_HOST=$DB_HOST)" >&2; exit 2; }
/opt/deltabt/run.sh --ensure-db

local_sql() { docker exec -i deltabt-pg psql -U deltabt -d "$DB" -v ON_ERROR_STOP=1 -tA "$@"; }
n="$(local_sql -c "select count(*) from information_schema.tables where table_schema = 'public'")"
[ "$n" = "0" ] || { echo "$DB on this host already has $n tables; refusing to overwrite" >&2; exit 3; }

S="$(aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$RDS_SECRET" --query SecretString --output text)"
RU="$(printf '%s' "$S" | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')"
RP="$(printf '%s' "$S" | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')"
unset S
remote() { docker run --rm -i -e PGPASSWORD="$RP" -e PGSSLMODE=require postgres:16 "$@"; }

echo "[migrate] dumping $DB from $RDS_HOST and restoring on this host"
remote pg_dump -h "$RDS_HOST" -U "$RU" -d "$DB" -Fc --no-owner --no-privileges \
  | docker exec -i deltabt-pg pg_restore -U deltabt -d "$DB" --no-owner --no-privileges --exit-on-error

echo "[migrate] comparing row counts, table by table"
tables="$(local_sql -c "select tablename from pg_tables where schemaname = 'public' order by 1")"
bad=0
for t in $tables; do
  a="$(remote psql -h "$RDS_HOST" -U "$RU" -d "$DB" -tA -c "select count(*) from \"$t\"")"
  b="$(local_sql -c "select count(*) from \"$t\"")"
  printf '  %-28s rds %8s  host %8s %s\n' "$t" "$a" "$b" "$([ "$a" = "$b" ] && echo ok || echo MISMATCH)"
  [ "$a" = "$b" ] || bad=1
done
[ "$bad" = 0 ] || { echo "[migrate] row counts differ; do NOT remove RDS" >&2; exit 4; }
echo "[migrate] $DB copied; every table's row count matches"

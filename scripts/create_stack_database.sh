#!/usr/bin/env bash
# Create the database for a second forward-test stack. Run ONCE, on a host that
# already has /opt/deltabt/env, via SSM.
#
# WHY THIS IS NOT IN TERRAFORM
#   The RDS instance is in a private subnet with no ingress from anywhere the
#   CI runner sits, so the postgresql Terraform provider cannot reach it. The
#   alternatives were a bastion, a NAT gateway, or a Lambda in the VPC -- all
#   of them permanent infrastructure existing to run one statement once.
#
# WHY NOT IN user-data
#   Creating a database is a privileged, shared-resource operation. Putting it
#   on the boot path means every replacement of every host re-runs it, and a
#   host that fails to reach RDS at boot would fail to bootstrap for a reason
#   unrelated to what it is for.
#
# The schema is NOT created here. The application runs schema.sql itself on
# first connect (PostgresRepository.migrate), so an empty database is all that
# is needed and the schema stays owned by exactly one thing.
#
# Usage:  create_stack_database.sh <dbname>
set -euo pipefail

DB_NEW="${1:?usage: create_stack_database.sh <dbname>}"

# Postgres has no CREATE DATABASE IF NOT EXISTS, and a bare CREATE against an
# existing database is an error rather than a no-op. Identifiers also cannot be
# parameterised, so the name is restricted to characters that need no quoting
# instead of being escaped.
case "$DB_NEW" in
  [a-z_][a-z0-9_]*) ;;
  *) echo "refusing '$DB_NEW': lowercase letters, digits and underscore only" >&2
     exit 2 ;;
esac

# shellcheck disable=SC1091
source /opt/deltabt/env

command -v psql >/dev/null 2>&1 || dnf -y install postgresql16 >/dev/null

SECRET="$(aws secretsmanager get-secret-value --region "$AWS_REGION" \
           --secret-id "$DB_SECRET_ARN" --query SecretString --output text)"
PGUSER="$(printf '%s' "$SECRET" | python3 -c 'import json,sys;print(json.load(sys.stdin)["username"])')"
PGPASSWORD="$(printf '%s' "$SECRET" | python3 -c 'import json,sys;print(json.load(sys.stdin)["password"])')"
unset SECRET
export PGPASSWORD

# NOT $DB_NAME. On the host that will USE the new database, /opt/deltabt/env
# already names it -- so connecting to $DB_NAME to create $DB_NEW means
# connecting to the database that does not exist yet:
#
#   FATAL:  database "deltabt_v2" does not exist
#
# The admin connection has to target something that certainly exists. `postgres`
# is created by RDS on every PostgreSQL instance and is never the application's
# database, so it cannot collide with a stack.
DB_ADMIN="${DB_ADMIN:-postgres}"

psql_admin() {
  psql --host "$DB_HOST" --port "$DB_PORT" --username "$PGUSER" \
       --dbname "$DB_ADMIN" --set ON_ERROR_STOP=1 --no-psqlrc --tuples-only "$@"
}

if [ -n "$(psql_admin -c "select 1 from pg_database where datname = '$DB_NEW'")" ]; then
  echo "database '$DB_NEW' already exists; nothing to do"
else
  psql_admin -c "CREATE DATABASE $DB_NEW"
  echo "created database '$DB_NEW'"
fi

# Prove it is reachable and empty, so a failure here is not discovered later as
# a bot that cannot start.
TABLES="$(psql --host "$DB_HOST" --port "$DB_PORT" --username "$PGUSER" \
            --dbname "$DB_NEW" --set ON_ERROR_STOP=1 --no-psqlrc --tuples-only \
            -c "select count(*) from information_schema.tables where table_schema = 'public'")"
echo "'$DB_NEW' is reachable; public tables: ${TABLES// /}"
echo "the bot creates the schema itself on first connect"

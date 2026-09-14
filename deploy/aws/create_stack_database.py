"""Create a stack's database and the bot's login role. Idempotent, creates only.

Terraform builds the RDS INSTANCE but not the databases inside it -- there is
no aws_db_instance sub-resource for that -- so a new stack's db_name exists in
the stacks map and nowhere on the server, and the bot dies on
InvalidCatalogNameError at first start.

Only CREATE DATABASE happens here. The schema is not applied: Repository.connect
calls migrate(), which runs schema.sql as CREATE TABLE IF NOT EXISTS on every
start, so the tables are the bot's own business and applying them from outside
would be a second copy of a definition that must not drift.

THIS IS THE ONE PLACE THE MASTER PASSWORD IS STILL USED, and it runs at
bootstrap rather than at every container start. It creates the role the bot
actually logs in as:

    CREATE ROLE deltabt_app LOGIN;
    GRANT rds_iam TO deltabt_app;

`rds_iam` makes Postgres accept a signed IAM token in place of a password. A
role holding that grant has NO password, so there is nothing for AWS to rotate
and nothing for the bot to cache and get wrong -- which is the failure this
replaces (see app/persistence/db_auth.py for what used to go wrong and when).

The role is deliberately NOT the database owner. It can create and write its
own tables, because migrate() must work, but it cannot drop the database that
holds the experiment record.
"""
import asyncio
import os
import sys
import urllib.parse

import asyncpg


def _safe_identifier(name: str, what: str) -> str:
    """Identifiers cannot be bound as parameters, so they are checked instead."""
    if not name or not name.replace("_", "").isalnum():
        sys.exit(f"refusing to use {what} named {name!r}")
    return name


async def ensure_app_role(con, role: str, target: str) -> None:
    """Create the IAM-auth login role and grant it what migrate() needs.

    Idempotent and safe to run for every stack: roles are cluster-wide, so the
    second stack finds the role already there and only re-applies grants, which
    Postgres treats as a no-op.
    """
    exists = await con.fetchval(
        "select 1 from pg_roles where rolname = $1", role)
    if exists:
        print(f"role {role!r} already exists")
    else:
        # LOGIN with no PASSWORD: authentication is by IAM token only.
        await con.execute(f'CREATE ROLE "{role}" WITH LOGIN')
        print(f"created role {role!r}")

    # `rds_iam` is an RDS-provided role. Without it Postgres will not accept a
    # signed token, and the bot fails authentication with a password error that
    # gives no hint that the grant is what is missing.
    await con.execute(f'GRANT rds_iam TO "{role}"')
    await con.execute(f'GRANT CONNECT ON DATABASE "{target}" TO "{role}"')
    print(f"granted rds_iam and CONNECT on {target!r} to {role!r}")


async def grant_schema_privileges(dsn: str, target: str, role: str) -> None:
    """Grant inside the target database. Schema grants are per-database.

    CREATE is required because Repository.migrate() runs schema.sql as
    CREATE TABLE IF NOT EXISTS on every start. The ALTER DEFAULT PRIVILEGES
    line is what stops the next new table being invisible to the bot: grants
    on existing tables do not cover tables created later.
    """
    parts = urllib.parse.urlparse(dsn)
    con = await asyncpg.connect(urllib.parse.urlunparse(
        parts._replace(path=f"/{target}")))
    try:
        await con.execute(f'GRANT USAGE, CREATE ON SCHEMA public TO "{role}"')
        await con.execute(
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES '
            f'IN SCHEMA public TO "{role}"')
        await con.execute(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        await con.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{role}"')
        await con.execute(
            f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{role}"')
        print(f"granted schema privileges in {target!r} to {role!r}")
    finally:
        await con.close()


async def main() -> int:
    admin = os.environ["ADMIN_DSN"]
    target = os.environ["TARGET_DB"]
    # Default matches variables.tf's db_app_username.
    role = _safe_identifier(
        os.environ.get("APP_DB_ROLE", "deltabt_app"), "a database role")
    con = await asyncpg.connect(admin)
    try:
        exists = await con.fetchval(
            "select 1 from pg_database where datname = $1", target)
        if exists:
            print(f"database {target!r} already exists")
        else:
            # Not parameterisable: an identifier cannot be bound. The name
            # comes from the Terraform stacks map, not from anything
            # user-facing, and it is checked against a strict pattern before
            # interpolation rather than trusted for being internal.
            _safe_identifier(target, "a database")
            await con.execute(f'CREATE DATABASE "{target}"')
            print(f"created database {target!r}")

        # Deliberately NOT inside the `else`. The role and its grants must be
        # re-applied on an existing database too: that is the path every stack
        # takes when IAM auth is switched on for a database that already
        # exists, which is exactly the migration this change is.
        await ensure_app_role(con, role, target)
    finally:
        await con.close()

    await grant_schema_privileges(admin, target, role)
    return 0


raise SystemExit(asyncio.run(main()))

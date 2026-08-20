"""Create a stack's database if it is not there. Idempotent, and creates only.

Terraform builds the RDS INSTANCE but not the databases inside it -- there is
no aws_db_instance sub-resource for that -- so a new stack's db_name exists in
the stacks map and nowhere on the server, and the bot dies on
InvalidCatalogNameError at first start.

Only CREATE DATABASE happens here. The schema is not applied: Repository.connect
calls migrate(), which runs schema.sql as CREATE TABLE IF NOT EXISTS on every
start, so the tables are the bot's own business and applying them from outside
would be a second copy of a definition that must not drift.
"""
import asyncio
import os
import sys

import asyncpg


async def main() -> int:
    admin = os.environ["ADMIN_DSN"]
    target = os.environ["TARGET_DB"]
    con = await asyncpg.connect(admin)
    try:
        exists = await con.fetchval(
            "select 1 from pg_database where datname = $1", target)
        if exists:
            print(f"database {target!r} already exists; nothing to do")
            return 0
        # Not parameterisable: an identifier cannot be bound. The name comes
        # from the Terraform stacks map, not from anything user-facing, and it
        # is checked against a strict pattern before interpolation rather than
        # trusted for being internal.
        if not target.replace("_", "").isalnum():
            sys.exit(f"refusing to create a database named {target!r}")
        await con.execute(f'CREATE DATABASE "{target}"')
        print(f"created database {target!r}")
    finally:
        await con.close()
    return 0


raise SystemExit(asyncio.run(main()))

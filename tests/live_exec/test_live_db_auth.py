"""The live bot authenticates to Postgres by IAM token, and can start that way.

WHAT HAPPENED. 2026-09-17. RDS rotated the master password at 05:07 UTC, on
its weekly schedule. At 06:21 tnet needed a new pooled connection, was refused
("password authentication failed"), and could not write for four minutes; it
limped on afterwards only because older connections were still open.

IAM-token auth (app/persistence/db_auth.py, #57) exists to make that
impossible, and was merged DORMANT: the flag was to be forwarded by the host
launcher, editing the launcher replaces the host, and nothing ever turned it
on. Nothing tested that it was on.

Turning it on exposed a second gap, pinned below against a real Postgres:
migrate() re-runs schema.sql on every start, Postgres checks table OWNERSHIP
before IF NOT EXISTS, and every deployed table is owned by the master user --
so the IAM role would have crash-looped at startup.
"""
from __future__ import annotations

import pathlib
import re
import urllib.parse
import uuid

import pytest
import pytest_asyncio

from app.persistence import db_auth
from app.persistence.repository import PostgresRepository, schema_objects
from tests.live.conftest import TEST_DSN, requires_pg

ROOT = pathlib.Path(__file__).resolve().parents[2]
LIVE_IMAGE = (ROOT / "deploy/docker/Dockerfile.live").read_text()
PAPER_IMAGE = (ROOT / "deploy/docker/Dockerfile").read_text()
SCHEMA = (ROOT / "app/persistence/schema.sql").read_text()


def _env(dockerfile: str) -> dict[str, str]:
    """ENV key=value pairs, including continuation lines."""
    out: dict[str, str] = {}
    joined = re.sub(r"\\\n\s*", " ", dockerfile)
    for line in joined.splitlines():
        if line.startswith("ENV "):
            for k, v in re.findall(r"(\w+)=(\S+)", line):
                out[k] = v
    return out


# --- the flag is on where it must be, and only there ----------------------

def test_the_live_image_turns_iam_auth_on():
    env = _env(LIVE_IMAGE)
    assert db_auth.IAM_ENV in env, "the live image leaves IAM auth dormant"
    assert db_auth.iam_enabled(env), f"{db_auth.IAM_ENV}={env[db_auth.IAM_ENV]!r} is not 'on'"


def test_the_paper_image_does_not():
    """Local development builds this image against the compose Postgres,
    which has no IAM. Paper turns it on in its launcher, deliberately."""
    assert db_auth.IAM_ENV not in _env(PAPER_IMAGE)


@pytest.mark.parametrize("launcher", ["deploy/aws/run_live.sh"])
def test_the_live_launcher_does_not_switch_it_back_off(launcher):
    """`docker run -e DB_IAM_AUTH=0` would override the image, silently."""
    assert db_auth.IAM_ENV not in (ROOT / launcher).read_text()


# --- the skip in migrate() is only ever a no-op ----------------------------

def _statements(sql: str) -> list[str]:
    body = "\n".join(line.split("--")[0] for line in sql.splitlines())
    return [s.strip() for s in body.split(";") if s.strip()]


def test_schema_sql_is_only_create_if_not_exists():
    """migrate() skips the script when every object it names exists. That is
    only equivalent to running it while every statement is CREATE ... IF NOT
    EXISTS. An `ALTER TABLE ... ADD COLUMN` added here would be SKIPPED on
    every deployed database -- it needs a real migration, not this file."""
    stmts = _statements(SCHEMA)
    names = schema_objects(SCHEMA)
    assert len(stmts) == len(names), [
        " ".join(s.split()[:6]) for s in stmts
        if not re.match(r"CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)\s+IF\s+NOT\s+EXISTS", s, re.I)]
    assert len(set(names)) == len(names)


class _Con:
    def __init__(self, present):
        self.present, self.executed = present, []

    async def fetchval(self, sql, names):
        return sum(1 for n in names if n in self.present)

    async def execute(self, sql):
        self.executed.append(sql)


class _Pool:
    def __init__(self, con):
        self.con = con

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.con

            async def __aexit__(self, *exc):
                return False
        return _Ctx()


def _repo(present):
    repo = PostgresRepository("postgresql://x@h/d")
    repo._pool = _Pool(_Con(present))
    return repo


@pytest.mark.asyncio
async def test_a_complete_schema_is_not_re_run():
    repo = _repo(set(schema_objects(SCHEMA)))
    await repo.migrate()
    assert repo._pool.con.executed == []


@pytest.mark.asyncio
async def test_one_missing_object_runs_the_whole_script():
    names = schema_objects(SCHEMA)
    repo = _repo(set(names) - {names[-1]})
    await repo.migrate()
    assert len(repo._pool.con.executed) == 1


# --- against a real Postgres: the role the bot actually connects as --------

def _as(dsn: str, user: str, password: str) -> str:
    p = urllib.parse.urlparse(dsn)
    netloc = f"{user}:{password}@{p.hostname}" + (f":{p.port}" if p.port else "")
    return urllib.parse.urlunparse(p._replace(netloc=netloc))


@pytest_asyncio.fixture
async def app_role():
    """A role like deltabt_app: read/write on tables it does not own."""
    import asyncpg
    owner = PostgresRepository(TEST_DSN)
    await owner.connect()                        # tables owned by the test superuser
    role, pw = f"t_app_{uuid.uuid4().hex[:8]}", "pw"
    db = urllib.parse.urlparse(TEST_DSN).path.lstrip("/")
    con = await asyncpg.connect(TEST_DSN)
    try:
        await con.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{pw}'")
        await con.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {role}')
        await con.execute(f"GRANT USAGE, CREATE ON SCHEMA public TO {role}")
        await con.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        await con.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
        yield _as(TEST_DSN, role, pw), con, owner
    finally:
        await owner.close()
        await con.execute(f"DROP OWNED BY {role}")
        await con.execute(f"DROP ROLE {role}")
        await con.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_role_that_owns_no_table_can_start(app_role):
    dsn, _, _ = app_role
    repo = PostgresRepository(dsn)
    await repo.connect()                          # raised "must be owner" before
    try:
        assert await repo.is_writable()
    finally:
        await repo.close()


@requires_pg
@pytest.mark.asyncio
async def test_a_missing_object_it_cannot_create_still_fails_loudly(app_role):
    import asyncpg
    dsn, admin, _ = app_role
    await admin.execute("DROP INDEX IF EXISTS ix_system_events_time")
    repo = PostgresRepository(dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await repo.connect()
    finally:
        await repo.close()
        await admin.execute(
            "CREATE INDEX IF NOT EXISTS ix_system_events_time "
            "ON system_events (occurred_at DESC)")

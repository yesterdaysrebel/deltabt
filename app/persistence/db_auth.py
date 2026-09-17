"""How the bot authenticates to Postgres, without a password that can go stale.

THE FAILURE THIS REPLACES

`infra/terraform/rds.tf` sets `manage_master_user_password = true`, so AWS
generates the master password and rotates it on its own schedule. The DSN is
assembled once, in `deploy/aws/run.sh`, and written to /run/deltabt/env at
container start:

    # The database password is fetched at START ... RDS rotates it; this picks
    # up the current value on every restart.

The mitigation is "restart", and this bot deliberately does not restart -- a
restart retires the running experiment and resets the sample to zero.

So after a rotation the cached DSN is wrong, and NOTHING BREAKS YET, which is
the dangerous part. Postgres does not re-authenticate established connections,
so the pool keeps serving. The failure appears later, when a new connection is
needed -- after a network blip, a pool recycle, the RDS maintenance window --
and from then on it is permanent until someone restarts the container. It
surfaces hours or days after the rotation that caused it, looking like an
unrelated outage.

THE FIX IS NOT TO REFRESH THE PASSWORD FASTER. IT IS TO NOT HAVE ONE.

With RDS IAM authentication the "password" is a signed token the client mints
locally, valid ~15 minutes. There is no shared secret to rotate, nothing
cached, and nothing to go stale. The master password keeps rotating on its own
schedule and no longer matters, because nothing uses it.

    asyncpg accepts a CALLABLE for `password`, and calls it for every new
    connection. That is the whole mechanism: a pooled connection opened an hour
    from now mints a fresh token at the moment it is opened.

IAM auth requires TLS, which is why `ssl` is set explicitly here rather than
left to the DSN -- once the components are passed separately, `sslmode` in the
DSN query string is no longer read.

NOTE ON NAMING: `app/safety.py` forbids the identifier `auth_token` anywhere in
`app/`, because it is an exchange-credential name. Nothing here is an exchange
credential, but the scan is a blunt instrument on purpose, so the database
token is called `db_token` throughout.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from typing import Any, Callable

log = logging.getLogger(__name__)

#: Set to "1" to authenticate with IAM instead of the password in the DSN.
#: Absent or "0" keeps the password path, which is what local development and
#: the docker-compose Postgres use -- neither has an IAM identity.
IAM_ENV = "DB_IAM_AUTH"

#: Region for the token signer. Optional, because the RDS hostname carries it.
#:
#: IT USED TO SAY boto3 resolves the region from instance metadata on EC2. It
#: does not -- botocore takes CREDENTIALS from instance metadata, never the
#: region. The first image to switch IAM auth on (125862a, 2026-09-17) died at
#: startup with NoRegionError and the host rolled back. The containers are
#: given no AWS_REGION, and forwarding one means editing the host launcher,
#: which replaces the host. So the region comes from the endpoint instead.
REGION_ENV = "AWS_REGION"

#: `<instance>.<cluster-id>.<region>.rds.amazonaws.com` (and `.com.cn`).
_RDS_HOST = re.compile(r"\.([a-z]{2}(?:-[a-z]+)+-\d+)\.rds\.amazonaws\.com(?:\.cn)?$")


def region_from_host(host: str) -> str | None:
    """The AWS region an RDS endpoint lives in, or None if it is not one."""
    m = _RDS_HOST.search(host or "")
    return m.group(1) if m else None

#: The Postgres role to connect AS under IAM, overriding whatever user the DSN
#: carries. The DSN is built once by deploy/aws/run.sh and names the master
#: user; substituting here rather than there keeps the shell script -- which is
#: gzipped into a 16 KB user_data budget and is not unit-tested -- unchanged
#: except for forwarding one flag. Matches variables.tf's db_app_username.
APP_ROLE_ENV = "DB_APP_ROLE"
DEFAULT_APP_ROLE = "deltabt_app"


def iam_enabled(env: dict[str, str] | None = None) -> bool:
    src = os.environ if env is None else env
    return str(src.get(IAM_ENV, "")).strip() in {"1", "true", "TRUE", "yes"}


def _db_token_provider(host: str, port: int, user: str,
                       region: str) -> Callable[[], str]:
    """A callable asyncpg invokes for EVERY new connection.

    The boto3 client is built once (it is thread-safe and holds no connection);
    only the token is minted per call. Tokens are ~15 minutes, so this is
    effectively free, and correctness does not depend on how long the process
    has been running.
    """
    import boto3  # imported lazily: only the AWS path needs it

    client = boto3.client("rds", region_name=region)

    def provide() -> str:
        return client.generate_db_auth_token(
            DBHostname=host, Port=port, DBUsername=user)

    return provide


def connect_kwargs(dsn: str, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Keyword arguments for `asyncpg.connect` / `asyncpg.create_pool`.

    Password mode returns ``{"dsn": dsn}`` so behaviour is byte-identical to
    what shipped before -- local development and the compose Postgres are
    untouched by this module.

    IAM mode decomposes the DSN and swaps the password for a token provider.
    """
    src = os.environ if env is None else env
    if not iam_enabled(src):
        return {"dsn": dsn}

    parts = urllib.parse.urlparse(dsn)
    host = parts.hostname
    port = parts.port or 5432
    database = (parts.path or "/").lstrip("/")
    # The DSN's own username is the MASTER user and is deliberately discarded:
    # the bot logs in as the app role, which has the rds_iam grant and cannot
    # drop the database. The password in the DSN, if any, is discarded too --
    # that is the credential this whole change exists to stop depending on.
    user = src.get(APP_ROLE_ENV) or DEFAULT_APP_ROLE
    if not host or not database:
        raise ValueError(
            f"{IAM_ENV} is set but the DSN is missing host or database "
            f"(host={host!r} database={database!r})")

    region = (src.get(REGION_ENV) or src.get("AWS_DEFAULT_REGION")
              or region_from_host(host))
    if not region:
        # Refuse here, with the reason, rather than let botocore raise
        # NoRegionError from inside pool creation.
        raise ValueError(
            f"{IAM_ENV} is set but no region is known: set {REGION_ENV}, or "
            f"connect to an RDS endpoint (host={host!r})")

    log.info("database auth: IAM tokens for %s@%s/%s (no stored password)",
             user, host, database)
    return {
        "host": host,
        "port": port,
        "user": user,
        "database": database,
        # The callable, not a value. asyncpg calls it per connection.
        "password": _db_token_provider(host, port, user, region),
        # IAM auth is refused over plaintext. Passing components separately
        # means `sslmode` in the DSN is no longer consulted, so it is explicit.
        "ssl": "require",
    }

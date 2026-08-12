"""PostgreSQL advisory lock: the real single-instance guarantee.

``replicas: 1`` and ``strategy: Recreate`` are Kubernetes promises. A manual
``kubectl scale``, a botched rollout, a stuck terminating pod, or somebody
running the bot on their laptop against the production database all break them,
and every one of those produces two bots trading the same paper account from
the same feed.

A session-scoped advisory lock cannot be broken that way. It is held by a
dedicated connection for the life of the process and released by the server the
moment that connection dies -- including on ``kill -9``, where no cleanup code
of ours would ever run.

The lock is deliberately NOT taken with ``pg_advisory_lock`` (which blocks and
would leave a second pod waiting to pounce). ``pg_try_advisory_lock`` fails
immediately, and the second process exits rather than lurking.
"""

from __future__ import annotations

import hashlib
import logging

log = logging.getLogger(__name__)

#: Namespace for the lock key. Derived from a fixed string so it is stable
#: across deploys but will not collide with another application's locks.
LOCK_NAMESPACE = "deltabot.v1.paper"


def lock_key(namespace: str = LOCK_NAMESPACE) -> int:
    """A stable signed 64-bit key for ``pg_try_advisory_lock``."""
    digest = hashlib.sha256(namespace.encode()).digest()[:8]
    val = int.from_bytes(digest, "big", signed=False)
    return val - (1 << 64) if val >= (1 << 63) else val


class SingleInstanceLock:
    """Holds the advisory lock on its own dedicated connection."""

    def __init__(self, dsn: str, *, namespace: str = LOCK_NAMESPACE) -> None:
        self.dsn = dsn
        self.namespace = namespace
        self.key = lock_key(namespace)
        self._con = None
        self.held = False

    async def acquire(self) -> bool:
        """Try to take the lock. False means another instance already has it."""
        import asyncpg
        self._con = await asyncpg.connect(self.dsn)
        got = await self._con.fetchval("SELECT pg_try_advisory_lock($1)", self.key)
        self.held = bool(got)
        if not self.held:
            await self._con.close()
            self._con = None
            log.error(
                "another bot instance holds the advisory lock (key=%d); "
                "refusing to start", self.key)
        else:
            log.info("acquired single-instance advisory lock (key=%d)", self.key)
        return self.held

    async def release(self) -> None:
        if self._con is None:
            return
        try:
            if self.held:
                await self._con.fetchval("SELECT pg_advisory_unlock($1)", self.key)
        finally:
            await self._con.close()
            self._con = None
            self.held = False

    async def is_alive(self) -> bool:
        """Verify the lock connection is still up.

        A dropped connection silently releases the lock server-side, so a bot
        that keeps trading on the assumption it still holds one is exactly the
        scenario this class exists to prevent.
        """
        if self._con is None or not self.held:
            return False
        try:
            return await self._con.fetchval("SELECT 1") == 1
        except Exception:                              # noqa: BLE001
            self.held = False
            return False

    async def __aenter__(self) -> "SingleInstanceLock":
        if not await self.acquire():
            raise RuntimeError(
                "another bot instance is already running (advisory lock held)")
        return self

    async def __aexit__(self, *exc) -> None:
        await self.release()

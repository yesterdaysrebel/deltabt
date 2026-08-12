"""Two clocks, kept apart on purpose.

AUDIT FINDING F8. Cooldowns, the daily-counter rollover and order expiry all
compared against ``time.time()`` while every bar, tick and signal carried an
**exchange** timestamp. Live the two coincide, which is exactly why 960 tests
missed it. It had two consequences:

* a single comparison spanned two independent clocks -- ``order.created_at`` was
  wall clock, ``tick.ts`` was exchange time, so container skew beyond the order
  TTL made every order expire immediately or never, neither loudly;
* whether a signal was rejected depended on when the *process* saw it rather
  than when the *market* produced it, so the run could not be verified by
  replaying its own record.

The rule this module enforces:

    MARKET decisions use MARKET time.   (cooldowns, expiry, daily limits,
                                         signal timing, funding settlement)
    OPERATIONAL health uses WALL time.  (feed staleness, uptime, heartbeats)

Staleness is the one thing the wall clock is genuinely right for: "have *we*
stopped hearing from the exchange" is a question about our own liveness, and
answering it from exchange timestamps would be circular -- a dead feed stops
advancing exchange time, so a market clock would report the feed as fresh
forever.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


class MarketClock:
    """Exchange time, advanced only by observed market data.

    Monotonic by construction: out-of-order or replayed messages cannot rewind
    it, so a late tick can never revive an expired cooldown.
    """

    __slots__ = ("_t",)

    def __init__(self, start: int = 0) -> None:
        self._t = int(start)

    def observe(self, exchange_ts: int | float) -> int:
        ts = int(exchange_ts)
        if ts > self._t:
            self._t = ts
        return self._t

    def now(self) -> int:
        """Latest exchange timestamp seen, in unix seconds."""
        return self._t

    @property
    def is_set(self) -> bool:
        return self._t > 0

    def __repr__(self) -> str:            # pragma: no cover - debugging aid
        return f"MarketClock(now={self._t})"


def wall_now() -> float:
    """Local wall clock. Only legitimate for staleness, uptime and heartbeats."""
    return time.time()


@dataclass(frozen=True)
class EventTime:
    """The timestamp pair every persisted event carries.

    ``exchange_ts`` is when the market produced the thing. ``received_ts`` is
    when this process learned about it. Recording both is what makes the run
    replayable *and* lets an operator see processing lag, which a single
    timestamp cannot do.
    """

    exchange_ts: int
    received_ts: float

    @classmethod
    def at(cls, exchange_ts: int | float) -> "EventTime":
        return cls(exchange_ts=int(exchange_ts), received_ts=wall_now())

    @property
    def lag_seconds(self) -> float:
        return max(0.0, self.received_ts - self.exchange_ts)

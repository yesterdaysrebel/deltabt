"""Historical warm-up and gap repair over the read-only REST endpoint.

Reuses ``deltabt.data.client.DeltaClient`` unchanged. That client is GET-only,
unauthenticated, and already encodes the endpoint's real behaviour -- the
4000-bar cap truncating on the OLD side, descending results, bar-open seconds,
and the forming bar arriving in the payload and being dropped.

The client is synchronous, so calls are pushed to a thread rather than blocking
the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.market_data.normalize import Candle
from deltabt.data.client import DeltaClient

log = logging.getLogger(__name__)

MINUTE = 60


def _rows_to_candles(symbol: str, rows: list[dict]) -> list[Candle]:
    out: list[Candle] = []
    for r in rows:
        try:
            out.append(Candle(
                symbol=symbol, start=int(r["time"]),
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                volume=float(r.get("volume") or 0.0),
                source="rest",
            ))
        except (KeyError, TypeError, ValueError):
            log.warning("skipping malformed backfill row", extra={"symbol": symbol})
    out.sort(key=lambda c: c.start)
    return out


class Backfiller:
    def __init__(self, client: DeltaClient | None = None) -> None:
        self.client = client or DeltaClient()

    def fetch_sync(self, symbol: str, start: int, end: int) -> list[Candle]:
        rows = self.client.candles(symbol, "1m", start, end, drop_forming=True)
        return _rows_to_candles(symbol, rows)

    async def fetch(self, symbol: str, start: int, end: int) -> list[Candle]:
        return await asyncio.to_thread(self.fetch_sync, symbol, start, end)

    async def warm_up(self, symbol: str, days: int, *, now: int | None = None) -> list[Candle]:
        """The startup history for one symbol."""
        now = now or int(time.time())
        start = now - days * 86400
        bars = await self.fetch(symbol, start, now)
        log.info("backfilled %d bars", len(bars), extra={"symbol": symbol,
                                                         "days": days})
        return bars

    async def fill_gap(self, symbol: str, expected_start: int, actual_start: int) -> list[Candle]:
        """Repair a detected hole. Logged, never silent."""
        if actual_start <= expected_start:
            return []
        bars = await self.fetch(symbol, expected_start, actual_start - MINUTE)
        log.warning("gap repair fetched %d of %d missing minutes",
                    len(bars), (actual_start - expected_start) // MINUTE,
                    extra={"symbol": symbol, "from": expected_start,
                           "to": actual_start})
        return bars

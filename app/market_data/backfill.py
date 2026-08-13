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


def find_gaps(bars: list[Candle], step: int = MINUTE) -> list[tuple[int, int]]:
    """Inclusive [lo, hi] ranges of missing bar-opens, oldest first."""
    out: list[tuple[int, int]] = []
    for a, z in zip(bars, bars[1:]):
        if z.start - a.start > step:
            out.append((a.start + step, z.start - step))
    return out


class Backfiller:
    def __init__(self, client: DeltaClient | None = None) -> None:
        self.client = client or DeltaClient()

    def fetch_sync(self, symbol: str, start: int, end: int) -> list[Candle]:
        rows = self.client.candles(symbol, "1m", start, end, drop_forming=True)
        return _rows_to_candles(symbol, rows)

    async def fetch(self, symbol: str, start: int, end: int) -> list[Candle]:
        return await asyncio.to_thread(self.fetch_sync, symbol, start, end)

    async def warm_up(self, symbol: str, days: int, *, now: int | None = None,
                      repair_passes: int = 2) -> list[Candle]:
        """The startup history for one symbol, verified contiguous.

        A bulk paginated fetch can drop a minute that a NARROW refetch of the
        same window returns -- observed on BTCUSD at 2026-08-10 09:57 UTC,
        where the 7-day pull returned 10,079 of 10,080 minutes and a targeted
        request for that one minute returned it immediately. The data exists;
        the bulk path loses it.

        That matters more than one bar suggests. A hole makes the 5m bucket
        containing it incomplete, so it is dropped from the resampled series
        entirely, and the Wilder chains then treat two non-adjacent bars as
        adjacent. The indicator values would differ from what the same window
        produces in the research code, which is precisely the equivalence the
        forward test exists to demonstrate.

        So the fetch is verified and holes are refetched. Any that remain are
        returned as-is and reported by the caller rather than papered over --
        a minute the exchange genuinely never served is a fact about the
        market, not a bug to hide.
        """
        now = now or int(time.time())
        start = now - days * 86400
        bars = await self.fetch(symbol, start, now)

        for attempt in range(repair_passes):
            holes = find_gaps(bars)
            if not holes:
                break
            log.warning("warm-up has %d hole(s); refetching", len(holes),
                        extra={"symbol": symbol, "attempt": attempt + 1})
            recovered: list[Candle] = []
            for lo, hi in holes:
                recovered.extend(await self.fetch(symbol, lo, hi))
            if not recovered:
                break
            merged = {b.start: b for b in bars}
            merged.update({b.start: b for b in recovered})
            bars = [merged[k] for k in sorted(merged)]

        remaining = find_gaps(bars)
        log.info("backfilled %d bars (%d unrecoverable hole(s))",
                 len(bars), len(remaining),
                 extra={"symbol": symbol, "days": days})
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

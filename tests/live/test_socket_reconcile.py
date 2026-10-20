"""Socket minutes are swapped for REST's before a decision (2026-10-20).

Delta's candlestick_1m stream opens each minute at the PREVIOUS minute's
close and stretches high/low to it; REST /v2/history/candles opens at the
minute's first trade (ARB, 7 days: 816 of 1,079 socket-built minutes
differed; close and volume agree). On 10-20 the live bot shorted ARB on a 5m
Supertrend flip that Delta's own candles do not show. These tests emulate
that socket and check the bot decides on REST's bars.
"""

from __future__ import annotations

import pytest

from app.market_data.candle_builder import QUIET_SOURCE, SymbolCandleBuilder
from app.market_data.normalize import Candle, CandleUpdate
from tests.live.test_quiet_minutes import (SYM, WARM, DeltaRest, Feed, make_archive,
                                           make_bot, now_aligned, one_pass)


def gapped_archive(n: int, start: int, forced_quiet: tuple[int, ...] = ()) -> list[Candle]:
    """Delta's archive where a minute's first trade is NOT the previous close
    (the shared make_archive always opens at it, which hides the quirk)."""
    out = []
    for i, c in enumerate(make_archive(n, start, quiet=0.0, forced_quiet=forced_quiet)):
        if c.volume == 0:
            out.append(c)            # Delta's filler: flat at the previous close
            continue
        o = c.open if i == 0 else round(c.open + (0.03 if i % 2 else -0.02), 4)
        out.append(Candle(SYM, c.start, o, max(c.high, o), min(c.low, o), c.close,
                          c.volume, source="rest"))
    return out


def socket_updates(bar: Candle, prev_close: float) -> list[CandleUpdate]:
    """What Delta's socket sends for a traded minute: open = previous close."""
    o = prev_close
    us = bar.start * 1_000_000
    return [CandleUpdate(SYM, bar.start, o, max(o, bar.open), min(o, bar.open), bar.open,
                         1.0, us + 1_000_000),
            CandleUpdate(SYM, bar.start, o, max(o, bar.high), min(o, bar.low), bar.close,
                         bar.volume, us + 50_000_000)]


async def socket_stream(bot, archive, lo, hi) -> None:
    for i in range(lo, hi):
        for u in socket_updates(archive[i], archive[i - 1].close):
            bot._on_candle(u)
            await one_pass(bot)


def ohlcv(c):
    return (c.start, c.open, c.high, c.low, c.close, c.volume)


def boundary_index(archive, after: int, minutes: int = 5) -> int:
    """Index of the first minute after ``after`` that closes a ``minutes`` bar."""
    return next(i for i in range(after, len(archive))
                if (archive[i].start + 60) % (minutes * 60) == 0)


# -- the builder ----------------------------------------------------------------

def test_reconcile_replaces_only_socket_minutes_in_the_window():
    b = SymbolCandleBuilder(SYM)
    b.bars.extend([
        Candle(SYM, 60, 1, 2, 1, 2, 5, source="rest"),
        Candle(SYM, 120, 2, 3, 2, 3, 5),                    # socket
        Candle(SYM, 180, 3, 3, 3, 3, 0, source=QUIET_SOURCE),
        Candle(SYM, 240, 3, 4, 2, 4, 7),                    # socket
        Candle(SYM, 300, 4, 5, 4, 5, 9),                    # socket, outside upto
    ])
    rest = [Candle(SYM, t, 9, 9, 9, 9, 99, source="rest") for t in (60, 120, 180, 240, 300)]
    replaced, kept = b.reconcile(rest, upto=240)
    assert [c.start for c in replaced] == [120, 240] and kept == 0
    held = {c.start: c for c in b.bars}
    assert held[60].open == 1 and held[180].source == QUIET_SOURCE, "non-socket bars untouched"
    assert held[120].open == 9 and held[120].source == "rest"
    assert held[300].open == 4 and held[300].source == "ws", "after the decision minute"
    assert b.stats.reconciled == 2


def test_reconcile_keeps_a_socket_minute_rest_lacks_or_has_fewer_trades_for():
    b = SymbolCandleBuilder(SYM)
    b.bars.extend([Candle(SYM, 60, 1, 2, 1, 2, 50), Candle(SYM, 120, 2, 3, 2, 3, 5)])
    replaced, kept = b.reconcile([Candle(SYM, 60, 1, 2, 1, 2, 40, source="rest")], upto=120)
    assert replaced == [] and kept == 2, "REST still aggregating (40 < 50) and REST silent"
    assert [c.source for c in b.bars] == ["ws", "ws"]


# -- the bot ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_bot_decides_on_rest_bars_not_the_sockets():
    start = now_aligned(WARM + 30)
    archive = gapped_archive(WARM + 20, start)
    bot = await make_bot(archive, Feed(connected_since=start, first=start))
    k = boundary_index(archive, WARM + 1)
    await socket_stream(bot, archive, WARM, k + 2)   # k closes when k+1 arrives

    held = {c.start: c for c in bot.builder[SYM].bars}
    for i in range(WARM, k + 1):
        assert ohlcv(held[archive[i].start]) == ohlcv(archive[i]), i
        assert held[archive[i].start].source == "rest"
    # the socket's view really was different, or this test proves nothing
    assert any(archive[i].open != archive[i - 1].close for i in range(WARM, k + 1))
    stored = bot.repo._s["candles"][(SYM, "1m")]
    assert ohlcv(stored[archive[k].start]) == ohlcv(archive[k]), "the stored row is replaced"
    # the decision bar is built from REST's minutes
    five = bot.repo._s["candles"][(SYM, "5m")][archive[k].start - 240]
    assert five.open == archive[k - 4].open
    snap = bot.health_snapshot()
    assert snap["reconciled_minutes"] == k + 1 - WARM and snap["reconcile_kept"] == 0


class LaggingRest(DeltaRest):
    """REST that serves the newest minute short of trades for ``lag`` calls."""

    def __init__(self, archive, lag: int):
        super().__init__(archive)
        self.lag = lag

    async def fetch(self, symbol, start, end):
        got = await super().fetch(symbol, start, end)
        if self.lag > 0 and got and got[-1].start == end:
            self.lag -= 1
            c = got[-1]
            got = got[:-1] + [Candle(SYM, c.start, c.open, c.high, c.low, c.close,
                                     c.volume - 1, source="rest")]
        return got


@pytest.mark.asyncio
async def test_rest_still_aggregating_is_asked_again():
    start = now_aligned(WARM + 30)
    archive = gapped_archive(WARM + 20, start)
    rest = LaggingRest(archive, lag=1)
    bot = await make_bot(archive, Feed(connected_since=start, first=start), rest)
    bot.RECONCILE_RETRIES = (0.0,)
    bot.RECONCILE_WINDOW = 1e9          # the streamed minutes are ~30 min old
    k = boundary_index(archive, WARM + 1)
    await socket_stream(bot, archive, WARM, k + 2)
    held = {c.start: c for c in bot.builder[SYM].bars}
    assert ohlcv(held[archive[k].start]) == ohlcv(archive[k])
    assert bot.health_snapshot()["reconcile_kept"] == 0


@pytest.mark.asyncio
async def test_rest_behind_after_every_retry_keeps_the_socket_bar_and_says_so():
    start = now_aligned(WARM + 30)
    archive = gapped_archive(WARM + 20, start)
    rest = LaggingRest(archive, lag=99)
    bot = await make_bot(archive, Feed(connected_since=start, first=start), rest)
    bot.RECONCILE_RETRIES = (0.0,)
    bot.RECONCILE_WINDOW = 1e9          # the streamed minutes are ~30 min old
    k = boundary_index(archive, WARM + 1)
    await socket_stream(bot, archive, WARM, k + 2)
    held = {c.start: c for c in bot.builder[SYM].bars}
    assert held[archive[k].start].source == "ws"
    assert held[archive[k - 1].start].source == "rest", "the rest of the window still swapped"
    assert bot.health_snapshot()["reconcile_kept"] == 1
    events = [e for e in await bot.repo.recent_system_events()
              if e["event_type"] == "SOCKET_BARS_KEPT"]
    assert events and events[0]["payload"]["kept"] == 1


@pytest.mark.asyncio
async def test_no_wait_once_the_minute_is_old():
    """REST lags only just after a close; for an old minute the bot does not
    sleep (a wait delays every coin queued behind this one)."""
    start = now_aligned(WARM + 30)
    archive = gapped_archive(WARM + 20, start)
    rest = LaggingRest(archive, lag=99)
    bot = await make_bot(archive, Feed(connected_since=start, first=start), rest)
    bot.RECONCILE_RETRIES = (30.0,)      # would hang the test if slept
    k = boundary_index(archive, WARM + 1)
    await socket_stream(bot, archive, WARM, k + 2)
    assert bot.health_snapshot()["reconcile_kept"] == 1


class BrokenRest(DeltaRest):
    def __init__(self, archive):
        super().__init__(archive)
        self.broken = False

    async def fetch(self, symbol, start, end):
        if self.broken:
            raise ConnectionError("REST down")
        return await super().fetch(symbol, start, end)


@pytest.mark.asyncio
async def test_a_failed_request_leaves_the_minutes_for_the_next_decision():
    start = now_aligned(WARM + 30)
    archive = gapped_archive(WARM + 20, start)
    rest = BrokenRest(archive)
    bot = await make_bot(archive, Feed(connected_since=start, first=start), rest)
    k1 = boundary_index(archive, WARM + 1)
    k2 = boundary_index(archive, k1 + 1)
    rest.broken = True
    await socket_stream(bot, archive, WARM, k1 + 2)
    assert bot.builder[SYM].bars[-2].source == "ws"
    events = [e for e in await bot.repo.recent_system_events()
              if e["event_type"] == "RECONCILE_FAILED"]
    assert events
    rest.broken = False
    await socket_stream(bot, archive, k1 + 2, k2 + 2)
    held = {c.start: c for c in bot.builder[SYM].bars}
    for i in range(WARM, k2 + 1):
        assert ohlcv(held[archive[i].start]) == ohlcv(archive[i]), i


@pytest.mark.asyncio
async def test_a_no_trade_socket_minute_is_not_asked_about_or_logged():
    """2026-10-20 live: the socket sends a flat volume-0 bar for a minute nobody
    traded; a REST window starting on it returns nothing, so every such minute
    was logged SOCKET_BARS_KEPT (~2,000 a day). It is already the archive's bar."""
    start = now_aligned(WARM + 30)
    probe = make_archive(WARM + 20, start, quiet=0.0)
    k = boundary_index(probe, WARM + 6)
    archive = gapped_archive(WARM + 20, start, forced_quiet=(k - 4,))   # first minute of the bucket
    assert archive[k - 4].volume == 0
    bot = await make_bot(archive, Feed(connected_since=start, first=start))
    bot.RECONCILE_WINDOW = 1e9
    await socket_stream(bot, archive, WARM, k + 2)
    held = {c.start: c for c in bot.builder[SYM].bars}
    for i in range(WARM, k + 1):
        assert ohlcv(held[archive[i].start]) == ohlcv(archive[i]), i
    assert bot.health_snapshot()["reconcile_kept"] == 0
    assert not [e for e in await bot.repo.recent_system_events()
                if e["event_type"] == "SOCKET_BARS_KEPT"]

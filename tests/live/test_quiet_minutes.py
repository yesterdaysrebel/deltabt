"""A minute with no trades is a flat bar, not a gap (2026-10-13).

Observed on the watch-only bot trading the day's top-10 gainers: /healthz red
for good on "7 gap(s) in the recent window", the log full of "gap repair
fetched 0 of 1 missing minutes" + ``candles GAP_PARTIAL``. Each was a minute
nobody traded in. The socket sends nothing for such a minute; Delta's candles,
the archive and the backtests carry it as a flat ``volume=0`` bar.

Delta's REST behaviour these tests emulate was MEASURED the same day on
BANKUSD / OPNUSD / SKLUSD: a request returns a flat filler bar for every
no-trade minute AFTER the first traded minute inside the requested window, and
nothing at all for a window made only of no-trade minutes. One second after a
minute closed, an anchored request already served it (12 of 12 probes).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from app.config.settings import RiskConfig, Settings
from app.config.variants import resolve_strategy
from app.market_data.backfill import Backfiller
from app.market_data.candle_builder import (QUIET_SOURCE, SymbolCandleBuilder,
                                            is_flat_no_trade)
from app.market_data.delta_ws import FeedStats
from app.market_data.normalize import Candle, CandleUpdate
from app.monitoring.health import evaluate_health
from app.persistence.repository import InMemoryRepository
from app.runtime.bot import TradingBot
from app.strategy.spec_arm import _decision_bar_tradable
from deltabt.data.quality import tradable_mask
from deltabt.strategy import resample_complete, resample_tradable
from tests.live.test_recovery import COSTS

SYM = "BTCUSD"
VARIANT = "SPEC:manual_scalp_both_t3_st@5"      # the deployed bot's rule
WARM = 800                                       # > 725 minutes of warm-up


# -- a synthetic exchange -----------------------------------------------------

def make_archive(n: int, start: int, *, seed: int = 7, quiet: float = 0.35,
                 forced_quiet: tuple[int, ...] = ()) -> list[Candle]:
    """Delta's 1m archive: traded bars plus a flat filler for each quiet minute."""
    rng = np.random.default_rng(seed)
    px, out = 100.0, []
    for i in range(n):
        t = start + 60 * i
        if i > 0 and (i in forced_quiet or rng.random() < quiet):
            out.append(Candle(SYM, t, px, px, px, px, 0.0, source="rest"))
            continue
        o = px
        c = round(px * (1 + rng.normal(0, 0.002)), 4)
        h = round(max(o, c) + abs(rng.normal(0, 0.05)) + 0.01, 4)
        lo = round(min(o, c) - abs(rng.normal(0, 0.05)) - 0.01, 4)
        out.append(Candle(SYM, t, o, h, lo, c, float(rng.integers(1, 50)),
                          source="rest"))
        px = c
    return out


class DeltaRest(Backfiller):
    """REST as measured: no filler before the window's first traded minute."""

    def __init__(self, archive: list[Candle], warm: int = WARM):
        self.archive, self.warm, self.calls = archive, warm, []

    async def fetch(self, symbol, start, end):
        self.calls.append((start, end))
        inside = [b for b in self.archive if start <= b.start <= end]
        k = next((i for i, b in enumerate(inside) if not is_flat_no_trade(b)),
                 len(inside))
        return inside[k:]

    async def warm_up(self, symbol, days, *, now=None, repair_passes=2):
        # everything before archive[warm]; that minute is the socket's first
        return await super().warm_up(symbol, days,
                                     now=self.archive[self.warm].start - 1,
                                     repair_passes=repair_passes)


class Feed:
    """A feed whose connection history the test sets."""

    def __init__(self, *, connected_since: float | None, first: float | None,
                 connected: bool = True):
        self.stats = FeedStats()
        self.stats.connected = connected
        self.stats.connected_since = connected_since
        self.stats.first_connected_at = first
        self.stats.last_message_at = time.time()

    async def run(self):
        return None

    def stop(self):
        return None


def updates_for(bar: Candle) -> list[CandleUpdate]:
    """Two socket snapshots of a traded minute: early, then final."""
    us = bar.start * 1_000_000
    return [CandleUpdate(SYM, bar.start, bar.open, bar.open, bar.open, bar.open,
                         1.0, us + 1_000_000),
            CandleUpdate(SYM, bar.start, bar.open, bar.high, bar.low, bar.close,
                         bar.volume, us + 50_000_000)]


async def make_bot(archive, feed, rest=None) -> TradingBot:
    spec = resolve_strategy({"DELTABOT_VARIANT": VARIANT})
    settings = Settings(symbols=(SYM,), risk=RiskConfig(starting_equity=10_000.0))
    bot = TradingBot(settings, InMemoryRepository({}), COSTS, strategy=spec,
                     backfiller=rest or DeltaRest(archive), feed=feed)
    assert await bot.start(), bot.recovery_error
    return bot


async def one_pass(bot) -> None:
    """The bar loop's body without the wall-clock roll (see _bar_loop)."""
    bot._pending_bars.extend(await bot._repair_new_gaps())
    bars, bot._pending_bars = bot._pending_bars, []
    for bar in sorted(bars, key=lambda b: (b.start, b.symbol)):
        await bot.on_closed_1m(bar)


async def stream(bot, bars) -> None:
    """Send only the TRADED minutes over the socket, as Delta does."""
    for bar in bars:
        if is_flat_no_trade(bar):
            continue
        for u in updates_for(bar):
            bot._on_candle(u)
            await one_pass(bot)


def frame(bars) -> pd.DataFrame:
    return pd.DataFrame([b.as_row() for b in bars])


def now_aligned(minutes_back: int) -> int:
    return (int(time.time()) // 60 - minutes_back) * 60


# -- REST: the narrow request was the bug ---------------------------------------

@pytest.mark.asyncio
async def test_repair_anchored_on_a_traded_minute_returns_delta_filler():
    archive = make_archive(20, 600_000, forced_quiet=(10, 11), quiet=0.0)
    rest = DeltaRest(archive)
    t = archive[10].start
    # the request the old repair made: only the quiet minutes -> nothing
    assert await rest.fetch(SYM, t, t + 60) == []
    # anchored on the traded minute before: Delta's fillers come back
    got = await rest.fill_gap(SYM, t, t + 120)
    assert [b.start for b in got] == [t, t + 60]
    assert all(is_flat_no_trade(b) for b in got)
    assert rest.calls[-1][0] == t - 60


@pytest.mark.asyncio
async def test_repair_reaches_back_to_the_last_traded_minute():
    archive = make_archive(20, 600_000, forced_quiet=(8, 9, 10, 11), quiet=0.0)
    rest = DeltaRest(archive)
    t = archive[10].start
    got = await rest.fill_gap(SYM, t, t + 120, anchor=archive[7].start)
    assert [b.start for b in got] == [t, t + 60]
    assert rest.calls[-1][0] == archive[7].start


# -- the builder ----------------------------------------------------------------

def test_a_hole_after_a_clock_rolled_bar_is_noted_on_the_first_update():
    b = SymbolCandleBuilder(SYM)
    b.ingest(CandleUpdate(SYM, 600, 1, 1, 1, 1, 1, 1))
    assert b.roll_on_clock(600 + 60 + 6, grace=5.0)
    assert not b.gaps
    b.ingest(CandleUpdate(SYM, 780, 1, 1, 1, 1, 1, 2))
    assert [(g.expected_start, g.actual_start) for g in b.gaps] == [(660, 780)], (
        "classified on arrival, not a minute later behind an evaluated bar")


def test_flat_bar_keeps_the_5m_bucket_and_is_not_tradable():
    archive = make_archive(10, 600, quiet=0.0, forced_quiet=(2, 3))
    df = frame(archive)
    assert resample_complete(df, 5)["time"].tolist() == [600, 900], (
        "a bucket with two no-trade minutes is kept once they are flat bars")
    holed = frame([b for b in archive if not is_flat_no_trade(b)])
    assert resample_complete(holed, 5)["time"].tolist() == [900], (
        "without them the bucket is dropped -- the parity break")
    mask = tradable_mask(df)
    assert not mask[2] and not mask[3] and mask[1] and mask[4]
    assert resample_tradable(df, mask, 5, times=np.array([600, 900])).tolist() == [
        False, True]
    # the live evaluator asks the same question of the decision bar
    assert not _decision_bar_tradable(df, 5, 600)
    assert _decision_bar_tradable(df, 5, 900)


# -- the bot: what is and is not a gap -------------------------------------------

@pytest.mark.asyncio
async def test_watched_quiet_minute_becomes_a_flat_bar_not_a_gap():
    start = now_aligned(WARM + 6)
    archive = make_archive(WARM + 4, start, quiet=0.0, forced_quiet=(WARM + 1,))
    bot = await make_bot(archive, Feed(connected_since=start, first=start))
    await stream(bot, archive[WARM:WARM + 4])

    b = bot.builder[SYM]
    held = {c.start: c for c in b.bars}
    q = held[archive[WARM + 1].start]
    assert q.source == QUIET_SOURCE and is_flat_no_trade(q)
    assert q.close == archive[WARM].close, "flat at the previous close"
    assert b.gaps == [] and b.stats.gaps == 0 and b.stats.missing_minutes == 0
    assert b.stats.quiet_minutes == 1

    snap = bot.health_snapshot()
    assert snap["recent_gaps"] == 0 and snap["quiet_minutes"] == 1
    report = evaluate_health(snap, db_writable=True)
    check = next(c for c in report.checks if c.name == "no_recent_gaps")
    assert check.ok and "1 no-trade minute" in check.detail
    stored = bot.repo._s["candles"][(SYM, "1m")]
    assert stored[q.start] == q, "persisted, the same bar the strategy saw"


@pytest.mark.asyncio
async def test_a_minute_missed_by_a_reconnect_is_repaired_not_a_health_failure():
    """2026-10-14: a minute nobody watched (reconnect) that REST serves is
    repaired: the series is complete, the event is recorded, health stays
    green. Before, every ~2 s overnight feed stall held /healthz red."""
    start = now_aligned(WARM + 6)
    archive = make_archive(WARM + 4, start, quiet=0.0, forced_quiet=(WARM + 1,))
    quiet_t = archive[WARM + 1].start
    # connected once, then reconnected AFTER the quiet minute began
    feed = Feed(connected_since=quiet_t + 30, first=start)
    bot = await make_bot(archive, feed)
    await stream(bot, archive[WARM:WARM + 4])

    b = bot.builder[SYM]
    snap = bot.health_snapshot()
    assert snap["recent_gaps"] == 0
    assert "no_recent_gaps" not in evaluate_health(snap, db_writable=True).failures
    assert quiet_t in {c.start for c in b.bars}, "the series is still continuous"
    events = await bot.repo.recent_system_events()
    ev = [e for e in events if e["event_type"] == "GAP_UNWATCHED"]
    assert ev and ev[0]["payload"]["repaired"] == 1


@pytest.mark.asyncio
async def test_trades_the_socket_missed_are_repaired_and_recorded():
    start = now_aligned(WARM + 6)
    archive = make_archive(WARM + 4, start, quiet=0.0)
    lost = archive[WARM + 1]
    bot = await make_bot(archive, Feed(connected_since=start, first=start))
    # the socket drops minute WARM+1 although it traded
    await stream(bot, [archive[WARM], archive[WARM + 2], archive[WARM + 3]])

    b = bot.builder[SYM]
    assert {c.start: c for c in b.bars}[lost.start] == lost, "repaired from REST"
    assert bot.health_snapshot()["recent_gaps"] == 0
    events = await bot.repo.recent_system_events()
    assert any(e["event_type"] == "GAP_MISSED_WHILE_CONNECTED" for e in events)


@pytest.mark.asyncio
async def test_a_minute_rest_does_not_serve_stays_a_gap():
    start = now_aligned(WARM + 6)
    archive = make_archive(WARM + 4, start, quiet=0.0, forced_quiet=(WARM + 1,))
    served = archive[:WARM + 1] + archive[WARM + 2:]
    bot = await make_bot(archive, Feed(connected_since=start, first=start),
                         rest=DeltaRest(served))
    await stream(bot, archive[WARM:WARM + 4])
    b = bot.builder[SYM]
    assert len(b.gaps) == 1 and b.stats.quiet_minutes == 0
    assert archive[WARM + 1].start not in {c.start for c in b.bars}


@pytest.mark.asyncio
async def test_the_real_bar_loop_classifies_before_it_evaluates(monkeypatch):
    import asyncio

    import app.runtime.bot as botmod
    start = 1_786_000_000 - 1_786_000_000 % 60
    archive = make_archive(WARM + 4, start, quiet=0.0, forced_quiet=(WARM + 1,))
    bot = await make_bot(archive, Feed(connected_since=start, first=start))
    # market time stands still for the clock fallback: nothing rolls early
    monkeypatch.setattr(botmod, "wall_now", lambda: float(archive[WARM].start + 1))
    for bar in (archive[WARM], archive[WARM + 2]):
        for u in updates_for(bar):
            bot._on_candle(u)
    task = asyncio.create_task(bot._bar_loop(interval=0.02))
    await asyncio.sleep(0.3)
    bot._stopping.set()
    await asyncio.wait_for(task, timeout=2)
    b = bot.builder[SYM]
    assert [c.source for c in b.bars][-2:] == ["ws", QUIET_SOURCE]
    assert b.gaps == []


# -- parity: live-built bars == the backtest's archive ----------------------------

@pytest.mark.asyncio
async def test_live_bars_with_quiet_minutes_equal_the_backtest_series():
    """Delta's archive (flat bars kept, as scripts/top_gainers_backtest.py
    reads it) vs the bars the bot builds from a socket that skips quiet
    minutes. Includes a 25-minute dead stretch (a halt by the research rule)."""
    n = WARM + 240
    start = 1_786_000_000 - 1_786_000_000 % 300
    archive = make_archive(n, start, seed=11,
                           forced_quiet=tuple(range(WARM + 100, WARM + 125)))
    while is_flat_no_trade(archive[-1]):
        archive.pop()
    bot = await make_bot(archive, Feed(connected_since=start, first=start))
    await stream(bot, archive[WARM:])

    live = bot.builder[SYM].frame()
    last_closed = int(live["time"].iloc[-1])
    want = frame([b for b in archive if b.start <= last_closed])
    assert last_closed == archive[-2].start, "all but the forming minute"
    pd.testing.assert_frame_equal(live.reset_index(drop=True), want,
                                  check_dtype=False)
    assert bot.builder[SYM].gaps == []
    assert bot.builder[SYM].stats.quiet_minutes == sum(
        is_flat_no_trade(b) for b in archive[WARM:] if b.start <= last_closed)

    # the same 5m bars, the same tradable flags
    pd.testing.assert_frame_equal(resample_complete(live, 5),
                                  resample_complete(want, 5), check_dtype=False)
    five = resample_complete(want, 5)["time"].to_numpy("int64")
    assert (resample_tradable(live, tradable_mask(live), 5, times=five)
            == resample_tradable(want, tradable_mask(want), 5, times=five)).all()

    # persisted 1m == what the strategy saw
    stored = bot.repo._s["candles"][(SYM, "1m")]
    assert sorted(stored) == live["time"].tolist()

    # every 5m bucket after warm-up was evaluated, including the dead ones
    evaluated = {r.bar_open for r in bot.repo._s["signals"].values()}
    buckets = set(five[(five >= archive[WARM].start) & (five + 300 <= last_closed + 60)])
    assert len(buckets) > 40 and buckets <= evaluated

    # and a restart's REST warm-up rebuilds the very same series
    warmed = frame(await DeltaRest(archive, warm=len(archive) - 1).warm_up(SYM, 1))
    pd.testing.assert_frame_equal(warmed, want, check_dtype=False)

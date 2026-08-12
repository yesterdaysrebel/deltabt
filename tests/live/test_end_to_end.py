"""End-to-end: real market data in, audit trail out.

Drives the whole pipeline -- candle builder, halt detection, strategy, risk
engine, paper broker, persistence -- over real cached Delta bars, on the bot's
own code path. A live smoke test cannot do this deterministically: whether a 5m
boundary is crossed in a few minutes of wall clock is luck, and whether a setup
fires in that window is more luck still.

Skips if the Parquet cache is absent, since it is gitignored (~550 MB).
"""

from __future__ import annotations

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.strategy import FROZEN
from app.market_data.normalize import Candle, CandleUpdate, Tick
from app.persistence.repository import InMemoryRepository
from app.runtime.bot import TradingBot
from tests.live.test_recovery import COSTS, DeadFeed

pytestmark = pytest.mark.asyncio

US = 1_000_000


def cached_bars(symbol="BTCUSD", n=4000):
    from deltabt.data.store import CandleStore
    df = CandleStore().read(symbol, "ltp", "1m")
    if df.empty or len(df) < n:
        pytest.skip("no cached 1m data (data/ is gitignored)")
    df = df.tail(n).reset_index(drop=True)
    return [Candle(symbol, int(r.time), float(r.open), float(r.high),
                   float(r.low), float(r.close), float(r.volume), source="rest")
            for r in df.itertuples()]


WARM = 2000


@pytest.fixture(scope="module")
def bars():
    return cached_bars()


def make_bot(store: dict, bars, **risk_over):
    """A bot warmed from the SAME real series the test then replays.

    Warming from a synthetic stub anchored to a different epoch, as the
    recovery tests do, would leave the builder holding two disjoint histories
    and make every derived 5m bucket incomplete.
    """
    class RealBackfill:
        async def warm_up(self, symbol, days, now=None):
            return list(bars[:WARM])

        async def fetch(self, *a, **k):
            return []

        async def fill_gap(self, *a, **k):
            return []

    settings = Settings(symbols=("BTCUSD",),
                        risk=RiskConfig(starting_equity=10_000.0, **risk_over))
    return TradingBot(settings, InMemoryRepository(store), COSTS,
                      strategy=FROZEN, backfiller=RealBackfill(), feed=DeadFeed())


async def drive(bot, bars, *, ticks=False):
    """Feed bars in through the builder, exactly as the websocket would.

    Not straight into on_closed_1m: that assumes the bar is already in the
    builder, and skipping the ingest path would test a path the bot never
    takes.
    """
    for b in bars[WARM:]:
        upd = CandleUpdate("BTCUSD", b.start, b.open, b.high, b.low, b.close,
                           b.volume, (b.start + 59) * US)
        for closed in bot.builder.ingest(upd):
            await bot.on_closed_1m(closed)
        if ticks:
            # Four ticks per bar: open, low, high, close.
            #
            # Feeding only the close would fill a triggered stop at the close
            # rather than near the stop itself -- measured at R = -1.84 on a
            # stop-out that should be about -1.0, because a minute can travel a
            # long way past the trigger. Walking the extremes bounds the fill
            # by the bar's own range, which is the honest answer at 1m
            # granularity. Live, real ticks make this exact rather than bounded.
            for i, px in enumerate((b.open, b.low, b.high, b.close)):
                bot._on_tick(Tick("BTCUSD", (b.start + 10 + i * 12) * US, px, px))
            await bot.drain_broker_events()


class TestEndToEnd:
    async def test_five_minute_bars_are_derived_and_evaluated(self, bars):
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars)
        assert bot.metrics.candles_5m > 300, (
            f"only {bot.metrics.candles_5m} 5m bars derived from "
            f"{len(bars) - 2000} minutes")
        assert bot.metrics.incomplete_5m == 0, "contiguous data, no partial buckets"

    async def test_every_evaluation_is_persisted_with_an_explanation(self, bars):
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars)
        rows = await bot.repo.recent_signals(limit=10_000)
        assert len(rows) == bot.metrics.candles_5m, (
            "every closed 5m bar must leave exactly one durable evaluation")
        for r in rows[:50]:
            assert r["strategy_config_hash"] == bot.strategy.config_hash
            assert r["indicators"]["primary"]["bar_open"] == r["bar_open"]
            assert r["conditions_passed"] or r["conditions_failed"] or \
                r["rejection_reason"]

    async def test_setups_fire_on_real_data(self, bars):
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars)
        assert bot.metrics.signals_detected > 0, "no setups on real data"
        rows = await bot.repo.recent_signals(limit=10_000)
        outcomes = {r["outcome"] for r in rows}
        assert outcomes <= {"NO_SETUP", "SUPPRESSED", "REJECTED", "APPROVED"}

    async def test_the_discipline_layer_throttles_the_setup_rate(self, bars):
        """Rejections require open positions, which require fills.

        Without ticks nothing fills, so nothing is ever rejected for
        `max_open_positions` or a cooldown -- which is why this runs the tick
        path. On real data the detection rate (~23/day/symbol) is far above the
        6-trades-a-day limit, so most setups must be turned away.
        """
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        assert bot.metrics.signals_detected > 0
        assert bot.metrics.signals_rejected > 0, (
            f"{bot.metrics.signals_detected} setups and not one rejection -- "
            f"the risk gates are not binding")
        rejected = [r for r in await bot.repo.recent_signals(limit=10_000)
                    if r["outcome"] == "REJECTED"]
        reasons = {r["rejection_reason"].split(":")[0] for r in rejected}
        assert reasons, "every rejection must name why"

    async def test_rejections_name_the_limit_they_hit(self, bars):
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars)
        rows = [r for r in await bot.repo.recent_signals(limit=10_000)
                if r["outcome"] == "REJECTED"]
        if not rows:
            pytest.skip("no rejections in this window")
        for r in rows:
            assert r["rejection_reason"], "a rejection with no reason is useless"

    async def test_a_full_trade_lifecycle_is_auditable(self, bars):
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        positions = await bot.repo.load_recent_positions(limit=100)
        if not positions:
            pytest.skip("no position opened in this window")
        p = positions[0]
        # Every "why?" from the brief, answered from the record alone.
        assert p.signal_key and p.strategy_version
        assert p.entry_price and p.stop_price and p.target_price
        assert p.quantity > 0 and p.initial_risk > 0
        assert p.equity_before > 0
        sig = [r for r in await bot.repo.recent_signals(limit=10_000)
               if r["idempotency_key"] == p.signal_key]
        assert sig, "a position must be traceable to the evaluation that made it"
        assert sig[0]["outcome"] == "APPROVED"
        assert sig[0]["conditions_passed"]

    async def test_risk_limits_hold_across_the_whole_run(self, bars):
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        cfg = bot.settings.risk
        for p in await bot.repo.load_recent_positions(limit=1000):
            budget = p.equity_before * cfg.risk_per_trade
            assert p.initial_risk <= budget * 1.000001, (
                f"{p.position_uid} risked {p.initial_risk} of a {budget} budget")
            # Realised RR, not planned. `minimum_rr` gates the SIGNAL; the
            # entry then slips and both legs move, so the realised figure is
            # always lower. It is floored explicitly by the broker rather than
            # assumed to equal the plan.
            rr = abs(p.target_price - p.entry_price) / abs(p.entry_price - p.stop_price)
            assert rr >= bot.broker.min_fill_rr - 1e-9, (
                f"realised RR {rr:.3f} below the fill floor")
            assert rr <= cfg.minimum_rr + 1e-9, (
                "realised RR can never EXCEED the plan on an adverse fill")
            if p.r_multiple is not None:
                # Bounded, not exactly -1: a stop is a market order, so it pays
                # taker fees and whatever the bar travelled past the trigger.
                assert -2.0 < p.r_multiple < 3.0, (
                    f"implausible R {p.r_multiple} -- check the fill model")
        assert bot.state.trades_today <= cfg.max_trades_per_day

    async def test_never_more_than_one_open_position(self, bars):
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        assert len(bot.broker.get_positions()) <= bot.settings.risk.max_open_positions

    async def test_no_duplicate_evaluations(self, bars):
        bot = make_bot({}, bars)
        await bot.start()
        await drive(bot, bars)
        rows = await bot.repo.recent_signals(limit=10_000)
        keys = [r["idempotency_key"] for r in rows]
        assert len(keys) == len(set(keys))
        assert bot.metrics.duplicate_signals == 0

    async def test_replaying_the_same_bars_creates_nothing_new(self, bars):
        """Idempotency under replay, which is what a restart does."""
        store: dict = {}
        a = make_bot(store, bars)
        await a.start()
        await drive(a, bars)
        before = len(await a.repo.recent_signals(limit=10_000))

        b = make_bot(store, bars)
        await b.start()
        await drive(b, bars)
        after = len(await b.repo.recent_signals(limit=10_000))
        assert after == before, "replay must not duplicate the audit trail"
        assert b.metrics.duplicate_signals > 0, "and must notice it is replaying"

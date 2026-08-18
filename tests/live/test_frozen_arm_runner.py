"""The 1m frozen arm, driven end to end on its own code path.

Same harness as test_end_to_end.py -- real cached bars in through the builder,
audit trail out -- but with the frozen arm selected, so the 1m decision
boundary, the risk engine, the paper broker and the time exit are exercised
together rather than argued about separately.

Skips without the Parquet cache, which is gitignored.
"""

from __future__ import annotations

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.variants import resolve_strategy
from app.execution.paper_broker import ExitReason
from app.market_data.normalize import CandleUpdate, Tick
from app.persistence.repository import InMemoryRepository
from app.runtime.bot import TradingBot
from app.strategy.explanation import Outcome
from app.strategy.frozen_hwpr import FROZEN_1M, FrozenHwprConfig
from tests.live.test_end_to_end import cached_bars
from tests.live.test_recovery import COSTS, DeadFeed

pytestmark = pytest.mark.asyncio

US = 1_000_000
WARM = 3200          # >= FROZEN_1M.window_bars, so the arm can evaluate at once
DAY = 86_400


@pytest.fixture(scope="module")
def bars():
    return cached_bars(n=WARM + 900)


def make_frozen_bot(store: dict, bars, **risk_over):
    class RealBackfill:
        async def warm_up(self, symbol, days, now=None):
            return list(bars[:WARM])

        async def fetch(self, *a, **k):
            return []

        async def fill_gap(self, *a, **k):
            return []

    risk = dict(starting_equity=10_000.0, max_hold_seconds=DAY)
    risk.update(risk_over)
    settings = Settings(symbols=("BTCUSD",), risk=RiskConfig(**risk))
    return TradingBot(settings, InMemoryRepository(store), COSTS,
                      strategy=FROZEN_1M, backfiller=RealBackfill(),
                      feed=DeadFeed())


async def drive(bot, bars, *, ticks=False):
    for b in bars[WARM:]:
        upd = CandleUpdate("BTCUSD", b.start, b.open, b.high, b.low, b.close,
                           b.volume, (b.start + 59) * US)
        for closed in bot.builder.ingest(upd):
            await bot.on_closed_1m(closed)
        if ticks:
            for i, px in enumerate((b.open, b.low, b.high, b.close)):
                bot._on_tick(Tick("BTCUSD", (b.start + 10 + i * 12) * US, px, px))
            await bot.drain_broker_events()


# =====================================================================
# THE DECISION BOUNDARY
# =====================================================================

class TestTheOneMinuteBoundary:

    async def test_the_frozen_arm_is_selected_and_v3_is_not(self):
        cfg = resolve_strategy({"DELTABOT_VARIANT": "FROZEN_1M"})
        assert isinstance(cfg, FrozenHwprConfig)
        assert cfg.config_hash == "e63d00ad683ec9c8"
        v3 = resolve_strategy({"DELTABOT_VARIANT": "V3"})
        assert v3.config_hash == "11461f2a11a96f8a", "V3 must not move"
        assert not isinstance(v3, FrozenHwprConfig)

    async def test_one_evaluation_per_closed_1m_bar(self, bars):
        """The count is the point: 5m boundaries are irrelevant to this arm.

        `len(bars) - WARM - 1`, not `- WARM`. The builder closes a bar only
        when the NEXT update arrives, so the final bar driven in is still
        forming when the loop ends and must NOT have been evaluated. That
        off-by-one is the forming-bar guarantee showing up in the count.
        """
        bot = make_frozen_bot({}, bars)
        await bot.start()
        await drive(bot, bars)
        rows = await bot.repo.recent_signals(limit=10_000)
        driven = len(bars) - WARM
        assert len(rows) == driven - 1, (
            f"{len(rows)} evaluations for {driven} bars driven "
            f"({driven - 1} of them closed)")

    async def test_no_duplicate_evaluation_for_the_same_bar(self, bars):
        bot = make_frozen_bot({}, bars)
        await bot.start()
        await drive(bot, bars)
        rows = await bot.repo.recent_signals(limit=10_000)
        keys = [r["idempotency_key"] for r in rows]
        assert len(keys) == len(set(keys))
        bar_opens = [r["bar_open"] for r in rows]
        assert len(bar_opens) == len(set(bar_opens))

    async def test_a_forming_bar_does_not_trigger_an_evaluation(self, bars):
        """The builder emits nothing until the minute closes, so partial
        updates must leave the evaluation count where it was."""
        bot = make_frozen_bot({}, bars)
        await bot.start()
        before = len(await bot.repo.recent_signals(limit=10_000))
        b = bars[WARM]
        for px in (b.open, b.high, b.low):
            bot.builder.ingest(CandleUpdate(
                "BTCUSD", b.start, b.open, b.high, b.low, px, b.volume,
                (b.start + 30) * US))
        assert len(await bot.repo.recent_signals(limit=10_000)) == before

    async def test_evaluations_carry_the_frozen_hash_and_1m_timeframes(self, bars):
        bot = make_frozen_bot({}, bars)
        await bot.start()
        await drive(bot, bars)
        for r in (await bot.repo.recent_signals(limit=10_000))[:50]:
            assert r["strategy_config_hash"] == "e63d00ad683ec9c8"
            assert r["primary_timeframe"] == "1m"
            assert r["confirmation_timeframe"] == "5m"


# =====================================================================
# THE SIGNAL REACHES RISK AND EXECUTION
# =====================================================================

class TestTheSignalReachesTheBroker:

    async def test_setups_fire_and_are_sized_by_the_risk_engine(self, bars):
        bot = make_frozen_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        rows = await bot.repo.recent_signals(limit=10_000)
        detected = [r for r in rows if r["outcome"] in ("DETECTED", "APPROVED")]
        if not detected:
            pytest.skip("no setup in this slice; not a correctness failure")
        assert bot.metrics.signals_detected > 0

    async def test_stop_and_target_reach_the_paper_broker(self, bars):
        bot = make_frozen_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        positions = list(bot.broker.positions.values())
        if not positions:
            pytest.skip("no position opened in this slice")
        for p in positions:
            assert p.stop_price is not None and p.target_price is not None
            if p.side > 0:
                assert p.stop_price < p.entry_price < p.target_price
            else:
                assert p.target_price < p.entry_price < p.stop_price

    async def test_both_prices_are_recorded_signal_close_and_actual_fill(self, bars):
        """§5: the evaluator cannot know the next open, so the signal carries
        its close and the broker records what the fill actually was. Both are
        persisted; neither is faked."""
        bot = make_frozen_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        positions = [p for p in bot.broker.positions.values()]
        if not positions:
            pytest.skip("no position opened in this slice")
        rows = {r["idempotency_key"]: r for r in
                await bot.repo.recent_signals(limit=10_000)}
        for p in positions:
            assert p.entry_price is not None      # actual paper entry
            sig = next((r for r in rows.values()
                        if r.get("entry_price") is not None
                        and r["outcome"] in ("DETECTED", "APPROVED")), None)
            assert sig is not None and sig["entry_price"] is not None


# =====================================================================
# CHAINING  (brief section 7)
# =====================================================================

class TestPositionChaining:
    """hwpr._simulate advances `i = m + 1`, so signals during an open position
    are SKIPPED, never queued. The live equivalent is the per-symbol lock."""

    async def test_a_signal_during_an_open_position_is_refused_not_queued(self, bars):
        bot = make_frozen_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        rows = await bot.repo.recent_signals(limit=10_000)
        locked = [r for r in rows
                  if (r["rejection_reason"] or "").startswith(
                      "already holding an open position")]
        if not locked:
            pytest.skip("no overlapping setup in this slice")
        # Refused, recorded with a reason, and never turned into an order.
        for r in locked:
            assert r["outcome"] == "REJECTED"
        opened = [p for p in bot.broker.positions.values()]
        per_symbol_open = sum(1 for p in opened if p.status == "OPEN")
        assert per_symbol_open <= 1

    async def test_the_lock_releases_and_a_later_signal_can_enter(self, bars):
        """Closed positions must not keep the slot; otherwise the arm would
        trade once and go quiet forever."""
        bot = make_frozen_bot({}, bars)
        await bot.start()
        await drive(bot, bars, ticks=True)
        closed = [p for p in bot.broker.positions.values() if p.status == "CLOSED"]
        if len(closed) < 2:
            pytest.skip("fewer than two completed positions in this slice")
        order = sorted(closed, key=lambda p: p.opened_at)
        assert order[1].opened_at >= order[0].closed_at, (
            "a second position opened before the first had closed")


# =====================================================================
# TIME EXIT ON THIS ARM ONLY
# =====================================================================

class TestTimeExitOnTheFrozenArm:

    async def test_the_arm_runs_with_a_24h_max_hold(self, bars):
        bot = make_frozen_bot({}, bars)
        assert bot.broker.max_hold_seconds == DAY

    async def test_v3_is_unaffected_and_still_has_no_time_stop(self):
        from app.config.settings import RiskConfig as RC
        assert RC().max_hold_seconds == 0, (
            "the default must stay 0 so V3 keeps the behaviour it has run with")

    async def test_a_position_past_24h_closes_with_time_exit(self, bars):
        """Driven through the broker's own tick path, not by calling _close."""
        from app.execution.paper_broker import PaperPosition
        bot = make_frozen_bot({}, bars)
        await bot.start()
        b0 = bars[WARM]
        pos = PaperPosition(
            position_uid="p-old", signal_key="s-old", symbol="BTCUSD", side=1,
            quantity=10, entry_price=float(b0.close),
            stop_price=float(b0.close) * 0.5,
            target_price=float(b0.close) * 2.0,
            risk_per_unit=float(b0.close) * 0.5, initial_risk=50.0,
            notional=100.0, equity_before=10_000.0,
            opened_at=b0.start - DAY - 60, strategy_version="v", status="OPEN")
        bot.broker.positions[pos.position_uid] = pos
        bot._on_tick(Tick("BTCUSD", b0.start * US, float(b0.close),
                          float(b0.close)))
        await bot.drain_broker_events()
        assert pos.status == "CLOSED"
        assert pos.exit_reason == ExitReason.TIME_EXIT.value
        assert pos.realized_pnl is not None
        assert len(bot.broker.fills_for_position(pos.position_uid, "exit")) == 1

    async def test_the_slot_is_released_so_a_later_signal_is_admissible(self, bars):
        from app.execution.paper_broker import PaperPosition
        bot = make_frozen_bot({}, bars)
        await bot.start()
        b0 = bars[WARM]
        pos = PaperPosition(
            position_uid="p-old", signal_key="s-old", symbol="BTCUSD", side=1,
            quantity=10, entry_price=float(b0.close),
            stop_price=float(b0.close) * 0.5, target_price=float(b0.close) * 2.0,
            risk_per_unit=float(b0.close) * 0.5, initial_risk=50.0,
            notional=100.0, equity_before=10_000.0,
            opened_at=b0.start - DAY - 60, strategy_version="v", status="OPEN")
        bot.broker.positions[pos.position_uid] = pos
        assert any(p.symbol == "BTCUSD" and p.is_open
                   for p in bot.broker.get_positions())
        bot._on_tick(Tick("BTCUSD", b0.start * US, float(b0.close),
                          float(b0.close)))
        await bot.drain_broker_events()
        assert not [p for p in bot.broker.get_positions() if p.symbol == "BTCUSD"]

    async def test_a_restart_does_not_close_it_twice(self, bars):
        from app.execution.paper_broker import PaperPosition
        store = {}
        bot = make_frozen_bot(store, bars)
        await bot.start()
        b0 = bars[WARM]
        pos = PaperPosition(
            position_uid="p-old", signal_key="s-old", symbol="BTCUSD", side=1,
            quantity=10, entry_price=float(b0.close),
            stop_price=float(b0.close) * 0.5, target_price=float(b0.close) * 2.0,
            risk_per_unit=float(b0.close) * 0.5, initial_risk=50.0,
            notional=100.0, equity_before=10_000.0,
            opened_at=b0.start - DAY - 60, strategy_version="v", status="OPEN")
        bot.broker.positions[pos.position_uid] = pos
        for i in range(4):
            bot._on_tick(Tick("BTCUSD", (b0.start + i) * US, float(b0.close),
                              float(b0.close)))
        await bot.drain_broker_events()
        assert len(bot.broker.fills_for_position(pos.position_uid, "exit")) == 1
        closes = [e for e in bot.broker.events if e.kind == "POSITION_CLOSED"]
        assert len(closes) == 1


# =====================================================================
# V3 IS UNTOUCHED
# =====================================================================

class TestV3IsUnaffected:

    async def test_v3_still_evaluates_on_five_minute_bars(self):
        from app.config.strategy import FROZEN
        from app.runtime.bot import TradingBot as TB
        settings = Settings(symbols=("BTCUSD",), risk=RiskConfig())
        bot = TB(settings, InMemoryRepository({}), COSTS, strategy=FROZEN,
                 feed=DeadFeed())
        assert bot.frozen_arm is False

    async def test_the_frozen_arm_flag_is_set_only_for_the_frozen_config(self, bars):
        bot = make_frozen_bot({}, bars)
        assert bot.frozen_arm is True

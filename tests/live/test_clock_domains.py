"""Clock-domain separation, and proof that container skew cannot move a decision.

AUDIT FINDING F8. Cooldowns, daily rollover and order expiry compared against
`time.time()` while bars and ticks carried exchange timestamps. Live the two
coincide, which is why 960 tests missed it. It meant a rejection depended on
when the PROCESS saw a signal rather than when the MARKET produced it, so the
forward test could not be verified by replaying its own record.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.clock import EventTime, MarketClock, wall_now
from app.config.strategy import FROZEN
from app.market_data.normalize import Candle, Tick
from app.runtime.bot import idempotency_key
from tests.live.test_recovery import make_bot

pytestmark = pytest.mark.asyncio
US = 1_000_000
ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Exchange time far from any plausible wall clock, so a leak is unmissable.
MKT = 1_600_000_000          # 2020-09-13


# =====================================================================
# THE ABSTRACTION
# =====================================================================


class TestMarketClock:
    async def test_starts_unset(self):
        c = MarketClock()
        assert c.now() == 0 and c.is_set is False

    async def test_advances_on_observation(self):
        c = MarketClock()
        c.observe(MKT)
        assert c.now() == MKT and c.is_set

    async def test_is_monotonic(self):
        """A late or replayed message must never rewind market time.

        If it could, an out-of-order tick would revive an expired cooldown.
        """
        c = MarketClock()
        c.observe(MKT)
        c.observe(MKT - 3600)
        assert c.now() == MKT

    async def test_event_time_records_both_domains(self):
        et = EventTime.at(MKT)
        assert et.exchange_ts == MKT
        assert et.received_ts > 1_700_000_000        # a real wall clock
        assert et.lag_seconds > 0


# =====================================================================
# STATIC RULE: no market decision may read the wall clock
# =====================================================================


def _wall_clock_calls(path: pathlib.Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "time" and getattr(node.func.value, "id", "") == "time":
                hits.append(node.lineno)
    return hits


class TestNoWallClockInDecisions:
    """Enforced against the source, so the fix cannot quietly regress."""

    @pytest.mark.parametrize("module", [
        "app/runtime/bot.py",
        "app/risk/engine.py",
        "app/execution/paper_broker.py",
        "app/strategy/rules.py",
    ], ids=lambda p: p)
    async def test_module_never_calls_time_time(self, module):
        hits = _wall_clock_calls(ROOT / module)
        assert not hits, (
            f"{module} calls time.time() at line(s) {hits}. Market decisions "
            f"must use the MarketClock; only app/monitoring and app/clock.py "
            f"may read the wall clock.")

    async def test_the_wall_clock_still_exists_where_it_belongs(self):
        """Staleness genuinely needs it -- the guard must not be absolute."""
        from app.monitoring import health
        assert "time" in dir(health)
        assert wall_now() > 1_700_000_000


# =====================================================================
# SKEW CANNOT MOVE A DECISION
# =====================================================================


async def _evaluate_at(bot, symbol, bar_open, direction, entry, stop, target):
    """Drive one decision through the real risk path at a given MARKET time."""
    from app.strategy.explanation import Explanation, Outcome
    exp = Explanation(symbol=symbol, bar_open=bar_open, primary_timeframe="5m",
                      confirmation_timeframe="1m",
                      strategy_version=FROZEN.version,
                      strategy_config_hash=FROZEN.config_hash,
                      outcome=Outcome.DETECTED, direction=direction)
    exp.entry_price, exp.stop_price, exp.target_price = entry, stop, target
    exp.detail["risk_per_unit"] = abs(entry - stop)
    exp.detail["idempotency_key"] = idempotency_key(
        symbol, bar_open, direction, FROZEN.config_hash)
    market_now = bar_open + 300
    return bot.risk.evaluate(exp, bot.state,
                             open_positions=bot.broker.get_positions(),
                             now=market_now, market_can_trade=True), exp


class TestSkewCannotChangeDecisions:
    """The whole point of F8: shift the container clock, change nothing."""

    @pytest.mark.parametrize("skew", [0, -86_400, +86_400, +365 * 86_400],
                             ids=["none", "-1d", "+1d", "+1y"])
    async def test_signal_approval_is_unaffected_by_skew(self, skew, monkeypatch):
        import app.clock as clockmod
        monkeypatch.setattr(clockmod, "wall_now", lambda: 1_800_000_000 + skew)
        bot = make_bot({})
        await bot.start()
        d, _ = await _evaluate_at(bot, "BTCUSD", MKT, 1, 63_000.0, 62_500.0, 64_000.0)
        assert d.approved is True, d.reason
        assert d.intent.quantity == 100

    async def test_cooldown_is_measured_in_market_time(self):
        """A trade at market T blocks entries until T + cooldown, whatever the
        wall clock says."""
        bot = make_bot({})
        await bot.start()
        bot.state.roll_day(MKT)
        bot.state.last_trade_at = MKT

        cd = bot.settings.risk.cooldown_after_trade_seconds
        early, _ = await _evaluate_at(bot, "BTCUSD", MKT + cd - 600, 1,
                                      63_000.0, 62_500.0, 64_000.0)
        assert not early.approved
        assert early.limit_name == "cooldown_after_trade_seconds"

        late, _ = await _evaluate_at(bot, "BTCUSD", MKT + cd + 600, 1,
                                     63_000.0, 62_500.0, 64_000.0)
        assert late.approved, late.reason

    async def test_the_bug_itself_is_gone(self):
        """The exact failure the audit found.

        Replaying 7 days of bars in ~10 minutes of wall clock produced 630
        'cooldown after trade' rejections from a SINGLE 15-minute cooldown,
        because the cooldown was measured against the wall clock. Market time
        advances with the bars, so it must now expire.
        """
        bot = make_bot({})
        await bot.start()
        bot.state.roll_day(MKT)
        bot.state.last_trade_at = MKT
        rejected = 0
        for i in range(1, 200):                       # 200 bars = ~16 hours
            d, _ = await _evaluate_at(bot, "BTCUSD", MKT + i * 300, 1,
                                      63_000.0, 62_500.0, 64_000.0)
            if not d.approved and d.limit_name == "cooldown_after_trade_seconds":
                rejected += 1
        cd = bot.settings.risk.cooldown_after_trade_seconds
        assert rejected <= cd // 300 + 1, (
            f"{rejected} cooldown rejections from one {cd}s cooldown -- "
            f"market time is not advancing")

    async def test_daily_rollover_follows_market_days(self):
        bot = make_bot({})
        await bot.start()
        bot.state.roll_day(MKT)
        bot.state.trades_today = 6
        assert bot.state.roll_day(MKT + 3600) is False
        assert bot.state.roll_day(MKT + 86_400) is True
        assert bot.state.trades_today == 0

    async def test_daily_limit_uses_market_time_not_wall_time(self, monkeypatch):
        import app.clock as clockmod
        monkeypatch.setattr(clockmod, "wall_now", lambda: 1_900_000_000)
        bot = make_bot({})
        await bot.start()
        bot.state.roll_day(MKT)
        bot.state.trades_today = 6
        blocked, _ = await _evaluate_at(bot, "BTCUSD", MKT + 300, 1,
                                        63_000.0, 62_500.0, 64_000.0)
        assert blocked.limit_name == "max_trades_per_day"
        # Next market day: the counter resets even though the wall clock moved
        # nowhere near a day.
        ok, _ = await _evaluate_at(bot, "BTCUSD", MKT + 90_000, 1,
                                   63_000.0, 62_500.0, 64_000.0)
        assert ok.approved, ok.reason


class TestOrderExpiryClock:
    async def test_expiry_is_market_time_on_both_sides(self):
        """created_at and the tick timestamp must be the same clock."""
        bot = make_bot({})
        await bot.start()
        d, exp = await _evaluate_at(bot, "BTCUSD", MKT, 1,
                                    63_000.0, 62_500.0, 64_000.0)
        order = bot.broker.submit_order(d.intent, now=MKT + 300)
        assert order.created_at == MKT + 300

        # A tick two seconds later (market time) fills it.
        bot._on_tick(Tick("BTCUSD", (MKT + 302) * US, 63_000.0, 63_000.0))
        assert len(bot.broker.get_positions()) == 1

    async def test_a_stale_order_expires_on_market_time(self):
        bot = make_bot({})
        await bot.start()
        d, _ = await _evaluate_at(bot, "BTCUSD", MKT, 1,
                                  63_000.0, 62_500.0, 64_000.0)
        o = bot.broker.submit_order(d.intent, now=MKT + 300)
        ttl = bot.broker.entry_ttl_seconds
        bot._on_tick(Tick("BTCUSD", (MKT + 300 + ttl + 10) * US, 63_000.0, 63_000.0))
        from app.execution.paper_broker import OrderStatus
        assert bot.broker.orders[o.order_uid].status is OrderStatus.EXPIRED
        assert bot.broker.get_positions() == []

    async def test_wall_clock_skew_cannot_expire_a_fresh_order(self, monkeypatch):
        """Under the old code a skewed container expired everything instantly."""
        import app.clock as clockmod
        monkeypatch.setattr(clockmod, "wall_now", lambda: 2_000_000_000)
        bot = make_bot({})
        await bot.start()
        d, _ = await _evaluate_at(bot, "BTCUSD", MKT, 1,
                                  63_000.0, 62_500.0, 64_000.0)
        bot.broker.submit_order(d.intent, now=MKT + 300)
        bot._on_tick(Tick("BTCUSD", (MKT + 302) * US, 63_000.0, 63_000.0))
        assert len(bot.broker.get_positions()) == 1


# =====================================================================
# THE CLOCK ADVANCES FROM MARKET DATA
# =====================================================================


class TestClockAdvancement:
    async def test_a_tick_advances_market_time(self):
        bot = make_bot({})
        await bot.start()
        bot._on_tick(Tick("BTCUSD", MKT * US, 63_000.0, 63_000.0))
        assert bot.clock.now() == MKT

    async def test_a_closed_bar_advances_to_its_CLOSE(self):
        bot = make_bot({})
        await bot.start()
        await bot.on_closed_1m(Candle("BTCUSD", MKT, 63_000.0, 63_100.0,
                                      62_900.0, 63_050.0, 5.0))
        assert bot.clock.now() == MKT + 60, "a bar is knowable at its close"

    async def test_an_unknown_symbol_does_not_advance_the_clock(self):
        bot = make_bot({})
        await bot.start()
        before = bot.clock.now()
        bot._on_tick(Tick("DOGEUSD", (MKT + 999) * US, 0.1, 0.1))
        assert bot.clock.now() == before

    async def test_health_reports_both_clocks(self):
        bot = make_bot({})
        await bot.start()
        bot._on_tick(Tick("BTCUSD", MKT * US, 63_000.0, 63_000.0))
        h = bot.health_snapshot()
        assert h["market_time"] == MKT
        assert h["uptime_seconds"] >= 0        # wall clock, correctly


# =====================================================================
# BOTH TIMESTAMPS ARE PERSISTED
# =====================================================================


class TestPersistedTimestamps:
    async def test_every_signal_carries_both(self):
        from app.strategy.explanation import Explanation, Outcome
        bot = make_bot({})
        await bot.start()
        exp = Explanation(symbol="BTCUSD", bar_open=MKT, primary_timeframe="5m",
                          confirmation_timeframe="1m",
                          strategy_version=FROZEN.version,
                          strategy_config_hash=FROZEN.config_hash,
                          outcome=Outcome.NO_SETUP)
        await bot._record_signal(
            exp, idempotency_key("BTCUSD", MKT, None, FROZEN.config_hash))
        row = (await bot.repo.recent_signals())[0]
        assert row["exchange_ts"] == MKT + 300
        assert row["received_ts"] > 1_700_000_000
        assert row["event_type"] == "SIGNAL_EVALUATED"

    async def test_lag_is_derivable(self):
        """received_ts - exchange_ts is the processing lag, which a single
        timestamp cannot express."""
        et = EventTime.at(MKT)
        assert et.received_ts - et.exchange_ts == pytest.approx(et.lag_seconds)

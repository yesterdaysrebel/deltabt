"""Restart recovery, idempotency, and the health/readiness endpoints.

The acceptance criterion from the brief: kill the process at each of five
moments, restart, and end up in exactly the right state. "Restart" here means
constructing a brand-new bot over the SAME durable store, which is what a
`kill -9` followed by a pod restart actually looks like -- no shutdown hook
runs, nothing in memory survives.
"""

from __future__ import annotations

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.strategy import FROZEN
from app.execution.paper_broker import ExitReason
from app.market_data.normalize import Candle, CandleUpdate, Tick
from app.monitoring.health import evaluate_health, evaluate_readiness
from app.persistence.repository import InMemoryRepository
from app.risk.engine import RiskState
from app.runtime.bot import STATE_KEY, TradingBot, idempotency_key
from deltabt.costs import SymbolCosts

pytestmark = pytest.mark.asyncio

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)
COSTS = {"BTCUSD": BTC}
US = 1_000_000


class StubBackfill:
    """Enough synthetic 1m history to clear indicator warm-up.

    Warm-up needs 145 closed 5m bars, so 900 minutes. Returning nothing would
    make every recovery test fail on warm-up before reaching what it tests --
    which is itself correct bot behaviour, just not what is under test here.
    """

    def __init__(self, bars: int = 900, first: int = 1786500000):
        self.bars, self.first = bars, first - (first % 300)

    async def warm_up(self, symbol, days, now=None):
        out = []
        for i in range(self.bars):
            px = 63_000.0 + (i % 40) * 5.0
            out.append(Candle(symbol, self.first + i * 60, px, px + 12.0,
                              px - 12.0, px + 4.0, 10.0, source="rest"))
        return out

    async def fetch(self, *a, **k):
        return []


class DeadFeed:
    """A feed that never connects, so tests drive bars directly."""

    class _S:
        connected = False
        messages = reconnects = stale_events = errors = 0
        last_message_at = 0.0
        connected_since = None
        seconds_since_last_message = float("inf")

        def as_dict(self):
            return {"websocket_messages": 0, "websocket_reconnects": 0,
                    "stale_feed_events": 0, "last_message_at": 0}

    stats = _S()

    def __init__(self, *a, **k):
        pass

    async def run(self):
        return None

    def stop(self):
        return None


def make_bot(store: dict, *, equity=10_000.0, **risk_over):
    settings = Settings(symbols=("BTCUSD",),
                        risk=RiskConfig(starting_equity=equity, **risk_over))
    return TradingBot(settings, InMemoryRepository(store), COSTS,
                      strategy=FROZEN, backfiller=StubBackfill(), feed=DeadFeed())


async def open_a_position(bot, *, entry=63_000.0, stop=62_500.0,
                          target=64_000.0, ts=1786560300):
    """Drive a position through the real risk -> broker -> persistence path."""
    from app.strategy.explanation import Explanation, Outcome
    exp = Explanation(symbol="BTCUSD", bar_open=ts, primary_timeframe="5m",
                      confirmation_timeframe="1m",
                      strategy_version=FROZEN.version,
                      strategy_config_hash=FROZEN.config_hash,
                      outcome=Outcome.DETECTED, direction=1)
    exp.entry_price, exp.stop_price, exp.target_price = entry, stop, target
    exp.detail["risk_per_unit"] = entry - stop
    key = idempotency_key("BTCUSD", ts, 1, FROZEN.config_hash)
    exp.detail["idempotency_key"] = key
    await bot._record_signal(exp, key)

    d = bot.risk.evaluate(exp, bot.state, open_positions=bot.broker.get_positions(),
                          now=ts, market_can_trade=True)
    assert d.approved, d.reason
    # market time, not wall time -- the order's expiry is compared against tick
    # timestamps, which are exchange time (audit F8)
    await bot._place(exp, d, ts)
    # Through the bot's own tick path, not the broker directly: the broker
    # returns events, but it is _on_tick that queues them for persistence.
    bot._on_tick(Tick("BTCUSD", ts * US, entry, entry))
    await bot.drain_broker_events()
    return d


# =====================================================================
# RESTART RECOVERY -- the five moments from the brief
# =====================================================================


class TestRestartRecovery:
    async def test_open_position_survives_a_hard_restart(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await open_a_position(a)
        before = a.broker.get_positions()[0]
        snapshot = (before.entry_price, before.stop_price, before.target_price,
                    before.quantity, before.initial_risk)

        # kill -9: no stop(), nothing in memory survives.
        b = make_bot(store)
        await b.start()

        after = b.broker.get_positions()
        assert len(after) == 1, "exactly one position, no duplicate"
        p = after[0]
        assert (p.entry_price, p.stop_price, p.target_price, p.quantity,
                p.initial_risk) == snapshot
        assert p.position_uid == before.position_uid

    async def test_restart_does_not_reopen_the_same_signal(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await open_a_position(a)

        b = make_bot(store)
        await b.start()
        # Replaying the identical evaluation must be refused.
        from app.strategy.explanation import Explanation, Outcome
        key = idempotency_key("BTCUSD", 1786560300, 1, FROZEN.config_hash)
        assert await b.repo.signal_exists(key)
        exp = Explanation(symbol="BTCUSD", bar_open=1786560300,
                          primary_timeframe="5m", confirmation_timeframe="1m",
                          strategy_version=FROZEN.version,
                          strategy_config_hash=FROZEN.config_hash,
                          outcome=Outcome.DETECTED, direction=1)
        assert await b._record_signal(exp, key) is False
        assert len(b.broker.get_positions()) == 1

    async def test_risk_state_survives_a_restart(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        a.state.consecutive_losses = 2
        a.state.trades_today = 4
        a.state.equity = 9_800.0
        a.state.roll_day(1786560300)
        a.state.consecutive_losses = 2
        a.state.trades_today = 4
        await a._save_state()

        b = make_bot(store)
        await b.start()
        assert b.state.consecutive_losses == 2
        assert b.state.trades_today == 4
        assert b.state.equity == 9_800.0
        assert b.broker.equity == 9_800.0

    async def test_a_recovered_position_is_immediately_protected(self):
        """The ticks that would have stopped it while down are gone."""
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await open_a_position(a, entry=63_000.0, stop=62_500.0, target=64_000.0)

        b = make_bot(store)
        await b.start()
        p = b.broker.get_positions()[0]
        assert p.armed_after_us is None, "must not wait for a post-entry tick"
        b.broker.process_market_event(
            Tick("BTCUSD", 1786570000 * US, 62_400.0, 62_400.0))
        assert p.exit_reason == ExitReason.STOP_LOSS.value

    async def test_crash_after_signal_before_fill_leaves_no_position(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        from app.strategy.explanation import Explanation, Outcome
        exp = Explanation(symbol="BTCUSD", bar_open=1786560300,
                          primary_timeframe="5m", confirmation_timeframe="1m",
                          strategy_version=FROZEN.version,
                          strategy_config_hash=FROZEN.config_hash,
                          outcome=Outcome.DETECTED, direction=1)
        exp.entry_price, exp.stop_price, exp.target_price = 63_000.0, 62_500.0, 64_000.0
        exp.detail["risk_per_unit"] = 500.0
        key = idempotency_key("BTCUSD", 1786560300, 1, FROZEN.config_hash)
        exp.detail["idempotency_key"] = key
        await a._record_signal(exp, key)
        d = a.risk.evaluate(exp, a.state, open_positions=[], now=1786560300)
        await a._place(exp, d, 1786560300)          # order created, never filled -- crash

        b = make_bot(store)
        await b.start()
        assert b.broker.get_positions() == [], (
            "an unfilled order must not become a position on restart")
        assert b.state.trades_today == 0

    async def test_crash_after_fill_does_not_double_fill(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await open_a_position(a)
        assert a.metrics.fills == 1
        fills_before = len(store["fills_by_order"])

        b = make_bot(store)
        await b.start()
        await b.drain_broker_events()
        assert len(store["fills_by_order"]) == fills_before

    async def test_closed_trade_history_survives(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await open_a_position(a)
        a._on_tick(Tick("BTCUSD", 1786560600 * US, 64_500.0, 64_500.0))
        await a.drain_broker_events()
        assert a.state.wins == 1

        b = make_bot(store)
        await b.start()
        assert b.broker.get_positions() == []
        assert b.state.wins == 1
        rows = await b.repo.load_recent_positions()
        assert rows[0].exit_reason == ExitReason.TAKE_PROFIT.value
        assert rows[0].status == "CLOSED"

    async def test_restart_while_a_candle_is_forming_loses_only_that_candle(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        closed_before = a.builder["BTCUSD"].last_closed_1m.start
        a.builder.ingest(CandleUpdate("BTCUSD", 1786560000, 63_000, 63_100,
                                      62_900, 63_050, 5.0, 1786560030 * US))
        assert a.builder["BTCUSD"].forming_start == 1786560000
        assert a.builder["BTCUSD"].last_closed_1m.start == closed_before, (
            "a forming bar must not be counted as closed")

        b = make_bot(store)
        await b.start()
        # The forming bar is simply gone. Nothing half-built is carried over,
        # and the backfill re-fetches the minute once it has actually closed.
        assert b.builder["BTCUSD"].forming_start is None


# =====================================================================
# RECONCILIATION
# =====================================================================


class TestReconciliation:
    async def test_duplicate_open_positions_block_startup(self):
        """Corrupt state must stop the bot, not be quietly tidied up."""
        from app.persistence.models import PositionRecord
        store: dict = {}
        repo = InMemoryRepository(store)
        await repo.connect()

        def pos(uid, sig):
            return PositionRecord(
                position_uid=uid, signal_key=sig, instance_uid="old",
                symbol="BTCUSD", side=1, status="OPEN", quantity=10,
                entry_price=63_000.0, stop_price=62_500.0, target_price=64_000.0,
                initial_risk=50.0, risk_per_unit=500.0, notional=630.0,
                equity_before=10_000.0, opened_at=1786560300,
                strategy_version=FROZEN.version)

        # Bypass the constraint to simulate corruption.
        store["positions"]["p1"] = pos("p1", "s1")
        store["positions"]["p2"] = pos("p2", "s2")

        b = make_bot(store)
        assert await b.start() is False
        assert "duplicate open positions" in b.recovery_error
        assert b.ready is False

    async def test_position_in_an_unconfigured_symbol_blocks_startup(self):
        from app.persistence.models import PositionRecord
        store: dict = {}
        store.setdefault("positions", {})["p1"] = PositionRecord(
            position_uid="p1", signal_key="s1", instance_uid="old",
            symbol="DOGEUSD", side=1, status="OPEN", quantity=10,
            entry_price=0.1, stop_price=0.09, target_price=0.12,
            initial_risk=50.0, risk_per_unit=0.01, notional=1.0,
            equity_before=10_000.0, opened_at=1786560300,
            strategy_version=FROZEN.version)
        b = make_bot(store)
        assert await b.start() is False
        assert "not in the configured universe" in b.recovery_error

    async def test_clean_startup_reports_ready(self):
        b = make_bot({})
        assert await b.start() is True
        assert b.ready and b.recovery_error is None


# =====================================================================
# IDEMPOTENCY KEYS
# =====================================================================


class TestIdempotency:
    async def test_key_is_deterministic(self):
        a = idempotency_key("BTCUSD", 1786560000, 1, "abc")
        b = idempotency_key("BTCUSD", 1786560000, 1, "abc")
        assert a == b

    async def test_key_separates_symbol_bar_and_direction(self):
        base = idempotency_key("BTCUSD", 1786560000, 1, "abc")
        assert base != idempotency_key("ETHUSD", 1786560000, 1, "abc")
        assert base != idempotency_key("BTCUSD", 1786560300, 1, "abc")
        assert base != idempotency_key("BTCUSD", 1786560000, -1, "abc")

    async def test_a_config_change_produces_a_different_key(self):
        """Otherwise a rule change would be silently discarded as a duplicate."""
        assert (idempotency_key("BTCUSD", 1786560000, 1, "abc") !=
                idempotency_key("BTCUSD", 1786560000, 1, "def"))

    async def test_no_setup_evaluations_get_a_stable_key_too(self):
        a = idempotency_key("BTCUSD", 1786560000, None, "abc")
        assert a == idempotency_key("BTCUSD", 1786560000, None, "abc")
        assert a != idempotency_key("BTCUSD", 1786560000, 1, "abc")


# =====================================================================
# HEALTH / READINESS -- section 14
# =====================================================================


def snap(**over):
    base = {"ws_connected": True, "seconds_since_ws_message": 2.0,
            "last_closed_1m": 1786560000, "recent_gaps": 0,
            "strategy_running": True, "ready": True, "recovery_error": None,
            "open_positions": 0, "equity": 10_000.0, "uptime_seconds": 100.0}
    base.update(over)
    return base


class TestHealth:
    async def test_healthy_when_everything_holds(self):
        r = evaluate_health(snap(), db_writable=True, now=1786560030)
        assert r.healthy and r.status_code == 200

    async def test_stale_websocket_is_unhealthy(self):
        """The failure every process-level probe misses."""
        r = evaluate_health(snap(seconds_since_ws_message=45.0),
                            db_writable=True, now=1786560030)
        assert not r.healthy and r.status_code == 503
        assert "websocket_fresh" in r.failures

    async def test_stale_candles_are_unhealthy(self):
        r = evaluate_health(snap(), db_writable=True, now=1786560000 + 200)
        assert not r.healthy
        assert "candles_fresh" in r.failures

    async def test_recent_gap_is_unhealthy(self):
        r = evaluate_health(snap(recent_gaps=2), db_writable=True,
                            now=1786560030)
        assert not r.healthy and "no_recent_gaps" in r.failures

    async def test_unwritable_database_is_unhealthy(self):
        """An unrecorded trade is worse than a missed one."""
        r = evaluate_health(snap(), db_writable=False, now=1786560030)
        assert not r.healthy and "database_writable" in r.failures

    async def test_stopped_strategy_is_unhealthy(self):
        r = evaluate_health(snap(strategy_running=False), db_writable=True,
                            now=1786560030)
        assert not r.healthy and "strategy_running" in r.failures

    async def test_every_failing_check_is_named(self):
        r = evaluate_health(snap(seconds_since_ws_message=999, recent_gaps=3,
                                 strategy_running=False),
                            db_writable=False, now=1786560030)
        assert set(r.failures) >= {"websocket_fresh", "no_recent_gaps",
                                   "database_writable", "strategy_running"}
        assert all(c.detail or c.ok for c in r.checks)


class TestReadiness:
    def _ready(self, **over):
        kw = dict(db_connected=True, lock_held=True, backfill_complete=True,
                  indicators_warm=True, execution_ready=True)
        kw.update(over)
        return evaluate_readiness(snap(), **kw)

    async def test_ready_when_startup_is_complete(self):
        assert self._ready().healthy

    @pytest.mark.parametrize("field,check", [
        ("db_connected", "database_connected"),
        ("lock_held", "advisory_lock_held"),
        ("backfill_complete", "backfill_complete"),
        ("indicators_warm", "indicators_warm"),
        ("execution_ready", "execution_initialized"),
    ])
    async def test_each_precondition_blocks_readiness(self, field, check):
        r = self._ready(**{field: False})
        assert not r.healthy and check in r.failures

    async def test_unresolved_recovery_blocks_readiness(self):
        r = evaluate_readiness(snap(recovery_error="duplicate positions"),
                               db_connected=True, lock_held=True,
                               backfill_complete=True, indicators_warm=True,
                               execution_ready=True)
        assert not r.healthy and "no_unresolved_recovery" in r.failures

    async def test_no_candles_yet_blocks_readiness(self):
        r = evaluate_readiness(snap(last_closed_1m=None), db_connected=True,
                               lock_held=True, backfill_complete=True,
                               indicators_warm=True, execution_ready=True)
        assert not r.healthy and "candles_synchronized" in r.failures


# =====================================================================
# END-TO-END: bars in, audit trail out
# =====================================================================


async def test_a_halt_suspends_positions_and_survives_restart():
    store: dict = {}
    a = make_bot(store)
    await a.start()
    await open_a_position(a)

    # 25 flat zero-volume 1m bars: maintenance.
    for i in range(25):
        await a.on_closed_1m(Candle("BTCUSD", 1786560360 + i * 60,
                                    63_000.0, 63_000.0, 63_000.0, 63_000.0, 0.0))
    assert not a.halts["BTCUSD"].can_trade
    assert a.broker.get_positions()[0].status == "SUSPENDED"

    # A violent move during the halt must not fill anything.
    a.broker.process_market_event(Tick("BTCUSD", 1786562000 * US, 50_000.0, 50_000.0))
    assert a.broker.get_positions()[0].exit_reason is None


async def test_evaluations_are_persisted_with_their_reasons():
    store: dict = {}
    bot = make_bot(store)
    await bot.start()
    from app.strategy.explanation import Explanation, Outcome
    exp = Explanation(symbol="BTCUSD", bar_open=1786560300,
                      primary_timeframe="5m", confirmation_timeframe="1m",
                      strategy_version=FROZEN.version,
                      strategy_config_hash=FROZEN.config_hash,
                      outcome=Outcome.NO_SETUP)
    exp.conditions_failed = ["primary_adx_ge_min", "primary_wpr_rising"]
    key = idempotency_key("BTCUSD", 1786560300, None, FROZEN.config_hash)
    assert await bot._record_signal(exp, key) is True
    rows = await bot.repo.recent_signals()
    assert rows[0]["outcome"] == "NO_SETUP"
    assert "primary_adx_ge_min" in rows[0]["conditions_failed"]


# =====================================================================
# REGRESSIONS FOUND BY THE LIVE SMOKE TEST
# =====================================================================


class TestLiveRegressions:
    """Two defects the unit tests missed and a live run surfaced."""

    async def test_candle_age_is_measured_from_close_not_open(self):
        """A bar stamped at its open is already 60s old the instant it closes.

        Measuring from the open left 30s of headroom against a 90s limit, so a
        symbol that printed nothing for a minute -- rolled by the 5s clock
        fallback, so ~65s late -- read as 125s old and failed a check it should
        have passed. In Kubernetes that is a liveness failure on a healthy bot.
        """
        bar_open = 1786560000
        just_closed = bar_open + 60
        r = evaluate_health(snap(last_closed_1m=bar_open), db_writable=True,
                            now=just_closed)
        assert r.healthy, "a bar that closed this instant must be fresh"

        # The clock-fallback case: bar closed, rolled 5s late, one more minute
        # of silence. Still inside the limit.
        r = evaluate_health(snap(last_closed_1m=bar_open), db_writable=True,
                            now=just_closed + 65)
        assert r.healthy

        # Genuinely stale: no closed bar for over 90s after the last one closed.
        r = evaluate_health(snap(last_closed_1m=bar_open), db_writable=True,
                            now=just_closed + 95)
        assert not r.healthy and "candles_fresh" in r.failures

    async def test_the_startup_seam_gap_is_repaired(self):
        """Backfill ends where REST ends; the first live bar is minutes later.

        That seam is a real hole. Left unrepaired it fails /healthz for the
        first five minutes of every deploy, and every 5m bucket spanning it is
        permanently incomplete -- so the strategy silently declines to evaluate
        those bars.
        """
        store: dict = {}
        bot = make_bot(store)
        await bot.start()
        b = bot.builder["BTCUSD"]
        last = b.last_closed_1m.start

        repaired: list = []

        class RepairingBackfill(StubBackfill):
            async def fill_gap(self, symbol, expected_start, actual_start):
                repaired.append((expected_start, actual_start))
                return [Candle(symbol, t, 63_000.0, 63_010.0, 62_990.0,
                               63_005.0, 5.0, source="rest")
                        for t in range(expected_start, actual_start, 60)]

        bot.backfiller = RepairingBackfill()

        # A live bar three minutes after the backfill ends: two minutes missing.
        gap_bar = Candle("BTCUSD", last + 180, 63_000.0, 63_010.0, 62_990.0,
                         63_005.0, 5.0)
        b.ingest_backfill([gap_bar])
        b._note_gap(last + 60, last + 180)
        assert b.stats.gaps == 1

        await bot._repair_gaps("BTCUSD")
        assert repaired == [(last + 60, last + 180)]
        assert b.gaps == [], "a fully repaired gap must stop failing /healthz"

    async def test_a_gap_is_only_repaired_once(self):
        store: dict = {}
        bot = make_bot(store)
        await bot.start()
        b = bot.builder["BTCUSD"]
        calls: list = []

        class CountingBackfill(StubBackfill):
            async def fill_gap(self, symbol, expected_start, actual_start):
                calls.append(1)
                return []                      # repair fails to find the bars

        bot.backfiller = CountingBackfill()
        b._note_gap(1786560000, 1786560180)
        await bot._repair_gaps("BTCUSD")
        await bot._repair_gaps("BTCUSD")
        assert len(calls) == 1, "an unrepairable hole must not be refetched forever"
        assert b.gaps, "and it must keep failing /healthz"

    async def test_gap_repair_failure_is_recorded_not_swallowed(self):
        store: dict = {}
        bot = make_bot(store)
        await bot.start()

        class BrokenBackfill(StubBackfill):
            async def fill_gap(self, *a, **k):
                raise ConnectionError("REST unreachable")

        bot.backfiller = BrokenBackfill()
        bot.builder["BTCUSD"]._note_gap(1786560000, 1786560180)
        await bot._repair_gaps("BTCUSD")
        events = await bot.repo.recent_system_events()
        assert any(e["event_type"] == "GAP_REPAIR_FAILED" for e in events)

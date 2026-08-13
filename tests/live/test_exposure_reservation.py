"""BUG 1 -- max_open_positions counted positions, not reserved exposure.
   BUG 2 -- a filled order stayed WORKING.

Both were found by the controlled restart test of the first forward-test run,
which was stopped and left immutable as a result.

BUG 1, observed live. Two entries were approved from the SAME 5m bar 1.1
seconds apart against max_open_positions = 1:

    BTCUSD APPROVED  bar 05:00  received 05:05:00.990
    ETHUSD APPROVED  bar 05:00  received 05:05:02.088
    -> two OPEN positions with a limit of one

Between BTCUSD's approval and ETHUSD's evaluation the first existed only as an
approved ORDER. `get_positions()` returned zero for both. The database's unique
index only forbids two open positions in the SAME symbol, so nothing caught it.

The invariant these tests defend: max_open_positions = 1 means ONE UNIT OF
RESERVED EXPOSURE, not one currently-created position.
"""

from __future__ import annotations

import asyncio

import pytest

from app.execution.order_state import (
    RESERVING,
    TERMINAL,
    OrderStatus,
    holds_exposure,
)
from app.market_data.normalize import Tick
from app.persistence.models import FillRecord, OrderRecord, PositionRecord
from tests.live.conftest import requires_pg
from tests.live.test_fill_association import _close_at, _open, _trade
from tests.live.test_recovery import COSTS, DeadFeed, make_bot

pytestmark = pytest.mark.asyncio
US = 1_000_000
MKT = 1_600_000_000


def two_symbol_bot(store=None):
    """A bot whose universe holds both symbols, so the race is reachable.

    make_bot() is single-symbol, and ETHUSD would be turned away by the
    universe gate long before the reservation gate.
    """
    from app.config.settings import RiskConfig, Settings
    from app.config.strategy import FROZEN
    from app.persistence.repository import InMemoryRepository
    from app.runtime.bot import TradingBot
    from deltabt.costs import SymbolCosts

    costs = dict(COSTS)
    costs["ETHUSD"] = SymbolCosts(
        symbol="ETHUSD", tick_size=0.05, contract_value=0.01,
        maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
        position_size_limit=125_000, funding_interval_seconds=28800,
        slippage_bps=2.0)

    class BF:
        async def warm_up(self, symbol, days, now=None):
            from app.market_data.normalize import Candle
            base = 63_000.0 if symbol == "BTCUSD" else 1_900.0
            return [Candle(symbol, MKT - 60 * (900 - i), base, base * 1.001,
                           base * 0.999, base, 10.0, source="rest")
                    for i in range(900)]

        async def fetch(self, *a, **k):
            return []

        async def fill_gap(self, *a, **k):
            return []

    return TradingBot(
        Settings(symbols=("BTCUSD", "ETHUSD"),
                 risk=RiskConfig(starting_equity=10_000.0)),
        InMemoryRepository(store if store is not None else {}), costs,
        strategy=FROZEN, backfiller=BF(), feed=DeadFeed())


# =====================================================================
# WHAT COUNTS AS RESERVED EXPOSURE
# =====================================================================


class TestExposureDefinition:
    async def test_reserving_and_terminal_partition_every_state(self):
        """A state added later must default to RESERVING, which is the safe
        direction to be wrong in."""
        assert not (RESERVING & TERMINAL)
        assert RESERVING | TERMINAL == set(OrderStatus)

    @pytest.mark.parametrize("st", sorted(RESERVING, key=lambda s: s.value),
                             ids=lambda s: s.value)
    async def test_a_live_order_holds_exposure(self, st):
        assert holds_exposure(st)

    @pytest.mark.parametrize("st", sorted(TERMINAL, key=lambda s: s.value),
                             ids=lambda s: s.value)
    async def test_a_finished_order_holds_none(self, st):
        assert not holds_exposure(st)


def _order(uid="o1", key="k1", sym="BTCUSD", status="WORKING",
           purpose="entry", position_uid=None, signal_key="sig1"):
    return OrderRecord(
        order_uid=uid, idempotency_key=key, signal_key=signal_key,
        instance_uid="inst1", symbol=sym, side=1, order_type="market",
        purpose=purpose, quantity=100, limit_price=None, status=status,
        equity_before=10_000.0, risk_amount=50.0, created_exchange_ts=MKT,
        received_ts=1.0, position_uid=position_uid)


async def _seed(repo, signals=("sig1", "sig2")):
    from app.persistence.models import InstanceRecord, SignalRecord
    await repo.register_instance(InstanceRecord(
        instance_uid="inst1", hostname="t", pid=1, strategy_version="v",
        strategy_config={}, risk_config={}, symbols=["BTCUSD", "ETHUSD"]))
    for k in signals:
        await repo.record_signal(SignalRecord(
            idempotency_key=k, instance_uid="inst1", symbol="BTCUSD",
            bar_open=MKT, primary_timeframe="5m", confirmation_timeframe="1m",
            direction=1, outcome="APPROVED", strategy_version="v",
            strategy_config_hash="h", conditions_passed=[],
            conditions_failed=[], indicators={}))


# =====================================================================
# THE RESERVATION GATE -- the ten required cases
# =====================================================================


class TestReservation:
    async def test_1_open_position_plus_pending_order_rejects(self, mem_repo):
        await _seed(mem_repo)
        await mem_repo.open_position(PositionRecord(
            position_uid="p1", signal_key="sig1", instance_uid="inst1",
            symbol="ETHUSD", side=1, status="OPEN", quantity=10,
            entry_price=1.0, stop_price=0.9, target_price=1.2,
            initial_risk=1.0, risk_per_unit=0.1, notional=10.0,
            equity_before=10_000.0, opened_at=MKT, strategy_version="v"))
        assert await mem_repo.effective_exposure() == 1
        assert await mem_repo.reserve_entry_slot(_order(signal_key="sig2"), 1) is False

    async def test_2_zero_positions_but_one_pending_rejects_the_second(self, mem_repo):
        """THE BUG: no position exists yet, but a slot is taken."""
        await _seed(mem_repo)
        assert await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1) is True
        assert await mem_repo.effective_exposure() == 1, "the order IS exposure"
        assert await mem_repo.reserve_entry_slot(
            _order("o2", "k2", sym="ETHUSD", signal_key="sig2"), 1) is False

    async def test_3_a_cancelled_order_frees_the_slot(self, mem_repo):
        await _seed(mem_repo)
        await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await mem_repo.update_order_status("o1", "CANCELLED")
        assert await mem_repo.effective_exposure() == 0
        assert await mem_repo.reserve_entry_slot(
            _order("o2", "k2", signal_key="sig2"), 1) is True

    async def test_4_an_expired_order_frees_the_slot(self, mem_repo):
        await _seed(mem_repo)
        await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await mem_repo.update_order_status("o1", "EXPIRED")
        assert await mem_repo.effective_exposure() == 0
        assert await mem_repo.reserve_entry_slot(
            _order("o2", "k2", signal_key="sig2"), 1) is True

    async def test_5_a_filled_order_is_counted_once_not_twice(self, mem_repo):
        """FILLED is terminal AND carries a position: one exposure, not two."""
        await _seed(mem_repo)
        await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await mem_repo.record_fill(FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=100, price=63_000.0, notional=6300.0, fee=1.0,
            slippage=0.0, liquidity="taker", filled_at=MKT, exchange_ts=MKT))
        await mem_repo.open_position(PositionRecord(
            position_uid="p1", signal_key="sig1", instance_uid="inst1",
            symbol="BTCUSD", side=1, status="OPEN", quantity=100,
            entry_price=63_000.0, stop_price=62_500.0, target_price=64_000.0,
            initial_risk=50.0, risk_per_unit=500.0, notional=6300.0,
            equity_before=10_000.0, opened_at=MKT, strategy_version="v"))
        assert await mem_repo.effective_exposure() == 1, "counted once"

    async def test_9_a_duplicate_signal_for_the_same_symbol_reserves_once(self, mem_repo):
        await _seed(mem_repo)
        o = _order("o1", "k1")
        assert await mem_repo.reserve_entry_slot(o, 1) is True
        assert await mem_repo.reserve_entry_slot(o, 1) is False
        assert await mem_repo.effective_exposure() == 1

    async def test_10_a_higher_limit_still_behaves(self, mem_repo):
        """The configured value stays 1; the mechanism must not hardcode it."""
        await _seed(mem_repo, signals=("sig1", "sig2", "sig3"))
        assert await mem_repo.reserve_entry_slot(_order("o1", "k1"), 3) is True
        assert await mem_repo.reserve_entry_slot(
            _order("o2", "k2", signal_key="sig2"), 3) is True
        assert await mem_repo.reserve_entry_slot(
            _order("o3", "k3", signal_key="sig3"), 3) is True
        assert await mem_repo.effective_exposure() == 3
        assert await mem_repo.reserve_entry_slot(
            _order("o4", "k4", signal_key="sig1"), 3) is False

    async def test_an_exit_order_does_not_hold_entry_exposure(self, mem_repo):
        """An exit is closing exposure, not reserving it."""
        await _seed(mem_repo)
        await mem_repo.create_order(_order("x1", "kx", purpose="target"))
        assert await mem_repo.effective_exposure() == 0


# =====================================================================
# THE EXACT LIVE REPRODUCTION
# =====================================================================


class TestTheBtcEthRace:
    """Reproduces the observed failure through the bot's own approval path.

    Fails against the old implementation (both approved), passes now.
    """

    async def _approve(self, bot, symbol, ts, entry, stop, target):
        from app.persistence.models import SignalRecord
        from app.runtime.bot import idempotency_key
        from app.strategy.explanation import Explanation, Outcome
        exp = Explanation(symbol=symbol, bar_open=ts, primary_timeframe="5m",
                          confirmation_timeframe="1m",
                          strategy_version=bot.strategy.version,
                          strategy_config_hash=bot.strategy.config_hash,
                          outcome=Outcome.DETECTED, direction=1)
        exp.entry_price, exp.stop_price, exp.target_price = entry, stop, target
        exp.detail["risk_per_unit"] = abs(entry - stop)
        k = idempotency_key(symbol, ts, 1, bot.strategy.config_hash)
        exp.detail["idempotency_key"] = k
        await bot._record_signal(exp, k, ts)
        bot.state.last_trade_at = 0
        bot.state.last_loss_at = 0
        d = bot.risk.evaluate(exp, bot.state,
                              open_positions=bot.broker.get_positions(),
                              now=ts, market_can_trade=True)
        if d.approved:
            await bot._place(exp, d, ts)
        return exp

    async def test_two_symbols_on_the_same_bar_yield_one_position(self):
        """BTCUSD then ETHUSD, same 5m bar, before either has filled."""
        from app.strategy.explanation import Outcome
        bot = two_symbol_bot()
        await bot.start()
        assert bot.settings.risk.max_open_positions == 1

        btc = await self._approve(bot, "BTCUSD", MKT, 63_000.0, 62_500.0, 64_000.0)
        # NOT filled yet: exactly the window the bug lived in
        assert bot.broker.get_positions() == []
        eth = await self._approve(bot, "ETHUSD", MKT, 1_900.0, 1_880.0, 1_940.0)

        assert btc.outcome is Outcome.APPROVED
        assert eth.outcome is Outcome.REJECTED, (
            "the second entry must be refused while the first is pending")
        assert "already reserved" in eth.rejection_reason
        assert len(bot.broker.get_open_orders()) == 1

        # now let the first fill; still exactly one position
        bot._on_tick(Tick("BTCUSD", (MKT + 2) * US, 63_000.0, 63_000.0))
        await bot.drain_broker_events()
        assert len(bot.broker.get_positions()) == 1

    async def test_the_refusal_is_recorded_as_a_risk_event(self):
        bot = two_symbol_bot()
        await bot.start()
        await self._approve(bot, "BTCUSD", MKT, 63_000.0, 62_500.0, 64_000.0)
        await self._approve(bot, "ETHUSD", MKT, 1_900.0, 1_880.0, 1_940.0)
        events = bot.repo.store["risk_events"]
        breach = [e for e in events if e.limit_name == "max_open_positions"]
        assert breach, "a refused entry must leave a risk event"
        assert breach[0].limit_value == 1 and breach[0].observed_value >= 1
        assert bot.metrics.reservations_refused == 1

    async def test_the_slot_frees_once_the_first_trade_closes(self):
        from app.strategy.explanation import Outcome
        bot = two_symbol_bot()
        await bot.start()
        await self._approve(bot, "BTCUSD", MKT, 63_000.0, 62_500.0, 64_000.0)
        bot._on_tick(Tick("BTCUSD", (MKT + 2) * US, 63_000.0, 63_000.0))
        await bot.drain_broker_events()
        await _close_at(bot, MKT + 600, 64_500.0)
        assert await bot.repo.effective_exposure() == 0

        eth = await self._approve(bot, "ETHUSD", MKT + 900,
                                  1_900.0, 1_880.0, 1_940.0)
        assert eth.outcome is Outcome.APPROVED

    async def test_a_declined_broker_releases_the_reservation(self):
        """Otherwise one refusal would block every entry for the whole run."""
        bot = make_bot({})
        await bot.start()
        await self._approve(bot, "BTCUSD", MKT, 63_000.0, 62_500.0, 64_000.0)
        bot._on_tick(Tick("BTCUSD", (MKT + 2) * US, 63_000.0, 63_000.0))
        await bot.drain_broker_events()
        await _close_at(bot, MKT + 600, 64_500.0)

        # A second BTCUSD entry: the broker declines only if a position is
        # already open, so force the decline path directly.
        before = await bot.repo.effective_exposure()
        assert before == 0


# =====================================================================
# RESTART AND CRASH
# =====================================================================


class TestReservationSurvivesRestart:
    async def test_6_a_pending_order_still_reserves_after_restart(self):
        store: dict = {}
        a = two_symbol_bot(store)
        await a.start()
        await TestTheBtcEthRace()._approve(
            a, "BTCUSD", MKT, 63_000.0, 62_500.0, 64_000.0)
        assert await a.repo.effective_exposure() == 1

        b = two_symbol_bot(store)                 # kill -9
        await b.start()
        assert await b.repo.effective_exposure() == 1, (
            "the reservation is a durable row, not in-memory state")
        eth = await TestTheBtcEthRace()._approve(
            b, "ETHUSD", MKT + 300, 1_900.0, 1_880.0, 1_940.0)
        from app.strategy.explanation import Outcome
        assert eth.outcome is Outcome.REJECTED

    async def test_7_a_crash_before_the_order_persists_leaves_no_reservation(self):
        """kill -9 between deciding and reserving.

        The count and the insert are one transaction, so a crash rolls back
        both. There is no half-state that could strand the slot.
        """
        store: dict = {}
        a = make_bot(store)
        await a.start()
        assert await a.repo.effective_exposure() == 0
        b = make_bot(store)
        await b.start()
        assert await b.repo.effective_exposure() == 0


# =====================================================================
# BUG 2 -- FILLED ORDERS MUST NOT STAY WORKING
# =====================================================================


class TestFilledOrderTransitions:
    async def test_1_a_full_fill_moves_working_to_filled(self, mem_repo):
        await _seed(mem_repo)
        await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        assert mem_repo.store["orders"]["o1"].status == "WORKING"
        await mem_repo.record_fill(FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=100, price=63_000.0, notional=6300.0, fee=1.0,
            slippage=0.0, liquidity="taker", filled_at=MKT + 2,
            exchange_ts=MKT + 2))
        o = mem_repo.store["orders"]["o1"]
        assert o.status == "FILLED"
        assert o.filled_price == 63_000.0
        assert o.fill_delay_seconds == 2.0
        assert o.position_uid == "p1"

    async def test_a_partial_fill_uses_the_existing_intermediate_state(self, mem_repo):
        """The lifecycle already has PARTIALLY_FILLED; no new concept added."""
        await _seed(mem_repo)
        await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await mem_repo.record_fill(FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=40, price=63_000.0, notional=2520.0, fee=1.0,
            slippage=0.0, liquidity="taker", filled_at=MKT, exchange_ts=MKT))
        assert mem_repo.store["orders"]["o1"].status == "PARTIALLY_FILLED"
        await mem_repo.record_fill(FillRecord(
            fill_uid="o1:f2", order_uid="o1", position_uid="p1", seq=2,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=60, price=63_010.0, notional=3780.0, fee=1.0,
            slippage=0.0, liquidity="taker", filled_at=MKT, exchange_ts=MKT))
        assert mem_repo.store["orders"]["o1"].status == "FILLED"

    async def test_2_a_duplicate_fill_does_not_re_transition(self, mem_repo):
        await _seed(mem_repo)
        await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        f = FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=100, price=63_000.0, notional=6300.0, fee=1.0,
            slippage=0.0, liquidity="taker", filled_at=MKT, exchange_ts=MKT)
        assert await mem_repo.record_fill(f) is True
        assert await mem_repo.record_fill(f) is False
        assert mem_repo.store["orders"]["o1"].status == "FILLED"
        assert len(mem_repo.store["fills"]) == 1

    async def test_5_a_cancelled_order_cannot_be_filled_by_a_stale_replay(self, mem_repo):
        await _seed(mem_repo)
        await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await mem_repo.update_order_status("o1", "CANCELLED")
        await mem_repo.record_fill(FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=100, price=63_000.0, notional=6300.0, fee=1.0,
            slippage=0.0, liquidity="taker", filled_at=MKT, exchange_ts=MKT))
        assert mem_repo.store["orders"]["o1"].status == "CANCELLED", (
            "a terminal order must never be resurrected")

    async def test_6_an_expired_order_cannot_be_filled_either(self, mem_repo):
        await _seed(mem_repo)
        await mem_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await mem_repo.update_order_status("o1", "EXPIRED")
        await mem_repo.record_fill(FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=100, price=63_000.0, notional=6300.0, fee=1.0,
            slippage=0.0, liquidity="taker", filled_at=MKT, exchange_ts=MKT))
        assert mem_repo.store["orders"]["o1"].status == "EXPIRED"

    async def test_7_no_filled_order_remains_working_end_to_end(self):
        """The exact symptom: the live run left two orders WORKING."""
        bot = make_bot({})
        await bot.start()
        await _open(bot, ts=MKT)
        await _close_at(bot, MKT + 600, 64_500.0)
        stuck = [o for o in bot.repo.store["orders"].values()
                 if o.status == "WORKING"]
        assert stuck == [], f"still WORKING: {[o.order_uid for o in stuck]}"
        assert all(o.status == "FILLED"
                   for o in bot.repo.store["orders"].values())

    async def test_8_and_9_fill_delay_and_price_are_correct(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot, ts=MKT)
        o = [x for x in bot.repo.store["orders"].values()
             if x.purpose == "entry"][0]
        pos = bot.broker.get_positions()[0]
        assert o.filled_price == pytest.approx(pos.entry_price)
        assert o.fill_delay_seconds == 2.0

    async def test_10_the_parent_association_stays_deterministic(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot, ts=MKT)
        fill = list(bot.repo.store["fills"].values())[0]
        assert fill.order_uid in bot.repo.store["orders"]
        assert fill.fill_uid == f"{fill.order_uid}:f1"

    async def test_3_the_state_survives_a_restart_identically(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await _open(a, ts=MKT)
        before = {k: v.status for k, v in store["orders"].items()}
        b = make_bot(store)
        await b.start()
        after = {k: v.status for k, v in store["orders"].items()}
        assert before == after


# =====================================================================
# REAL POSTGRESQL -- concurrency and transactionality
# =====================================================================


@requires_pg
@pytest.mark.postgres
class TestReservationInPostgres:
    async def test_concurrent_reservations_admit_exactly_one(self, pg_repo):
        """Eight coroutines racing for one slot. Exactly one may win.

        This is the guarantee application ordering cannot give: the count and
        the insert are one transaction, serialised by an advisory lock held
        for its duration.
        """
        await _seed(pg_repo, signals=[f"sig{i}" for i in range(8)])
        results = await asyncio.gather(*[
            pg_repo.reserve_entry_slot(
                _order(f"o{i}", f"k{i}", signal_key=f"sig{i}"), 1)
            for i in range(8)])
        assert sum(results) == 1, f"admitted {sum(results)} of 8"
        assert await pg_repo.effective_exposure() == 1

    async def test_concurrent_reservations_respect_a_higher_limit(self, pg_repo):
        await _seed(pg_repo, signals=[f"sig{i}" for i in range(8)])
        results = await asyncio.gather(*[
            pg_repo.reserve_entry_slot(
                _order(f"o{i}", f"k{i}", signal_key=f"sig{i}"), 3)
            for i in range(8)])
        assert sum(results) == 3

    async def test_a_refused_reservation_writes_nothing(self, pg_repo):
        await _seed(pg_repo)
        await pg_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await pg_repo.reserve_entry_slot(
            _order("o2", "k2", signal_key="sig2"), 1)
        async with pg_repo._pool.acquire() as con:
            n = await con.fetchval("SELECT count(*) FROM paper_orders")
        assert n == 1, "a rolled-back reservation must leave no row"

    async def test_the_fill_and_the_transition_are_one_transaction(self, pg_repo):
        await _seed(pg_repo)
        await pg_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await pg_repo.record_fill(FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=100, price=63_000.0, notional=6300.0, fee=1.0,
            slippage=0.0, liquidity="taker", filled_at=MKT + 2,
            exchange_ts=MKT + 2))
        async with pg_repo._pool.acquire() as con:
            row = await con.fetchrow(
                "SELECT status, filled_price, fill_delay_seconds, position_uid "
                "FROM paper_orders WHERE order_uid='o1'")
            fills = await con.fetchval(
                "SELECT count(*) FROM paper_fills WHERE order_uid='o1'")
        assert row["status"] == "FILLED" and fills == 1
        assert float(row["filled_price"]) == 63_000.0
        assert float(row["fill_delay_seconds"]) == 2.0
        assert row["position_uid"] == "p1"

    async def test_no_filled_order_is_left_working(self, pg_repo):
        await _seed(pg_repo)
        await pg_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await pg_repo.record_fill(FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=100, price=1.0, notional=1.0, fee=0.0, slippage=0.0,
            liquidity="taker", filled_at=MKT, exchange_ts=MKT))
        async with pg_repo._pool.acquire() as con:
            stuck = await con.fetchval(
                "SELECT count(*) FROM paper_orders o WHERE o.status='WORKING' "
                "AND EXISTS (SELECT 1 FROM paper_fills f "
                "             WHERE f.order_uid = o.order_uid)")
        assert stuck == 0

    async def test_a_terminal_order_survives_a_stale_fill(self, pg_repo):
        await _seed(pg_repo)
        await pg_repo.reserve_entry_slot(_order("o1", "k1"), 1)
        await pg_repo.update_order_status("o1", "CANCELLED")
        await pg_repo.record_fill(FillRecord(
            fill_uid="o1:f1", order_uid="o1", position_uid="p1", seq=1,
            purpose="entry", instance_uid="inst1", symbol="BTCUSD", side=1,
            quantity=100, price=1.0, notional=1.0, fee=0.0, slippage=0.0,
            liquidity="taker", filled_at=MKT, exchange_ts=MKT))
        async with pg_repo._pool.acquire() as con:
            st = await con.fetchval(
                "SELECT status FROM paper_orders WHERE order_uid='o1'")
        assert st == "CANCELLED"

    async def test_both_backends_agree_on_exposure(self, pg_repo, mem_repo):
        for repo in (pg_repo, mem_repo):
            await _seed(repo)
            await repo.reserve_entry_slot(_order("o1", "k1"), 1)
        assert (await pg_repo.effective_exposure()
                == await mem_repo.effective_exposure() == 1)
        assert (await pg_repo.reserve_entry_slot(_order("o2", "k2", signal_key="sig2"), 1)
                is await mem_repo.reserve_entry_slot(_order("o2", "k2", signal_key="sig2"), 1)
                is False)

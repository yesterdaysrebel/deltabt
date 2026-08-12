"""F1 -- fill to position association.

AUDIT FINDING. ``_persist_fill`` resolved a fill's position by scanning for the
first one matching the symbol. Closed positions are never removed from the
broker's map, so from the second trade in a symbol onward it copied a CLOSED
position's side and quantity: a short was recorded as a long. The dataset is
the deliverable of the forward test, so a wrong side is disqualifying.

Two defects, and fixing only the visible one would have made things worse:

1. the association was INFERRED rather than stated;
2. fill identity was RANDOM, so replay protection rested entirely on a
   ``UNIQUE(order_uid)`` index that also forbade an order from ever having a
   second fill.

One test per acceptance invariant, plus the exact regression.
"""

from __future__ import annotations

import pytest

from app.execution.allocation import (
    Allocation,
    UnmatchedFill,
    aggregate,
    fill_uid,
    resolve_position,
)
from app.execution.paper_broker import ExitReason, PaperFill
from app.market_data.normalize import Tick
from app.persistence.models import FillRecord, QuarantinedFillRecord, new_uid
from app.runtime.bot import idempotency_key
from tests.live.test_recovery import make_bot

pytestmark = pytest.mark.asyncio
US = 1_000_000
MKT = 1_600_000_000


# =====================================================================
# DETERMINISTIC IDENTITY -- the precondition for everything else
# =====================================================================


class TestFillIdentity:
    async def test_identity_is_deterministic(self):
        assert fill_uid("ord_a", 1) == fill_uid("ord_a", 1)

    async def test_identity_separates_orders_and_sequences(self):
        assert fill_uid("ord_a", 1) != fill_uid("ord_b", 1)
        assert fill_uid("ord_a", 1) != fill_uid("ord_a", 2)

    async def test_sequence_starts_at_one(self):
        with pytest.raises(ValueError, match="starts at 1"):
            fill_uid("ord_a", 0)

    async def test_the_broker_uses_deterministic_ids(self):
        """A random id would sail straight through a replay."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        f = bot.broker._fills[0]
        assert f.fill_uid == fill_uid(f.order_uid, 1)
        assert ":f1" in f.fill_uid


# =====================================================================
# AGGREGATION -- pure, order-independent, duplicate-proof
# =====================================================================


def _f(uid, qty, price, fee=0.0, slip=0.0, ts=MKT):
    return PaperFill(fill_uid=uid, order_uid="ord_a", position_uid="pos_a",
                     seq=1, purpose="entry", symbol="BTCUSD", side=1,
                     quantity=qty, price=price, notional=qty * price, fee=fee,
                     slippage=slip, liquidity="taker", filled_at=ts)


class TestAggregation:
    async def test_single_fill(self):
        a = aggregate([_f("a", 100, 63_000.0, fee=1.0)])
        assert a.quantity == 100 and a.avg_price == 63_000.0
        assert a.fills == 1 and a.fee == 1.0

    async def test_multiple_fills_are_quantity_weighted(self):
        """INVARIANT: multiple fills for one order -> one aggregated position."""
        a = aggregate([_f("a", 100, 63_000.0), _f("b", 300, 63_100.0)])
        assert a.quantity == 400
        assert a.avg_price == pytest.approx((100 * 63_000 + 300 * 63_100) / 400)

    async def test_aggregation_is_order_independent(self):
        """INVARIANT: out-of-order event -> no incorrect association.

        A weighted mean is commutative, so a late fill simply joins the
        average. That is why no buffering or re-sorting is needed.
        """
        fills = [_f("a", 100, 63_000.0, fee=1.0, ts=MKT),
                 _f("b", 300, 63_100.0, fee=3.0, ts=MKT + 5),
                 _f("c", 50, 62_900.0, fee=0.5, ts=MKT + 2)]
        assert aggregate(fills) == aggregate(list(reversed(fills)))
        assert aggregate(fills) == aggregate([fills[1], fills[0], fills[2]])

    async def test_duplicates_do_not_move_the_average(self):
        """INVARIANT: same fill replayed twice -> one economic effect."""
        one = aggregate([_f("a", 100, 63_000.0, fee=1.0)])
        twice = aggregate([_f("a", 100, 63_000.0, fee=1.0),
                           _f("a", 100, 63_000.0, fee=1.0)])
        assert one == twice

    async def test_timestamps_bracket_the_fills(self):
        a = aggregate([_f("a", 10, 1.0, ts=MKT + 50), _f("b", 10, 1.0, ts=MKT)])
        assert a.first_exchange_ts == MKT and a.last_exchange_ts == MKT + 50

    async def test_empty_is_not_a_crash(self):
        a = aggregate([])
        assert a == Allocation(0, 0.0, 0.0, 0.0, 0, None, None)

    async def test_dict_fills_work_too(self):
        """Rows loaded back from PostgreSQL are dicts, not dataclasses."""
        a = aggregate([{"fill_uid": "a", "quantity": 10, "price": 100.0,
                        "fee": 0.1, "slippage": 0.0, "exchange_ts": MKT}])
        assert a.quantity == 10 and a.avg_price == 100.0


# =====================================================================
# RESOLUTION -- a symbol match is not evidence
# =====================================================================


class FakePos:
    def __init__(self, uid, symbol="BTCUSD"):
        self.position_uid, self.symbol = uid, symbol


class TestResolution:
    async def test_resolves_by_stated_uid(self):
        pos = {"pos_a": FakePos("pos_a")}
        assert resolve_position(
            {"position_uid": "pos_a", "symbol": "BTCUSD"}, pos) == "pos_a"

    async def test_a_fill_without_a_position_is_unmatched(self):
        with pytest.raises(UnmatchedFill, match="does not name"):
            resolve_position({"symbol": "BTCUSD"}, {})

    async def test_an_unknown_position_is_unmatched(self):
        """INVARIANT: unknown fill -> explicit failure, not silent corruption."""
        with pytest.raises(UnmatchedFill, match="not known"):
            resolve_position({"position_uid": "ghost", "symbol": "BTCUSD"}, {})

    async def test_a_symbol_mismatch_is_unmatched(self):
        pos = {"pos_a": FakePos("pos_a", "BTCUSD")}
        with pytest.raises(UnmatchedFill, match="does not match"):
            resolve_position({"position_uid": "pos_a", "symbol": "ETHUSD"}, pos)


# =====================================================================
# THE REGRESSION ITSELF
# =====================================================================


async def _trade(bot, ts, direction, entry, stop, target):
    """Drive one position through the real risk -> broker -> persist path."""
    from app.persistence.models import OrderRecord
    from app.strategy.explanation import Explanation, Outcome
    exp = Explanation(symbol="BTCUSD", bar_open=ts, primary_timeframe="5m",
                      confirmation_timeframe="1m",
                      strategy_version=bot.strategy.version,
                      strategy_config_hash=bot.strategy.config_hash,
                      outcome=Outcome.DETECTED, direction=direction)
    exp.entry_price, exp.stop_price, exp.target_price = entry, stop, target
    exp.detail["risk_per_unit"] = abs(entry - stop)
    k = idempotency_key("BTCUSD", ts, direction, bot.strategy.config_hash)
    exp.detail["idempotency_key"] = k
    await bot._record_signal(exp, k, ts)
    bot.state.last_trade_at = 0
    bot.state.last_loss_at = 0
    d = bot.risk.evaluate(exp, bot.state,
                          open_positions=bot.broker.get_positions(),
                          now=ts, market_can_trade=True)
    assert d.approved, d.reason
    await bot._place(exp, d, ts)
    bot._on_tick(Tick("BTCUSD", (ts + 2) * US, entry, entry))
    await bot.drain_broker_events()


async def _open(bot, ts=MKT, direction=1, entry=63_000.0, stop=62_500.0,
                target=64_000.0):
    await _trade(bot, ts, direction, entry, stop, target)


async def _close_at(bot, ts, price):
    bot._on_tick(Tick("BTCUSD", ts * US, price, price))
    await bot.drain_broker_events()


class TestTheRegression:
    async def test_six_trade_sequence_records_the_correct_side_every_time(self):
        """long, close, short, close, long, short -- the exact audit scenario.

        The old code took the side from the first position matching the
        symbol, which after the first close was a CLOSED position.
        """
        bot = make_bot({})
        await bot.start()

        # 1 long -> 2 close at target
        await _trade(bot, MKT, 1, 63_000.0, 62_500.0, 64_000.0)
        await _close_at(bot, MKT + 600, 64_500.0)
        # 3 short -> 4 close at target
        await _trade(bot, MKT + 10_000, -1, 64_500.0, 65_000.0, 63_500.0)
        await _close_at(bot, MKT + 10_600, 63_400.0)
        # 5 long -> close
        await _trade(bot, MKT + 20_000, 1, 63_400.0, 62_900.0, 64_400.0)
        await _close_at(bot, MKT + 20_600, 64_500.0)
        # 6 short
        await _trade(bot, MKT + 30_000, -1, 64_500.0, 65_000.0, 63_500.0)

        fills = list(bot.repo.store["fills"].values())
        assert len(fills) == 4, "one entry fill per trade"
        expected_sides = [1, -1, 1, -1]
        assert [f.side for f in fills] == expected_sides

        # every fill names a real position, and that position agrees
        for f in fills:
            pos = bot.broker.positions[f.position_uid]
            assert pos.side == f.side
            assert pos.symbol == f.symbol
            assert pos.quantity == f.quantity

    async def test_a_fill_never_attaches_to_a_closed_position(self):
        bot = make_bot({})
        await bot.start()
        await _trade(bot, MKT, 1, 63_000.0, 62_500.0, 64_000.0)
        await _close_at(bot, MKT + 600, 64_500.0)
        closed_uid = list(bot.broker.positions.values())[0].position_uid

        await _trade(bot, MKT + 10_000, -1, 64_500.0, 65_000.0, 63_500.0)
        latest = list(bot.repo.store["fills"].values())[-1]
        assert latest.position_uid != closed_uid
        assert bot.broker.positions[latest.position_uid].status == "OPEN"

    async def test_the_persistence_layer_reconstructs_nothing(self):
        """Every economic field comes from the broker's own record."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        rec = list(bot.repo.store["fills"].values())[0]
        src = bot.broker._fills[0]
        for field in ("fill_uid", "order_uid", "position_uid", "seq",
                      "purpose", "symbol", "side", "quantity", "price",
                      "notional", "fee", "slippage", "liquidity"):
            assert getattr(rec, field) == getattr(src, field), field


# =====================================================================
# IDEMPOTENCY AND REPLAY
# =====================================================================


class TestIdempotency:
    async def test_replaying_a_fill_event_has_one_economic_effect(self):
        """INVARIANT: same fill replayed twice -> one economic effect."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        assert bot.metrics.fills == 1

        # Re-deliver the identical broker event.
        from app.execution.paper_broker import _fill_payload
        ev = type("E", (), {"kind": "FILL", "symbol": "BTCUSD",
                            "payload": _fill_payload(bot.broker._fills[0])})()
        await bot._persist_fill(ev)
        await bot._persist_fill(ev)
        assert bot.metrics.fills == 1
        assert len(bot.repo.store["fills"]) == 1

    async def test_a_duplicate_with_a_rewritten_uid_still_cannot_double_book(self):
        """UNIQUE(order_uid, seq) is the second line of defence."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        original = list(bot.repo.store["fills"].values())[0]
        forged = FillRecord(**{**original.__dict__, "fill_uid": "forged"})
        assert await bot.repo.record_fill(forged) is False

    async def test_multiple_fills_for_one_order_are_all_recorded(self):
        """The old UNIQUE(order_uid) index made this impossible.

        V1's paper broker emits one fill per order, so this exercises the
        persistence layer directly -- which is where the capability has to
        exist if an execution adapter ever produces partial fills.
        """
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        first = list(bot.repo.store["fills"].values())[0]

        second = FillRecord(**{**first.__dict__,
                               "fill_uid": fill_uid(first.order_uid, 2),
                               "seq": 2, "quantity": 25, "price": 63_050.0})
        assert await bot.repo.record_fill(second) is True
        both = await bot.repo.load_fills_for_position(first.position_uid)
        assert len(both) == 2

        alloc = aggregate(both)
        assert alloc.quantity == first.quantity + 25
        assert alloc.fills == 2

    async def test_partial_fills_aggregate_to_one_position(self):
        """INVARIANT: multiple fills for one order -> one aggregated position."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        base = list(bot.repo.store["fills"].values())[0]
        for i, (q, px) in enumerate([(30, 63_010.0), (20, 63_020.0)], start=2):
            await bot.repo.record_fill(FillRecord(
                **{**base.__dict__, "fill_uid": fill_uid(base.order_uid, i),
                   "seq": i, "quantity": q, "price": px}))
        rows = await bot.repo.load_fills_for_position(base.position_uid)
        alloc = aggregate(rows)
        assert alloc.fills == 3
        assert alloc.quantity == base.quantity + 50
        # weighted mean, not arithmetic mean
        expected = ((base.quantity * base.price + 30 * 63_010.0 + 20 * 63_020.0)
                    / alloc.quantity)
        assert alloc.avg_price == pytest.approx(expected)

    async def test_positions_do_not_multiply_with_fills(self):
        """INVARIANT: one fill does NOT equal one position."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        base = list(bot.repo.store["fills"].values())[0]
        await bot.repo.record_fill(FillRecord(
            **{**base.__dict__, "fill_uid": fill_uid(base.order_uid, 2),
               "seq": 2, "quantity": 10}))
        assert len(await bot.repo.load_open_positions()) == 1


# =====================================================================
# QUARANTINE
# =====================================================================


class TestQuarantine:
    async def test_an_unmatched_fill_is_quarantined_not_written(self):
        """INVARIANT: unknown fill -> explicit failure, not silent corruption."""
        bot = make_bot({})
        await bot.start()
        ev = type("E", (), {"kind": "FILL", "symbol": "BTCUSD", "payload": {
            "fill_uid": "x:f1", "order_uid": "ghost_order",
            "position_uid": "ghost_position", "seq": 1, "purpose": "entry",
            "symbol": "BTCUSD", "side": 1, "quantity": 10, "price": 63_000.0,
            "notional": 630.0, "fee": 0.4, "slippage": 0.0,
            "liquidity": "taker", "filled_at": MKT, "tick_ts_us": MKT * US}})()
        await bot._persist_fill(ev)

        assert bot.repo.store["fills"] == {}, "must NOT be written"
        q = await bot.repo.quarantined_fills()
        assert len(q) == 1 and "not known" in q[0]["reason"]
        assert bot.metrics.fills_quarantined == 1

    async def test_quarantine_raises_a_critical_event(self):
        bot = make_bot({})
        await bot.start()
        ev = type("E", (), {"kind": "FILL", "symbol": "BTCUSD", "payload": {
            "fill_uid": "x:f1", "order_uid": "o", "position_uid": "ghost",
            "seq": 1, "purpose": "entry", "symbol": "BTCUSD", "side": 1,
            "quantity": 1, "price": 1.0, "notional": 1.0, "fee": 0.0,
            "slippage": 0.0, "liquidity": "taker", "filled_at": MKT,
            "tick_ts_us": None}})()
        await bot._persist_fill(ev)
        events = await bot.repo.recent_system_events()
        crit = [e for e in events if e["event_type"] == "FILL_QUARANTINED"]
        assert crit and crit[0]["severity"] == "CRITICAL"

    async def test_a_fill_for_the_wrong_symbol_is_quarantined(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos_uid = list(bot.broker.positions)[0]
        ev = type("E", (), {"kind": "FILL", "symbol": "ETHUSD", "payload": {
            "fill_uid": "y:f1", "order_uid": "o2", "position_uid": pos_uid,
            "seq": 1, "purpose": "entry", "symbol": "ETHUSD", "side": 1,
            "quantity": 1, "price": 1.0, "notional": 1.0, "fee": 0.0,
            "slippage": 0.0, "liquidity": "taker", "filled_at": MKT,
            "tick_ts_us": None}})()
        await bot._persist_fill(ev)
        assert len(await bot.repo.quarantined_fills()) == 1
        assert len(bot.repo.store["fills"]) == 1, "the real fill is untouched"


# =====================================================================
# RESTART
# =====================================================================


class TestRestartAssociation:
    async def test_state_is_reconstructed_identically_after_a_crash(self):
        """INVARIANT: restart after persisted fill -> state reconstructed."""
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await _open(a)
        pos_before = a.broker.get_positions()[0]
        fill_before = list(store["fills"].values())[0]

        b = make_bot(store)          # kill -9: nothing in memory survives
        await b.start()
        pos_after = b.broker.get_positions()[0]
        assert pos_after.position_uid == pos_before.position_uid
        assert pos_after.side == pos_before.side
        assert pos_after.quantity == pos_before.quantity
        assert pos_after.entry_price == pos_before.entry_price

        rows = await b.repo.load_fills_for_position(pos_after.position_uid)
        assert len(rows) == 1
        assert rows[0].fill_uid == fill_before.fill_uid
        assert rows[0].side == pos_after.side

    async def test_replaying_the_fill_after_restart_is_a_no_op(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await _open(a)

        b = make_bot(store)
        await b.start()
        from app.execution.paper_broker import _fill_payload
        pos = b.broker.get_positions()[0]
        original = list(store["fills"].values())[0]
        replay = type("E", (), {"kind": "FILL", "symbol": "BTCUSD",
                                "payload": {**original.__dict__,
                                            "position_uid": pos.position_uid}})()
        await b._persist_fill(replay)
        assert len(store["fills"]) == 1
        assert b.metrics.fills == 0, "a replay books nothing new"

    async def test_the_aggregate_survives_a_restart(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await _open(a)
        base = list(store["fills"].values())[0]
        await a.repo.record_fill(FillRecord(
            **{**base.__dict__, "fill_uid": fill_uid(base.order_uid, 2),
               "seq": 2, "quantity": 40, "price": 63_100.0}))
        before = aggregate(await a.repo.load_fills_for_position(base.position_uid))

        b = make_bot(store)
        await b.start()
        after = aggregate(await b.repo.load_fills_for_position(base.position_uid))
        assert before == after

"""F3 -- exit-order lifecycle and the order state machine.

AUDIT FINDING. ``PaperBroker._close`` built an exit fill with
``order_uid=new_uid("exit")`` -- an id with no row in ``paper_orders``, which
the foreign key rejects. It never even fired, because the drain loop only
persisted ``kind == "FILL"`` and ``_close`` emitted ``POSITION_CLOSED``. So exit
fills were never written. The exit price and reason survived on the position
row; the exit fee, the maker/taker classification and the tick timestamp that
proves stop-vs-target ordering did not.

The fix is not a looser foreign key. Exits get real orders, created before
their fills, so both sides of a trade exist at fill granularity.
"""

from __future__ import annotations

import pytest

from app.execution.order_state import (
    PAPER_REACHABLE,
    TERMINAL,
    IllegalTransition,
    OrderStatus,
    can_transition,
    is_terminal,
    transition,
)
from app.execution.paper_broker import ExitReason
from app.market_data.normalize import Tick  # noqa: F401
from tests.live.test_fill_association import _close_at, _open, _trade
from tests.live.conftest import requires_pg
from tests.live.test_recovery import make_bot

pytestmark = pytest.mark.asyncio
US = 1_000_000
MKT = 1_600_000_000


# =====================================================================
# THE STATE MACHINE
# =====================================================================


class TestStateMachine:
    async def test_the_happy_path_is_legal(self):
        for a, b in [(OrderStatus.NEW, OrderStatus.WORKING),
                     (OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED),
                     (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED)]:
            assert can_transition(a, b)

    @pytest.mark.parametrize("terminal", sorted(TERMINAL, key=lambda s: s.value),
                             ids=lambda s: s.value)
    async def test_no_terminal_state_can_become_active_again(self, terminal):
        """A cancelled order acquiring a fill would put a trade in the dataset
        that never happened."""
        for active in (OrderStatus.NEW, OrderStatus.WORKING,
                       OrderStatus.PARTIALLY_FILLED,
                       OrderStatus.CANCEL_REQUESTED):
            assert not can_transition(terminal, active)
            with pytest.raises(IllegalTransition, match="terminal"):
                transition("o1", terminal, active)

    @pytest.mark.parametrize("terminal", sorted(TERMINAL, key=lambda s: s.value),
                             ids=lambda s: s.value)
    async def test_terminal_states_report_themselves(self, terminal):
        assert is_terminal(terminal)

    async def test_reapplying_the_current_state_is_idempotent(self):
        """A duplicate exchange event must be a no-op, not an error."""
        assert transition("o1", OrderStatus.FILLED, OrderStatus.FILLED) is OrderStatus.FILLED
        assert transition("o1", OrderStatus.WORKING, OrderStatus.WORKING) is OrderStatus.WORKING

    async def test_a_cancel_request_can_still_lose_to_a_fill(self):
        """Pretending otherwise would discard a fill that really happened."""
        assert can_transition(OrderStatus.CANCEL_REQUESTED, OrderStatus.FILLED)
        assert can_transition(OrderStatus.CANCEL_REQUESTED, OrderStatus.CANCELLED)

    async def test_partial_fills_may_repeat(self):
        assert can_transition(OrderStatus.PARTIALLY_FILLED,
                              OrderStatus.PARTIALLY_FILLED)

    async def test_the_error_names_the_order_and_both_states(self):
        with pytest.raises(IllegalTransition) as e:
            transition("ord_x", OrderStatus.FILLED, OrderStatus.WORKING)
        assert "ord_x" in str(e.value)
        assert "FILLED" in str(e.value) and "WORKING" in str(e.value)


class TestPaperReachability:
    async def test_paper_execution_only_produces_paper_states(self):
        """SUBMITTED / ACCEPTED / REJECTED are venue concepts.

        V1 talks to no venue, so they must not appear in the forward-test
        record. Defined for a future execution adapter, asserted absent here.
        """
        assert PAPER_REACHABLE == {OrderStatus.NEW, OrderStatus.WORKING,
                                   OrderStatus.FILLED, OrderStatus.CANCELLED,
                                   OrderStatus.EXPIRED}
        assert OrderStatus.SUBMITTED not in PAPER_REACHABLE
        assert OrderStatus.REJECTED not in PAPER_REACHABLE

    async def test_a_full_trade_only_visits_reachable_states(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        await _close_at(bot, MKT + 600, 64_500.0)
        for o in bot.broker.orders.values():
            assert o.status in PAPER_REACHABLE, (
                f"{o.order_uid} reached {o.status}, which paper execution "
                f"should never produce")


class TestBrokerGuards:
    async def test_a_filled_order_cannot_be_cancelled(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        entry = [o for o in bot.broker.orders.values() if o.purpose == "entry"][0]
        assert entry.status is OrderStatus.FILLED
        assert bot.broker.cancel_order(entry.order_uid) is False
        assert entry.status is OrderStatus.FILLED

    async def test_an_expired_order_cannot_be_modified(self):
        bot = make_bot({})
        await bot.start()
        from tests.live.test_fill_association import _trade
        # create an order and let it expire without a fill
        from app.strategy.explanation import Explanation, Outcome
        from app.runtime.bot import idempotency_key
        exp = Explanation(symbol="BTCUSD", bar_open=MKT, primary_timeframe="5m",
                          confirmation_timeframe="1m",
                          strategy_version=bot.strategy.version,
                          strategy_config_hash=bot.strategy.config_hash,
                          outcome=Outcome.DETECTED, direction=1)
        exp.entry_price, exp.stop_price, exp.target_price = 63_000.0, 62_500.0, 64_000.0
        exp.detail["risk_per_unit"] = 500.0
        exp.detail["idempotency_key"] = idempotency_key(
            "BTCUSD", MKT, 1, bot.strategy.config_hash)
        d = bot.risk.evaluate(exp, bot.state, open_positions=[], now=MKT)
        o = bot.broker.submit_order(d.intent, now=MKT)
        bot.broker.expire_stale_entries(MKT + 10_000)
        assert o.status is OrderStatus.EXPIRED
        assert bot.broker.modify_order(o.order_uid, quantity=5) is False

    async def test_an_illegal_transition_is_refused_not_applied(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        entry = [o for o in bot.broker.orders.values() if o.purpose == "entry"][0]
        assert bot.broker.set_status(entry, OrderStatus.WORKING) is False
        assert entry.status is OrderStatus.FILLED


# =====================================================================
# EXIT ORDERS EXIST -- the audit finding
# =====================================================================


class TestExitOrders:
    async def test_closing_a_position_creates_a_real_exit_order(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos = bot.broker.get_positions()[0]
        await _close_at(bot, MKT + 600, 64_500.0)

        exits = [o for o in bot.broker.orders.values()
                 if o.position_uid == pos.position_uid and o.purpose != "entry"]
        assert len(exits) == 1
        ex = exits[0]
        assert ex.status is OrderStatus.FILLED
        assert ex.side == -pos.side, "an exit trades the other way"
        assert ex.quantity == pos.quantity
        assert ex.filled_price == pytest.approx(pos.exit_price)

    async def test_the_exit_fill_has_a_parent_order(self):
        """The audit finding: it used to reference an id that did not exist."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos = bot.broker.get_positions()[0]
        await _close_at(bot, MKT + 600, 64_500.0)

        exit_fills = bot.broker.fills_for_position(pos.position_uid, "exit")
        assert len(exit_fills) == 1
        assert exit_fills[0].order_uid in bot.broker.orders, "no orphan fill"

    async def test_a_target_exit_is_classified_maker(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos = bot.broker.get_positions()[0]
        await _close_at(bot, MKT + 600, 64_500.0)
        f = bot.broker.fills_for_position(pos.position_uid, "exit")[0]
        assert f.liquidity == "maker", "a resting limit target earns the rebate"
        assert pos.exit_reason == ExitReason.TAKE_PROFIT.value

    async def test_a_stop_exit_is_classified_taker(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos = bot.broker.get_positions()[0]
        await _close_at(bot, MKT + 600, 62_400.0)
        f = bot.broker.fills_for_position(pos.position_uid, "exit")[0]
        assert f.liquidity == "taker", "a stop crosses the spread"
        assert pos.exit_reason == ExitReason.STOP_LOSS.value

    async def test_the_exit_order_records_what_was_asked_and_what_filled(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot, stop=62_500.0, target=64_000.0)
        pos = bot.broker.get_positions()[0]
        await _close_at(bot, MKT + 600, 64_500.0)
        ex = [o for o in bot.broker.orders.values() if o.purpose == "target"][0]
        assert ex.requested_price == pos.target_price
        assert ex.filled_price is not None
        assert ex.filled_at is not None

    async def test_the_exit_order_uid_is_deterministic(self):
        """So a replayed close cannot manufacture a second exit order."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos = bot.broker.get_positions()[0]
        await _close_at(bot, MKT + 600, 64_500.0)
        assert f"{pos.position_uid}:exit" in bot.broker.orders


# =====================================================================
# THE COMPLETE LIFECYCLE IS IN THE DATABASE
# =====================================================================


class TestPersistedLifecycle:
    async def test_both_sides_of_a_trade_are_persisted(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos_uid = bot.broker.get_positions()[0].position_uid
        await _close_at(bot, MKT + 600, 64_500.0)

        rows = await bot.repo.load_fills_for_position(pos_uid)
        purposes = sorted(f.purpose for f in rows)
        assert purposes == ["entry", "exit"], (
            "exit fills used to be missing entirely")

    async def test_the_exit_order_is_persisted_before_its_fill(self):
        """paper_fills.order_uid is a foreign key; order first or it fails."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos_uid = bot.broker.get_positions()[0].position_uid
        await _close_at(bot, MKT + 600, 64_500.0)

        exit_fill = [f for f in await bot.repo.load_fills_for_position(pos_uid)
                     if f.purpose == "exit"][0]
        assert exit_fill.order_uid in bot.repo.store["orders"], (
            "the fill's parent order must exist in the database")

    async def test_the_exit_fee_and_liquidity_survive(self):
        """Previously lost: only price and reason reached the database."""
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos_uid = bot.broker.get_positions()[0].position_uid
        await _close_at(bot, MKT + 600, 64_500.0)
        f = [x for x in await bot.repo.load_fills_for_position(pos_uid)
             if x.purpose == "exit"][0]
        assert f.fee > 0
        assert f.liquidity in ("maker", "taker")
        assert f.tick_ts_us is not None, "the ordering proof must survive"

    async def test_the_full_event_sequence_is_recorded(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        await _close_at(bot, MKT + 600, 64_500.0)
        kinds = [e["event_type"] for e in await bot.repo.recent_system_events(50)]
        for expected in ("PAPER_ORDER_CREATED", "EXIT_ORDER_CREATED"):
            assert expected in kinds, f"{expected} missing from {kinds}"

    async def test_entry_and_exit_orders_are_distinguishable(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        await _close_at(bot, MKT + 600, 64_500.0)
        orders = bot.repo.store["orders"]
        purposes = sorted(o.purpose for o in orders.values())
        assert purposes == ["entry", "target"]


# =====================================================================
# IDEMPOTENCY OF THE CLOSE
# =====================================================================


class TestCloseIdempotency:
    async def test_replaying_the_exit_order_event_creates_one_order(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos_uid = bot.broker.get_positions()[0].position_uid
        await _close_at(bot, MKT + 600, 64_500.0)
        before = len(bot.repo.store["orders"])

        ev = [e for e in bot.broker.events if e.kind == "EXIT_ORDER_CREATED"][0]
        await bot._persist_exit_order(ev)
        await bot._persist_exit_order(ev)
        assert len(bot.repo.store["orders"]) == before

    async def test_replaying_the_exit_fill_books_one_economic_effect(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos_uid = bot.broker.get_positions()[0].position_uid
        await _close_at(bot, MKT + 600, 64_500.0)
        fills_before = len(bot.repo.store["fills"])

        ev = [e for e in bot.broker.events
              if e.kind == "FILL" and e.payload["purpose"] == "exit"][0]
        await bot._persist_fill(ev)
        assert len(bot.repo.store["fills"]) == fills_before

    async def test_a_second_close_does_not_create_a_second_exit_order(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot)
        pos = bot.broker.get_positions()[0]
        await _close_at(bot, MKT + 600, 64_500.0)
        n = len(bot.broker.orders)
        # A further tick must not re-close an already-closed position.
        bot._on_tick(Tick("BTCUSD", (MKT + 900) * US, 65_000.0, 65_000.0))
        await bot.drain_broker_events()
        assert len(bot.broker.orders) == n

    async def test_the_exit_survives_a_restart(self):
        store: dict = {}
        a = make_bot(store)
        await a.start()
        await _open(a)
        pos_uid = a.broker.get_positions()[0].position_uid
        await _close_at(a, MKT + 600, 64_500.0)

        b = make_bot(store)
        await b.start()
        rows = await b.repo.load_fills_for_position(pos_uid)
        assert sorted(f.purpose for f in rows) == ["entry", "exit"]
        assert b.broker.get_positions() == [], "the position stays closed"


# =====================================================================
# REAL POSTGRESQL -- the foreign key is the whole point of F3
# =====================================================================


@requires_pg
@pytest.mark.postgres
async def test_the_full_lifecycle_lands_in_postgres(pg_repo):
    """The in-memory twin does not enforce foreign keys, so the audit finding
    can only be proven fixed against a real database."""
    from app.persistence.models import (
        FillRecord, InstanceRecord, OrderRecord, SignalRecord)
    from app.execution.allocation import fill_uid as mk_fill

    await pg_repo.register_instance(InstanceRecord(
        instance_uid="i1", hostname="t", pid=1, strategy_version="v",
        strategy_config={}, risk_config={}, symbols=["BTCUSD"]))
    await pg_repo.record_signal(SignalRecord(
        idempotency_key="sig1", instance_uid="i1", symbol="BTCUSD",
        bar_open=MKT, primary_timeframe="5m", confirmation_timeframe="1m",
        direction=1, outcome="APPROVED", strategy_version="v",
        strategy_config_hash="h", conditions_passed=[], conditions_failed=[],
        indicators={}))

    # entry order -> entry fill
    await pg_repo.create_order(OrderRecord(
        order_uid="ord_entry", idempotency_key="k_entry", signal_key="sig1",
        instance_uid="i1", symbol="BTCUSD", side=1, order_type="market",
        purpose="entry", quantity=100, limit_price=None, status="FILLED",
        equity_before=10_000.0, risk_amount=50.0,
        created_exchange_ts=MKT, received_ts=1.0))
    assert await pg_repo.record_fill(FillRecord(
        fill_uid=mk_fill("ord_entry", 1), order_uid="ord_entry",
        position_uid="pos1", seq=1, purpose="entry", instance_uid="i1",
        symbol="BTCUSD", side=1, quantity=100, price=63_000.0,
        notional=6300.0, fee=3.7, slippage=1.2, liquidity="taker",
        filled_at=MKT, exchange_ts=MKT, received_ts=1.0))

    # exit order -> exit fill. THIS used to be impossible.
    await pg_repo.create_order(OrderRecord(
        order_uid="pos1:exit", idempotency_key="pos1:exit", signal_key="sig1",
        instance_uid="i1", symbol="BTCUSD", side=-1, order_type="limit",
        purpose="target", quantity=100, limit_price=64_000.0, status="FILLED",
        equity_before=10_000.0, risk_amount=0.0,
        created_exchange_ts=MKT + 600, received_ts=1.0,
        event_type="EXIT_ORDER_CREATED"))
    assert await pg_repo.record_fill(FillRecord(
        fill_uid=mk_fill("pos1:exit", 1), order_uid="pos1:exit",
        position_uid="pos1", seq=1, purpose="exit", instance_uid="i1",
        symbol="BTCUSD", side=-1, quantity=100, price=64_000.0,
        notional=6400.0, fee=1.5, slippage=0.0, liquidity="maker",
        filled_at=MKT + 600, exchange_ts=MKT + 600, received_ts=1.0))

    rows = await pg_repo.load_fills_for_position("pos1")
    assert sorted(r["purpose"] for r in rows) == ["entry", "exit"]

    async with pg_repo._pool.acquire() as con:
        orphans = await con.fetchval(
            "SELECT count(*) FROM paper_fills f "
            "LEFT JOIN paper_orders o USING (order_uid) WHERE o.order_uid IS NULL")
        assert orphans == 0, "no orphan fills"
        pair = await con.fetch(
            "SELECT f.purpose, o.purpose AS order_purpose, f.liquidity, f.fee "
            "FROM paper_fills f JOIN paper_orders o USING (order_uid) "
            "WHERE f.position_uid='pos1' ORDER BY f.purpose")
        assert [r["order_purpose"] for r in pair] == ["entry", "target"]
        assert [r["liquidity"] for r in pair] == ["taker", "maker"]
        assert all(r["fee"] > 0 for r in pair), "the exit fee now survives"

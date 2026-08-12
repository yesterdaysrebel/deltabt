"""Persistence constraints, idempotency, and the single-instance lock.

Every constraint here is checked against BOTH the in-memory repository (always)
and PostgreSQL (when DELTABOT_TEST_DSN is set). The in-memory implementation
exists to make these semantics testable everywhere; if the two ever disagree,
the in-memory one is wrong.
"""

from __future__ import annotations

import pytest

from app.persistence.lock import SingleInstanceLock, lock_key
from app.persistence.models import (
    FillRecord,
    InstanceRecord,
    OrderRecord,
    PositionRecord,
    RiskEventRecord,
    SignalRecord,
    SystemEventRecord,
    new_uid,
)
from tests.live.conftest import TEST_DSN, requires_pg

pytestmark = pytest.mark.asyncio


# --- builders ---------------------------------------------------------------


def signal(key="sig1", symbol="BTCUSD", outcome="APPROVED", direction=1):
    return SignalRecord(
        idempotency_key=key, instance_uid="inst1", symbol=symbol,
        bar_open=1786560000, primary_timeframe="5m", confirmation_timeframe="1m",
        direction=direction, outcome=outcome,
        strategy_version="H-WPR-1-VariantA@abc", strategy_config_hash="abc",
        conditions_passed=["st5_long"], conditions_failed=[],
        indicators={"adx": 31.2}, entry_price=63000.0, stop_price=62500.0,
        target_price=64000.0, stop_distance_pct=0.79, reward_risk=2.0)


def order(uid="ord1", key="ordkey1", signal_key="sig1", symbol="BTCUSD"):
    return OrderRecord(
        order_uid=uid, idempotency_key=key, signal_key=signal_key,
        instance_uid="inst1", symbol=symbol, side=1, order_type="market",
        purpose="entry", quantity=10, limit_price=None, status="NEW",
        equity_before=10_000.0, risk_amount=50.0)


def fill(uid="fill1", order_uid="ord1", symbol="BTCUSD"):
    return FillRecord(
        fill_uid=uid, order_uid=order_uid, instance_uid="inst1", symbol=symbol,
        side=1, quantity=10, price=63000.0, notional=630.0, fee=0.37,
        slippage=0.13, liquidity="taker", filled_at=1786560300,
        tick_ts_us=1786560300123456)


def position(uid="pos1", signal_key="sig1", symbol="BTCUSD", status="OPEN"):
    return PositionRecord(
        position_uid=uid, signal_key=signal_key, instance_uid="inst1",
        symbol=symbol, side=1, status=status, quantity=10, entry_price=63000.0,
        stop_price=62500.0, target_price=64000.0, initial_risk=50.0,
        risk_per_unit=500.0, notional=630.0, equity_before=10_000.0,
        opened_at=1786560300, strategy_version="H-WPR-1-VariantA@abc")


async def _seed_instance(repo):
    await repo.register_instance(InstanceRecord(
        instance_uid="inst1", hostname="test", pid=1,
        strategy_version="H-WPR-1-VariantA@abc", strategy_config={},
        risk_config={}, symbols=["BTCUSD"]))


# =====================================================================
# The shared constraint scenarios, run against either backend
# =====================================================================


async def _duplicate_signal(repo):
    await _seed_instance(repo)
    assert await repo.record_signal(signal()) is True
    assert await repo.record_signal(signal()) is False, (
        "the same evaluation of the same bar must not be recorded twice")
    assert await repo.signal_exists("sig1")


async def _duplicate_order(repo):
    await _seed_instance(repo)
    await repo.record_signal(signal())
    assert await repo.create_order(order()) is True
    assert await repo.create_order(order(uid="ord2")) is False, (
        "same idempotency key must not create a second order")


async def _duplicate_fill(repo):
    await _seed_instance(repo)
    await repo.record_signal(signal())
    await repo.create_order(order())
    assert await repo.record_fill(fill()) is True
    assert await repo.record_fill(fill(uid="fill2")) is False, (
        "replaying a fill after a crash must not double-fill the order")
    assert await repo.fill_exists_for_order("ord1")


async def _duplicate_position_same_signal(repo):
    await _seed_instance(repo)
    await repo.record_signal(signal())
    assert await repo.open_position(position()) is True
    assert await repo.open_position(position(uid="pos2")) is False, (
        "one signal may open at most one position")


async def _two_open_positions_same_symbol(repo):
    await _seed_instance(repo)
    await repo.record_signal(signal("sigA"))
    await repo.record_signal(signal("sigB"))
    assert await repo.open_position(position("posA", "sigA")) is True
    assert await repo.open_position(position("posB", "sigB")) is False, (
        "the database itself must refuse a second open position per symbol")
    assert len(await repo.load_open_positions()) == 1


async def _reopen_after_close(repo):
    await _seed_instance(repo)
    await repo.record_signal(signal("sigA"))
    await repo.record_signal(signal("sigB"))
    p = position("posA", "sigA")
    await repo.open_position(p)
    p.status = "CLOSED"
    p.exit_price, p.realized_pnl, p.r_multiple = 64000.0, 95.0, 1.9
    p.exit_reason, p.closed_at = "TAKE_PROFIT", 1786563000
    await repo.update_position(p)
    assert await repo.open_position(position("posB", "sigB")) is True, (
        "a closed position must not block the next one")


async def _state_roundtrip(repo):
    await repo.set_state("risk", {"consecutive_losses": 2, "equity": 9950.5})
    assert (await repo.get_state("risk"))["consecutive_losses"] == 2
    await repo.set_state("risk", {"consecutive_losses": 0, "equity": 10050.0})
    assert (await repo.get_state("risk"))["consecutive_losses"] == 0
    assert await repo.get_state("nonexistent") is None


SCENARIOS = [
    _duplicate_signal, _duplicate_order, _duplicate_fill,
    _duplicate_position_same_signal, _two_open_positions_same_symbol,
    _reopen_after_close, _state_roundtrip,
]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__.strip("_"))
async def test_constraints_in_memory(mem_repo, scenario):
    await scenario(mem_repo)


@requires_pg
@pytest.mark.postgres
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda f: f.__name__.strip("_"))
async def test_constraints_postgres(pg_repo, scenario):
    """The same scenarios against the real schema.

    This is what makes the in-memory repository trustworthy: it is not a
    convenient fiction, it is checked to behave like the database.
    """
    await scenario(pg_repo)


# =====================================================================
# Audit trail
# =====================================================================


async def test_rejected_signals_are_persisted(mem_repo):
    """"Why did it NOT enter" must be answerable from the database."""
    await _seed_instance(mem_repo)
    r = signal("sigR", outcome="REJECTED")
    r.rejection_reason = "reward_risk 1.4 < minimum_rr 2.0"
    await mem_repo.record_signal(r)
    rows = await mem_repo.recent_signals()
    assert rows[0]["outcome"] == "REJECTED"
    assert "minimum_rr" in rows[0]["rejection_reason"]


async def test_risk_and_system_events_recorded(mem_repo):
    await mem_repo.record_risk_event(RiskEventRecord(
        event_id=new_uid("risk"), instance_uid="inst1", event_type="LIMIT_BREACH",
        reason="daily loss limit reached", limit_name="max_daily_loss_pct",
        limit_value=0.02, observed_value=0.023))
    await mem_repo.record_system_event(SystemEventRecord(
        event_id=new_uid("evt"), instance_uid="inst1", component="feed",
        event_type="WS_RECONNECT", severity="WARNING"))
    assert len(mem_repo.store["risk_events"]) == 1
    assert (await mem_repo.recent_system_events())[0]["event_type"] == "WS_RECONNECT"


async def test_writable_probe_reflects_connection_state(mem_repo):
    assert await mem_repo.is_writable() is True
    mem_repo.writable = False
    assert await mem_repo.is_writable() is False


# =====================================================================
# Advisory lock -- section 8
# =====================================================================


async def test_lock_key_is_stable_and_fits_bigint():
    k = lock_key()
    assert k == lock_key()
    assert -(2 ** 63) <= k < 2 ** 63


async def test_lock_key_namespaced():
    assert lock_key("a") != lock_key("b")


@requires_pg
@pytest.mark.postgres
async def test_second_instance_refuses_to_start():
    """Process A holds the lock; process B must refuse to operate."""
    a = SingleInstanceLock(TEST_DSN, namespace="test.single.instance")
    b = SingleInstanceLock(TEST_DSN, namespace="test.single.instance")
    try:
        assert await a.acquire() is True
        assert await b.acquire() is False, (
            "a second bot must never operate the same paper account")
        assert b.held is False
    finally:
        await a.release()
        await b.release()


@requires_pg
@pytest.mark.postgres
async def test_lock_is_reusable_after_release():
    """Models a normal Recreate rollout: old pod exits, new pod starts."""
    a = SingleInstanceLock(TEST_DSN, namespace="test.reuse")
    assert await a.acquire() is True
    await a.release()
    b = SingleInstanceLock(TEST_DSN, namespace="test.reuse")
    try:
        assert await b.acquire() is True
    finally:
        await b.release()


@requires_pg
@pytest.mark.postgres
async def test_lock_released_when_connection_dies():
    """kill -9 runs no cleanup code; the server must release the lock anyway."""
    import asyncpg
    key = lock_key("test.kill9")
    victim = await asyncpg.connect(TEST_DSN)
    assert await victim.fetchval("SELECT pg_try_advisory_lock($1)", key) is True
    await victim.close()                      # abrupt: no unlock call

    survivor = await asyncpg.connect(TEST_DSN)
    try:
        assert await survivor.fetchval("SELECT pg_try_advisory_lock($1)", key) is True
        await survivor.fetchval("SELECT pg_advisory_unlock($1)", key)
    finally:
        await survivor.close()


@requires_pg
@pytest.mark.postgres
async def test_context_manager_raises_for_the_second_instance():
    a = SingleInstanceLock(TEST_DSN, namespace="test.ctx")
    await a.acquire()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            async with SingleInstanceLock(TEST_DSN, namespace="test.ctx"):
                pass
    finally:
        await a.release()

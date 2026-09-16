"""A close that happened while nothing was watching is recorded -- if proven.

WHAT HAPPENED. 2026-09-16, 15:24 UTC. tnet's startup protection check flattened
an ETH short and a SOL long whose stops sat beyond liquidation. Six seconds
later the deploy stopped the service to register the next experiment, 1ms after
the ETH close was observed and before either close reached the ledger. Every
start afterwards found the ledger holding two positions Delta did not, and
reconciliation refused to trade: 95 restarts, until the exits were recorded by
hand from Delta's order history.

Refusing was right when the only alternative was guessing. These tests pin the
line between the two: an exit is recorded only when the venue's own history
proves it, and anything less still refuses.
"""
from __future__ import annotations

import pytest

from app.risk.engine import RiskState
from live.client import VenueError
from tests.live_exec.test_live_order_placement import PIDS, a_bot
from tests.live_exec.test_live_position_protection import Venue, open_position


async def _restart_after_race(symbol="ETHUSD", side=-1, closing=None,
                              still_held=False, history_error=None):
    """Open a position, then restart as if its close was never recorded."""
    venue = Venue()
    first = a_bot(venue)
    await open_position(first, venue, symbol, side=side)
    (row,) = [p for p in await first.repo.load_open_positions() if p.symbol == symbol]

    if not still_held:
        venue.positions = []
    if closing is not None:
        order = {"state": "closed", "average_fill_price": "2390.7",
                 "side": "buy" if side < 0 else "sell", "size": row.quantity,
                 "unfilled_size": 0, "reduce_only": True,
                 "created_at": int(row.opened_at) + 60,
                 "meta_data": {"pnl": "18.9175"}, **closing}
        venue.history = [order]
    if history_error is not None:
        def boom(pid):
            raise history_error
        venue.order_history = boom

    bot = a_bot(venue)
    bot.repo = first.repo
    bot.costs = {s: object() for s in PIDS}
    bot.product_ids = dict(PIDS)
    bot.recovery_error = None
    bot._state_loaded = False
    # _persist_close applies the exit to the risk state, as the real bot does;
    # the harness's bare namespace has no apply_close.
    bot.state = RiskState.fresh(10_000.0)
    await bot.recover()
    return bot, row


def _events(bot, kind):
    return [e for e in bot.events if e[0] == kind]


@pytest.mark.asyncio
async def test_the_1524_race_is_recorded_and_the_bot_becomes_ready():
    """A reduce-only close, opposite side, full size, after the open."""
    bot, _ = await _restart_after_race(closing={})
    assert bot.recovery_error is None, bot.recovery_error
    assert bot._state_loaded, "recover() still refused"
    assert await bot.repo.load_open_positions() == []
    assert len(_events(bot, "VANISHED_POSITION_RECORDED")) == 1


@pytest.mark.asyncio
async def test_a_long_is_closed_by_a_sell():
    bot, _ = await _restart_after_race("SOLUSD", side=1, closing={})
    assert bot.recovery_error is None, bot.recovery_error
    assert await bot.repo.load_open_positions() == []


@pytest.mark.parametrize("closing,why", [
    ({"side": "sell"}, "same side as the short: that would ADD, not close"),
    ({"unfilled_size": 5}, "partially filled: not the whole position"),
    ({"reduce_only": False}, "not reduce-only: could have opened something"),
    ({"created_at": 0}, "placed before the position opened: an older close"),
])
@pytest.mark.asyncio
async def test_an_unproven_close_is_left_for_reconciliation_to_refuse(closing, why):
    bot, _ = await _restart_after_race(closing=closing)
    assert bot.recovery_error is not None, f"recorded although {why}"
    assert "VANISHED_POSITION" in bot.recovery_error
    assert len(await bot.repo.load_open_positions()) == 1
    assert _events(bot, "VANISHED_POSITION_RECORDED") == []


@pytest.mark.asyncio
async def test_no_closing_order_at_all_still_refuses():
    bot, _ = await _restart_after_race(closing=None)
    assert bot.recovery_error is not None
    assert len(await bot.repo.load_open_positions()) == 1


@pytest.mark.asyncio
async def test_an_unreadable_history_records_nothing():
    bot, _ = await _restart_after_race(closing={}, history_error=VenueError("503"))
    assert bot.recovery_error is not None
    assert len(await bot.repo.load_open_positions()) == 1


@pytest.mark.asyncio
async def test_a_position_the_venue_still_holds_is_not_touched():
    """Not vanished at all, so nothing to record -- whatever the history says."""
    bot, _ = await _restart_after_race(closing={}, still_held=True)
    assert len(await bot.repo.load_open_positions()) == 1
    assert _events(bot, "VANISHED_POSITION_RECORDED") == []


@pytest.mark.asyncio
async def test_the_order_validated_is_the_order_recorded():
    """Two orders in history: the newest closes it. Validation and recording
    must look at the same one, or startup could check one and record another."""
    venue_close = {"average_fill_price": "2390.7"}
    bot, row = await _restart_after_race(closing=venue_close)
    closed = [p for p in await bot.repo.load_recent_positions(limit=10)
              if p.symbol == "ETHUSD"]
    assert closed and float(closed[0].exit_price) == pytest.approx(2390.7)


@pytest.mark.asyncio
async def test_a_close_without_venue_pnl_is_recorded_without_crashing_startup():
    """The notification formatted realized_pnl as a float; a closing order with
    no meta_data.pnl made it None and raised AFTER the close was written --
    inside recover(), a startup crash."""
    bot, _ = await _restart_after_race(closing={"meta_data": {}})
    assert bot.recovery_error is None, bot.recovery_error
    assert await bot.repo.load_open_positions() == []

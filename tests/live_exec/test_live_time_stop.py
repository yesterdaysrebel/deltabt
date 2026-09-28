"""A live position older than max_hold_seconds is closed, as the paper bot does.

WHAT HAPPENED. PaperBroker has closed positions at max_hold_seconds (TIME_EXIT)
since the time stop was added, and tnet's risk_hash carries 259200s. But
LiveBroker was constructed without the setting and nothing in live/ read it.
On 2026-09-28 tnet held a BTC long opened 09-24 15:05 UTC -- 91 hours old,
19 past the limit its own experiment identity declared.

These drive the real LiveBroker and LiveTradingBot through the scripted venue
in test_live_position_protection.py.
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from app.clock import MarketClock
from live.ledger import close_facts
from live.runtime import LiveTradingBot
from tests.live_exec.test_live_order_placement import MKT, PIDS, a_bot
from tests.live_exec.test_live_position_protection import (Venue, flattens,
                                                            open_position)

HOLD = 72 * 3600


async def a_held_position(*, age: int, symbol="BTCUSD", side=1, limit=HOLD):
    """A bot holding one protected position `age` seconds old in exchange time."""
    venue = Venue()
    bot = a_bot(venue)
    bot.broker.max_hold_seconds = limit
    await open_position(bot, venue, symbol, side=side)
    # The harness venue stamps created_at in microseconds; the real one sends
    # ISO text. Pin the recorded open to MKT so ages here are exact.
    (rec,) = await bot.repo.load_open_positions()
    await bot.repo.update_position(dataclasses.replace(rec, opened_at=MKT))
    bot.clock = MarketClock(MKT + age)
    return bot, venue


def time_stops(bot):
    return [e for e in bot.events if e[0] == "TIME_STOP"]


@pytest.mark.asyncio
async def test_the_tnet_btc_long_is_closed_at_91_hours():
    bot, venue = await a_held_position(age=91 * 3600)
    await bot._enforce_time_stop()

    (close,) = flattens(bot, venue, "BTCUSD")
    assert close.reduce_only
    assert close.size == bot.broker.positions["BTCUSD"].contracts
    (ev,) = time_stops(bot)
    assert ev[3]["held_seconds"] == 91 * 3600
    assert ev[3]["max_hold_seconds"] == HOLD
    assert any("TIME STOP" in s for s in bot.notifier.sent)


@pytest.mark.asyncio
async def test_a_position_inside_the_limit_is_left_alone():
    bot, venue = await a_held_position(age=HOLD - 1)
    await bot._enforce_time_stop()
    assert flattens(bot, venue, "BTCUSD") == []
    assert time_stops(bot) == []


@pytest.mark.asyncio
async def test_the_limit_itself_closes_as_the_paper_broker_does():
    """PaperBroker._timed_out is `>=`; the live rule must not be a second apart."""
    bot, venue = await a_held_position(age=HOLD)
    await bot._enforce_time_stop()
    assert len(flattens(bot, venue, "BTCUSD")) == 1


@pytest.mark.asyncio
async def test_zero_disables_it_as_on_paper():
    bot, venue = await a_held_position(age=500 * 3600, limit=0)
    await bot._enforce_time_stop()
    assert flattens(bot, venue, "BTCUSD") == []


@pytest.mark.asyncio
async def test_no_market_time_yet_closes_nothing():
    """Right after a restart the market clock is 0 until the feed ticks. Age
    against 0 would be negative, never 'expired' -- but say it outright."""
    bot, venue = await a_held_position(age=91 * 3600)
    bot.clock = MarketClock()
    await bot._enforce_time_stop()
    assert flattens(bot, venue, "BTCUSD") == []


@pytest.mark.asyncio
async def test_it_asks_once_not_every_check():
    bot, venue = await a_held_position(age=91 * 3600)
    await bot._enforce_time_stop()
    await bot._enforce_time_stop()
    assert len(flattens(bot, venue, "BTCUSD")) == 1


@pytest.mark.asyncio
async def test_a_refused_close_is_critical_and_not_hammered():
    bot, venue = await a_held_position(age=91 * 3600)
    venue.outcome = "reject"
    await bot._enforce_time_stop()
    await bot._enforce_time_stop()
    (failed,) = [e for e in bot.events if e[0] == "FLATTEN_FAILED"]
    assert failed[2] == "CRITICAL" and failed[3]["reason"] == "time_exit"
    assert len(flattens(bot, venue, "BTCUSD")) == 1


@pytest.mark.asyncio
async def test_a_position_the_venue_no_longer_holds_is_not_closed_again():
    bot, venue = await a_held_position(age=91 * 3600)
    bot.broker.positions.clear()          # a bracket fired; poll has seen it
    bot.broker.positions["ETHUSD"] = object()   # something else still open
    await bot._enforce_time_stop()
    assert flattens(bot, venue, "BTCUSD") == []


def test_the_close_is_recorded_as_time_exit_not_manual_close():
    """The reason the ledger writes must be the paper broker's TIME_EXIT."""
    from app.execution.paper_broker import ExitReason

    row = {"reduce_only": True, "stop_order_type": None,
           "average_fill_price": "83000"}
    assert close_facts(row, requested="time_exit")["exit_reason"] == ExitReason.TIME_EXIT.value
    # ...and a bracket that beat it to the fill is still the bracket.
    row["stop_order_type"] = "stop_loss_order"
    assert close_facts(row, requested="time_exit")["exit_reason"] == "STOP_LOSS"


def test_the_live_bot_hands_the_setting_to_its_broker(monkeypatch):
    """The actual bug: the value existed in settings and never reached LiveBroker."""
    import live.runtime as rt

    def parent_init(self, *a, **k):
        self.settings = SimpleNamespace(risk=SimpleNamespace(
            exit_on_wpr_band_exit=False, wpr_exit_long_level=-80.0,
            wpr_exit_short_level=-20.0, max_hold_seconds=259200))

    monkeypatch.setattr(rt.TradingBot, "__init__", parent_init)
    bot = LiveTradingBot(client=object(), product_ids=PIDS)
    assert bot.broker.max_hold_seconds == 259200

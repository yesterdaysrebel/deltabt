"""A market entry the book cannot fill near its reference is never sent.

WHAT HAPPENED. 2026-09-16, 16:20-17:00 UTC, tnet. The SOLUSD book sat ~1.4%
above the price the signals are computed from (testnet ask 99.226 against a
spot of 97.983 at 17:05). Every SOL entry filled 1.2-1.5R from its reference
-- 97.3 filled at 98.692 -- and the fill check closed it about three seconds
later. Five round trips in forty minutes, each paying spread and fees to learn
what the ticker already showed before the order went out.

The fill check stays: a market order walks past the touch. These pin the
cheaper refusal in front of it.
"""
from __future__ import annotations

import pytest

from live.client import VenueError
from tests.live_exec.test_live_order_placement import FakeVenue, a_bot, approve, place

ENTRY, RPU = 75_000.0, 400.0            # approve(): stop = entry -/+ 1R, limit 0.25R


class Book(FakeVenue):
    """A ticker with separate sides, and a record of every leverage write."""

    def __init__(self, ask=ENTRY, bid=ENTRY, mark=ENTRY):
        super().__init__("fill")
        self.ask, self.bid, self.mark = ask, bid, mark
        self.ticker_error: Exception | None = None

    def get_ticker(self, symbol):
        if self.ticker_error:
            raise self.ticker_error
        quotes = {}
        if self.ask is not None:
            quotes["best_ask"] = str(self.ask)
        if self.bid is not None:
            quotes["best_bid"] = str(self.bid)
        out = {"quotes": quotes}
        if self.mark is not None:
            out["mark_price"] = str(self.mark)
        return out


async def refused(venue, side=1):
    bot = a_bot(venue)
    await place(bot, "SOLUSD", side)
    return bot


def assert_refused_cleanly(bot, venue):
    assert venue.placed == [], "an order left for a book it could not fill near"
    assert "leverage" not in venue.__dict__, "leverage was changed for no order"
    assert any(e[0] == "ENTRY_NOT_OPENED" for e in bot.events)
    assert bot.broker._pending == {}


@pytest.mark.asyncio
async def test_the_solusd_evening_is_refused_before_anything_is_sent():
    """97.3 filled at 98.692 is 1.51R; in this harness's prices, 1.5R."""
    venue = Book(ask=ENTRY + 1.5 * RPU)
    bot = await refused(venue, side=1)
    assert_refused_cleanly(bot, venue)
    assert await bot.repo.effective_exposure() == 0, "the slot was kept"


@pytest.mark.asyncio
async def test_a_book_within_the_limit_is_traded():
    venue = Book(ask=ENTRY + 0.2 * RPU)
    bot = await refused(venue, side=1)
    assert len(venue.placed) == 1


@pytest.mark.asyncio
async def test_a_short_whose_bid_is_already_beyond_its_stop_is_refused():
    """Beyond the stop is refused even with the deviation limit switched off."""
    venue = Book(bid=ENTRY + 1.1 * RPU)
    bot = a_bot(venue)
    bot.broker.max_entry_deviation = 0
    await place(bot, "SOLUSD", -1)
    assert_refused_cleanly(bot, venue)


@pytest.mark.asyncio
async def test_a_buy_meets_the_ask_and_a_sell_meets_the_bid():
    """The deviation is measured on the touch the order meets.

    REWRITTEN 2026-10-02. This used to show a 1.5R-wide ask NOT blocking a
    sell. Since the spread check, any book wider than 0.5R is refused whatever
    the side -- 09-28's SOL entries passed the one-sided check and were then
    flattened into the far side at -1.65..-4.08R. The side-specific deviation
    is still pinned here, inside a spread the new rule allows.
    """
    sell = Book(ask=ENTRY + 0.45 * RPU, bid=ENTRY)   # bid on reference: 0R
    await refused(sell, side=-1)
    assert len(sell.placed) == 1

    blocked = Book(ask=ENTRY + 0.45 * RPU, bid=ENTRY + 0.1 * RPU)
    bot = a_bot(blocked)
    bot.broker.max_entry_deviation = 0.3              # ask is 0.45R off
    await place(bot, "SOLUSD", 1)
    assert_refused_cleanly(bot, blocked)


@pytest.mark.asyncio
async def test_the_solusd_spread_of_09_28_is_refused_on_either_side():
    """09-28, tnet: entries passed the touch check into a 3-4% wide book."""
    for side in (1, -1):
        venue = Book(ask=ENTRY + 1.5 * RPU, bid=ENTRY) if side < 0 else \
            Book(ask=ENTRY, bid=ENTRY - 1.5 * RPU)
        bot = await refused(venue, side=side)
        assert_refused_cleanly(bot, venue)


@pytest.mark.asyncio
async def test_a_spread_inside_the_limit_is_traded():
    venue = Book(ask=ENTRY + 0.1 * RPU, bid=ENTRY - 0.1 * RPU)
    await refused(venue, side=1)
    assert len(venue.placed) == 1


@pytest.mark.asyncio
async def test_a_one_sided_book_refuses():
    """No far side, no measurable spread, no way to close near the entry."""
    venue = Book(bid=None)
    bot = await refused(venue, side=1)
    assert_refused_cleanly(bot, venue)


@pytest.mark.asyncio
async def test_a_mark_already_beyond_the_stop_refuses():
    """The leading hypothesis for Delta silently dropping a bracket."""
    venue = Book(mark=ENTRY - 1.2 * RPU)                 # long stop is 1R below
    bot = await refused(venue, side=1)
    assert_refused_cleanly(bot, venue)


@pytest.mark.asyncio
async def test_a_missing_mark_refuses():
    venue = Book(mark=None)
    bot = await refused(venue, side=1)
    assert_refused_cleanly(bot, venue)


@pytest.mark.asyncio
async def test_an_unreadable_ticker_refuses():
    venue = Book()
    venue.ticker_error = VenueError("503")
    bot = await refused(venue)
    assert_refused_cleanly(bot, venue)


@pytest.mark.asyncio
async def test_a_missing_quote_on_the_needed_side_refuses():
    venue = Book(ask=None)
    bot = await refused(venue, side=1)
    assert_refused_cleanly(bot, venue)


@pytest.mark.asyncio
async def test_a_limit_entry_is_not_checked_against_the_book():
    """Its own price bounds the fill; the ticker is not consulted."""
    venue = Book()
    venue.ticker_error = AssertionError("the ticker was read for a limit order")
    bot = a_bot(venue)
    exp, decision = approve("SOLUSD", 1)
    object.__setattr__(decision.intent, "order_type", "limit")
    object.__setattr__(decision.intent, "limit_price", ENTRY)
    bot.broker._refuse_if_book_dislocated(decision.intent)

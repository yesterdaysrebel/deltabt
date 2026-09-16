"""Every close has its own client order id.

WHAT HAPPENED. 2026-09-16, 16:20-17:00 UTC, tnet. Five SOLUSD entries filled
~1.4% from their references and were flattened for entry_deviation. All five
closes went out as cid 727639a06bd897e7: the exit id was keyed on
`position_uid or symbol`, and Delta's margined positions carry no uid.

The venue accepted them, so nothing failed. What makes it dangerous is
client.place_order: a write that times out or returns 5xx is resolved by
looking its cid up. With a reused cid that lookup finds the PREVIOUS, filled
close and reports the new one as landed -- while the position, being
flattened because it was unsafe, is still open.
"""
from __future__ import annotations

from live.broker import LiveBroker
from tests.live_exec.test_live_order_placement import (PIDS, TICKS, FakeVenue,
                                                        approve)

SYMBOL = "SOLUSD"


def a_broker(venue):
    return LiveBroker(venue, product_ids=PIDS, experiment_id="EXP",
                      tick_size=TICKS, kill_switch_path="/nonexistent")


def _hold(venue, intent, entry_price):
    venue.positions = [{"product_id": PIDS[intent.symbol],
                        "product_symbol": intent.symbol,
                        "size": intent.side * intent.quantity,
                        "entry_price": str(entry_price)}]


def _last_close(venue):
    return [o for o in venue.placed if o.reduce_only][-1].client_order_id


def trade_and_close(broker, venue, entry_price=98.692, reason="entry_deviation"):
    """Open a position through the broker, flatten it, and see it close."""
    _, decision = approve(SYMBOL, 1)
    broker.submit_order(decision.intent)
    _hold(venue, decision.intent, entry_price)
    broker.poll()
    broker.close_position(SYMBOL, reason)
    cid = _last_close(venue)
    venue.positions = []
    broker.poll()
    return cid


def test_two_closes_of_two_positions_on_one_symbol_have_different_ids():
    """The 2026-09-16 evening, twice over: same symbol, same reason."""
    venue = FakeVenue()
    broker = a_broker(venue)
    first = trade_and_close(broker, venue)
    second = trade_and_close(broker, venue)
    assert first != second, "a timed-out close would resolve to the previous one"


def test_retrying_the_close_of_one_position_keeps_its_id():
    """The id is still an idempotency key: the same close, asked twice."""
    venue = FakeVenue()
    broker = a_broker(venue)
    _, decision = approve(SYMBOL, 1)
    broker.submit_order(decision.intent)
    _hold(venue, decision.intent, 98.692)
    broker.poll()
    broker.close_position(SYMBOL, "entry_deviation")
    once = _last_close(venue)
    broker.close_position(SYMBOL, "entry_deviation")
    assert _last_close(venue) == once


def test_a_recovered_position_is_told_apart_from_the_last_one_closed():
    """After a restart no entry id is in memory and Delta gives no uid; the
    venue's own facts about the position still separate the two."""
    venue = FakeVenue()
    before = a_broker(venue)
    earlier = trade_and_close(before, venue, entry_price=98.692)

    after = a_broker(venue)                     # a new process
    _, decision = approve(SYMBOL, 1)
    _hold(venue, decision.intent, 97.3)          # opened before the restart
    after.poll()
    after.close_position(SYMBOL, "unprotected")
    recovered = _last_close(venue)
    after.close_position(SYMBOL, "unprotected")
    assert _last_close(venue) == recovered, "not stable across retries"

    other = a_broker(venue)
    _hold(venue, decision.intent, 98.692)
    other.poll()
    other.close_position(SYMBOL, "unprotected")
    assert recovered != _last_close(venue)
    assert earlier not in (recovered, _last_close(venue))


def test_the_venue_uid_is_used_when_the_venue_gives_one():
    venue = FakeVenue()
    ids = []
    for uid in ("pos-1", "pos-2"):
        broker = a_broker(venue)
        _, decision = approve(SYMBOL, 1)
        _hold(venue, decision.intent, 98.692)
        venue.positions[0]["position_uid"] = uid
        broker.poll()
        broker.close_position(SYMBOL, "unprotected")
        ids.append(_last_close(venue))
    assert ids[0] != ids[1]

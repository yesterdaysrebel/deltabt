"""The live runtime's two jobs: fail closed on a venue disagreement, and
actually run its poll loop.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from live import runtime as live_runtime
from live.runtime import POLL_SECONDS, RECONCILE_SECONDS, LiveTradingBot


class FakeRepo:
    def __init__(self, positions=None, state=None):
        self._positions = positions or []
        self._state = state

    async def get_state(self, key):
        return self._state

    async def load_open_positions(self):
        return self._positions


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, subject, body):
        self.sent.append((subject, body))


class FakeVenue:
    def __init__(self, positions=None, error=None):
        self._positions = positions or []
        self._error = error

    def get_positions(self):
        if self._error:
            raise self._error
        return self._positions


class Ledger:
    def __init__(self, symbol, contracts, side="LONG"):
        self.symbol, self.contracts, self.side = symbol, contracts, side
        self.position_uid = f"uid-{symbol}"


def a_bot(venue, ledger=(), costs=("BEATUSD",)):
    """A LiveTradingBot with only the collaborators these tests touch.

    __new__ rather than __init__ on purpose: TradingBot.__init__ builds a feed,
    a backfiller and a PaperBroker, none of which these tests exercise, and
    constructing them would test the parent rather than this class.
    """
    bot = LiveTradingBot.__new__(LiveTradingBot)
    bot.client = venue
    bot.repo = FakeRepo(list(ledger))
    bot.notifier = FakeNotifier()
    bot.costs = {s: object() for s in costs}
    bot.recovery_error = None
    bot._symbol_for = {27: "BEATUSD"}
    bot.product_ids = {"BEATUSD": 27}
    bot.events = []
    bot._state_loaded = False

    class _State:
        equity = 10_000.0
    bot.state = _State()

    async def _event(component, event_type, *, symbol=None, severity="INFO",
                     payload=None):
        bot.events.append((component, event_type, severity, payload))

    bot._event = _event

    class _Broker:
        def poll(self):
            return []

    bot.broker = _Broker()
    return bot


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# -- reconciliation is the gate ---------------------------------------------

def test_a_clean_venue_reconciles():
    bot = a_bot(FakeVenue([]), ledger=[])
    assert run(bot.reconcile_with_venue([])) is True
    assert bot.recovery_error is None


def test_an_unknown_venue_position_refuses_to_start():
    """The crash case: the venue holds what the ledger does not."""
    bot = a_bot(FakeVenue([{"product_symbol": "BEATUSD", "size": 565}]))
    assert run(bot.reconcile_with_venue([])) is False
    assert "UNKNOWN_POSITION" in bot.recovery_error
    assert any(e[1] == "RECONCILIATION_FAILED" and e[2] == "CRITICAL"
               for e in bot.events)
    assert bot.notifier.sent, "a halt nobody is told about is a halt nobody acts on"


def test_a_matching_ledger_reconciles():
    bot = a_bot(FakeVenue([{"product_symbol": "BEATUSD", "size": 565}]))
    assert run(bot.reconcile_with_venue([Ledger("BEATUSD", 565)])) is True


def test_a_short_in_the_ledger_is_compared_as_a_short():
    bot = a_bot(FakeVenue([{"product_symbol": "BEATUSD", "size": -565}]))
    assert run(bot.reconcile_with_venue([Ledger("BEATUSD", 565, "SHORT")])) is True
    bot2 = a_bot(FakeVenue([{"product_symbol": "BEATUSD", "size": -565}]))
    assert run(bot2.reconcile_with_venue([Ledger("BEATUSD", 565, "LONG")])) is False


def test_a_venue_we_cannot_read_also_refuses_to_start():
    """'Assume flat' is how an unknown position gets traded around."""
    from live.client import VenueUnavailable
    bot = a_bot(FakeVenue(error=VenueUnavailable("timeout")))
    assert run(bot.reconcile_with_venue([])) is False
    assert "could not read positions" in bot.recovery_error
    assert bot.notifier.sent


# -- recover() fails closed before it reaches the venue ----------------------

def test_duplicate_ledger_rows_stop_before_the_venue_is_asked():
    """A database that disagrees with itself cannot be compared to anything."""
    venue = FakeVenue([])
    bot = a_bot(venue, ledger=[Ledger("BEATUSD", 1), Ledger("BEATUSD", 2)])
    run(bot.recover())
    assert "duplicate open positions" in bot.recovery_error
    assert bot._state_loaded is False


def test_a_position_outside_the_universe_stops_too():
    bot = a_bot(FakeVenue([]), ledger=[Ledger("NOPEUSD", 1)])
    run(bot.recover())
    assert "not in the configured universe" in bot.recovery_error


def test_a_clean_recover_marks_state_loaded():
    bot = a_bot(FakeVenue([]), ledger=[])
    run(bot.recover())
    assert bot.recovery_error is None and bot._state_loaded is True


# -- the loop ----------------------------------------------------------------

def test_the_poll_loop_tests_an_event_not_its_truthiness():
    """REGRESSION. `_stopping` is an asyncio.Event, which has no __bool__ and
    is therefore always truthy. `while not self._stopping` is permanently
    False: the loop would never run one iteration while looking correct."""
    src = inspect.getsource(LiveTradingBot._poll_loop)
    assert "self._stopping.is_set()" in src
    assert "while not self._stopping:" not in src


def test_the_intervals_are_sane():
    assert 0 < POLL_SECONDS <= 30, "fills should not wait a minute to be seen"
    assert RECONCILE_SECONDS >= POLL_SECONDS

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

    async def load_reserving_entry_orders(self):
        # recover() now sweeps orphaned entry orders after reconciling. These
        # tests are about reconciliation, so there are none to sweep; the
        # sweep has its own tests in test_live_order_placement.py.
        return []


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


# -- prod may not start with the breakers off -----------------------------

def test_prod_refuses_to_start_with_the_breakers_disabled(tmp_path):
    """Wired, not merely available. A guard nothing calls is decoration."""
    from dataclasses import dataclass

    @dataclass
    class _Risk:
        max_drawdown_pct: float = 1.0        # what variables.tf ships today
        max_daily_loss_pct: float = 1.0
        max_consecutive_losses: int = 0

    class _Settings:
        risk = _Risk()

    bot = a_bot(FakeVenue([]))
    bot.settings = _Settings()
    bot.venue = "prod"
    assert run(bot.start()) is False
    assert "circuit breakers off" in bot.recovery_error
    assert bot.notifier.sent, "a refusal nobody is told about is one nobody fixes"


def test_the_kill_switch_stops_an_order_at_the_broker(tmp_path):
    from live.broker import LiveBroker
    from live.client import VenueError

    switch = tmp_path / "HALT"
    b = LiveBroker(object(), product_ids={"BTCUSD": 84}, experiment_id="E",
                   kill_switch_path=str(switch))

    class _Intent:
        symbol, side, quantity, order_type = "BTCUSD", 1, 1, "market"
        limit_price = None
        stop_price, target_price, risk_per_unit = 1.0, 3.0, 1.0
        intent_id, signal_key = "i", "k"

    switch.write_text("stop")
    with pytest.raises(VenueError, match="kill switch"):
        b.submit_order(_Intent())


# -- recording what the venue did --------------------------------------------

class _Repo(FakeRepo):
    def __init__(self, positions=None):
        super().__init__(positions)
        self.opened, self.updated, self.accept = [], [], True

    async def open_position(self, rec):
        self.opened.append(rec)
        return self.accept

    async def update_position(self, rec):
        self.updated.append(rec)


def _persisting_bot(venue_orders, intent, cid="cid1", ledger=()):
    bot = a_bot(FakeVenue([]), ledger=ledger)
    bot.repo = _Repo(list(ledger))
    bot.instance_uid = "inst"
    bot.experiment_id = "EXP"
    bot.identity = None
    bot.venue = "testnet"
    bot.product_ids = {"BEATUSD": 27}

    class _Strategy:
        version = "v1"
    bot.strategy = _Strategy()

    class _Clock:
        def now(self):
            return 1788768600
    bot.clock = _Clock()

    class _Client:
        def get_order_by_client_id(self, c):
            return venue_orders.get(c)

        def order_history(self, pid=None, page_size=20):
            return venue_orders.get("history", [])
    bot.client = _Client()

    class _Broker:
        def intent_for(self, symbol):
            return (cid, intent) if intent else (None, None)
    bot.broker = _Broker()

    bot.state.trades_today = 0
    bot.state.apply_close = lambda *a, **k: None
    async def _save():
        pass
    bot._save_state = _save
    return bot


INTENT = dict(side=1, symbol="BEATUSD", signal_key="k", quantity=1,
              stop_price=90.0, target_price=130.0, risk_per_unit=10.0,
              entry_reference=100.0, notional=100.0)
ENTRY_ORDER = {"id": 1, "client_order_id": "cid1", "average_fill_price": "100.0",
               "paid_commission": "0.05", "size": 1, "unfilled_size": 0,
               "created_at": "2026-09-15T06:55:54.630597Z"}


def test_an_opened_position_is_recorded_with_the_venue_price():
    bot = _persisting_bot({"cid1": ENTRY_ORDER}, INTENT)
    run(bot._persist_open(type("E", (), {"symbol": "BEATUSD", "payload": {}})()))
    assert len(bot.repo.opened) == 1
    rec = bot.repo.opened[0]
    assert rec.entry_price == 100.0 and rec.quantity == 1
    assert rec.experiment_id == "EXP" and rec.status == "OPEN"


def test_a_position_we_cannot_explain_is_not_guessed_at():
    """After a restart there is no intent in memory. Inventing one would hide
    exactly what reconciliation exists to surface."""
    bot = _persisting_bot({}, None)
    run(bot._persist_open(type("E", (), {"symbol": "BEATUSD", "payload": {}})()))
    assert bot.repo.opened == []
    assert any(e[1] == "UNATTRIBUTED_POSITION" and e[2] == "CRITICAL"
               for e in bot.events)


def test_a_close_records_the_reason_the_venue_gives():
    """A bracket leg closed it; the reason is read off the order, not guessed
    from the price."""
    stop_fill = {"id": 2, "state": "closed", "average_fill_price": "90.0",
                 "paid_commission": "0.05", "stop_order_type": "stop_loss_order",
                 "updated_at": "2026-09-15T07:55:54.630597Z",
                 "meta_data": {"pnl": "-10.05"}}
    opened = _persisting_bot({"cid1": ENTRY_ORDER}, INTENT)
    run(opened._persist_open(type("E", (), {"symbol": "BEATUSD", "payload": {}})()))
    rec = opened.repo.opened[0]

    bot = _persisting_bot({"history": [stop_fill]}, INTENT, ledger=[rec])
    run(bot._persist_close(type("E", (), {"symbol": "BEATUSD", "payload": {}})()))
    assert len(bot.repo.updated) == 1
    closed = bot.repo.updated[0]
    assert closed.exit_reason == "STOP_LOSS"
    assert closed.status == "CLOSED"
    assert closed.realized_pnl == -10.05
    assert closed.r_multiple == pytest.approx(-1.0)


def test_a_close_with_no_open_row_is_reported_not_invented():
    bot = _persisting_bot({"history": []}, INTENT, ledger=[])
    run(bot._persist_close(type("E", (), {"symbol": "BEATUSD", "payload": {}})()))
    assert bot.repo.updated == []


def test_the_poll_loop_is_what_calls_them():
    """Wired, not merely defined."""
    src = inspect.getsource(LiveTradingBot._poll_loop)
    assert "_persist_open" in src and "_persist_close" in src

"""LiveBroker: the venue decides fills, and the stop cap points the right way."""

from __future__ import annotations

from decimal import Decimal

import pytest

from live.broker import BrokerEvent, LiveBroker, LivePosition
from live.client import VenueError
from live.orders import STOP_LIMIT_CAP_R

PIDS = {"BEATUSD": 27, "AKEUSD": 31}


class FakeClient:
    """Records what the broker sent, and replays what the venue says."""

    def __init__(self, positions=None, open_orders=None, balances=None):
        self.placed: list = []
        self.cancelled: list = []
        self._positions = positions if positions is not None else []
        self._open_orders = open_orders or []
        self._balances = balances or [{"asset_symbol": "USD", "balance": "10000"}]

    def place_order(self, order):
        self.placed.append(order)
        return {"id": 100 + len(self.placed), "state": "open",
                "client_order_id": order.client_order_id}

    def get_positions(self):
        return self._positions

    def get_open_orders(self, product_id=None):
        return self._open_orders

    def get_balance(self):
        return self._balances

    def cancel_order(self, order_id, product_id):
        self.cancelled.append((order_id, product_id))
        return {"id": order_id, "state": "cancelled"}

    # Leverage is chosen and confirmed per entry since 2026-09-16.
    leverage: dict = {}

    #: The touch every market entry meets (LiveBroker._refuse_if_book_dislocated).
    #: Defaults to the reference this file's intents use: a healthy book.
    touch: float = 0.0929214 + 0.0097

    def get_ticker(self, symbol):
        return {"quotes": {"best_ask": str(self.touch), "best_bid": str(self.touch)}}

    def get_product(self, symbol):
        return {"initial_margin": "2", "maintenance_margin": "1",
                "contract_value": "1"}

    def get_wallet_balance(self, asset="USD"):
        return {"asset_symbol": "USD", "balance": "10000",
                "available_balance": "10000"}

    def set_order_leverage(self, product_id, leverage):
        self.leverage = {**self.leverage, product_id: leverage}
        return {"leverage": str(leverage)}

    def get_order_leverage(self, product_id):
        return float(self.leverage.get(product_id, 0))


class FakeIntent:
    """Enough of ApprovedOrderIntent for the broker's purposes."""

    def __init__(self, symbol="BEATUSD", side=1, quantity=565,
                 order_type="market", limit_price=None,
                 stop_price=0.0929214, target_price=0.1300,
                 risk_per_unit=0.0097):
        self.symbol, self.side, self.quantity = symbol, side, quantity
        self.order_type, self.limit_price = order_type, limit_price
        self.stop_price, self.target_price = stop_price, target_price
        self.risk_per_unit = risk_per_unit
        self.entry_reference = stop_price + side * risk_per_unit
        self.intent_id = "intent-1"
        self.signal_key = "BEATUSD:1788768000:long"


def a_broker(**kw):
    client = FakeClient(**kw)
    return LiveBroker(client, product_ids=PIDS, experiment_id="EXP-1"), client


# -- the inert half ----------------------------------------------------------

def test_market_events_never_produce_fills():
    """THE load-bearing property. Guessing a fill from a tick manufactures a
    position the venue does not hold -- the one thing reconciliation catches
    and cannot fix."""
    b, _ = a_broker()
    assert b.process_market_event(object()) == []
    assert b.process_bar(object()) == []


def test_suspend_blocks_opening_but_says_nothing_about_exits():
    b, client = a_broker()
    b.suspend("BEATUSD")
    with pytest.raises(VenueError, match="suspended"):
        b.submit_order(FakeIntent())
    assert client.placed == []
    assert b.resume("BEATUSD") is True
    assert b.resume("BEATUSD") is False


# -- placing -----------------------------------------------------------------

def test_the_entry_carries_its_brackets_to_the_venue():
    b, client = a_broker()
    b.submit_order(FakeIntent())
    payload = client.placed[0].to_payload()
    assert payload["bracket_stop_loss_price"] == "0.0929214"
    # Trailing zeros are not preserved and need not be: the venue parses the
    # string as a number, and Decimal("0.13") == Decimal("0.1300").
    assert Decimal(payload["bracket_take_profit_price"]) == Decimal("0.13")
    assert payload["stop_trigger_method"] == "mark_price"
    assert "bracket_stop_loss_limit_price" in payload


@pytest.mark.parametrize("side,cmp", [(1, "lt"), (-1, "gt")])
def test_the_stop_cap_sits_beyond_the_stop_in_the_losing_direction(side, cmp):
    """A long exits by SELLING, so its cap is BELOW the stop. Getting this
    backwards puts the cap between entry and stop, where it would refuse every
    fill and leave the position unprotected."""
    b, client = a_broker()
    client.touch = FakeIntent(side=side).entry_reference
    b.submit_order(FakeIntent(side=side))
    p = client.placed[0].to_payload()
    stop = Decimal(p["bracket_stop_loss_price"])
    cap = Decimal(p["bracket_stop_loss_limit_price"])
    if cmp == "lt":
        assert cap < stop, f"long cap {cap} must be below stop {stop}"
    else:
        assert cap > stop, f"short cap {cap} must be above stop {stop}"
    assert abs(abs(cap - stop) - STOP_LIMIT_CAP_R * Decimal("0.0097")) < Decimal("1e-9")


def test_a_market_entry_is_ioc_so_it_cannot_rest_at_an_unknown_price():
    b, client = a_broker()
    b.submit_order(FakeIntent(order_type="market"))
    assert client.placed[0].to_payload()["time_in_force"] == "ioc"


def test_an_unknown_symbol_is_refused_rather_than_guessed():
    b, _ = a_broker()
    with pytest.raises(VenueError, match="no product_id"):
        b.submit_order(FakeIntent(symbol="NOPEUSD"))


def test_prices_are_rounded_to_the_tick():
    client = FakeClient()
    b = LiveBroker(client, product_ids=PIDS, experiment_id="E",
                   tick_size={"BEATUSD": Decimal("0.0001")})
    b.submit_order(FakeIntent(stop_price=0.09294567))
    assert client.placed[0].to_payload()["bracket_stop_loss_price"] == "0.0929"


# -- closing -----------------------------------------------------------------

def test_closing_is_always_reduce_only():
    """Without reduce_only a size that disagrees with the venue by one
    contract OPENS an opposite position instead of closing."""
    b, client = a_broker()
    b.positions["BEATUSD"] = LivePosition("BEATUSD", 1, 565, 0.10, 27,
                                          position_uid="p1")
    b.close_position("BEATUSD", "time_exit")
    p = client.placed[0].to_payload()
    assert p["reduce_only"] is True
    assert p["side"] == "sell"          # closing a long
    assert p["size"] == 565


def test_closing_a_position_we_do_not_hold_raises():
    b, _ = a_broker()
    with pytest.raises(VenueError, match="no open position"):
        b.close_position("BEATUSD", "whatever")


# -- learning what happened --------------------------------------------------

def test_poll_reports_a_position_the_venue_opened():
    b, client = a_broker(positions=[
        {"product_id": 27, "product_symbol": "BEATUSD", "size": 565,
         "entry_price": "0.10"}])
    events = b.poll()
    assert [e.kind for e in events] == ["POSITION_OPENED"]
    assert b.positions["BEATUSD"].contracts == 565


def test_poll_reports_a_position_the_venue_closed_behind_our_back():
    """A bracket leg fired, or we were liquidated, while nothing watched."""
    b, client = a_broker(positions=[])
    b.positions["BEATUSD"] = LivePosition("BEATUSD", 1, 565, 0.10, 27)
    events = b.poll()
    assert [e.kind for e in events] == ["POSITION_CLOSED"]
    assert events[0].payload["closed_by"] == "venue"
    assert b.positions == {}


def test_poll_reports_a_partial():
    b, client = a_broker(positions=[
        {"product_id": 27, "product_symbol": "BEATUSD", "size": 300,
         "entry_price": "0.10"}])
    b.positions["BEATUSD"] = LivePosition("BEATUSD", 1, 565, 0.10, 27)
    events = b.poll()
    assert [e.kind for e in events] == ["FILL"]
    assert events[0].payload["delta"] == -265


def test_poll_is_a_diff_so_a_missed_poll_costs_latency_not_correctness():
    b, client = a_broker(positions=[
        {"product_id": 27, "product_symbol": "BEATUSD", "size": 565,
         "entry_price": "0.10"}])
    assert len(b.poll()) == 1
    assert b.poll() == []          # unchanged venue, no repeat event


def test_poll_reads_a_short_as_short():
    b, client = a_broker(positions=[
        {"product_id": 27, "product_symbol": "BEATUSD", "size": 565,
         "side": "sell", "entry_price": "0.10"}])
    b.poll()
    assert b.positions["BEATUSD"].side == -1


def test_flat_rows_are_not_positions():
    b, client = a_broker(positions=[
        {"product_id": 27, "product_symbol": "BEATUSD", "size": 0}])
    assert b.poll() == [] and b.positions == {}


# -- stale entries -----------------------------------------------------------

def test_an_exit_order_is_never_cancelled_for_being_old():
    """Cancelling a resting exit because it is old removes the protection."""
    b, client = a_broker(open_orders=[
        {"id": 1, "product_id": 27, "created_at": "1788768000", "reduce_only": True},
        {"id": 2, "product_id": 27, "created_at": "1788768000"},
    ])
    b.expire_stale_entries(now=1788768000 + 10_000)
    assert client.cancelled == [(2, 27)]


def test_a_fresh_entry_is_left_alone():
    b, client = a_broker(open_orders=[
        {"id": 3, "product_id": 27, "created_at": "1788768000"}])
    b.expire_stale_entries(now=1788768000 + 5)
    assert client.cancelled == []

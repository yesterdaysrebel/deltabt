"""Every broker the bot can be given must answer everything the bot calls.

WHY THIS EXISTS

On 2026-09-15 the first live deploy reached a running container, passed
/readyz, and was recorded a success. The bot then failed once per second for
23 minutes:

    AttributeError: 'LiveBroker' object has no attribute
                    'close_if_setup_invalidated'

LiveBroker was missing three of the methods TradingBot calls. 2821 tests
passed. Every one of them drove LiveBroker DIRECTLY against a FakeClient --
none went through TradingBot, which is the only caller that reaches those
methods. The class was complete as a thing in itself and incomplete as the
thing it had to be.

THE LIST IS READ FROM bot.py, NOT RESTATED HERE. A restated list is a second
place to remember, and this repository's whole history of deploy failures is
second places that drifted. Adding a `self.broker.foo()` call to the bot makes
this fail until every broker grows a `foo`.
"""

from __future__ import annotations

import pathlib
import re

from app.execution.paper_broker import PaperBroker
from live.broker import LiveBroker

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOT = (ROOT / "app/runtime/bot.py").read_text()

#: `self.broker.<name>` anywhere in the bot, attribute or call.
CALLS = re.compile(r"self\.broker\.([A-Za-z_][A-Za-z0-9_]*)")


def required() -> set[str]:
    return set(CALLS.findall(BOT))


def _instance(cls):
    """A broker built without touching a venue or a database.

    __init__ is called for real -- attributes set there (positions,
    entry_ttl_seconds) are part of the surface just as much as methods, and
    checking the CLASS alone reports them missing. That mistake was made while
    diagnosing the outage above and inflated three missing methods to five.
    """
    obj = cls.__new__(cls)
    if cls is LiveBroker:
        cls.__init__(obj, client=None, product_ids={"BTCUSD": 84},
                     experiment_id="test")
    else:
        cls.__init__(obj, {}, starting_equity=10_000.0)
    return obj


def test_the_scan_finds_the_calls_that_are_really_there():
    """A regex that matches nothing would pass this file vacuously."""
    names = required()
    assert len(names) >= 8, f"only found {sorted(names)}; the pattern is wrong"
    # The one whose absence took the live bot down, and two others known real.
    for known in ("close_if_setup_invalidated", "submit_order", "get_positions"):
        assert known in names, f"{known} not detected in bot.py"


def _absent(broker, name: str) -> bool:
    """Is `name` genuinely not provided?

    hasattr() SWALLOWS AttributeError RAISED BY A PROPERTY GETTER, so a
    property that needs a live client reports as missing on a broker built
    without one -- `equity` does exactly that. Checking the class as well
    separates "not provided" from "provided and unhappy with a null client".
    Missing this inflated the outage's three missing methods to four.
    """
    return not (hasattr(type(broker), name) or hasattr(broker, name))


def test_the_live_broker_answers_everything_the_bot_calls():
    broker = _instance(LiveBroker)
    missing = sorted(n for n in required() if _absent(broker, n))
    assert not missing, (
        f"LiveBroker does not provide {missing}, which app/runtime/bot.py "
        f"calls. The bot will raise AttributeError inside its bar loop -- "
        f"after /readyz has passed and the deploy has been recorded a success.")


def test_the_paper_broker_answers_everything_the_bot_calls():
    """The control. If this ever fails the scan has started over-reading."""
    broker = _instance(PaperBroker)
    missing = sorted(n for n in required() if _absent(broker, n))
    assert not missing, f"PaperBroker does not provide {missing}"


def test_the_check_catches_a_broker_missing_a_method():
    """A scanner that cannot fail is decoration."""
    class Incomplete:
        pass

    missing = sorted(n for n in required() if _absent(Incomplete(), n))
    assert "close_if_setup_invalidated" in missing


# ---------------------------------------------------------------------------
# The band exit, behaviourally. The three settings it reads were added with the
# missing methods, and a setting that reaches nothing is this repository's most
# repeated bug -- `live_venue` was declared, validated and wired to nothing;
# the credential ARN was never passed to Terraform. Asserting the wiring by
# grepping runtime.py would be the same kind of test that missed the outage
# above, so this drives the behaviour instead.

from live.broker import LivePosition


class _RecordingClient:
    """Records orders instead of placing them."""

    def __init__(self):
        self.orders = []

    def place_order(self, order):
        self.orders.append(order)
        return {"id": 1, "state": "filled"}


def _broker_with_position(side, **kw):
    client = _RecordingClient()
    b = LiveBroker(client, product_ids={"BTCUSD": 84}, experiment_id="e", **kw)
    b.positions["BTCUSD"] = LivePosition(
        symbol="BTCUSD", side=side, contracts=1, entry_price=100.0,
        product_id=84, position_uid="p1")
    return b, client


class TestSetupInvalidatedExit:
    def test_disabled_by_default_closes_nothing(self):
        b, c = _broker_with_position(1)
        assert b.close_if_setup_invalidated("BTCUSD", -95.0, 99.0, 0) == []
        assert not c.orders, "closed a position with the band exit disabled"

    def test_a_long_closes_below_the_floor(self):
        b, c = _broker_with_position(1, exit_on_wpr_band_exit=True)
        b.close_if_setup_invalidated("BTCUSD", -95.0, 99.0, 0)
        assert len(c.orders) == 1, "a long below the floor was not closed"
        assert c.orders[0].reduce_only, (
            "the close was not reduce_only; a size that disagrees with the "
            "venue's by one contract would OPEN an opposite position")

    def test_a_long_past_the_CEILING_is_left_alone(self):
        """ONLY THE ADVERSE SIDE COUNTS.

        A long that climbs past the ceiling is winning. Getting this backwards
        closes exactly the trades that reach target, which is the failure the
        paper broker's docstring warns about -- and it would look like the exit
        rule working.
        """
        b, c = _broker_with_position(1, exit_on_wpr_band_exit=True)
        b.close_if_setup_invalidated("BTCUSD", -5.0, 101.0, 0)
        assert not c.orders, "closed a WINNING long that passed the ceiling"

    def test_a_short_closes_above_the_ceiling(self):
        b, c = _broker_with_position(-1, exit_on_wpr_band_exit=True)
        b.close_if_setup_invalidated("BTCUSD", -5.0, 101.0, 0)
        assert len(c.orders) == 1, "a short above the ceiling was not closed"

    def test_a_short_past_the_FLOOR_is_left_alone(self):
        b, c = _broker_with_position(-1, exit_on_wpr_band_exit=True)
        b.close_if_setup_invalidated("BTCUSD", -95.0, 99.0, 0)
        assert not c.orders, "closed a WINNING short that passed the floor"

    def test_a_nan_reading_closes_nothing(self):
        b, c = _broker_with_position(1, exit_on_wpr_band_exit=True)
        assert b.close_if_setup_invalidated("BTCUSD", float("nan"), 99.0, 0) == []
        assert not c.orders, "acted on a non-finite %R reading"

    def test_no_fill_event_is_invented(self):
        """A live close is a market order, not a fact.

        PaperBroker closes in memory and returns the event. Here the order may
        fill elsewhere, later, or not at all -- poll() reports what the venue
        actually did. Returning a fill here would be inventing one.
        """
        b, _ = _broker_with_position(1, exit_on_wpr_band_exit=True)
        assert b.close_if_setup_invalidated("BTCUSD", -95.0, 99.0, 0) == []


class TestFundingIsTheVenuesJob:
    def test_settle_funding_books_nothing(self):
        b, _ = _broker_with_position(1)
        assert b.settle_funding("BTCUSD", 0, rate_percent=0.01,
                                mark_price=100.0, interval=28800) == []

    def test_mark_funding_charged_consumes_a_generator(self):
        """A silent no-op would leave a generator argument unevaluated.

        The bot passes a generator expression. A body of `pass` never runs it,
        which is a different bug wearing this one's clothes.
        """
        b, _ = _broker_with_position(1)
        seen = []
        b.mark_funding_charged(seen.append(x) or x for x in (1, 2, 3))
        assert seen == [1, 2, 3]


# -- /api/positions on a LIVE broker (500 on every live host until 2026-10-02) --

def test_positions_endpoint_serves_a_live_position():
    """The endpoint reads the PaperPosition surface; LivePosition lacked it and
    every live host answered 500, including the monitor document's probe."""
    from fastapi.testclient import TestClient

    from app.api.app import create_app
    from deltabt.costs import SymbolCosts
    from live.broker import LivePosition

    pos = LivePosition("BEATUSD", 1, 100, 0.0900, 27, stop_price=0.0880,
                       target_price=0.0960, risk_per_unit=0.0020,
                       opened_at=1_790_000_000)
    pos.last_price = 0.0910

    class _Broker:
        def get_positions(self, symbol=None):
            return [pos]

    class _Bot:
        broker = _Broker()
        costs = {"BEATUSD": SymbolCosts(symbol="BEATUSD", tick_size=0.0001,
                                        contract_value=10.0, maker_fee=0.0002,
                                        taker_fee=0.0005, max_leverage=20.0,
                                        position_size_limit=92000,
                                        funding_interval_seconds=14400)}

    r = TestClient(create_app(_Bot())).get("/api/positions")
    assert r.status_code == 200, r.text
    row = r.json()[0]
    assert row["quantity"] == 100 and row["status"] == "OPEN"
    assert row["current_price"] == 0.0910
    # +0.0010 on 100 contracts of 10 units = $1.00; risk 0.002*100*10 = $2.00
    assert row["unrealized_pnl"] == 1.0
    assert row["r"] == 0.5


def test_poll_keeps_what_the_venue_row_does_not_hold():
    """poll() rebuilds the cache from the venue every 5 s; the stop, target,
    risk and open time must survive that, or the endpoint reads zeros."""
    from live.broker import LiveBroker

    class _Client:
        rows = [{"product_id": 27, "product_symbol": "BEATUSD", "size": 100,
                 "entry_price": "0.0900", "liquidation_price": "0.0800"}]

        def get_positions(self):
            return self.rows

    b = LiveBroker.__new__(LiveBroker)
    b.client, b.positions, b._symbol_for = _Client(), {}, {27: "BEATUSD"}
    b._closing_reason, b._entry_cid = {}, {"BEATUSD": "cid1"}
    b._pending = {"cid1": {"stop_price": 0.088, "target_price": 0.096,
                           "risk_per_unit": 0.002, "intent_id": "i1"}}
    b.poll(now=1_790_000_000)
    b._pending.clear()                     # consumed when the open is recorded
    b.positions["BEATUSD"].last_price = 0.091
    b.poll(now=1_790_000_300)
    p = b.positions["BEATUSD"]
    assert (p.stop_price, p.target_price, p.risk_per_unit) == (0.088, 0.096, 0.002)
    assert p.opened_at == 1_790_000_000 and p.last_price == 0.091

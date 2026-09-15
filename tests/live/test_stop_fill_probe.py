"""Stop-fill telemetry: what it records, and that it cannot change an exit.

WHY IT EXISTS

Delta triggers a stop on MARK and fills it at LAST TRADED. On an illiquid
instrument mark lags LTP in a fast move, so the fill lands past the stop. The
live `atr` arm's six BEATUSD stops filled a mean 0.247R past their trigger --
1.48R, about a quarter of everything that arm has made -- while AKEUSD's filled
at the stop.

A stop-LIMIT bounds that. live/orders.py caps it at 1.5R and says "needs tick
telemetry before it is taken. Do not tighten this without that data." On the
backtest a 1.2R cap is worth +20.8R over 363 trades -- but only if the limit
FILLS, and a limit that does not fill leaves the position open while price
keeps going. The non-fill rate is the number nobody has. This measures it.

THE FIRST PROPERTY IS THAT IT CHANGES NOTHING. A measurement that can alter
what it measures is not one, and this one sits directly in the path that
decides exits.
"""
from __future__ import annotations

from app.execution.paper_broker import ExitReason, PaperBroker
from app.market_data.normalize import Tick
from deltabt.costs import SymbolCosts

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)
COSTS = {"BTCUSD": BTC}
US = 1_000_000
T0 = 1786560300

from tests.live.test_paper_execution import intent  # noqa: E402  (shared fixture)


def tick(ltp, mark=None, ts=T0):
    return Tick("BTCUSD", ts * US, ltp, mark if mark is not None else ltp)


def opened(entry=63000.0, stop=62500.0, side=1):
    """A broker holding one open position, armed."""
    b = PaperBroker(COSTS, starting_equity=10_000.0, slippage_bps=2.0)
    b.submit_order(intent(side=side, entry=entry, stop=stop,
                          target=entry + side * 1000.0))
    b.process_market_event(tick(entry, ts=T0))          # fills the entry
    b.process_market_event(tick(entry, ts=T0 + 1))      # arms stop/target
    assert b.positions, "fixture did not open a position"
    return b


def run_window(b, ltp, mark, start, seconds=61, step=10):
    for k in range(0, seconds + step, step):
        b.process_market_event(tick(ltp, mark, ts=start + k))


class TestItCannotChangeAnExit:
    def test_the_stop_still_closes_at_the_same_price_and_reason(self):
        b = opened()
        # Mark reaches the stop; LTP is already well past it.
        b.process_market_event(tick(62000.0, 62500.0, ts=T0 + 2))
        closed = [p for p in b.positions.values() if p.status != "OPEN"]
        assert closed, "the stop did not close the position"
        # `==`, not `is`: exit_reason is stored as the enum's VALUE.
        assert closed[0].exit_reason == ExitReason.STOP_LOSS

    def test_probes_do_not_enter_the_broker_event_stream(self):
        """They must not reach the trade record, where a reader would take a
        measurement for an exit."""
        b = opened()
        b.process_market_event(tick(62000.0, 62500.0, ts=T0 + 2))
        kinds = {e.kind for e in b.events}
        assert "STOP_FILL_PROBE" not in kinds
        assert not any("PROBE" in k for k in kinds), kinds


class TestWhatItRecords:
    def test_it_records_the_overshoot_between_trigger_and_fill(self):
        b = opened(entry=63000.0, stop=62500.0)     # risk 500
        b.process_market_event(tick(62000.0, 62500.0, ts=T0 + 2))
        probe = b._stop_probes_open["BTCUSD"]
        assert probe["trigger_mark"] == 62500.0
        assert probe["stop_price"] == 62500.0
        # Filled ~1000 below a 500-wide stop: about 1R past it, positive.
        assert probe["overshoot_r"] > 0.9, probe["overshoot_r"]

    def test_a_fill_at_the_stop_records_no_overshoot(self):
        b = opened(entry=63000.0, stop=62500.0)
        b.process_market_event(tick(62500.0, 62500.0, ts=T0 + 2))
        probe = b._stop_probes_open["BTCUSD"]
        assert abs(probe["overshoot_r"]) < 0.05, probe["overshoot_r"]

    def test_a_cap_is_reachable_only_if_ltp_actually_gets_there(self):
        b = opened(entry=63000.0, stop=62500.0)
        b.process_market_event(tick(62450.0, 62500.0, ts=T0 + 2))
        probe = b._stop_probes_open["BTCUSD"]
        # DERIVED FROM THE POSITION, not assumed. The entry filled with
        # slippage, so entry_price is not 63000 and risk is not 500 -- writing
        # the levels by hand made this test wrong, not the code.
        entry, risk = probe["entry_price"], probe["risk_per_unit"]
        # Park LTP just past the 1.1R level and nowhere near 1.2R.
        parked = entry - 1.12 * risk
        run_window(b, parked, parked, T0 + 2)
        assert b.stop_probes, "the probe never closed"
        caps = b.stop_probes[0]["cap_reachable"]
        assert caps["1.1"] is True, "a limit at -1.1R was traded through, unseen"
        assert caps["1.2"] is False, "a limit at -1.2R was never reached"
        assert caps["1.5"] is False


class TestTheWindow:
    def test_the_probe_closes_after_its_window_and_is_drainable(self):
        b = opened()
        b.process_market_event(tick(62000.0, 62500.0, ts=T0 + 2))
        assert not b.stop_probes, "closed before the window elapsed"
        run_window(b, 62000.0, 62000.0, T0 + 2)
        drained = b.drain_stop_probes()
        assert len(drained) == 1
        assert drained[0]["ticks_observed"] > 1
        assert b.drain_stop_probes() == [], "drain did not clear the buffer"


class TestTheLiveBrokerHasNone:
    def test_the_runtime_tolerates_a_broker_without_probes(self):
        """LiveBroker does not collect these -- the venue decides the fill
        there. A missing method must not take down the tick path, which is
        exactly the failure that cost the first live deploy."""
        from live.broker import LiveBroker
        b = LiveBroker.__new__(LiveBroker)
        LiveBroker.__init__(b, client=None, product_ids={"BTCUSD": 84},
                            experiment_id="x")
        assert getattr(b, "drain_stop_probes", None) is None

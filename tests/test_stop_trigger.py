"""Which price the stop watches, and that an unknown value fails loudly."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.config import StrategyParams
from deltabt.costs import SymbolCosts
from deltabt.engine import LONG, run_backtest
from deltabt.strategy import Signals

COSTS = SymbolCosts(symbol="T", tick_size=0.0001, contract_value=1,
                    maker_fee=0.0, taker_fee=0.0, max_leverage=50,
                    position_size_limit=1e9, funding_interval_seconds=28800,
                    slippage_bps=0.0)


def _frame(bars):
    t = np.arange(len(bars), dtype="int64") * 60
    o, h, l, c = map(list, zip(*bars))
    return pd.DataFrame({"time": t, "open": o, "high": h, "low": l,
                         "close": c, "volume": [1.0] * len(bars)})


def _signals(n, stop=1.0):
    z = np.zeros(n, dtype=bool)
    le = z.copy()
    le[1] = True
    ones = np.ones(n)
    return Signals(long_entry=le, short_entry=z.copy(),
                   stop_long=np.full(n, 100.0 - stop),
                   stop_short=np.full(n, 100.0 + stop),
                   supertrend=np.full(n, 100.0), direction=ones,
                   atr=ones, wpr=np.full(n, -50.0), adx_1m=ones * 30,
                   adx_5m=ones * 30, bull_1m=np.ones(n, dtype=bool),
                   bear_1m=np.ones(n, dtype=bool), long_base=le,
                   short_base=z.copy(), warmup=0)


def _run(ltp_bars, mark_bars, trigger):
    ltp, mark = _frame(ltp_bars), _frame(mark_bars)
    p = StrategyParams(base_minutes=1, confirm_minutes=5, max_hold_bars=10_000,
                       exit_on_trend_flip=False, reward_risk=3.0,
                       stop_trigger=trigger)
    return run_backtest(ltp, mark, pd.DataFrame(), _signals(len(ltp_bars)),
                        p, COSTS, initial_capital=100_000.0)


#: LTP dips through the stop on bar 2; MARK lags and does not. This is the
#: divergence the whole question is about -- an illiquid book printing a low
#: the mark price smooths away.
LTP_BARS = [(100, 100, 100, 100), (100, 100, 100, 100),
            (100, 100, 98.5, 99.5), (99.5, 99.5, 99.5, 99.5)]
MARK_BARS = [(100, 100, 100, 100), (100, 100, 100, 100),
             (100, 100, 99.5, 99.7), (99.7, 99.7, 99.7, 99.7)]


def test_mark_is_the_default_and_does_not_fire_on_an_ltp_only_dip():
    assert StrategyParams().stop_trigger == "mark"
    res = _run(LTP_BARS, MARK_BARS, "mark")
    assert not any(t.exit_reason == "stop" for t in res.trades), (
        "a mark-triggered stop fired on a dip only LTP saw")


def test_ltp_fires_on_the_same_dip():
    res = _run(LTP_BARS, MARK_BARS, "ltp")
    assert any(t.exit_reason == "stop" for t in res.trades), (
        "an LTP-triggered stop ignored a dip LTP clearly made")


def test_the_two_settings_are_not_the_same_run():
    """If they agreed here the tests above would prove nothing."""
    a = _run(LTP_BARS, MARK_BARS, "mark")
    b = _run(LTP_BARS, MARK_BARS, "ltp")
    assert [t.exit_reason for t in a.trades] != [t.exit_reason for t in b.trades]


def test_an_unknown_trigger_is_refused():
    """A typo must not silently fall back to the default and look like a run."""
    with pytest.raises(ValueError, match="stop_trigger"):
        _run(LTP_BARS, MARK_BARS, "market")

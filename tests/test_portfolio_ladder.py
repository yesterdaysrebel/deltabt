"""The stop ladder in ``run_portfolio``, on hand-built price paths.

Four things are pinned, because each is a way the backtest could disagree
with the paper broker it is meant to reproduce:

* a rung that arms mid-bar can stop the trade out INSIDE the same 5m bar
  (the paper broker works on ticks; bar-level evaluation cannot do this);
* a minute that hits the stop does not also promote -- exits are tested
  before rungs, as in ``PaperBroker`` ("THE LADDER MOVES THE STOP AFTER THE
  TESTS ABOVE, NEVER BEFORE");
* a trade that runs to target is still booked as a target, with the
  promotion recorded;
* an empty ladder reproduces the unladdered run field for field.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from deltabt.config import StrategyParams
from deltabt.costs import SymbolCosts
from deltabt.portfolio import Book, RiskGates, run_portfolio
from deltabt.strategy import Signals, resample_ohlcv

T0 = 1_700_000_400  # a 5m boundary
RUNGS = ((0.5, 0.0), (1.0, 0.5), (1.5, 1.0), (2.0, 1.5))


def _costs() -> SymbolCosts:
    return SymbolCosts(symbol="TEST", tick_size=0.01, contract_value=1.0,
                       maker_fee=0.0002, taker_fee=0.0005, max_leverage=50.0,
                       position_size_limit=1e9, funding_interval_seconds=28_800,
                       slippage_bps=0.0)


def _minutes(closes: list[float], spread: float = 0.05) -> pd.DataFrame:
    c = np.asarray(closes, dtype="float64")
    return pd.DataFrame({
        "time": T0 + 60 * np.arange(len(c), dtype="int64"),
        "open": c, "high": c + spread, "low": c - spread, "close": c,
        "volume": np.ones(len(c)),
    })


def _book(closes: list[float], *, entry_bar: int = 1, stop_distance: float = 1.0) -> Book:
    ltp = _minutes(closes)
    bars = resample_ohlcv(ltp, 5).reset_index(drop=True)
    n = len(bars)
    z = np.zeros(n, dtype=bool)
    nan = np.full(n, np.nan)
    long_entry = z.copy()
    long_entry[entry_bar] = True
    stop_long = nan.copy()
    stop_long[entry_bar] = float(bars["close"].iloc[entry_bar]) - stop_distance
    sig = Signals(long_entry=long_entry, short_entry=z.copy(), stop_long=stop_long,
                  stop_short=nan.copy(), supertrend=nan.copy(), direction=np.zeros(n),
                  atr=np.full(n, 0.25), wpr=nan.copy(), adx_1m=nan.copy(),
                  adx_5m=nan.copy(), bull_1m=z.copy(), bear_1m=z.copy(),
                  long_base=z.copy(), short_base=z.copy(), warmup=0)
    return Book(symbol="TEST", bars=bars, signals=sig, costs=_costs(), mark=bars,
                tradable=None, fill_ltp=ltp, fill_mark=ltp)


def _params(rungs=()) -> StrategyParams:
    return StrategyParams(base_minutes=5, confirm_minutes=6, max_hold_bars=10_000,
                          exit_on_trend_flip=False, reward_risk=3.0,
                          stop_fill="ltp_close", ladder_rungs=tuple(rungs))


def _run(closes, rungs=()):
    res = run_portfolio({"TEST": _book(closes)}, _params(rungs), RiskGates.off(),
                        initial_capital=10_000.0)
    return pd.DataFrame([dataclasses.asdict(t) for t in res.trades])


# Bars 0-1 flat at 100 (entry fires on bar 1's close, px 100, stop 99, 3R target 103).
FLAT = [100.0] * 10


def test_rung_rescues_a_loser_inside_the_next_bar():
    # bar 2 reaches +0.7R (arms breakeven), bar 3 falls through the original stop
    path = FLAT + [100.2, 100.4, 100.6, 100.65, 100.5] + [99.9, 99.6, 99.2, 98.9, 98.5]
    base = _run(path)
    lad = _run(path, RUNGS)
    assert len(base) == 1 and len(lad) == 1
    assert base.exit_reason[0] == "stop" and not base.stop_promoted[0]
    assert base.r_multiple[0] < -1.0                       # 99.0 stop filled at 98.9
    assert lad.exit_reason[0] == "stop" and lad.stop_promoted[0]
    assert lad.stop_price[0] == 100.0                      # breakeven rung
    assert lad.exit_price[0] == 99.9                       # first minute whose low <= 100
    assert -0.4 < lad.r_multiple[0] < 0.0                  # breakeven minus the round trip


def test_higher_rung_locks_half_an_r():
    # bar 2 reaches +1.25R (arms the +0.5R rung), bar 3 gives it back
    path = FLAT + [100.3, 100.7, 101.0, 101.2, 101.1] + [100.9, 100.6, 100.3, 100.0, 99.5]
    lad = _run(path, RUNGS)
    assert lad.exit_reason[0] == "stop" and lad.stop_promoted[0]
    assert lad.stop_price[0] == 100.5
    assert lad.exit_price[0] == 100.3                      # minute 17: low 100.25 <= 100.5
    assert 0.0 < lad.r_multiple[0] < 0.5


def test_a_minute_that_stops_out_does_not_promote():
    # one minute spikes to +0.7R and through the stop; the stop wins and no rung arms
    path = FLAT + [100.0, 100.0, 98.8, 98.7, 98.6] + [98.5] * 5
    lad = _run(path, RUNGS)
    assert lad.exit_reason[0] == "stop" and not lad.stop_promoted[0]
    assert lad.stop_price[0] == 99.0
    assert lad.exit_price[0] == 98.8


def test_runner_still_books_the_target_and_records_the_promotion():
    path = FLAT + [100.6, 101.2, 102.0, 102.8, 103.3] + [103.0] * 5
    base = _run(path)
    lad = _run(path, RUNGS)
    assert base.exit_reason[0] == "target" and lad.exit_reason[0] == "target"
    assert lad.stop_promoted[0] and not base.stop_promoted[0]
    assert abs(lad.r_multiple[0] - base.r_multiple[0]) < 1e-9


def test_empty_ladder_is_the_unladdered_run():
    path = FLAT + [100.2, 100.4, 100.6, 100.65, 100.5] + [99.9, 99.6, 99.2, 98.9, 98.5]
    a = _run(path)
    b = _run(path, ())
    pd.testing.assert_frame_equal(a, b)


# --- the trail (2026-10-02): same walk, deltabt/exits.py decides the stop ----

def _run_trail(closes):
    from dataclasses import replace
    params = replace(_params(), trail_after_r=0.5, trail_r=0.5)
    res = run_portfolio({"TEST": _book(closes)}, params, RiskGates.off(),
                        initial_capital=10_000.0)
    return pd.DataFrame([dataclasses.asdict(t) for t in res.trades])


def test_trail_locks_more_than_the_ladder_between_rungs():
    # entry 100, R = 1: up to +1.25R (high 101.25 with the 0.05 spread), then down
    path = FLAT + [100.3, 100.7, 101.0, 101.2, 101.1] + [100.9, 100.6, 100.3, 100.0, 99.5]
    ladder = _run(path, RUNGS)
    trail = _run_trail(path)
    assert ladder.stop_price[0] == 100.5                 # the +0.5R rung
    assert trail.stop_price[0] == pytest.approx(100.75)  # high 101.25 - 0.5R
    assert trail.exit_reason[0] == "stop" and trail.stop_promoted[0]
    assert trail.r_multiple[0] > ladder.r_multiple[0]


def test_trail_does_nothing_before_plus_half_r():
    path = FLAT + [100.1, 100.2, 100.3, 100.2, 100.1] + [99.9, 99.6, 99.2, 98.9, 98.5]
    base, trail = _run(path), _run_trail(path)
    assert not trail.stop_promoted[0]
    assert trail.r_multiple[0] == pytest.approx(base.r_multiple[0])

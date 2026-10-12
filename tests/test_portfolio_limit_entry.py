"""StrategyParams.entry_limit_atr: a resting limit entry instead of a market one.

One long signal on bar 1 (close 100, ATR 0.25, stop 99). The 1m fixture gives
every minute high = close + 0.05 and low = close - 0.05, so the path after the
signal decides whether a limit at 100 (k = 0) or 99.875 (k = 0.5) is traded
THROUGH.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from deltabt.config import StrategyParams
from deltabt.costs import SymbolCosts
from deltabt.portfolio import Book, RiskGates, run_portfolio
from deltabt.strategy import Signals, resample_ohlcv

from tests.test_portfolio_ladder import T0, _costs, _minutes


def _book(closes, *, maker_entry=False) -> Book:
    ltp = _minutes(closes)
    bars = resample_ohlcv(ltp, 5).reset_index(drop=True)
    n = len(bars); z = np.zeros(n, dtype=bool); nan = np.full(n, np.nan)
    long_entry = z.copy(); long_entry[1] = True
    stop_long = nan.copy(); stop_long[1] = float(bars["close"].iloc[1]) - 1.0
    sig = Signals(long_entry=long_entry, short_entry=z.copy(), stop_long=stop_long,
                  stop_short=nan.copy(), supertrend=nan.copy(), direction=np.zeros(n),
                  atr=np.full(n, 0.25), wpr=nan.copy(), adx_1m=nan.copy(),
                  adx_5m=nan.copy(), bull_1m=z.copy(), bear_1m=z.copy(),
                  long_base=z.copy(), short_base=z.copy(), warmup=0)
    costs = dataclasses.replace(_costs(), maker_entry=maker_entry)
    return Book(symbol="TEST", bars=bars, signals=sig, costs=costs, mark=bars,
                tradable=None, fill_ltp=ltp, fill_mark=ltp)


def _run(closes, *, k=None, wait=3, maker_entry=False):
    params = StrategyParams(base_minutes=5, confirm_minutes=6, max_hold_bars=4, cooldown_bars=0,
                            exit_on_trend_flip=False, reward_risk=3.0, stop_fill="ltp_close",
                            entry_limit_atr=k, entry_limit_wait_bars=wait)
    res = run_portfolio({"TEST": _book(closes, maker_entry=maker_entry)}, params, RiskGates.off(),
                        initial_capital=10_000.0)
    return pd.DataFrame([dataclasses.asdict(t) for t in res.trades]), res.rejects


# Bars 0-1 flat at 100 (signal on bar 1's close). Then:
DIPS = [100.0] * 10 + [99.9] * 5 + [100.0] * 25        # bar 2 trades down to 99.85: through 100
RUNS_AWAY = [100.0] * 10 + [101.0] * 30                # never below 100.95
TOUCHES = [100.0] * 10 + [100.05] * 30                 # low exactly 100.00: a touch, not a fill


def test_market_entry_is_unchanged_when_the_limit_is_off():
    trades, rejects = _run(DIPS, k=None)
    assert len(trades) == 1 and trades.entry_price.iloc[0] == 100.0
    assert rejects["limit_unfilled"] == 0


def test_a_limit_at_the_close_fills_when_price_trades_through_it():
    trades, rejects = _run(DIPS, k=0.0)
    assert len(trades) == 1
    assert trades.entry_price.iloc[0] == 100.0
    assert trades.entry_time.iloc[0] == T0 + 2 * 300          # filled inside bar 2
    assert trades.stop_price.iloc[0] == 99.0
    assert rejects["limit_unfilled"] == 0


def test_an_unfilled_limit_is_cancelled_and_no_trade_is_taken():
    trades, rejects = _run(RUNS_AWAY, k=0.0, wait=3)
    assert trades.empty
    assert rejects["limit_unfilled"] == 1


def test_a_touch_is_not_a_fill():
    trades, rejects = _run(TOUCHES, k=0.0)
    assert trades.empty and rejects["limit_unfilled"] == 1


def test_an_offset_limit_waits_for_the_deeper_price():
    # k = 0.5 * ATR 0.25 -> 99.875, rounded DOWN to the 0.01 tick for a buy: 99.87.
    # DIPS reaches 99.85 in bar 2: filled there.
    trades, _ = _run(DIPS, k=0.5)
    assert len(trades) == 1 and trades.entry_price.iloc[0] == 99.87
    # The shallower path (low 99.95) never gets there.
    trades, rejects = _run([100.0] * 40, k=0.5)
    assert trades.empty and rejects["limit_unfilled"] == 1


def test_the_maker_entry_pays_less_than_the_market_one():
    market, _ = _run(DIPS, k=None)
    limit, _ = _run(DIPS, k=0.0, maker_entry=True)
    assert limit.fees.iloc[0] < market.fees.iloc[0]

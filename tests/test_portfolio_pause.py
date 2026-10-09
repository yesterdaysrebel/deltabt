"""RiskGates.pause_seconds: a per-symbol pause after a selected closed trade.

Three entry signals on one symbol: bar 1 (stopped out on bar 2, a loss),
bar 4 (inside a one-hour pause), bar 20 (after it). Every trade time-exits
after three bars once it is open, so each one closes and frees the slot.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd

from deltabt.config import StrategyParams
from deltabt.portfolio import Book, RiskGates, run_portfolio
from deltabt.strategy import Signals, resample_ohlcv

from tests.test_portfolio_ladder import T0, _costs, _minutes

ENTRIES = (1, 4, 20)
# 100 for bars 0-1, then 98.5 for good: bar 2 stops the first long (stop 99).
CLOSES = [100.0] * 10 + [98.5] * 130


def _book() -> Book:
    ltp = _minutes(CLOSES)
    bars = resample_ohlcv(ltp, 5).reset_index(drop=True)
    n = len(bars); z = np.zeros(n, dtype=bool); nan = np.full(n, np.nan)
    long_entry = z.copy(); stop_long = nan.copy()
    for b in ENTRIES:
        long_entry[b] = True
        stop_long[b] = float(bars["close"].iloc[b]) - 1.0
    sig = Signals(long_entry=long_entry, short_entry=z.copy(), stop_long=stop_long,
                  stop_short=nan.copy(), supertrend=nan.copy(), direction=np.zeros(n),
                  atr=np.full(n, 0.25), wpr=nan.copy(), adx_1m=nan.copy(),
                  adx_5m=nan.copy(), bull_1m=z.copy(), bear_1m=z.copy(),
                  long_base=z.copy(), short_base=z.copy(), warmup=0)
    return Book(symbol="TEST", bars=bars, signals=sig, costs=_costs(), mark=bars,
                tradable=None, fill_ltp=ltp, fill_mark=ltp)


def _run(gates: RiskGates):
    params = StrategyParams(base_minutes=5, confirm_minutes=6, max_hold_bars=3, cooldown_bars=0,
                            exit_on_trend_flip=False, reward_risk=3.0, stop_fill="ltp_close")
    res = run_portfolio({"TEST": _book()}, params, gates, initial_capital=10_000.0)
    return pd.DataFrame([dataclasses.asdict(t) for t in res.trades]), res.rejects


def _entry_bars(trades: pd.DataFrame) -> list[int]:
    return [int((t - T0) // 300) for t in trades.entry_time]


def test_default_gates_take_every_signal():
    trades, rejects = _run(RiskGates.off())
    assert _entry_bars(trades) == [1, 4, 20]
    assert trades.r_multiple.iloc[0] < 0
    assert rejects["pause"] == 0


def test_a_pause_without_a_selector_changes_nothing():
    trades, _ = _run(dataclasses.replace(RiskGates.off(), pause_seconds=3600))
    assert _entry_bars(trades) == [1, 4, 20]


def test_pause_after_a_loss_skips_the_symbol_inside_the_window_only():
    gates = dataclasses.replace(RiskGates.off(), pause_seconds=3600, pause_after=lambda t: t.r_multiple < 0)
    trades, rejects = _run(gates)
    assert _entry_bars(trades) == [1, 20]
    assert rejects["pause"] == 1


def test_pause_keyed_to_wins_ignores_a_loss():
    gates = dataclasses.replace(RiskGates.off(), pause_seconds=3600, pause_after=lambda t: t.r_multiple > 0)
    trades, _ = _run(gates)
    assert _entry_bars(trades) == [1, 4, 20]

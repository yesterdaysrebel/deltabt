"""The stop-injection contract. Guards the silent short-deletion failure."""

from __future__ import annotations

import numpy as np
import pytest

from deltabt.costs import SymbolCosts
from deltabt.research.hwpr import _simulate
from deltabt.research.stops import injection_arrays

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)

SIGNAL_BAR = 10


def make_series(n=300):
    """A steady decline, so a short entered early reaches 2R and never stops out."""
    t = np.arange(n, dtype="float64")
    c = 100.0 - t * 0.05
    o = np.concatenate(([100.0], c[:-1]))
    h = np.maximum(o, c) + 0.02
    lo = np.minimum(o, c) - 0.02
    return o, h, lo, c


def run(st1, leg_lo, leg_hi, long_sig, short_sig):
    o, h, lo, c = make_series()
    n = o.size
    return _simulate(
        long_sig, short_sig, o, h, lo, c, h, lo, st1, leg_lo, leg_hi,
        np.ones(n, dtype=np.bool_), 2.0, BTC.effective_taker, BTC.slippage_rate,
        BTC.tick_size, BTC.contract_value, 3.0, 0.005, 10_000.0, False, 0.05)


def short_only():
    n = make_series()[0].size
    long_sig = np.zeros(n, dtype=np.bool_)
    short_sig = np.zeros(n, dtype=np.bool_)
    short_sig[SIGNAL_BAR] = True
    stop_long = np.full(n, 90.0)
    stop_short = np.full(n, 100.5)
    return long_sig, short_sig, stop_long, stop_short


def test_injected_stops_produce_a_short_trade():
    long_sig, short_sig, stop_long, stop_short = short_only()
    st1, leg_lo, leg_hi = injection_arrays(long_sig, short_sig, stop_long, stop_short)
    arr, _, _ = run(st1, leg_lo, leg_hi, long_sig, short_sig)
    assert arr.shape[0] == 1
    assert arr[0, 0] == -1
    assert arr[0, 6] == 100.5, "the short's stop is stop_short, not stop_long"


def test_unfilled_long_stop_is_dropped_silently_by_the_simulator():
    """The exact failure ``injection_arrays`` exists to make impossible."""
    long_sig, short_sig, _, stop_short = short_only()
    n = short_sig.size
    unfilled = np.full(n, np.nan)
    arr, _, _ = run(unfilled, unfilled, stop_short, long_sig, short_sig)
    assert arr.shape[0] == 0, "max(nan, stop) is nan, and the trade vanishes"


def test_unfilled_long_stop_raises_instead():
    long_sig, short_sig, stop_long, stop_short = short_only()
    stop_long = stop_long.copy()
    stop_long[SIGNAL_BAR] = np.nan
    with pytest.raises(ValueError, match=r"stop_long is not finite.*index 10"):
        injection_arrays(long_sig, short_sig, stop_long, stop_short)


def test_unfilled_short_stop_raises():
    long_sig, short_sig, stop_long, stop_short = short_only()
    stop_short = stop_short.copy()
    stop_short[SIGNAL_BAR] = np.nan
    with pytest.raises(ValueError, match=r"stop_short is not finite"):
        injection_arrays(long_sig, short_sig, stop_long, stop_short)


def test_non_finite_away_from_a_signal_bar_is_allowed():
    long_sig, short_sig, stop_long, stop_short = short_only()
    stop_long = stop_long.copy()
    stop_long[SIGNAL_BAR + 1] = np.nan
    st1, _, _ = injection_arrays(long_sig, short_sig, stop_long, stop_short)
    assert np.isnan(st1[SIGNAL_BAR + 1])


def test_crossed_stops_raise():
    long_sig, short_sig, stop_long, stop_short = short_only()
    stop_long = stop_long.copy()
    stop_long[SIGNAL_BAR] = 101.0
    with pytest.raises(ValueError, match="strictly below"):
        injection_arrays(long_sig, short_sig, stop_long, stop_short)


def test_non_boolean_signal_raises():
    long_sig, short_sig, stop_long, stop_short = short_only()
    with pytest.raises(TypeError, match="long_sig must be a boolean array"):
        injection_arrays(long_sig.astype("int64"), short_sig, stop_long, stop_short)


def test_non_float_stop_raises():
    long_sig, short_sig, stop_long, stop_short = short_only()
    with pytest.raises(TypeError, match="stop_long must be a float array"):
        injection_arrays(long_sig, short_sig, stop_long.astype("int64"), stop_short)


def test_length_mismatch_raises():
    long_sig, short_sig, stop_long, stop_short = short_only()
    with pytest.raises(ValueError, match="stop_short has length"):
        injection_arrays(long_sig, short_sig, stop_long, stop_short[:-1])

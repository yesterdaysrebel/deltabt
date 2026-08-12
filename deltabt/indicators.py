"""Pine v6-exact indicator reimplementations.

Every function here mirrors TradingView semantics rather than a convenient
library equivalent, because the whole point of the parity mode is to reproduce
what the author saw on their chart. The details that actually cause divergence:

* ``ta.rma`` seeds with a simple mean of the first ``n`` values and then runs
  ``alpha = 1/n``. Using a plain EWM with ``adjust=True`` does not match.
* ``ta.wpr`` uses the rolling max of **high** and min of **low**, inclusive of
  the current bar -- not the rolling max/min of close.
* ``ta.supertrend`` carries its bands forward conditionally and seeds its
  direction on the first valid ATR bar. The carry rule is what makes
  ``direction < 0`` imply ``close > supertrend``.
* Pine's ``na`` propagates; NumPy's ``nan`` mostly does too, but ``0/0`` needs
  forcing to NaN rather than inf.
"""

from __future__ import annotations

import numpy as np
from numba import njit

_F = np.float64


# --- moving averages --------------------------------------------------------


@njit(cache=True)
def _rma(values: np.ndarray, length: int) -> np.ndarray:
    """Wilder's smoothing, seeded as Pine's ``ta.rma`` seeds it."""
    n = values.size
    out = np.full(n, np.nan, dtype=_F)
    if length < 1 or n < length:
        return out

    alpha = 1.0 / length

    # Seed with the simple mean of the first `length` finite values.
    seed_sum = 0.0
    seen = 0
    seed_idx = -1
    for i in range(n):
        v = values[i]
        if np.isnan(v):
            continue
        seed_sum += v
        seen += 1
        if seen == length:
            seed_idx = i
            break
    if seed_idx < 0:
        return out

    prev = seed_sum / length
    out[seed_idx] = prev
    for i in range(seed_idx + 1, n):
        v = values[i]
        if np.isnan(v):
            out[i] = prev
            continue
        prev = alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def rma(values: np.ndarray, length: int) -> np.ndarray:
    return _rma(np.ascontiguousarray(values, dtype=_F), int(length))


# --- true range / ATR -------------------------------------------------------


@njit(cache=True)
def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    n = high.size
    out = np.empty(n, dtype=_F)
    if n == 0:
        return out
    # Pine's first bar has no previous close, so TR is simply the bar range.
    out[0] = high[0] - low[0]
    for i in range(1, n):
        pc = close[i - 1]
        a = high[i] - low[i]
        b = abs(high[i] - pc)
        c = abs(low[i] - pc)
        m = a
        if b > m:
            m = b
        if c > m:
            m = c
        out[i] = m
    return out


def true_range(high, low, close) -> np.ndarray:
    return _true_range(
        np.ascontiguousarray(high, dtype=_F),
        np.ascontiguousarray(low, dtype=_F),
        np.ascontiguousarray(close, dtype=_F),
    )


def atr(high, low, close, length: int) -> np.ndarray:
    """``ta.atr`` -- RMA of true range."""
    return rma(true_range(high, low, close), length)


# --- Williams %R ------------------------------------------------------------


@njit(cache=True)
def _wpr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int) -> np.ndarray:
    """``ta.wpr``: 100 * (close - highest(high, n)) / (highest - lowest).

    Returns values in [-100, 0]. NaN for the first ``length - 1`` bars, and NaN
    when the range is degenerate -- Pine yields ``na`` for 0/0, whereas naive
    NumPy would give inf or a warning. Flat halted segments hit this.
    """
    n = close.size
    out = np.full(n, np.nan, dtype=_F)
    if length < 1 or n < length:
        return out

    for i in range(length - 1, n):
        hh = high[i]
        ll = low[i]
        bad = False
        for j in range(i - length + 1, i + 1):
            hv = high[j]
            lv = low[j]
            if np.isnan(hv) or np.isnan(lv):
                bad = True
                break
            if hv > hh:
                hh = hv
            if lv < ll:
                ll = lv
        if bad:
            continue
        rng = hh - ll
        if rng <= 0.0:
            continue
        out[i] = 100.0 * (close[i] - hh) / rng
    return out


def wpr(high, low, close, length: int) -> np.ndarray:
    return _wpr(
        np.ascontiguousarray(high, dtype=_F),
        np.ascontiguousarray(low, dtype=_F),
        np.ascontiguousarray(close, dtype=_F),
        int(length),
    )


# --- DMI / ADX --------------------------------------------------------------


@njit(cache=True)
def _dmi(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    di_length: int,
    adx_smoothing: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``ta.dmi(diLength, adxSmoothing) -> (+DI, -DI, ADX)``."""
    n = high.size
    plus_dm = np.zeros(n, dtype=_F)
    minus_dm = np.zeros(n, dtype=_F)

    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0.0) else 0.0
        minus_dm[i] = down if (down > up and down > 0.0) else 0.0

    tr = _true_range(high, low, close)
    tr_s = _rma(tr, di_length)
    plus_s = _rma(plus_dm, di_length)
    minus_s = _rma(minus_dm, di_length)

    plus_di = np.full(n, np.nan, dtype=_F)
    minus_di = np.full(n, np.nan, dtype=_F)
    dx = np.full(n, np.nan, dtype=_F)

    for i in range(n):
        t = tr_s[i]
        if np.isnan(t) or t == 0.0:
            continue
        p = 100.0 * plus_s[i] / t
        m = 100.0 * minus_s[i] / t
        plus_di[i] = p
        minus_di[i] = m
        denom = p + m
        if denom > 0.0:
            dx[i] = 100.0 * abs(p - m) / denom
        else:
            dx[i] = 0.0

    adx = _rma(dx, adx_smoothing)
    return plus_di, minus_di, adx


def dmi(high, low, close, di_length: int, adx_smoothing: int):
    """Note the argument order matches Pine: DI length first, ADX smoothing second."""
    return _dmi(
        np.ascontiguousarray(high, dtype=_F),
        np.ascontiguousarray(low, dtype=_F),
        np.ascontiguousarray(close, dtype=_F),
        int(di_length),
        int(adx_smoothing),
    )


# --- Supertrend -------------------------------------------------------------


@njit(cache=True)
def _supertrend(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    factor: float,
    atr_period: int,
) -> tuple[np.ndarray, np.ndarray]:
    """``ta.supertrend(factor, atrPeriod) -> (supertrend, direction)``.

    Direction is -1 in an uptrend and +1 in a downtrend -- the opposite of what
    most people assume, and the single most commonly inverted line in
    Supertrend ports.
    """
    n = close.size
    st = np.full(n, np.nan, dtype=_F)
    direction = np.full(n, np.nan, dtype=_F)
    if n == 0:
        return st, direction

    tr = _true_range(high, low, close)
    atr_v = _rma(tr, atr_period)

    prev_upper = np.nan
    prev_lower = np.nan
    prev_st = np.nan
    prev_dir = 0.0
    started = False

    for i in range(n):
        a = atr_v[i]
        if np.isnan(a):
            continue

        mid = (high[i] + low[i]) / 2.0
        upper = mid + factor * a
        lower = mid - factor * a

        if not started:
            # Pine seeds direction to 1 (downtrend) on the first valid bar.
            prev_upper = upper
            prev_lower = lower
            prev_dir = 1.0
            prev_st = upper
            st[i] = prev_st
            direction[i] = prev_dir
            started = True
            continue

        pc = close[i - 1]
        # Bands ratchet: they only widen away from price, never back toward it,
        # unless price has broken the previous band.
        if lower > prev_lower or pc < prev_lower:
            pass
        else:
            lower = prev_lower
        if upper < prev_upper or pc > prev_upper:
            pass
        else:
            upper = prev_upper

        if prev_dir == 1.0:
            # Was in a downtrend, tracking the upper band.
            if close[i] > prev_upper:
                d = -1.0
            else:
                d = 1.0
        else:
            if close[i] < prev_lower:
                d = 1.0
            else:
                d = -1.0

        value = lower if d == -1.0 else upper
        st[i] = value
        direction[i] = d

        prev_upper = upper
        prev_lower = lower
        prev_st = value
        prev_dir = d

    return st, direction


def supertrend(high, low, close, factor: float, atr_period: int):
    """Note the argument order matches Pine: factor first, ATR period second."""
    return _supertrend(
        np.ascontiguousarray(high, dtype=_F),
        np.ascontiguousarray(low, dtype=_F),
        np.ascontiguousarray(close, dtype=_F),
        float(factor),
        int(atr_period),
    )

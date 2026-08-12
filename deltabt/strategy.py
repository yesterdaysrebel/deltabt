"""Signal generation for both the parity and corrected variants.

Signals are computed as arrays up front; the engine consumes them bar by bar.
The one exception is the WPR latch when ``clear_in_position`` is set, because
that reset depends on position state that is itself downstream of the fires.
The engine drives that case incrementally via ``wpr_latch.step_state``.

Higher-timeframe values use the confirmed-value idiom: the 5m series is
resampled from 1m, shifted by one 5m bar, and then broadcast onto the 1m grid.
This yields the value of the last *closed* 5m bar, which is identical on
historical and realtime bars. The original script's ``request.security`` call
does not leak future data, but it does evaluate a developing 5m bar in
realtime, so live takes trades the backtest never saw -- concentrated on
provisional Supertrend flips that later revert.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from deltabt import indicators as ind
from deltabt.config import StrategyParams


@dataclass
class Signals:
    """Per-bar arrays aligned to the 1m index."""

    long_entry: np.ndarray
    short_entry: np.ndarray
    stop_long: np.ndarray
    stop_short: np.ndarray
    supertrend: np.ndarray
    direction: np.ndarray
    atr: np.ndarray
    wpr: np.ndarray
    adx_1m: np.ndarray
    adx_5m: np.ndarray
    bull_1m: np.ndarray
    bear_1m: np.ndarray
    #: Trend-stack truth without the WPR gate, so the engine can drive the
    #: latch incrementally and still apply every other filter.
    long_base: np.ndarray
    short_base: np.ndarray
    warmup: int


def resample_ohlcv(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1m bars to ``minutes``, UTC-aligned.

    Verified against the exchange's own 5m candles: the sum/min/max of five 1m
    bars reproduces the served 5m bar exactly, so resampling locally is both
    correct and guarantees the alignment the engine assumes.
    """
    if minutes <= 1:
        return df.reset_index(drop=True)
    step = 60 * minutes
    bucket = (df["time"].to_numpy(dtype="int64") // step) * step
    out = (
        df.assign(_b=bucket)
        .groupby("_b", sort=True)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
    )
    return out.rename(columns={"_b": "time"})


def resample_5m(df: pd.DataFrame) -> pd.DataFrame:
    """Backwards-compatible alias."""
    return resample_ohlcv(df, 5)


def resample_tradable(df: pd.DataFrame, tradable: np.ndarray, minutes: int) -> np.ndarray:
    """Project a per-1m-bar tradability mask onto resampled bars.

    A resampled bar counts as tradable only if *every* constituent minute was.
    Requiring all rather than any is the conservative choice: an aggregate bar
    that spans a halt has a range no order could have been filled across.
    """
    if minutes <= 1:
        return np.asarray(tradable, dtype=bool)
    step = 60 * minutes
    bucket = (df["time"].to_numpy(dtype="int64") // step) * step
    s = pd.Series(np.asarray(tradable, dtype=bool)).groupby(bucket).all()
    return s.to_numpy(dtype=bool)


def _broadcast_confirmed(
    htf_values: np.ndarray, htf_time: np.ndarray, ltf_time: np.ndarray
) -> np.ndarray:
    """Project a 5m series onto the 1m grid using the last CLOSED 5m bar.

    Shifting by one 5m bar before broadcasting is what makes this
    non-repainting: within the 09:00-09:05 period every 1m bar sees the
    08:55-09:00 value, which is fully determined and identical live.
    """
    shifted = np.concatenate(([np.nan], htf_values[:-1].astype("float64")))
    slot = np.searchsorted(htf_time, ltf_time, side="right") - 1
    out = np.full(len(ltf_time), np.nan, dtype="float64")
    ok = slot >= 0
    out[ok] = shifted[slot[ok]]
    return out


def _threshold(values: np.ndarray, absolute: float | None, percentile: float) -> float:
    """Resolve an ADX threshold.

    An absolute 25 is not a trend filter at DI=14 -- the driftless noise floor
    is around 23, so a random walk clears it 38% of the time, and on real 1m
    data it passes ~77% of bars. Percentile calibration makes the gate mean the
    same thing across symbols and timeframes.
    """
    if absolute is not None:
        return float(absolute)
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0
    return float(np.quantile(finite, percentile))


def build_signals(df: pd.DataFrame, params: StrategyParams) -> Signals:
    """Compute entry signals and stop levels for one symbol."""
    params.validate()

    high = df["high"].to_numpy(dtype="float64")
    low = df["low"].to_numpy(dtype="float64")
    close = df["close"].to_numpy(dtype="float64")
    time = df["time"].to_numpy(dtype="int64")
    n = len(df)

    # -- 1m indicators ------------------------------------------------------
    st, direction = ind.supertrend(high, low, close, params.st_factor, params.st_atr_period)
    plus_di, minus_di, adx = ind.dmi(high, low, close, params.di_length, params.adx_smoothing)
    wpr_1m = ind.wpr(high, low, close, params.wpr.length)
    atr_1m = ind.atr(high, low, close, params.st_atr_period)

    bull_1m = direction < 0
    bear_1m = direction > 0

    thr_1m = _threshold(adx, params.adx_threshold_1m, params.adx_percentile_1m)
    adx_ok = adx > thr_1m
    if params.require_adx_rising:
        # ADX is direction-agnostic: a strengthening trend raises it whichever
        # way price is going, so both sides want *rising*, not mirrored slopes.
        # Level+slope measured +1.054R vs +0.965R for level alone, while a
        # fresh crossover of the threshold was worst at +0.755R -- by the time
        # a ~40-bar-lag indicator first confirms, the impulse is spent.
        adx_rising = np.concatenate(([False], adx[1:] > adx[:-1]))
        adx_bull = adx_ok & (plus_di > minus_di) & adx_rising
        adx_bear = adx_ok & (minus_di > plus_di) & adx_rising
    else:
        adx_bull = adx_ok & (plus_di > minus_di)
        adx_bear = adx_ok & (minus_di > plus_di)

    # -- higher-timeframe confirmation --------------------------------------
    # `df` is already at the base timeframe, so the confirmation series is
    # built by aggregating it further.
    htf_factor = max(params.confirm_minutes // max(params.base_minutes, 1), 2)
    h5 = resample_ohlcv(df, htf_factor)
    t5 = h5["time"].to_numpy(dtype="int64")
    st5, dir5 = ind.supertrend(
        h5["high"].to_numpy(dtype="float64"),
        h5["low"].to_numpy(dtype="float64"),
        h5["close"].to_numpy(dtype="float64"),
        params.st_factor,
        params.st_atr_period,
    )
    p5, m5, adx5 = ind.dmi(
        h5["high"].to_numpy(dtype="float64"),
        h5["low"].to_numpy(dtype="float64"),
        h5["close"].to_numpy(dtype="float64"),
        params.di_length,
        params.adx_smoothing,
    )

    dir5_1m = _broadcast_confirmed(dir5, t5, time)
    adx5_1m = _broadcast_confirmed(adx5, t5, time)
    p5_1m = _broadcast_confirmed(p5, t5, time)
    m5_1m = _broadcast_confirmed(m5, t5, time)

    if params.use_5m_confirmation:
        bull5 = dir5_1m < 0
        bear5 = dir5_1m > 0
        if params.use_5m_adx:
            # The 5m ADX runs systematically higher than the 1m ADX (median
            # ~48.6 vs ~35.5 on simulated data), so reusing the 1m threshold
            # here makes the gate reject almost nothing. It gets its own.
            thr_5m = _threshold(adx5_1m, params.adx_threshold_5m, params.adx_percentile_5m)
            five_long = bull5 & (adx5_1m > thr_5m) & (p5_1m > m5_1m)
            five_short = bear5 & (adx5_1m > thr_5m) & (m5_1m > p5_1m)
        else:
            five_long, five_short = bull5, bear5
    else:
        five_long = np.ones(n, dtype=bool)
        five_short = np.ones(n, dtype=bool)

    # -- structure ----------------------------------------------------------
    if params.use_structure:
        # Effectively a no-op: measured on 1.33M bars of real BTCUSD 1m data,
        # `close > st` excluded 6 bars that `direction < 0` had admitted
        # (0.0009%). Those 6 are a faithful reproduction of a genuine Pine
        # edge case where the band ratchets above price without flipping
        # direction, not a porting error. Retained only so parity mode can
        # include the original condition.
        price_bull = close > st
        price_bear = close < st
    else:
        price_bull = np.ones(n, dtype=bool)
        price_bear = np.ones(n, dtype=bool)

    long_base = bull_1m & adx_bull & price_bull & five_long
    short_base = bear_1m & adx_bear & price_bear & five_short

    # -- WPR gate -----------------------------------------------------------
    if not params.wpr.enabled:
        wpr_long = np.ones(n, dtype=bool)
        wpr_short = np.ones(n, dtype=bool)
    elif params.mode == "parity":
        # The original rule: a single-bar uptick out of an extreme. Measured
        # P(uptick | below -80) is 0.504, so it discards half the sample for
        # no information -- and at length 140 the extreme is nearly
        # unreachable while the trend stack is true.
        # NaN comparisons already yield False, matching Pine v6 where `na` in
        # boolean context is false, so no extra guard is needed.
        prev = np.concatenate(([np.nan], wpr_1m[:-1]))
        with np.errstate(invalid="ignore"):
            wpr_long = (prev < -80.0) & (wpr_1m > prev)
            wpr_short = (prev > -20.0) & (wpr_1m < prev)
    else:
        # Corrected mode uses the latch. When it also clears on position
        # state, the engine re-drives it incrementally; these arrays are the
        # no-reset baseline.
        from deltabt import wpr_latch

        wpr_long = wpr_latch.latch_fires(
            wpr_1m,
            arm_level=params.wpr.arm_long,
            fire_level=params.wpr.fire_long,
            expiry_bars=params.wpr.expiry_bars,
            long_side=True,
        )
        wpr_short = wpr_latch.latch_fires(
            wpr_1m,
            arm_level=params.wpr.arm_short,
            fire_level=params.wpr.fire_short,
            expiry_bars=params.wpr.expiry_bars,
            long_side=False,
        )

    long_sig = long_base & wpr_long
    short_sig = short_base & wpr_short

    # -- edge trigger -------------------------------------------------------
    if params.edge_trigger:
        long_sig = long_sig & ~np.concatenate(([False], long_sig[:-1]))
        short_sig = short_sig & ~np.concatenate(([False], short_sig[:-1]))

    # -- stops --------------------------------------------------------------
    # Pine placed the stop at min(supertrend, low) for longs. That is the
    # Supertrend line in practice, which is exactly where reversals cluster,
    # and it can sit one tick away -- the root of the uncapped-size problem.
    stop_long = np.minimum(st, low)
    stop_short = np.maximum(st, high)

    warmup = min(params.warmup_bars, n)
    if warmup:
        long_sig[:warmup] = False
        short_sig[:warmup] = False

    return Signals(
        long_entry=long_sig,
        short_entry=short_sig,
        stop_long=stop_long,
        stop_short=stop_short,
        supertrend=st,
        direction=direction,
        atr=atr_1m,
        wpr=wpr_1m,
        adx_1m=adx,
        adx_5m=adx5_1m,
        bull_1m=bull_1m,
        bear_1m=bear_1m,
        long_base=long_base,
        short_base=short_base,
        warmup=warmup,
    )

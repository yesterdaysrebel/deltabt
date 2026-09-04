"""Turn a :class:`~deltabt.spec.StrategySpec` into signal arrays.

ONE IMPLEMENTATION, TWO CALLERS
    Everything here is vectorised over whatever array it is handed, and the
    rule at bar ``t`` reads only bars ``<= t``. That is what lets the same code
    serve both callers:

        backtest   compute(full_series, spec) -> arrays -> engine.run_backtest
        live       compute(trailing_window, spec) -> read row [-1]

    The live path is the bounded-window recomputation the bot already used; the
    difference is that the rule is no longer written a second time to support
    it. A strategy that backtests is the strategy that trades, by construction
    rather than by parity test.

INDICATORS ARE NOT REIMPLEMENTED HERE
    Every value comes from ``deltabt.indicators``, the same numba functions the
    backtester and all fourteen research experiments used, and the leg extreme
    comes from ``deltabt.research.hwpr``. There is exactly one Supertrend,
    ADX/DI and Williams %R in this repository.

WHAT "CLOSED BAR" MEANS HERE
    Every array is assumed to contain closed bars only. ``compute`` has no way
    to detect a forming bar and does not try; the caller never puts one in.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from deltabt import indicators as ind
from deltabt.research.hwpr import _leg_extreme
from deltabt.spec import StrategySpec, TimeframeRules


class InsufficientHistory(Exception):
    """Fewer bars than the indicator warm-up needs."""


@dataclass
class TimeframeIndicators:
    """Indicator arrays for one timeframe, all aligned to that timeframe."""

    time: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    st: np.ndarray
    direction: np.ndarray
    plus_di: np.ndarray
    minus_di: np.ndarray
    adx: np.ndarray
    wpr: np.ndarray
    leg_low: np.ndarray
    leg_high: np.ndarray
    atr: np.ndarray
    #: Index of the bar the current Supertrend leg started on.
    leg_start: np.ndarray
    #: False where the leg began inside the warm-up region, so its extreme is
    #: an artifact of where the array starts rather than a market event.
    leg_determinate: np.ndarray

    def __len__(self) -> int:
        return len(self.time)


def _prev(a: np.ndarray, fill=np.nan) -> np.ndarray:
    """``a`` shifted one bar forward; bar 0 gets ``fill``."""
    out = np.empty_like(a, dtype="float64")
    out[0] = fill
    out[1:] = a[:-1]
    return out


def indicators(df: pd.DataFrame, spec: StrategySpec) -> TimeframeIndicators:
    """Compute every indicator a spec can reference, on one timeframe.

    All of them are computed regardless of which gates are enabled. They are
    cheap next to the fetch, and a gate that is off today is swept on tomorrow;
    branching here would make the arrays' contents depend on the spec in a way
    that is invisible at the call site.
    """
    h = df["high"].to_numpy("float64")
    l = df["low"].to_numpy("float64")
    c = df["close"].to_numpy("float64")
    t = df["time"].to_numpy("int64")

    st, direction = ind.supertrend(h, l, c, spec.st_multiplier, spec.st_atr_period)
    plus_di, minus_di, adx = ind.dmi(h, l, c, spec.di_period, spec.adx_period)
    wpr = ind.wpr(h, l, c, spec.wpr_period)
    leg_low, leg_high = _leg_extreme(h, l, direction)
    atr = ind.atr(h, l, c, spec.stop_atr_period)

    # Where the current leg began. A flip is a finite direction differing from
    # the previous finite direction; bars before the first finite direction
    # have no leg at all.
    n = len(t)
    finite = np.isfinite(direction)
    prev_dir = _prev(direction)
    flipped = finite & np.isfinite(prev_dir) & (direction != prev_dir)
    first = int(np.argmax(finite)) if finite.any() else n
    idx = np.arange(n)
    # A leg starts at the first finite bar, and at every flip thereafter.
    starts = np.where(flipped | ((idx == first) & finite), idx, -1)
    leg_start = np.maximum.accumulate(starts)

    # The leg extreme does NOT converge the way Wilder smoothing does: it is an
    # extremum since the last flip, so if the leg began inside the warm-up the
    # value is the extremum of an arbitrary truncation. Supertrend also seeds
    # its bands from the first bars of whatever array it is given, so the first
    # apparent direction change is an artifact of where the array starts, not a
    # market event -- measured on a strong trend it moves the leg extreme by
    # 60% between window lengths. Both are why this is a flag, not a fallback.
    leg_determinate = finite & (leg_start >= spec.warmup_bars)

    return TimeframeIndicators(
        time=t, high=h, low=l, close=c, st=st, direction=direction,
        plus_di=plus_di, minus_di=minus_di, adx=adx, wpr=wpr,
        leg_low=leg_low, leg_high=leg_high, atr=atr,
        leg_start=leg_start, leg_determinate=leg_determinate,
    )


def gate(ti: TimeframeIndicators, rules: TimeframeRules) -> tuple[np.ndarray, np.ndarray]:
    """The long and short conjunctions on one timeframe, per bar.

    A disabled component contributes ``True``; a NaN never passes, so warm-up
    bars fail every enabled component rather than passing a comparison against
    NaN by accident.
    """
    n = len(ti)
    if n == 0:
        empty = np.zeros(0, dtype=bool)
        return empty, empty
    long_ok = np.ones(n, dtype=bool)
    short_ok = np.ones(n, dtype=bool)

    if rules.supertrend != "off":
        # Pine returns direction -1 for an UPTREND: bullish is direction < 0.
        d, dp = ti.direction, _prev(ti.direction)
        bull = np.isfinite(d) & (d < 0)
        bear = np.isfinite(d) & (d > 0)
        if rules.supertrend == "flip":
            bull &= np.isfinite(dp) & (dp >= 0)
            bear &= np.isfinite(dp) & (dp <= 0)
        if rules.supertrend == "counter":
            # The inverse gate: a long against a bearish Supertrend, a short
            # against a bullish one. See ``banded_fade`` below for why.
            long_ok &= bear
            short_ok &= bull
        else:
            long_ok &= bull
            short_ok &= bear

    if rules.di:
        ok = np.isfinite(ti.plus_di) & np.isfinite(ti.minus_di)
        long_ok &= ok & (ti.plus_di > ti.minus_di)
        short_ok &= ok & (ti.minus_di > ti.plus_di)

    if rules.adx_min is not None:
        strong = np.isfinite(ti.adx) & (ti.adx >= rules.adx_min)
        long_ok &= strong
        short_ok &= strong

    if rules.wpr_rule == "variant_a":
        # Above the level AND rising, mirrored for shorts. `wpr_prev` is the
        # previous CLOSED bar, never a forming one.
        w, wp = ti.wpr, _prev(ti.wpr)
        ok = np.isfinite(w) & np.isfinite(wp)
        long_ok &= ok & (w > rules.wpr_long_level) & (w > wp)
        short_ok &= ok & (w < rules.wpr_short_level) & (w < wp)
    elif rules.wpr_rule == "banded":
        # variant_a WITH A CEILING. `variant_a` is `%R > -80 AND rising`, a
        # floor with nothing above it, so %R = -4 qualifies: price at the high
        # of the 140-bar window is a valid long. The live ATR arm entered
        # longs at -4.3, -6.9, -8.6, -11.8 and -12.9 on 2026-08-26/27 with a
        # 2xATR stop worth 0.2-0.5% of price.
        #
        # Here a long must still be rising, but must not yet have crossed the
        # MIDPOINT of the band; a short mirrors it above the midpoint. The
        # direction requirement is unchanged -- this narrows WHERE in the
        # range an entry may happen, it does not invert the rule.
        #
        # Unlike `cross_levels` this is not a one-bar event, so it keeps the
        # repeat-suppression behaviour of the chosen trigger instead of
        # collapsing the trade count by an order of magnitude.
        w, wp = ti.wpr, _prev(ti.wpr)
        mid = 0.5 * (rules.wpr_long_level + rules.wpr_short_level)
        ok = np.isfinite(w) & np.isfinite(wp)
        long_ok &= ok & (w > rules.wpr_long_level) & (w < mid) & (w > wp)
        short_ok &= ok & (w < rules.wpr_short_level) & (w > mid) & (w < wp)
    elif rules.wpr_rule == "banded_fade":
        # ``banded`` with the sides swapped. The SETUP is the same set of
        # bars, only the label changes: the bar that is a banded LONG setup
        # is a banded_fade SHORT setup, so an edge trigger fires on exactly
        # the same bars. With ``supertrend = "counter"`` on the same
        # timeframe this is the precise inverse of manual_scalp_st_banded,
        # which is the property tests/test_banded_fade.py asserts.
        w, wp = ti.wpr, _prev(ti.wpr)
        mid = 0.5 * (rules.wpr_long_level + rules.wpr_short_level)
        ok = np.isfinite(w) & np.isfinite(wp)
        long_ok &= ok & (w < rules.wpr_short_level) & (w > mid) & (w < wp)
        short_ok &= ok & (w > rules.wpr_long_level) & (w < mid) & (w > wp)
    elif rules.wpr_rule == "cross_levels":
        # Crossed OUT of the band on this bar: long leaves oversold, short
        # leaves overbought. A one-bar event, so this is already edge-like.
        w, wp = ti.wpr, _prev(ti.wpr)
        ok = np.isfinite(w) & np.isfinite(wp)
        long_ok &= ok & (w > rules.wpr_long_level) & (wp <= rules.wpr_long_level)
        short_ok &= ok & (w < rules.wpr_short_level) & (wp >= rules.wpr_short_level)

    return long_ok, short_ok


def align_confirm(primary_time: np.ndarray, confirm_time: np.ndarray,
                  spec: StrategySpec) -> np.ndarray:
    """Index into the confirmation timeframe for each primary bar.

    ``time`` is the bar's OPEN. A primary bar opening at ``T`` closes at
    ``T + primary_minutes*60``, and the confirmation bar that closes at the
    same instant opens ``confirm_minutes*60`` earlier. Choosing the last
    confirmation bar at or before that instant is exactly what the bot sees
    when a primary bar closes: the most recent CLOSED confirmation bar.

    Returns -1 where no confirmation bar exists yet.
    """
    target = (primary_time
              + spec.primary_minutes * 60
              - spec.confirm_minutes * 60)
    idx = np.searchsorted(confirm_time, target, side="right") - 1
    return idx


@dataclass
class SignalArrays:
    """Per-bar signals on the PRIMARY timeframe grid."""

    time: np.ndarray
    close: np.ndarray
    long_entry: np.ndarray
    short_entry: np.ndarray
    stop_long: np.ndarray
    stop_short: np.ndarray
    target_long: np.ndarray
    target_short: np.ndarray
    atr: np.ndarray
    #: Setup truth before the trigger is applied. Kept because "the setup was
    #: already true on the previous bar" is a rejection the audit trail names,
    #: and reconstructing it from the fired arrays alone is impossible.
    long_setup: np.ndarray
    short_setup: np.ndarray
    #: Per-bar reasons a true setup produced no entry.
    rejected_stop_pct: np.ndarray
    rejected_leg_truncated: np.ndarray
    warmup: int
    spec: StrategySpec
    primary: TimeframeIndicators
    confirm: TimeframeIndicators
    confirm_index: np.ndarray

    def __len__(self) -> int:
        return len(self.time)


def _stops(pi: TimeframeIndicators, spec: StrategySpec) -> tuple[np.ndarray, np.ndarray]:
    """Stop price for a long and for a short, at every bar."""
    c = pi.close
    if spec.stop == "leg_extreme":
        # The frozen H-WPR-1 stop: the leg extreme, bounded by the Supertrend
        # line itself.
        return np.minimum(pi.leg_low, pi.st), np.maximum(pi.leg_high, pi.st)
    if spec.stop == "atr":
        return c - spec.stop_atr_multiplier * pi.atr, c + spec.stop_atr_multiplier * pi.atr
    if spec.stop == "fixed_pct":
        return c * (1.0 - spec.stop_pct), c * (1.0 + spec.stop_pct)
    raise ValueError(f"unknown stop mode {spec.stop!r}")


def compute(primary: pd.DataFrame, confirm: pd.DataFrame | None,
            spec: StrategySpec) -> SignalArrays:
    """Evaluate ``spec`` over closed bars.

    ``primary`` and ``confirm`` are both closed-bar frames with ``time`` as the
    bar open. ``confirm`` may be None or the same frame as ``primary`` when the
    spec's confirmation gate is disabled or single-timeframe.
    """
    spec.validate()
    pi = indicators(primary, spec)
    n = len(pi)

    long_setup, short_setup = gate(pi, spec.primary)

    if spec.confirm.enabled:
        if confirm is None:
            raise ValueError("spec enables a confirmation gate but no frame was given")
        ci = indicators(confirm, spec)
        cidx = align_confirm(pi.time, ci.time, spec)
        c_long, c_short = gate(ci, spec.confirm)
        # A primary bar with no confirmation bar yet passes nothing.
        have = cidx >= 0
        safe = np.where(have, cidx, 0)
        long_setup &= have & c_long[safe]
        short_setup &= have & c_short[safe]
    else:
        ci = pi
        cidx = np.full(n, -1, dtype="int64")

    if spec.trigger == "edge":
        # Fire only on the setup's FALSE -> TRUE edge, derived from the arrays
        # rather than from remembered state. A flag carried between bars would
        # have to survive restarts, replays and duplicate feed messages;
        # recomputing the previous bar cannot drift and cannot be stale after a
        # redeploy.
        fired_long = long_setup & ~np.r_[False, long_setup[:-1]]
        fired_short = short_setup & ~np.r_[False, short_setup[:-1]]
    else:
        fired_long, fired_short = long_setup.copy(), short_setup.copy()

    # Structurally impossible for Supertrend to be both, but if it ever happens
    # the honest response is to take neither.
    both = fired_long & fired_short
    fired_long &= ~both
    fired_short &= ~both

    stop_long, stop_short = _stops(pi, spec)
    risk_long = pi.close - stop_long
    risk_short = stop_short - pi.close

    ok_long = np.isfinite(risk_long) & (risk_long > 0)
    ok_short = np.isfinite(risk_short) & (risk_short > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        pct_long = np.where(ok_long, risk_long / pi.close, np.nan)
        pct_short = np.where(ok_short, risk_short / pi.close, np.nan)

    too_wide_long = ok_long & (pct_long > spec.max_stop_pct)
    too_wide_short = ok_short & (pct_short > spec.max_stop_pct)
    rejected_stop_pct = (fired_long & (too_wide_long | ~ok_long)) | \
                        (fired_short & (too_wide_short | ~ok_short))

    # Only the leg-extreme stop can be undeterminable from a bounded window.
    # Suppress rather than substitute a different stop, which would size the
    # position off a rule nobody wrote.
    if spec.stop == "leg_extreme":
        truncated = ~pi.leg_determinate
    else:
        truncated = np.zeros(n, dtype=bool)
    rejected_leg_truncated = (fired_long | fired_short) & truncated

    valid_long = fired_long & ok_long & ~too_wide_long & ~truncated
    valid_short = fired_short & ok_short & ~too_wide_short & ~truncated

    return SignalArrays(
        time=pi.time, close=pi.close,
        long_entry=valid_long, short_entry=valid_short,
        stop_long=stop_long, stop_short=stop_short,
        target_long=pi.close + spec.target_r * risk_long,
        target_short=pi.close - spec.target_r * risk_short,
        atr=pi.atr,
        long_setup=long_setup, short_setup=short_setup,
        rejected_stop_pct=rejected_stop_pct,
        rejected_leg_truncated=rejected_leg_truncated,
        warmup=spec.warmup_bars,
        spec=spec, primary=pi, confirm=ci, confirm_index=cidx,
    )


def to_engine_signals(sig: SignalArrays):
    """Adapt to the :class:`deltabt.strategy.Signals` the backtester consumes.

    ``deltabt.engine.run_backtest`` reads ``long_entry``, ``short_entry``,
    ``stop_long``, ``stop_short``, ``atr`` and ``warmup`` on its default path.
    The remaining fields belong to the stateful Williams %R latch, which the
    engine only consults when ``params.wpr.enabled`` is set -- a gate this
    repository measured as cutting the sample 86% while worsening expectancy,
    and which no spec expresses. They are populated with the real indicator
    values where one exists so that anything inspecting them sees the truth
    rather than a placeholder, and with the setup arrays where the latch would
    otherwise expect its own pre-gate truth.

    The frame handed to ``run_backtest`` must be the PRIMARY-timeframe frame,
    because every array here is on the primary grid.
    """
    from deltabt.strategy import Signals

    pi = sig.primary
    bull = np.isfinite(pi.direction) & (pi.direction < 0)
    bear = np.isfinite(pi.direction) & (pi.direction > 0)
    return Signals(
        long_entry=sig.long_entry,
        short_entry=sig.short_entry,
        stop_long=sig.stop_long,
        stop_short=sig.stop_short,
        supertrend=pi.st,
        direction=pi.direction,
        atr=sig.atr,
        wpr=pi.wpr,
        adx_1m=pi.adx,
        adx_5m=pi.adx,
        bull_1m=bull,
        bear_1m=bear,
        long_base=sig.long_setup,
        short_base=sig.short_setup,
        warmup=sig.warmup,
    )

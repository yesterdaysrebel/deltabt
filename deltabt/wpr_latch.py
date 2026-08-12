"""Stateful Williams %R band-traverse gate.

The original strategy fired on ``wpr[1] < -80 and wpr > wpr[1]`` -- a one-bar
uptick from an extreme. That is a coin flip (P(uptick | below -80) measured
0.504) and, combined with a trend stack that pins median WPR(140) to -2.1, it
fires roughly four times a year.

This module implements the revision: latch when WPR enters the arming zone,
then fire when it later crosses the target level. "Climbing from -80" is
path-dependent, so this is a state machine rather than an expression.

The measured gain over the memoryless equivalent ("-80 < wpr < -20 and rising")
comes from *entry location*, not selectivity: the latch enters at a median stop
distance of 30.9 bps versus 16.9 bps, so fixed basis-point costs consume 0.39R
instead of 0.71R. A corollary worth internalising -- under a fixed R-multiple
target, entering close to your stop is actively harmful, because it shrinks the
target in absolute terms while costs stay flat.

Block order inside the scan is load-bearing: expiry -> arm -> fire -> resets.
"""

from __future__ import annotations

import numpy as np
from numba import njit

_F = np.float64


@njit(cache=True)
def _scan(
    values: np.ndarray,
    arm_level: float,
    fire_level: float,
    expiry_bars: int,
    long_side: bool,
    in_position: np.ndarray,
    clear_in_position: bool,
    adverse_flip: np.ndarray,
    clear_on_adverse_flip: bool,
) -> np.ndarray:
    """Forward-scan FSM. Returns a boolean fire pulse, at most one bar wide."""
    n = values.size
    fire = np.zeros(n, dtype=np.bool_)
    arm_bar = -1  # sentinel for Pine's `na`

    for i in range(n):
        v = values[i]
        vp = values[i - 1] if i > 0 else np.nan
        valid = (not np.isnan(v)) and (not np.isnan(vp))

        # (1) expiry
        if arm_bar >= 0 and (i - arm_bar) >= expiry_bars:
            arm_bar = -1

        # (2) arm / refresh. Restamping while still in the zone means the
        # expiry counts from the LAST bar in the zone, so a long basing period
        # does not consume the traverse budget.
        if valid:
            if long_side:
                if v < arm_level:
                    arm_bar = i
            else:
                if v > arm_level:
                    arm_bar = i

        # (3) fire on a strict cross of the target while armed
        if valid and arm_bar >= 0:
            if long_side:
                crossed = v > fire_level and vp <= fire_level
            else:
                crossed = v < fire_level and vp >= fire_level
            if crossed:
                fire[i] = True
                # Consume unconditionally, even though downstream filters may
                # reject this bar. Leaving the latch armed would let the gate
                # stay true on later bars and rebuild the plateau this exists
                # to remove.
                arm_bar = -1

        # (4) resets, applied after the fire test so a fire on the same bar as
        # an exit is still honoured
        if clear_in_position and in_position[i]:
            arm_bar = -1
        if clear_on_adverse_flip and adverse_flip[i]:
            arm_bar = -1

    return fire


def latch_fires(
    values: np.ndarray,
    *,
    arm_level: float,
    fire_level: float,
    expiry_bars: int,
    long_side: bool,
    in_position: np.ndarray | None = None,
    clear_in_position: bool = False,
    adverse_flip: np.ndarray | None = None,
    clear_on_adverse_flip: bool = False,
) -> np.ndarray:
    """Run the latch over a WPR series.

    ``in_position`` and ``adverse_flip`` are optional per-bar boolean arrays.
    Passing ``in_position`` requires it to be known in advance, which is only
    true when replaying a completed run -- the live engine drives the FSM
    incrementally instead (see :func:`step_state`).
    """
    v = np.ascontiguousarray(values, dtype=_F)
    n = v.size
    zeros = np.zeros(n, dtype=np.bool_)
    return _scan(
        v,
        float(arm_level),
        float(fire_level),
        int(expiry_bars),
        bool(long_side),
        zeros if in_position is None else np.ascontiguousarray(in_position, dtype=np.bool_),
        bool(clear_in_position),
        zeros if adverse_flip is None else np.ascontiguousarray(adverse_flip, dtype=np.bool_),
        bool(clear_on_adverse_flip),
    )


@njit(cache=True)
def step_state(
    arm_bar: int,
    i: int,
    value: float,
    prev_value: float,
    arm_level: float,
    fire_level: float,
    expiry_bars: int,
    long_side: bool,
) -> tuple[int, bool]:
    """Advance the latch one bar. Returns ``(new_arm_bar, fired)``.

    The engine calls this instead of :func:`latch_fires` because the
    ``clear_in_position`` reset depends on position state that is itself
    downstream of the fires -- a circular dependency that cannot be resolved by
    precomputing the whole array.
    """
    valid = (not np.isnan(value)) and (not np.isnan(prev_value))

    if arm_bar >= 0 and (i - arm_bar) >= expiry_bars:
        arm_bar = -1

    if valid:
        if long_side:
            if value < arm_level:
                arm_bar = i
        else:
            if value > arm_level:
                arm_bar = i

    fired = False
    if valid and arm_bar >= 0:
        if long_side:
            crossed = value > fire_level and prev_value <= fire_level
        else:
            crossed = value < fire_level and prev_value >= fire_level
        if crossed:
            fired = True
            arm_bar = -1

    return arm_bar, fired


def latch_fires_vectorised(
    values: np.ndarray,
    *,
    arm_level: float,
    fire_level: float,
    expiry_bars: int,
    long_side: bool,
) -> np.ndarray:
    """Branch-free equivalent of :func:`latch_fires` without state resets.

    Used in the sweep's inner loop where position-state resets are disabled.
    Tested for exact equality against the FSM.

    The ``prev_cross < armed_at`` term is what reproduces consume-on-fire:
    without it, a second cross of the target without an intervening re-arm
    would fire again.
    """
    v = np.ascontiguousarray(values, dtype=_F)
    n = v.size
    if n == 0:
        return np.zeros(0, dtype=bool)

    idx = np.arange(n)
    finite = np.isfinite(v)
    prev_finite = np.concatenate(([False], finite[:-1]))
    valid = finite & prev_finite

    in_zone = valid & ((v < arm_level) if long_side else (v > arm_level))
    armed_at = np.maximum.accumulate(np.where(in_zone, idx, -1))

    vp = np.concatenate(([np.nan], v[:-1]))
    with np.errstate(invalid="ignore"):
        if long_side:
            cross = valid & (v > fire_level) & (vp <= fire_level)
        else:
            cross = valid & (v < fire_level) & (vp >= fire_level)

    crossed_at = np.maximum.accumulate(np.where(cross, idx, -1))
    prev_cross = np.concatenate(([-1], crossed_at[:-1]))

    return (
        cross
        & (armed_at >= 0)
        & ((idx - armed_at) < expiry_bars)
        & (prev_cross < armed_at)
    )


def map_htf_fires_to_ltf(
    htf_fires: np.ndarray,
    htf_index: np.ndarray,
    ltf_index: np.ndarray,
) -> np.ndarray:
    """Project higher-timeframe fires onto the base timeframe.

    Takes the rising edge at each HTF boundary rather than forward-filling.
    Forward-filling a boolean would turn one 5m traverse event into five
    consecutive 1m entry opportunities -- rebuilding the plateau, and the most
    likely way a port silently disagrees with the Pine original.

    The fire is also shifted by one HTF bar so it lands on the first base bar
    after the HTF bar closed, never inside the bar that produced it.
    """
    htf_fires = np.asarray(htf_fires, dtype=bool)
    if htf_fires.size == 0:
        return np.zeros(len(ltf_index), dtype=bool)

    shifted = np.concatenate(([False], htf_fires[:-1]))
    # For each base bar, which HTF bar was most recently completed.
    slot = np.searchsorted(htf_index, ltf_index, side="right") - 1
    valid = slot >= 0

    out = np.zeros(len(ltf_index), dtype=bool)
    fired = np.zeros(len(ltf_index), dtype=bool)
    fired[valid] = shifted[slot[valid]]

    # Rising edge only: keep the first base bar of each HTF slot.
    new_slot = np.concatenate(([True], slot[1:] != slot[:-1]))
    out[valid & new_slot] = fired[valid & new_slot]
    return out

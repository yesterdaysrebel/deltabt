"""Arbitrary per-bar stop rules for the frozen H-WPR-1 simulator.

``hwpr._simulate`` does not take a stop price. It derives one at the signal bar
from three arrays it is handed::

    LONG   stop = min(st1[i], leg_lo[i])
    SHORT  stop = max(st1[i], leg_hi[i])

so a stop rule other than the Supertrend leg extreme can be tested without
touching the frozen simulator: hand it ``st1 = leg_lo = stop_long`` and
``leg_hi = stop_short``. A long then resolves to ``stop_long`` and a short to
``stop_short``, because a long's stop is always below a short's. This is the
same composition H-WPR-1 already relies on, used with different inputs.

THE FAILURE THIS EXISTS TO PREVENT
    numba's ``max`` propagates NaN exactly as Python's does. A short signal
    whose ``st1`` slot was left unfilled therefore gives
    ``max(nan, stop_short) -> nan``, and the simulator's ``isfinite(stop)``
    guard discards the trade SILENTLY -- no exception, no counter, nothing in
    the output that distinguishes it from a rule that never fired. Measured
    during H-Structure-1 this deleted every short trade in the study while the
    long-only arm looked correct throughout.

    ``injection_arrays`` fills BOTH stop slots at every signal bar and refuses,
    loudly, to build arrays that would reproduce that failure.

These arrays are for the structural stop path only. ``_simulate`` called with
``legacy_stop=True`` ignores ``leg_lo``/``leg_hi`` and reads the signal bar's
own low/high instead, which is a different rule and not what this builds.
"""

from __future__ import annotations

import numpy as np


def _signal_array(name: str, values) -> np.ndarray:
    a = np.asarray(values)
    if a.dtype != np.bool_:
        raise TypeError(f"{name} must be a boolean array, got dtype {a.dtype}")
    if a.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional, got shape {a.shape}")
    return a


def _price_array(name: str, values) -> np.ndarray:
    a = np.asarray(values)
    if not np.issubdtype(a.dtype, np.floating):
        raise TypeError(f"{name} must be a float array, got dtype {a.dtype}")
    if a.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional, got shape {a.shape}")
    return np.ascontiguousarray(a, dtype="float64")


def injection_arrays(long_sig, short_sig, stop_long, stop_short):
    """Build ``(st1, leg_lo, leg_hi)`` for ``hwpr._simulate`` from explicit stops.

    Every check here is against a state the simulator would otherwise consume
    without complaint and turn into a missing trade.
    """
    lo = _signal_array("long_sig", long_sig)
    sh = _signal_array("short_sig", short_sig)
    sl = _price_array("stop_long", stop_long)
    ss = _price_array("stop_short", stop_short)

    n = lo.size
    for name, a in (("short_sig", sh), ("stop_long", sl), ("stop_short", ss)):
        if a.size != n:
            raise ValueError(
                f"{name} has length {a.size}, expected {n} to match long_sig")

    fired = np.flatnonzero(lo | sh)
    for name, a in (("stop_long", sl), ("stop_short", ss)):
        bad = fired[~np.isfinite(a[fired])]
        if bad.size:
            raise ValueError(
                f"{name} is not finite at {bad.size} signal bar(s), first at "
                f"index {bad[0]}; the simulator would discard those trades "
                f"without reporting them")

    crossed = fired[sl[fired] >= ss[fired]]
    if crossed.size:
        raise ValueError(
            f"stop_long must be strictly below stop_short at every signal bar; "
            f"violated at {crossed.size} bar(s), first at index {crossed[0]}")

    return sl, sl, ss

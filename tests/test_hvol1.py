"""H-VOL-1 Stage A guards.

The machinery is shared with H-STRUCTURE-2 and tested there. What is specific
here is the compression state, the boundary-break event, and the claim that the
two branches are mutually exclusive.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.research import hstructure2 as h2
from deltabt.research import hvol1 as v1


def bars(n: int, *, start: int = 1735689600, seed: int = 0, vol: float = 0.001):
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    o = np.concatenate(([100.0], c[:-1]))
    hi = np.maximum(o, c) * (1 + rng.random(n) * vol)
    lo = np.minimum(o, c) * (1 - rng.random(n) * vol)
    return pd.DataFrame(dict(time=start + 60 * np.arange(n), open=o, high=hi,
                             low=lo, close=c, volume=np.ones(n)))


# ------------------------------------------------------- inheritance is real

def test_compression_constants_match_hcompress_frozen_values():
    """These are H-Compress-1's, not new numbers picked today."""
    from deltabt.research import hcompress
    assert v1.PCT_LOOKBACK == hcompress.PCT_LOOKBACK_15M
    assert v1.PERCENTILE == hcompress.PRIMARY["percentile"]
    assert v1.MIN_DURATION == hcompress.PRIMARY["min_duration"]


def test_execution_parameters_are_not_inherited():
    """Stage A has no execution and may not carry execution parameters.

    Checks the namespace, not the source text -- the docstring names the dropped
    parameters deliberately, and a substring search would fail on the very
    explanation of why they are absent.
    """
    from deltabt.research import hcompress
    for banned in ("volume_mult", "range_max_atr", "arm", "target_r",
                   "max_stop_pct", "order_lifetime"):
        assert not hasattr(v1, banned), f"v1 exposes execution parameter {banned}"
    # every H-Compress-1 PRIMARY key that H-VOL-1 keeps must be a STATE key
    kept = {k for k in hcompress.PRIMARY if hasattr(v1, k.upper())}
    assert kept <= {"percentile", "min_duration"}


def test_stage_a_machinery_is_imported_not_reimplemented():
    assert v1.estimate is h2.estimate
    assert v1.control is h2.control
    assert v1.in_split is h2.in_split
    assert v1.day_cluster is h2.day_cluster
    assert v1.HORIZONS_MIN is h2.HORIZONS_MIN
    assert v1.PRIMARY_HORIZON_MIN == h2.PRIMARY_HORIZON_MIN


# ------------------------------------------------------------------ lookahead

def test_compression_state_is_unchanged_by_future_bars():
    df = bars(30000, seed=3)
    full = v1.compression(df)
    for cut in (18000, 24000):
        part = v1.compression(df.iloc[:cut].reset_index(drop=True))
        k = len(part["time"]) - 1
        for key in ("compressed", "zone_high", "zone_low", "zone_ok", "threshold"):
            assert np.array_equal(full[key][:k], part[key][:k], equal_nan=True), key


def test_events_are_never_timed_before_the_bar_that_defines_them():
    df = bars(40000, seed=5)
    ev = v1.events(df, "TEST")
    C = v1.compression(df)
    assert len(ev)
    knowable = C["time"][ev["bar_i"].to_numpy()] + v1.TF_MIN * 60
    assert (ev["t0"].to_numpy() >= knowable).all()


def test_threshold_excludes_the_current_bar():
    """A percentile that included bar t would let the bar help decide whether it
    is itself unusual."""
    df = bars(30000, seed=7)
    C = v1.compression(df)
    spiked = df.copy()
    i = 25000
    spiked.loc[i, "high"] *= 1.5                # a huge bar, far past warmup
    C2 = v1.compression(spiked)
    t = i // v1.TF_MIN
    assert np.isclose(C["threshold"][t], C2["threshold"][t], equal_nan=True)


# ------------------------------------------------------------------ events

def test_up_and_down_breaks_are_mutually_exclusive():
    for seed in (11, 13, 17):
        C = v1.compression(bars(30000, seed=seed))
        F = v1.event_flags(C)
        assert not (F["EXP_UP"] & F["EXP_DOWN"]).any()


def test_a_break_of_both_boundaries_raises_instead_of_being_resolved():
    C = v1.compression(bars(30000, seed=19))
    C["zone_ok"][:] = True
    C["zone_high"] = C["close"] - 1.0          # close is above the high...
    C["zone_low"] = C["close"] + 1.0           # ...and below the low
    with pytest.raises(AssertionError, match="mutually exclusive"):
        v1.event_flags(C)


def test_events_require_a_valid_zone_on_the_previous_bar():
    C = v1.compression(bars(30000, seed=23))
    F = v1.event_flags(C)
    fired = np.flatnonzero(F["EXP_UP"] | F["EXP_DOWN"])
    assert fired.size
    assert C["zone_ok"][fired - 1].all()


def test_oneshot_does_not_re_fire_while_the_break_persists():
    C = v1.compression(bars(30000, seed=29))
    F = v1.event_flags(C)
    for k in v1.EVENTS:
        idx = np.flatnonzero(F[k])
        assert not np.any(np.diff(idx) == 1)


def test_warmup_suppresses_events_before_the_percentile_window_is_full():
    C = v1.compression(bars(30000, seed=31))
    F = v1.event_flags(C)
    for k in v1.EVENTS:
        assert not F[k][:min(C["warmup"], F[k].size)].any()


def test_direction_signs_are_the_breakout_direction():
    assert v1.DIRECTION == {"EXP_UP": 1, "EXP_DOWN": -1}


def test_one_family_only():
    assert list(v1.FAMILIES) == ["V1-EXP"]
    with pytest.raises(ValueError, match="family must be one of"):
        v1.family_frame(pd.DataFrame({"event": ["EXP_UP"]}), "V1-FADE")


# ------------------------------------------------------------------ TEST lock

def test_valid_split_can_never_read_a_test_price():
    ev = pd.DataFrame({"t0": [v1.VALID[1] - 60], "y_1440": [0.01]})
    assert len(v1.in_split(ev, v1.VALID, 1440)) == 0


def test_a_split_reaching_past_the_test_boundary_is_refused():
    ev = pd.DataFrame({"t0": [v1.TRAIN[0]], "y_60": [0.0]})
    with pytest.raises(ValueError, match="locked TEST boundary"):
        v1.in_split(ev, (v1.TRAIN[0], v1.TEST_START + 1), 60)


# ------------------------------------------------------------------ horizons

def test_a_cache_gap_drops_the_event_instead_of_shortening_the_horizon():
    df = bars(40000, seed=37)
    holed = df.drop(df.index[20000:20080]).reset_index(drop=True)
    ev = v1.events(holed, "TEST")
    assert len(ev)
    for h in v1.HORIZONS_MIN:
        got = ev[f"t_{h}"].to_numpy()
        live = got > 0
        assert (got[live] - ev["t0"].to_numpy()[live] == (h - 1) * 60).all()
        assert np.isnan(ev[f"y_{h}"].to_numpy()[~live]).all()

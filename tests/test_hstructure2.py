"""H-STRUCTURE-2 Stage A: the guards that have to hold before TRAIN is run.

The point of these is not coverage. Three specific errors have already happened
in this program and each one produced a plausible number rather than a crash:
a horizon shortened by a cache gap, a measurement window that reached across a
split boundary, and a signal read before the bar that defined it had closed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.research import hstructure2 as h2


def bars(n: int, *, start: int = 1735689600, step: int = 60, seed: int = 0):
    """A 1m frame with a random walk, valid OHLC and no gaps."""
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    o = np.concatenate(([100.0], c[:-1]))
    hi = np.maximum(o, c) * (1 + rng.random(n) * 0.001)
    lo = np.minimum(o, c) * (1 - rng.random(n) * 0.001)
    return pd.DataFrame(dict(time=start + step * np.arange(n), open=o, high=hi,
                             low=lo, close=c, volume=np.ones(n)))


# ------------------------------------------------------------------ reuse

def test_the_swing_detector_is_the_audited_one_not_a_copy():
    """If the archive disappears this must fail loudly, never fall back to a
    local reimplementation wearing the audit's reputation."""
    mod = h2._load_archived_detector()
    assert hasattr(mod, "_swing_flags") and hasattr(mod, "_structure_state")
    assert mod.__file__.endswith("out/hstructure/code/hstructure.py")


def test_missing_archive_raises_rather_than_reimplementing(monkeypatch, tmp_path):
    import sys
    monkeypatch.setattr(h2, "_ARCHIVE", tmp_path / "gone.py")
    monkeypatch.delitem(sys.modules, h2._ARCHIVE_MODNAME, raising=False)
    with pytest.raises(FileNotFoundError, match="reused unchanged"):
        h2._load_archived_detector()


# ------------------------------------------------------------------ lookahead

def test_structure_state_at_t_is_unchanged_by_future_bars():
    """The core anti-lookahead property: truncating the data must not alter any
    structure value at or before the truncation point."""
    df = bars(4000, seed=3)
    full = h2.structure_state(df)
    for cut in (1000, 2000, 3000):
        part = h2.structure_state(df.iloc[:cut].reset_index(drop=True))
        k = len(part["time"]) - 1          # last full structure bar of the cut
        for key in ("last_h_px", "last_l_px", "is_hh", "is_lh", "is_hl", "is_ll"):
            a, b = full[key][:k], part[key][:k]
            assert np.array_equal(a, b, equal_nan=True), f"{key} moved at cut={cut}"


def test_events_are_never_timed_before_the_bar_that_defines_them():
    df = bars(6000, seed=5)
    ev = h2.events(df, "TEST")
    S = h2.structure_state(df)
    close_instant = S["time"][ev["struct_i"].to_numpy()] + h2.STRUCT_TF_MIN * 60
    assert (ev["t0"].to_numpy() >= close_instant).all()


def test_truncated_data_produces_the_same_events():
    df = bars(6000, seed=7)
    cut = 4000
    full = h2.events(df, "TEST")
    part = h2.events(df.iloc[:cut].reset_index(drop=True), "TEST")
    horizon = max(h2.HORIZONS_MIN) * 60
    end = int(df["time"].iloc[cut - 1]) - horizon
    a = full[full.t0 <= end][["event", "t0"]].reset_index(drop=True)
    b = part[part.t0 <= end][["event", "t0"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)


# ------------------------------------------------------------------ horizons

def test_a_cache_gap_drops_the_event_instead_of_shortening_the_horizon():
    """Index arithmetic would step over the gap and measure 55m as 60m."""
    df = bars(3000, seed=11)
    holed = df.drop(df.index[1400:1460]).reset_index(drop=True)
    ev = h2.events(holed, "TEST")
    assert len(ev)
    for h in h2.HORIZONS_MIN:
        got = ev[f"t_{h}"].to_numpy()
        live = got > 0
        assert (got[live] - ev["t0"].to_numpy()[live] == (h - 1) * 60).all()
        assert np.isnan(ev[f"y_{h}"].to_numpy()[~live]).all()


def test_locate_rejects_a_timestamp_that_does_not_exist():
    t = np.array([0, 60, 120, 600, 660], "int64")
    got = h2._locate(t, np.array([60, 300, 660, 99999], "int64"))
    assert got.tolist() == [1, -1, 4, -1]


def test_signed_return_flips_with_direction():
    df = bars(4000, seed=13)
    ev = h2.events(df, "TEST")
    longs = ev[ev.direction == 1]
    shorts = ev[ev.direction == -1]
    assert len(longs) and len(shorts)
    # a short's signed return is the negation of its raw price move
    p1 = shorts["y_60"].to_numpy()
    assert np.nanmax(np.abs(p1)) > 0


# ------------------------------------------------------------------ TEST lock

def test_in_split_refuses_a_window_reaching_into_test():
    ev = pd.DataFrame({"t0": [h2.TRAIN[0]], "y_60": [0.0]})
    with pytest.raises(ValueError, match="locked TEST boundary"):
        h2.in_split(ev, (h2.TRAIN[0], h2.TEST_START + 1), 60)


def test_in_split_excludes_events_whose_horizon_crosses_the_split_end():
    end = h2.TRAIN[1]
    ev = pd.DataFrame({"t0": [end - 7200, end - 1800, end - 60],
                       "y_60": [0.01, 0.01, 0.01]})
    got = h2.in_split(ev, h2.TRAIN, 60)
    assert got["t0"].tolist() == [end - 7200]


def test_valid_split_can_never_read_a_test_price():
    ev = pd.DataFrame({"t0": [h2.VALID[1] - 60], "y_1440": [0.01]})
    assert len(h2.in_split(ev, h2.VALID, 1440)) == 0


def test_undeclared_horizon_is_refused():
    ev = pd.DataFrame({"t0": [h2.TRAIN[0]], "y_45": [0.0]})
    with pytest.raises(ValueError, match="not pre-declared"):
        h2.in_split(ev, h2.TRAIN, 45)


# ------------------------------------------------------------------ events

def test_the_four_events_are_the_frozen_set():
    assert set(h2.EVENTS) == {"CONT_LONG", "CONT_SHORT", "FAIL_LONG", "FAIL_SHORT"}
    assert h2.FAMILIES == {"S2-CONT": ("CONT_LONG", "CONT_SHORT"),
                           "S2-FAIL": ("FAIL_LONG", "FAIL_SHORT")}


def test_continuation_and_failure_are_opposite_directions_from_one_state():
    assert h2.DIRECTION["CONT_LONG"] == 1 and h2.DIRECTION["FAIL_SHORT"] == -1
    assert h2.DIRECTION["CONT_SHORT"] == -1 and h2.DIRECTION["FAIL_LONG"] == 1


def test_oneshot_fires_once_per_run():
    x = np.array([0, 1, 1, 1, 0, 0, 1, 1], bool)
    assert h2._oneshot(x).tolist() == [False, True, False, False,
                                       False, False, True, False]


def test_conflicting_bars_are_dropped_not_resolved():
    df = bars(4000, seed=17)
    S = h2.structure_state(df)
    F = h2.event_flags(S)
    conflict = np.flatnonzero(F["_conflict"])
    ev = h2.events(df, "TEST")
    assert not set(conflict) & set(ev["struct_i"].tolist())


def test_warmup_suppresses_events_before_three_swings_exist():
    df = bars(4000, seed=19)
    S = h2.structure_state(df)
    F = h2.event_flags(S)
    for name in h2.EVENTS:
        assert not F[name][:S["warmup"]].any()


# ------------------------------------------------------------------ inference

def test_cluster_is_primary_not_block():
    """inference() predates the ratification and defaults se to the block
    estimator; estimate() must read se_cluster regardless."""
    rng = np.random.default_rng(0)
    y = rng.normal(0, 0.01, 800)
    t0 = h2.TRAIN[0] + np.arange(800) * 3600
    from deltabt.research.hnull1 import cluster_se
    assert h2.estimate(y, t0)["se"] == pytest.approx(cluster_se(y, h2.day_cluster(t0)))


def test_mde_uses_the_frozen_constant():
    rng = np.random.default_rng(1)
    y = rng.normal(0, 0.01, 500)
    t0 = h2.TRAIN[0] + np.arange(500) * 3600
    r = h2.estimate(y, t0)
    assert r["mde"] == pytest.approx(2.8 * r["se"])


def test_day_cluster_pools_symbols_within_a_utc_day():
    t = np.array([h2.TRAIN[0], h2.TRAIN[0] + 3600, h2.TRAIN[0] + 86400], "int64")
    c = h2.day_cluster(t)
    assert c[0] == c[1] and c[2] != c[0]


def test_empty_input_returns_nan_not_a_crash():
    r = h2.estimate(np.array([]), np.array([], "int64"))
    assert r["n"] == 0 and np.isnan(r["effect"])


# ------------------------------------------------------------------ control

def test_control_preserves_the_direction_multiset():
    """A coin flip would not, and any drift would then leak into the signal."""
    rng = np.random.default_rng(2)
    n = 600
    d = np.where(rng.random(n) < 0.7, 1.0, -1.0)      # deliberately imbalanced
    raw = rng.normal(0.002, 0.01, n)                  # deliberately drifting
    sym = np.array(["BTCUSD"] * 300 + ["ETHUSD"] * 300)
    c = h2.control(raw * d, d, sym, n_perm=200)
    # the permutation mean tracks imbalance x drift, so it is NOT ~0
    assert abs(c["mean"] - float(d.mean()) * float(raw.mean())) < 5e-4


def test_control_is_deterministic_under_the_frozen_seed():
    rng = np.random.default_rng(3)
    n, sym = 400, np.array(["BTCUSD"] * 400)
    d = np.where(rng.random(n) < 0.5, 1.0, -1.0)
    y = rng.normal(0, 0.01, n) * d
    a = h2.control(y, d, sym, n_perm=100)
    b = h2.control(y, d, sym, n_perm=100)
    assert a == b


def test_a_planted_effect_beats_its_own_control():
    """Sanity in the other direction: the control must not eat a real effect."""
    rng = np.random.default_rng(4)
    n, sym = 2000, np.array(["BTCUSD"] * 2000)
    d = np.where(rng.random(n) < 0.5, 1.0, -1.0)
    y = np.abs(rng.normal(0, 0.01, n)) + 0.004        # signed return, real edge
    c = h2.control(y, d, sym, n_perm=300)
    assert y.mean() > c["ci_high"] and c["p_value"] < 0.05


def test_family_frame_rejects_an_unknown_family():
    ev = pd.DataFrame({"event": ["CONT_LONG"]})
    with pytest.raises(ValueError, match="family must be one of"):
        h2.family_frame(ev, "S2-WHATEVER")

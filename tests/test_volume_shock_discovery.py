"""The six leakage controls the discovery gate requires, plus the arithmetic.

Nothing here reads a result. If any of it fails the analysis does not run.

The failure mode that matters most is not a coding slip -- it is that volume
and volatility cluster together, so "high volume predicts big moves" is nearly
true by construction unless the trailing baseline is strictly causal and the
volatility-normalised endpoint is measured alongside. The tests below pin the
causality; the spec's decision rule handles the confound.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import volume_shock_discovery as vs  # noqa: E402


def _series(n: int, vol: float = 100.0, price: float = 100.0,
            start: int = 1_700_000_000) -> pd.DataFrame:
    return pd.DataFrame({
        "time": start + 60 * np.arange(n),
        "open": price, "high": price, "low": price, "close": float(price),
        "volume": float(vol),
    })


# ------------------------------------------------- 1. append-invariance

def test_event_classification_cannot_change_when_future_bars_are_appended():
    """THE core control. Classify on a prefix, then on the full series; the
    prefix's verdicts must be identical."""
    rng = np.random.default_rng(0)
    n = 4000
    d = _series(n)
    d["volume"] = rng.lognormal(4, 0.5, n)
    d.loc[2000, "volume"] = d["volume"].iloc[:2000].median() * 50
    d["close"] = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))

    cut = 2600
    full = vs.events(vs.build(d), "rvol_median", vs.RVOL_THRESHOLD)
    pre = vs.events(vs.build(d.iloc[:cut].copy()), "rvol_median", vs.RVOL_THRESHOLD)
    assert np.array_equal(full[:cut], pre), (
        "an event's classification changed when later bars were added")


def test_future_volume_cannot_influence_an_event():
    """Volume after t explodes; the verdict at t must not move."""
    rng = np.random.default_rng(1)
    n = 3000
    d = _series(n)
    d["volume"] = rng.lognormal(4, 0.5, n)
    a = vs.build(d)["rvol_median"].to_numpy()[:2000]
    d2 = d.copy()
    d2.loc[2000:, "volume"] = 1e9
    b = vs.build(d2)["rvol_median"].to_numpy()[:2000]
    np.testing.assert_allclose(a, b, equal_nan=True)


def test_future_price_cannot_influence_an_event():
    rng = np.random.default_rng(2)
    n = 3000
    d = _series(n)
    d["volume"] = rng.lognormal(4, 0.5, n)
    d["close"] = 100.0
    e1 = vs.events(vs.build(d), "rvol_median", vs.RVOL_THRESHOLD)
    d2 = d.copy()
    d2.loc[2000:, "close"] = 1e6
    e2 = vs.events(vs.build(d2), "rvol_median", vs.RVOL_THRESHOLD)
    assert np.array_equal(e1[:2000], e2[:2000])


def test_the_bars_own_volume_is_not_inside_its_own_baseline():
    """Without the .shift(1) every shock is partly self-referential."""
    n = 2000
    d = _series(n, vol=100.0)
    d.loc[1500, "volume"] = 100_000.0
    b = vs.build(d)
    assert b["trail_med"].iloc[1500] == pytest.approx(100.0)
    assert b["trail_med"].iloc[1501] == pytest.approx(100.0)


# --------------------------------------------- 4/5. outcome timing & end

def test_outcome_starts_strictly_after_the_event():
    n = 200
    d = _series(n)
    d["close"] = 100.0
    d.loc[100:, "close"] = 110.0          # jump lands ON bar 100
    o = vs.outcomes(vs.build(d), horizons=(15,))
    # bar 99 -> bar 114 spans the jump; bar 100 -> 115 does not.
    assert o["r15"].iloc[99] > 0
    assert o["r15"].iloc[100] == pytest.approx(0.0)


def test_events_without_a_complete_outcome_window_are_excluded():
    n = vs.LOOKBACK + 50
    d = _series(n)
    d.loc[n - 10, "volume"] = 1e6
    o = vs.outcomes(vs.build(d))
    assert np.isnan(o["r60"].iloc[n - 10]), "outcome computed past the data end"
    assert np.isnan(o["r30"].iloc[n - 1])


def test_outcome_window_is_never_truncated_to_fit():
    n = 100
    d = _series(n)
    o = vs.outcomes(vs.build(d), horizons=(60,))
    assert o["r60"].iloc[-60:].isna().all()


# ------------------------------------------------------ 6. timestamp gaps

def test_a_timestamp_gap_invalidates_the_window_rather_than_being_spanned():
    n = 3000
    d = _series(n)
    d.loc[1500:, "time"] = d.loc[1500:, "time"] + 3600   # one-hour hole
    b = vs.build(d)
    assert not b["window_ok"].iloc[1501:1501 + vs.LOOKBACK - 1].any(), (
        "a window spanning the gap was accepted")
    assert b["window_ok"].iloc[1501 + vs.LOOKBACK + 5]


def test_the_outcome_requires_the_exact_future_bar_to_exist():
    n = 500
    d = _series(n)
    d = d.drop(index=range(200, 260)).reset_index(drop=True)   # 60 bars missing
    o = vs.outcomes(vs.build(d), horizons=(30,))
    assert np.isnan(o["r30"].iloc[190]), "an outcome jumped across a gap"


# --------------------------------------------------- episodes and baseline

def test_consecutive_shock_bars_are_one_episode():
    n = 3000
    d = _series(n)
    d.loc[2000:2010, "volume"] = 1e6
    ev = vs.events(vs.build(d), "rvol_median", vs.RVOL_THRESHOLD)
    assert ev.sum() == 1, f"{ev.sum()} events from one episode"


def test_the_cooldown_equals_the_longest_horizon_so_windows_cannot_overlap():
    assert vs.COOLDOWN_MIN == max(vs.HORIZONS)


def test_baseline_excludes_the_window_after_an_event():
    n = 3000
    d = _series(n)
    d.loc[2000, "volume"] = 1e6
    b = vs.build(d)
    ev = vs.events(b, "rvol_median", vs.RVOL_THRESHOLD)
    bs = vs.baseline_mask(b, ev)
    assert not bs[2000:2061].any(), "post-event bars leaked into the baseline"
    assert bs[2061]


# ----------------------------------------------------------- construction

def test_rvol_is_a_ratio_to_the_trailing_median():
    n = 2000
    d = _series(n, vol=10.0)
    d.loc[1500, "volume"] = 70.0
    assert vs.build(d)["rvol_median"].iloc[1500] == pytest.approx(7.0)


def test_zero_trailing_median_yields_no_signal_rather_than_infinity():
    n = 2000
    d = _series(n, vol=0.0)
    d.loc[1500, "volume"] = 5.0
    assert np.isnan(vs.build(d)["rvol_median"].iloc[1500])


def test_logvol_z_is_standardised_on_logs():
    rng = np.random.default_rng(3)
    n = 2000
    d = _series(n)
    d["volume"] = rng.lognormal(4, 0.5, n)
    z = vs.build(d)["logvol_z"].dropna()
    assert abs(z.mean()) < 0.5 and 0.5 < z.std() < 2.0


def test_normalised_endpoint_divides_by_trailing_vol_and_sqrt_horizon():
    rng = np.random.default_rng(4)
    n = 3000
    d = _series(n)
    d["volume"] = rng.lognormal(4, 0.5, n)
    d["close"] = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    o = vs.outcomes(vs.build(d))
    i = 2500
    expect = o["r30"].iloc[i] / (o["sigma_trail"].iloc[i] * np.sqrt(30))
    assert o["rn30"].iloc[i] == pytest.approx(expect)


# ------------------------------------------------------------- statistics

def test_permutation_test_returns_a_large_p_when_labels_are_meaningless():
    rng = np.random.default_rng(5)
    y = np.abs(rng.normal(size=4000))
    lab = np.zeros(4000, dtype=bool)
    lab[rng.choice(4000, 60, replace=False)] = True
    p = vs.block_permutation_p(y, lab, vs.BLOCK_MIN, 400)
    assert p > 0.05, f"p={p} on pure noise"


def test_permutation_test_detects_a_planted_effect():
    rng = np.random.default_rng(6)
    y = np.abs(rng.normal(size=4000))
    lab = np.zeros(4000, dtype=bool)
    lab[rng.choice(4000, 120, replace=False)] = True
    y[lab] *= 4.0
    p = vs.block_permutation_p(y, lab, vs.BLOCK_MIN, 400)
    assert p < 0.02, f"p={p} on a 4x planted effect"


def test_frozen_parameters_match_the_written_specification():
    spec = (ROOT / "docs" / "volume_shock_discovery_spec.md").read_text()
    assert "L = 1440" in spec and vs.LOOKBACK == 1440
    assert "rvol_median >= 5.0" in spec and vs.RVOL_THRESHOLD == 5.0
    assert "logvol_z    >= 4.0" in spec and vs.LOGZ_THRESHOLD == 4.0
    assert "PRIMARY HORIZON: 30 minutes" in spec and vs.PRIMARY_HORIZON == 30
    assert vs.HORIZONS == (15, 30, 60)


def test_no_pnl_or_execution_anywhere_in_the_module():
    src = (ROOT / "scripts" / "volume_shock_discovery.py").read_text()
    for banned in ("pnl", "sharpe", "fill", "slippage", "commission",
                   "profit", "equity"):
        assert banned not in src.lower(), f"{banned} appears in a discovery gate"


def test_holding_the_baseline_median_fixed_matches_the_exact_permutation():
    """The efficiency shortcut must not change the answer.

    Exact form recomputes median(y[~lab]) every permutation; the shipped form
    holds it at its point estimate. Compared here on a subsample small enough
    that the exact form is affordable.
    """
    rng = np.random.default_rng(11)
    n = 20_000
    y = np.abs(rng.normal(size=n))
    lab = np.zeros(n, dtype=bool)
    lab[rng.choice(n, 200, replace=False)] = True
    y[lab] *= 1.4

    def exact(n_perm, seed):
        r = np.random.default_rng(seed)
        obs = np.median(y[lab]) / np.median(y[~lab])
        nb = int(np.ceil(n / vs.BLOCK_MIN))
        hits = 0
        for _ in range(n_perm):
            blocks = r.permutation(nb)
            order = np.concatenate([np.arange(b * vs.BLOCK_MIN,
                                              min((b + 1) * vs.BLOCK_MIN, n))
                                    for b in blocks])
            L = np.roll(lab[order], int(r.integers(0, n)))
            if not L.any():
                continue
            if np.median(y[L]) / np.median(y[~L]) >= obs:
                hits += 1
        return (hits + 1) / (n_perm + 1)

    fast = vs.block_permutation_p(y, lab, vs.BLOCK_MIN, 600, seed=99)
    slow = exact(600, 99)
    assert abs(fast - slow) < 0.02, f"fast={fast} exact={slow}"

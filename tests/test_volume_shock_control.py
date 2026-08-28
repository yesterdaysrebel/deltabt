"""Leakage and correctness for the contemporaneous-volatility control.

The one genuinely delicate point: `sigma_contemp` deliberately does NOT shift,
unlike every trailing feature in the discovery module. That is correct here and
would be a leak there, so it is pinned explicitly rather than left to a reader
to reason about.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import volume_shock_control as vc  # noqa: E402
import volume_shock_discovery as vs  # noqa: E402


def _series(n, vol=100.0, start=1_700_000_000):
    return pd.DataFrame({"time": start + 60 * np.arange(n),
                         "open": 100.0, "high": 100.0, "low": 100.0,
                         "close": 100.0, "volume": float(vol)})


# ------------------------------------------------------- sigma_contemp timing

def test_sigma_contemp_uses_nothing_after_the_decision_instant():
    """Everything after bar t explodes; sigma_contemp at t must not move."""
    rng = np.random.default_rng(0)
    n = 2000
    d = _series(n)
    d["close"] = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    a = vc.add_contemp_vol(d)["sigma_contemp"].to_numpy()[:1500]
    d2 = d.copy()
    d2.loc[1500:, "close"] = 1e6
    b = vc.add_contemp_vol(d2)["sigma_contemp"].to_numpy()[:1500]
    np.testing.assert_allclose(a, b, equal_nan=True)


def test_sigma_contemp_includes_the_event_bars_own_return():
    """It conditions on the burst; excluding bar t would defeat the control."""
    n = 100
    d = _series(n)
    d["close"] = 100.0
    d.loc[50, "close"] = 110.0
    s = vc.add_contemp_vol(d, bars=15)["sigma_contemp"]
    assert s.iloc[50] > 0, "bar t's own move is missing from sigma_contemp"
    assert s.iloc[49] == pytest.approx(0.0)


def test_sigma_contemp_spans_exactly_the_declared_window():
    n = 200
    d = _series(n)
    d["close"] = 100.0
    d.loc[100, "close"] = 110.0
    s = vc.add_contemp_vol(d, bars=15)["sigma_contemp"]
    # the jump enters at 100 (its own return) and 101 (the return back), and
    # leaves the 15-bar window afterwards
    assert s.iloc[115] > 0 and s.iloc[116] == pytest.approx(0.0)


def test_the_discovery_features_still_shift_and_the_control_does_not():
    """The asymmetry is intentional; pin it so nobody 'fixes' one of them."""
    src_d = (ROOT / "scripts" / "volume_shock_discovery.py").read_text()
    src_c = (ROOT / "scripts" / "volume_shock_control.py").read_text()
    assert 'rolling(lookback).median().shift(1)' in src_d
    assert 'rolling(bars).std(ddof=1)' in src_c
    assert 'rolling(bars).std(ddof=1).shift' not in src_c


# ------------------------------------------------------------ stratification

def test_deciles_partition_every_usable_bar_exactly_once():
    rng = np.random.default_rng(1)
    sig = np.abs(rng.normal(size=10_000)) + 1e-6
    mask = np.ones(10_000, dtype=bool)
    _, bucket = vc.strata(sig, mask)
    assert set(np.unique(bucket)) <= set(range(vc.N_DECILES))
    counts = np.bincount(bucket, minlength=vc.N_DECILES)
    assert counts.min() > 700 and counts.max() < 1300


def test_strata_edges_are_finite_at_the_extremes():
    sig = np.array([1.0, 2.0, 3.0, 4.0])
    edges, _ = vc.strata(sig, np.ones(4, dtype=bool))
    assert edges[0] == -np.inf and edges[-1] == np.inf


def test_thin_strata_are_reported_but_excluded_from_the_weighted_mean():
    n = 4000
    y = np.ones(n)
    ev = np.zeros(n, dtype=bool)
    bs = np.zeros(n, dtype=bool)
    bucket = np.zeros(n, dtype=int)
    ev[:5] = True                    # 5 shocks in decile 0 -- below the floor
    bs[100:200] = True
    bucket[:200] = 0
    ev[1000:1100] = True             # 100 shocks in decile 1 -- used
    bs[1100:1300] = True
    bucket[1000:1300] = 1
    wr, rows = vc._weighted_ratio(y, ev, bs, bucket)
    used = {r["decile"]: r["used"] for r in rows}
    assert used[0] is False and used[1] is True
    assert np.isfinite(wr)


# ------------------------------------------------------------ the control works

def test_a_pure_volatility_confound_is_killed_by_the_control():
    """THE test that matters. Build data where the shock carries NO information
    beyond contemporaneous volatility, and confirm the control removes it."""
    rng = np.random.default_rng(2)
    n = 60_000
    regime = rng.random(n) < 0.10                      # 10% high-vol bars
    sigma = np.where(regime, 4.0, 1.0)
    y = np.abs(rng.normal(0, sigma))
    # shocks land only in the high-vol regime, adding nothing of their own
    ev = regime & (rng.random(n) < 0.05)
    bs = ~ev
    bucket = regime.astype(int)                        # 2 strata, exact match
    naive = np.median(y[ev]) / np.median(y[bs])
    wr, _ = vc._weighted_ratio(y, ev, bs, bucket)
    assert naive > 2.0, f"the confound was not planted ({naive})"
    assert 0.90 < wr < 1.10, f"the control failed to remove a pure confound ({wr})"


def test_a_genuine_effect_survives_the_control():
    rng = np.random.default_rng(3)
    n = 60_000
    regime = rng.random(n) < 0.10
    sigma = np.where(regime, 4.0, 1.0)
    y = np.abs(rng.normal(0, sigma))
    ev = regime & (rng.random(n) < 0.05)
    y[ev] *= 1.5                                       # real, on top of regime
    bs = ~ev
    bucket = regime.astype(int)
    wr, _ = vc._weighted_ratio(y, ev, bs, bucket)
    assert wr > 1.35, f"a genuine 1.5x effect was destroyed by the control ({wr})"


def test_stratified_permutation_preserves_the_strata():
    """A permutation that ignored strata would recreate the confound."""
    rng = np.random.default_rng(4)
    n = 40_000
    regime = rng.random(n) < 0.10
    y = np.abs(rng.normal(0, np.where(regime, 4.0, 1.0)))
    ev = regime & (rng.random(n) < 0.05)
    bs = ~ev
    bucket = regime.astype(int)
    p = vc.stratified_permutation_p(y, ev, bs, bucket, n_perm=300)
    assert p > 0.05, f"pure confound returned p={p} under a stratified null"


def test_event_definition_is_imported_unchanged_from_discovery():
    src = (ROOT / "scripts" / "volume_shock_control.py").read_text()
    assert "vs.events(d, \"rvol_median\", vs.RVOL_THRESHOLD)" in src
    assert "vs.baseline_mask(d, ev)" in src
    assert "RVOL_THRESHOLD =" not in src, "the control redefines the threshold"


def test_frozen_parameters_match_the_written_specification():
    spec = (ROOT / "docs" / "volume_shock_control_spec.md").read_text()
    assert "[t-14 .. t] )      # 15 bars" in spec and vc.CONTEMP_BARS == 15
    assert "deciles" in spec and vc.N_DECILES == 10
    assert "at least 30 shock events" in spec and vc.MIN_SHOCKS_PER_STRATUM == 30
    assert "KILL TEST, NOT A VALIDATION" in spec


def test_no_pnl_or_execution_in_the_control_module():
    src = (ROOT / "scripts" / "volume_shock_control.py").read_text()
    for banned in ("pnl", "sharpe", "fill", "slippage", "profit", "equity"):
        assert banned not in src.lower()

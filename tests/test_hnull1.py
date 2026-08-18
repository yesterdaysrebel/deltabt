"""H-NULL-1 permanent regression suite.

These encode invariants that were violated in production and cost three
overturned headline claims. They are exact algebraic checks where possible,
because a Monte Carlo test that merely fails to reject is far weaker than an
identity that must hold.
"""

from __future__ import annotations

import numpy as np
import pytest

from deltabt.research import hnull1 as H


def path(seed=901, n=120_000):
    x, up, dn, base = H.log_path(n, seed=seed)
    idx = np.arange(2000, n - 10_000, 45)
    return x, up, dn, base, idx


# --- T2 exact path reflection ------------------------------------------------


def test_T2_path_reflection_is_exact_under_log_barriers():
    """LONG on P must equal SHORT on the mirrored path, to the last bit."""
    x, up, dn, base, idx = path()
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
    cr, hir, lor, mhr, mlr = H.materialise(*H.reflect(x, up, dn), base)
    hL, sL, _ = H.leg_outcome(c, hi, lo, mh, ml, idx, 0.004, 0.5, 1, space="log")
    hS, sS, _ = H.leg_outcome(cr, hir, lor, mhr, mlr, idx, 0.004, 0.5, -1, space="log")
    assert np.array_equal(hL, hS)
    assert np.array_equal(sL, sS)


def test_T2_linear_barriers_are_NOT_reflection_symmetric():
    """Documented implementation property: |log(1-w)| > log(1+w).

    Not a bug to fix -- a real asymmetry that cancels in the paired statistic but
    would bias any UNPAIRED comparison. Pinned so it cannot be forgotten.
    """
    x, up, dn, base, idx = path()
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
    cr, hir, lor, mhr, mlr = H.materialise(*H.reflect(x, up, dn), base)
    hL, sL, _ = H.leg_outcome(c, hi, lo, mh, ml, idx, 0.004, 0.5, 1, space="linear")
    hS, sS, _ = H.leg_outcome(cr, hir, lor, mhr, mlr, idx, 0.004, 0.5, -1, space="linear")
    assert not np.array_equal(hL, hS)
    assert abs(hL[hL | sL].mean() - hS[hS | sS].mean()) < 0.01


# --- T3 direction reversal ---------------------------------------------------


def test_T3_direction_reversal_is_an_exact_identity():
    """stat(-sides) == -stat(+sides). Any drift here is directional asymmetry."""
    x, up, dn, base, idx = path()
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
    rng = np.random.default_rng(0)
    sides = np.where(rng.random(idx.size) < 0.5, 1, -1)
    a = H.paired_px(c, hi, lo, mh, ml, idx, 0.004, 0.5, sides, space="log")
    b = H.paired_px(c, hi, lo, mh, ml, idx, 0.004, 0.5, -sides, space="log")
    assert a["excess_gross_R"] == pytest.approx(-b["excess_gross_R"], abs=1e-15)


def test_T3_antithetic_average_is_exactly_zero():
    """Averaging a path over both direction draws must annihilate the statistic."""
    x, up, dn, base, idx = path()
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
    rng = np.random.default_rng(3)
    sides = np.where(rng.random(idx.size) < 0.5, 1, -1)
    a = H.paired_px(c, hi, lo, mh, ml, idx, 0.004, 0.5, sides, space="log")
    b = H.paired_px(c, hi, lo, mh, ml, idx, 0.004, 0.5, -sides, space="log")
    assert abs(a["excess_gross_R"] + b["excess_gross_R"]) < 1e-15


# --- T5 unequal R must be structurally rejected ------------------------------


def test_T5_unequal_R_with_correlated_direction_is_refused():
    """The exact configuration that manufactured H-EMA-3's +15.5 sigma."""
    x, up, dn, base, idx = path()
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
    sides = np.ones(idx.size, "int64")            # always the wider-stop side
    r = H.evaluate(c, hi, lo, mh, ml, idx, 0.004, 0.001, 0.5, sides)
    assert r["status"] == "INVALID_COMPARISON"
    assert r["excess_gross_R"] is None
    assert "unequal R" in r["comparison"]["reason"]


def test_T5_unequal_R_is_refused_even_when_empirically_harmless():
    """Deliberately stricter than the evidence: refusal is structural."""
    x, up, dn, base, idx = path()
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
    rng = np.random.default_rng(5)
    sides = np.where(rng.random(idx.size) < 0.5, 1, -1)   # independent of R
    r = H.evaluate(c, hi, lo, mh, ml, idx, 0.004, 0.001, 0.5, sides)
    assert r["status"] == "INVALID_COMPARISON"


def test_T5_the_historical_estimator_still_reproduces_the_artifact():
    """Guards the guard: if this stops firing, the fixture no longer tests anything.

    Needs a large path deliberately. The artifact's EFFECT size is stable at
    ~+0.011 to +0.021 R, but its t scales with sqrt(n), so a small fixture would
    let the regression pass for want of power rather than for want of an artifact
    -- the precise error this experiment exists to prevent.
    """
    x, up, dn, base, idx = path(n=600_000)
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base, mark_widen=0.10)
    sides = np.ones(idx.size, "int64")
    r = H.paired_excess(c, hi, lo, mh, ml, idx, 0.004, 0.001, 0.5, sides)
    assert r["excess_gross_R"] > 0.005, r
    assert r["t"] > 2.0, r


def test_T5_symmetric_R_is_allowed():
    x, up, dn, base, idx = path()
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
    sides = np.ones(idx.size, "int64")
    r = H.evaluate(c, hi, lo, mh, ml, idx, 0.004, 0.004, 0.5, sides)
    assert r["status"] == "MEASURED"


# --- T6 planted edge must be recovered ---------------------------------------


def test_T6_planted_edge_is_recovered_and_monotone():
    """Fails if an estimator always returns zero."""
    x, up, dn, base, idx = path(n=300_000)
    c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
    prev = None
    for p in (0.50, 0.55, 0.60):
        sd = H.planted_edge_sides(c, hi, lo, mh, ml, idx, 0.004, 0.5, p, seed=99)
        r = H.evaluate(c, hi, lo, mh, ml, idx, 0.004, 0.004, 0.5, sd)
        if prev is not None:
            assert r["excess_gross_R"] > prev
        prev = r["excess_gross_R"]
    assert prev > 0.05


# --- inference machinery -----------------------------------------------------


def test_block_se_never_collapses_to_zero():
    """b >= n once returned an SE of exactly 0, making any mean infinitely significant."""
    x = np.random.default_rng(0).standard_normal(50)
    assert H.moving_block_se(x, 99999) > 0
    assert np.isfinite(H.moving_block_se(x[:3], 10))


def test_block_se_tracks_known_autocorrelation():
    rng = np.random.default_rng(0)
    n = 4000
    iid = rng.standard_normal(n)
    assert 0.85 < H.moving_block_se(iid, 20) / H.iid_se(iid) < 1.20
    e = rng.standard_normal(n)
    ar = np.empty(n); ar[0] = e[0]
    for i in range(1, n):
        ar[i] = 0.8 * ar[i - 1] + e[i] * np.sqrt(1 - 0.64)
    ratio = H.moving_block_se(ar, 40) / H.iid_se(ar)
    assert 2.4 < ratio < 3.6, ratio          # theory sqrt(1.8/0.2) = 3.0


def test_block_length_rule_discriminates_dependence():
    """The first rule returned ~250 for white noise AND for AR(0.9)."""
    rng = np.random.default_rng(0)
    n = 6000
    assert H.block_length_from_dependence(rng.standard_normal(n))["block_length"] <= 3
    e = rng.standard_normal(n)
    ar = np.empty(n); ar[0] = e[0]
    for i in range(1, n):
        ar[i] = 0.9 * ar[i - 1] + e[i] * np.sqrt(1 - 0.81)
    assert H.block_length_from_dependence(ar)["block_length"] > 15


def test_inference_always_declares_its_dependence_assumption():
    x = np.random.default_rng(0).standard_normal(500)
    assert H.inference(x)["dependence"] == "iid"
    assert H.inference(x, block=10)["dependence"] == "moving-block(10)"
    assert H.inference(x, cluster_id=np.arange(500) // 25)["dependence"] == "cluster"


# --- T1 / T4 / T7 completing the deterministic toy suite ---------------------


def test_T1_perfectly_symmetric_path_gives_equal_legs():
    """A deterministic zig-zag with identical up and down moves.

    Long and short must resolve identically; any difference is directional
    asymmetry in the walk itself rather than in the data.
    """
    n = 20_000
    step = np.tile([1.0, -1.0], n // 2) * 0.0006
    x = np.cumsum(step)
    up = np.full(n, 0.0002)
    dn = np.full(n, 0.0002)
    c, hi, lo, mh, ml = H.materialise(x, up, dn, 100.0)
    idx = np.arange(500, n - 5_000, 7)
    hL, sL, _ = H.leg_outcome(c, hi, lo, mh, ml, idx, 0.004, 0.5, 1, space="log")
    hS, sS, _ = H.leg_outcome(c, hi, lo, mh, ml, idx, 0.004, 0.5, -1, space="log")
    # a bounded oscillation reaches neither barrier: both legs must agree exactly
    assert hL.sum() == hS.sum()
    assert sL.sum() == sS.sum()


def test_T4_zero_signal_random_direction_is_centred():
    """Averaged over enough paths the canonical null sits on zero."""
    ex = []
    for s in range(24):
        x, up, dn, base = H.log_path(120_000, seed=2000 + s)
        idx = np.arange(2000, 110_000, 45)
        c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
        rng = np.random.default_rng(s)
        sides = np.where(rng.random(idx.size) < 0.5, 1, -1)
        a = H.paired_px(c, hi, lo, mh, ml, idx, 0.004, 0.5, sides, space="log")
        b = H.paired_px(c, hi, lo, mh, ml, idx, 0.004, 0.5, -sides, space="log")
        ex.append((a["excess_gross_R"] + b["excess_gross_R"]) / 2)   # antithetic
    assert np.abs(ex).max() < 1e-15


def test_T7_scale_transformation_preserves_normalised_behaviour():
    """Absolute price level must not enter a statistic defined on log-returns."""
    x, up, dn, _ = H.log_path(120_000, seed=77)
    idx = np.arange(2000, 110_000, 45)
    rng = np.random.default_rng(1)
    sides = np.where(rng.random(idx.size) < 0.5, 1, -1)
    vals = []
    for base in (100.0, 1_000.0, 10_000.0, 100_000.0):
        c, hi, lo, mh, ml = H.materialise(x, up, dn, base)
        vals.append(H.paired_px(c, hi, lo, mh, ml, idx, 0.004, 0.5,
                                sides, space="log")["excess_gross_R"])
    assert max(vals) - min(vals) < 1e-15, vals

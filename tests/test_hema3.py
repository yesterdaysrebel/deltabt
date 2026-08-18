"""H-EMA-3 estimator invariants. These gate any barrier result."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.research import hema3
from deltabt.research.hema3 import _barrier_walk, outcomes, paired_statistic


def walk_gbm(n=400_000, seed=0, base=100.0, vol=0.0004):
    """A driftless random walk: the null the estimator must return zero on."""
    rng = np.random.default_rng(seed)
    c = base * np.exp(np.cumsum(rng.standard_normal(n) * vol))
    w = np.abs(rng.standard_normal(n)) * 0.3 * vol * c
    return c, c + w, c - w


def run_walk(entry_idx, stop_pct, side, c, hi, lo, k_max=4.0):
    ent = c[entry_idx]
    stop = ent * (1 - side * stop_pct)
    return _barrier_walk(entry_idx.astype("int64"), ent, stop,
                         np.full(entry_idx.size, side, "int64"),
                         hi, lo, hi, lo, k_max, c.size)


@pytest.mark.parametrize("k", [0.5, 1.0, 2.0, 4.0])
def test_martingale_hit_rate_is_one_over_one_plus_k(k):
    """The null the whole design rests on: P(+kR before -1R) = 1/(1+k)."""
    c, hi, lo = walk_gbm()
    idx = np.arange(1000, 300_000, 50)
    best, stopped = run_walk(idx, 0.004, 1, c, hi, lo)
    hit, res = outcomes(best, stopped, k)
    p = hit[res].mean()
    assert abs(p - 1 / (1 + k)) < 0.03, f"k={k} p={p:.4f} vs {1/(1+k):.4f}"


@pytest.mark.parametrize("stop_pct", [0.002, 0.004, 0.008, 0.016])
def test_null_is_scale_free_in_stop_width(stop_pct):
    """The property that makes the mirror immune to stop-geometry confounds."""
    c, hi, lo = walk_gbm()
    idx = np.arange(1000, 300_000, 50)
    best, stopped = run_walk(idx, stop_pct, 1, c, hi, lo)
    hit, res = outcomes(best, stopped, 1.0)
    assert abs(hit[res].mean() - 0.5) < 0.03, f"stop={stop_pct} p={hit[res].mean():.4f}"


def test_paired_statistic_is_zero_on_random_signals():
    """Random direction on a random walk must show no excess."""
    c, hi, lo = walk_gbm(seed=3)
    idx = np.arange(1000, 300_000, 40)
    rng = np.random.default_rng(11)
    ent = c[idx]
    bets = {"bar": idx, "entry_idx": idx, "entry_time": idx * 60,
            "entry_px": ent, "symbol": "X", "exec_tf": 5,
            "side": np.where(rng.random(idx.size) < 0.5, 1, -1)}
    for tag, side in (("L", 1), ("S", -1)):
        b, s = run_walk(idx, 0.004, side, c, hi, lo)
        bets[f"best_{tag}"], bets[f"stopped_{tag}"] = b, s
    df = pd.DataFrame(bets)
    for k in hema3.BARRIERS:
        r = paired_statistic(df, k)
        assert abs(r["excess_gross_R"]) < 3 * r["se_gross_R"] + 1e-9, (k, r)


def test_a_planted_edge_is_recovered():
    """Guards the reverse failure: an estimator that always returns zero."""
    c, hi, lo = walk_gbm(seed=5)
    idx = np.arange(1000, 300_000, 40)
    b_l, s_l = run_walk(idx, 0.004, 1, c, hi, lo)
    b_s, s_s = run_walk(idx, 0.004, -1, c, hi, lo)
    hit_l, _ = outcomes(b_l, s_l, 1.0)
    # a signal that knows the answer 60% of the time
    rng = np.random.default_rng(7)
    side = np.where(hit_l, 1, -1)
    flip = rng.random(idx.size) < 0.40
    side = np.where(flip, -side, side)
    df = pd.DataFrame({"bar": idx, "entry_idx": idx, "entry_time": idx * 60,
                       "entry_px": c[idx], "symbol": "X", "exec_tf": 5,
                       "side": side, "best_L": b_l, "stopped_L": s_l,
                       "best_S": b_s, "stopped_S": s_s})
    r = paired_statistic(df, 1.0)
    assert r["t"] > 5, r
    assert r["excess_gross_R"] > 0.05, r


def test_walk_never_reads_past_the_boundary():
    c, hi, lo = walk_gbm(seed=9)
    idx = np.array([1000, 2000, 3000], dtype="int64")
    ent = c[idx]
    stop = ent * 0.5           # so far away it can never trigger
    n_keep = 3500
    best, stopped = _barrier_walk(idx, ent, stop, np.ones(3, "int64"),
                                  hi, lo, hi, lo, 1e9, n_keep)
    assert not stopped.any()
    # excursion can only reflect bars strictly before n_keep
    for a, j in enumerate(idx):
        reachable = (hi[j:n_keep].max() - ent[a]) / (ent[a] - stop[a])
        assert best[a] <= reachable + 1e-12


def test_same_bar_resolves_to_the_stop():
    """A bar touching both barrier and stop must not count as a hit."""
    n = 50
    c = np.full(n, 100.0)
    hi = np.full(n, 100.0); lo = np.full(n, 100.0)
    hi[5] = 130.0      # would clear +2R
    lo[5] = 90.0       # but also takes out the stop
    idx = np.array([4], dtype="int64")
    best, stopped = _barrier_walk(idx, np.array([100.0]), np.array([95.0]),
                                  np.ones(1, "int64"), hi, lo, hi, lo, 4.0, n)
    assert stopped[0]
    assert best[0] == 0.0, best[0]


def test_unresolved_bets_are_excluded_not_counted_as_misses():
    c, hi, lo = walk_gbm(seed=13)
    idx = np.arange(1000, 20_000, 100)
    ent = c[idx]
    stop = ent * 0.5
    best, stopped = _barrier_walk(idx.astype("int64"), ent, stop,
                                  np.ones(idx.size, "int64"), hi, lo, hi, lo,
                                  4.0, 20_500)
    df = pd.DataFrame({"bar": idx, "entry_idx": idx, "entry_time": idx * 60,
                       "entry_px": ent, "symbol": "X", "exec_tf": 5,
                       "side": np.ones(idx.size, "int64"),
                       "best_L": best, "stopped_L": stopped,
                       "best_S": best, "stopped_S": stopped})
    r = paired_statistic(df, 4.0)
    assert r["unresolved"] > 0
    assert r["n"] + r["unresolved"] == len(df)

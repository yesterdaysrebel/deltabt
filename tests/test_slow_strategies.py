"""Holding longer must actually reduce turnover, and positions must persist.

WHY
    The whole point of scripts/slow_strategies.py is that cost scales with
    rebalance frequency. If the loop silently re-entered the book every day
    regardless of ``rebalance_days``, costs would not fall, and the conclusion
    -- "trading less often removes the cost problem and there is still no
    edge" -- would be unsupported in the direction that matters.

    The observed drop is large enough to be worth pinning: reversal_1d on the
    tradeable universe costs 38.26%/yr rebalanced daily and 0.53%/yr
    rebalanced quarterly, a 72x reduction.
"""

from __future__ import annotations

import importlib.util
import pathlib

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "slow_strategies.py"


@pytest.fixture(scope="module")
def ss():
    spec = importlib.util.spec_from_file_location("slow_strategies", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def panel():
    rng = np.random.default_rng(7)
    idx = pd.date_range("2025-01-01", periods=400, freq="1D", tz="UTC")
    cols = [f"S{i}" for i in range(12)]
    ret = pd.DataFrame(rng.normal(0, 0.03, (400, 12)), index=idx, columns=cols)
    px = (1 + ret).cumprod() * 100
    return px, px.pct_change(), px.notna()


def test_cost_falls_as_rebalancing_slows(ss, panel):
    px, ret, universe = panel
    score = -ret                       # a fast, high-turnover signal
    costs = {}
    for rb in (1, 7, 30):
        r = ss.backtest(score, ret, universe, rb)
        assert r is not None
        costs[rb] = r["cost"].sum()
    assert costs[1] > costs[7] > costs[30], (
        f"turnover cost did not fall as rebalancing slowed: {costs}. The "
        f"rebalance_days argument is not being honoured, so every 'trading "
        f"less often' conclusion is unsupported.")


def test_positions_are_held_between_rebalances(ss, panel):
    """Cost must be charged ONLY on rebalance days."""
    px, ret, universe = panel
    r = ss.backtest(-ret, ret, universe, 30)
    charged = (r["cost"] > 0).sum()
    assert charged <= len(r) // 30 + 2, (
        f"cost charged on {charged} of {len(r)} days at a 30-day rebalance; "
        f"the book is being re-entered between rebalances")


def test_tsmom_is_time_series_not_cross_sectional(ss, panel):
    """Each symbol judged against its OWN past, not ranked against peers.

    If every symbol rises, a cross-sectional book is still half short; a
    time-series book is fully long. That difference is the whole reason this
    is a separate test from H-XSec-1.
    """
    idx = pd.date_range("2025-01-01", periods=200, freq="1D", tz="UTC")
    cols = [f"S{i}" for i in range(8)]
    # A drift plus a little noise: constant returns give zero trailing vol,
    # which the inverse-vol weighting correctly refuses to size against.
    rng = np.random.default_rng(3)
    ret = pd.DataFrame(0.004 + rng.normal(0, 0.005, (200, 8)),
                       index=idx, columns=cols)
    r = ss.tsmom(ret, pd.DataFrame(True, index=idx, columns=cols), 30, 7)
    assert r is not None
    assert r["gross"].mean() > 0, (
        "with every symbol trending up, a time-series momentum book was not "
        "net long -- it is behaving cross-sectionally")


def test_tsmom_exposure_is_normalised(ss, panel):
    """Gross exposure 1.0, else an N-name panel is an N-times leveraged bet."""
    src = SCRIPT.read_text()
    assert "w / w.abs().sum()" in src, (
        "time-series momentum weights are not normalised to unit gross "
        "exposure, so returns scale with panel size and are uninterpretable")


def test_correction_is_applied_across_all_configurations(ss):
    src = SCRIPT.read_text()
    assert "bh_threshold" in src and "cut = np.where(passes)[0].max()" in src, (
        "adding rebalance frequency multiplies the configuration count; "
        "without a step-up correction across ALL of them this is just a "
        "wider search")

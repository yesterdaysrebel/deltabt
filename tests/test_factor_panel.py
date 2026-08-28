"""The panel's two load-bearing pieces: no look-ahead, and honest correction.

WHY
    scripts/factor_panel.py reports that short-term reversal has a GROSS
    t-statistic of 4.01 on the illiquid half and is then removed entirely by
    41%/yr of rebalancing cost. Both halves of that sentence can be faked:

    * a missing ``.shift(1)`` would rank symbols on the same day's return they
      are about to earn, which manufactures exactly a large reversal t-stat;
    * a Benjamini-Hochberg step-up applied as a naive elementwise comparison
      lets an isolated small p-value through even when the ones ranked above it
      failed, which is how a 32-test sweep quietly produces a "finding".
"""

from __future__ import annotations

import importlib.util
import pathlib

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "factor_panel.py"


@pytest.fixture(scope="module")
def fp():
    spec = importlib.util.spec_from_file_location("factor_panel", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scores_are_shifted_before_ranking(fp):
    """Day t's portfolio must be built from data strictly before day t."""
    src = SCRIPT.read_text()
    assert "score.shift(1).where(universe.shift(1))" in src, (
        "the factor score is not shifted before ranking, so the book is "
        "chosen using the very return it is about to collect -- which alone "
        "produces a large apparent reversal effect")


def test_a_perfect_lookahead_signal_would_be_caught(fp):
    """Sanity: ranking on TODAY's return must beat ranking on yesterday's.

    If it does not, the shift is not doing anything and the test above is
    checking a string that has no effect.
    """
    rng = np.random.default_rng(0)
    idx = pd.date_range("2025-01-01", periods=200, freq="1D", tz="UTC")
    cols = [f"S{i}" for i in range(9)]
    ret = pd.DataFrame(rng.normal(0, 0.03, (200, 9)), index=idx, columns=cols)
    px = (1 + ret).cumprod() * 100
    ret = px.pct_change()
    universe = px.notna()

    cheat = fp.backtest(ret.shift(-1), ret, universe)   # sees tomorrow
    honest = fp.backtest(ret, ret, universe)            # sees yesterday
    assert cheat is not None and honest is not None
    assert cheat["gross"].mean() > honest["gross"].mean(), (
        "a signal that can see tomorrow did not beat one that cannot, so "
        "backtest() is not using the scores it is handed")


def test_costs_are_charged_on_turnover_not_on_notional(fp):
    src = SCRIPT.read_text()
    assert 'w.subtract(prev, fill_value=0.0).abs().sum()' in src, (
        "turnover is not computed as the change in weights, so an unchanged "
        "position is being charged as if it were re-entered daily")
    assert fp.LEG_COST == pytest.approx(0.00079)


def test_gross_and_net_are_reported_separately(fp):
    """A factor eaten by costs is a different finding from an absent one."""
    src = SCRIPT.read_text()
    for field in ("gross_ann", "cost_ann", "gross_t"):
        assert field in src, f"{field} is not reported"


def test_benjamini_hochberg_is_a_step_up_not_elementwise(fp):
    """Once a rank fails, everything below it must fail too."""
    src = SCRIPT.read_text()
    assert "cut = np.where(d[\"survives_bh\"].values)[0].max()" in src, (
        "BH is applied elementwise, so an isolated small p-value deep in the "
        "ranking can be reported as surviving when the procedure says it does "
        "not")


def test_terciles_not_deciles(fp):
    """With ~30 tradeable names a decile is three coins, not a portfolio."""
    assert fp.FRACTION == pytest.approx(1.0 / 3.0)

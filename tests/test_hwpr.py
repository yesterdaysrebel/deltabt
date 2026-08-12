"""§19 look-ahead audit for H-WPR-1. These gate any positive result."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.costs import SymbolCosts
from deltabt.research import hwpr
from deltabt.research.hwpr import (
    ADX_PERIOD,
    DI_PERIOD,
    WPR_PERIOD,
    _confirmed_5m,
    _leg_extreme,
    arm_signals,
    build_conditions,
)

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)
WARM = max(WPR_PERIOD, DI_PERIOD + 2 * ADX_PERIOD) + 50


def make_1m(n=40_000, seed=0, base=60_000.0) -> pd.DataFrame:
    """Trending/ranging alternation so the trend stack actually fires."""
    rng = np.random.default_rng(seed)
    drift = np.where((np.arange(n) // 2000) % 2 == 0, 0.00004, -0.00003)
    r = drift + rng.standard_normal(n) * 0.0003
    c = base * np.exp(np.cumsum(r))
    w = np.abs(rng.standard_normal(n)) * 0.0004 * c
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 60,
        "open": np.concatenate(([base], c[:-1])),
        "high": c + w, "low": c - w, "close": c,
        "volume": rng.random(n) * 100 + 10,
    })


@pytest.fixture(scope="module")
def df():
    return make_1m()


@pytest.fixture(scope="module")
def cond(df):
    return build_conditions(df)


@pytest.fixture(scope="module")
def trades(df, cond):
    r = hwpr.run(df, None, pd.DataFrame(), BTC, cond, arm="A", start=0)
    t = r.to_frame()
    if t.empty:
        pytest.skip("no trades on the synthetic series")
    return t


def test_baseline_generates_trades(trades):
    """Guards every assertion below from passing vacuously."""
    assert len(trades) >= 20


# --- 5m confirmation may not leak into the 1m bar it sits inside -----------


class TestFiveMinuteAlignment:
    def test_uses_only_closed_5m_bars(self):
        t5 = np.array([0, 300, 600, 900], dtype="int64")
        v5 = np.array([10.0, 20.0, 30.0, 40.0])
        t1 = np.arange(0, 1200, 60, dtype="int64")
        out = _confirmed_5m(v5, t5, t1)
        assert np.all(np.isnan(out[0:5])), "nothing has closed before t=300"
        assert np.all(out[5:10] == 10.0), "the 0-300 bar, first usable at 300"
        assert np.all(out[10:15] == 20.0)

    def test_current_forming_5m_bar_never_appears(self):
        t5 = np.array([0, 300, 600], dtype="int64")
        v5 = np.array([10.0, 20.0, 30.0])
        t1 = np.arange(0, 900, 60, dtype="int64")
        out = _confirmed_5m(v5, t5, t1)
        assert 30.0 not in set(out[np.isfinite(out)].tolist())


# --- structural stop uses no future bar ------------------------------------


class TestStructuralStop:
    def test_leg_extreme_resets_on_flip_and_is_causal(self):
        high = np.array([10, 11, 12, 20, 13, 14], dtype="float64")
        low = np.array([9, 8, 7, 15, 12, 11], dtype="float64")
        d = np.array([-1, -1, -1, 1, 1, 1], dtype="float64")
        lo, hi = _leg_extreme(high, low, d)
        assert lo[2] == 7.0, "running low of the bullish leg"
        assert lo[3] == 15.0, "resets on the flip; must not see the earlier 7"
        assert hi[5] == 20.0

    def test_future_bars_cannot_change_a_past_leg_extreme(self):
        rng = np.random.default_rng(0)
        n = 500
        high = np.cumsum(rng.standard_normal(n)) + 100
        low = high - 1
        d = np.where((np.arange(n) // 50) % 2 == 0, -1.0, 1.0)
        a_lo, a_hi = _leg_extreme(high, low, d)
        h2, l2 = high.copy(), low.copy()
        h2[300:] += 500; l2[300:] -= 500
        b_lo, b_hi = _leg_extreme(h2, l2, d)
        assert np.allclose(a_lo[:300], b_lo[:300], equal_nan=True)
        assert np.allclose(a_hi[:300], b_hi[:300], equal_nan=True)

    def test_stop_is_on_the_correct_side_of_entry(self, trades):
        longs = trades[trades.side > 0]
        shorts = trades[trades.side < 0]
        if len(longs):
            assert (longs.stop_price < longs.entry_price).all()
            assert (longs.target_price > longs.entry_price).all()
        if len(shorts):
            assert (shorts.stop_price > shorts.entry_price).all()
            assert (shorts.target_price < shorts.entry_price).all()

    def test_r_multiple_geometry(self, trades):
        tg = trades[trades.exit_reason == "target"]
        st = trades[trades.exit_reason == "stop"]
        if len(tg):
            assert np.allclose(tg.r_gross, 2.0, atol=1e-9)
        if len(st):
            assert np.allclose(st.r_gross, -1.0, atol=1e-9)


# --- entry timing -----------------------------------------------------------


class TestEntryTiming:
    def test_entry_is_the_next_1m_bar(self, trades):
        assert (trades.entry_time - trades.signal_time == 60).all()

    def test_exit_at_or_after_entry(self, trades):
        assert (trades.exit_time >= trades.entry_time).all()

    def test_one_position_at_a_time(self, trades):
        t = trades.sort_values("entry_time")
        assert (t.entry_time.to_numpy()[1:] > t.exit_time.to_numpy()[:-1]).all()


# --- perturbation: future data cannot change earlier decisions --------------


class TestPerturbation:
    def test_future_bars_leave_earlier_signals_identical(self, df):
        a = build_conditions(df)
        lo_a, sh_a = arm_signals(a, "A")
        cut = len(df) * 2 // 3
        d2 = df.copy()
        rng = np.random.default_rng(7)
        for c in ("open", "high", "low", "close"):
            d2.loc[cut:, c] = d2.loc[cut:, c] * (1 + rng.standard_normal(len(df) - cut) * 0.1)
        d2.loc[cut:, "volume"] *= 40
        b = build_conditions(d2)
        lo_b, sh_b = arm_signals(b, "A")
        # allow the 5m bar straddling the cut to differ
        edge = cut - 10
        assert np.array_equal(lo_a[:edge], lo_b[:edge])
        assert np.array_equal(sh_a[:edge], sh_b[:edge])

    def test_future_bars_leave_earlier_trades_identical(self, df):
        c1 = build_conditions(df)
        t1 = hwpr.run(df, None, pd.DataFrame(), BTC, c1, arm="A", start=0).to_frame()
        if t1.empty:
            pytest.skip("no trades")
        cut_t = int(t1.entry_time.quantile(0.6))
        d2 = df.copy()
        m = d2.time >= cut_t
        for c in ("open", "high", "low", "close"):
            d2.loc[m, c] *= 1.3
        c2 = build_conditions(d2)
        t2 = hwpr.run(d2, None, pd.DataFrame(), BTC, c2, arm="A", start=0).to_frame()
        a = t1[t1.exit_time < cut_t - 600]
        b = t2[t2.exit_time < cut_t - 600]
        assert len(a) > 0
        assert a.entry_time.tolist() == b.entry_time.tolist()
        assert np.allclose(a.r_gross.to_numpy(), b.r_gross.to_numpy())


# --- arm composition is what it claims to be --------------------------------


class TestArms:
    def test_arm_b_removes_only_the_wpr_condition(self, cond):
        a_lo, _ = arm_signals(cond, "A")
        b_lo, _ = arm_signals(cond, "B")
        assert b_lo.sum() >= a_lo.sum()
        assert np.all(b_lo[a_lo]), "every Arm A signal must survive into Arm B"

    def test_arm_e_is_the_loosest(self, cond):
        counts = {a: arm_signals(cond, a)[0].sum() for a in hwpr.ARMS}
        assert counts["E"] >= counts["A"]
        assert counts["B"] >= counts["A"]

    def test_no_pullback_condition_in_the_baseline(self, cond):
        """The frozen rule must NOT require prior oversold, unlike the old one."""
        a_lo, _ = arm_signals(cond, "A")
        p_lo, _ = arm_signals(cond, "PULLBACK")
        assert a_lo.sum() > p_lo.sum() * 5, (
            "removing the pullback must materially increase signal count"
        )

    def test_wpr_variants_differ(self, cond):
        counts = {v: arm_signals(cond, "A", v)[0].sum() for v in hwpr.WPR_VARIANTS}
        assert len(set(counts.values())) > 1, counts

    def test_warmup_blanks_early_bars(self, cond):
        lo, sh = arm_signals(cond, "A")
        assert not lo[:cond["warmup"]].any()
        assert not sh[:cond["warmup"]].any()


# --- direction sanity -------------------------------------------------------


def test_long_signals_require_bullish_regimes(cond):
    lo, _ = arm_signals(cond, "A")
    idx = np.flatnonzero(lo)
    if idx.size == 0:
        pytest.skip("no long signals")
    assert cond["st1_long"][idx].all()
    assert cond["f5_long"][idx].all()
    assert cond["adx1_long"][idx].all()
    assert (cond["wpr"][idx] > -80.0).all()


def test_end_boundary_locks_the_test_split(df, cond):
    full = hwpr.run(df, None, pd.DataFrame(), BTC, cond, arm="A", start=0).to_frame()
    if full.empty:
        pytest.skip("no trades")
    cut = int(full.entry_time.quantile(0.5))
    t = hwpr.run(df, None, pd.DataFrame(), BTC, cond, arm="A", start=0, end=cut).to_frame()
    assert len(t) > 0
    assert t.signal_time.max() <= cut


def test_cost_components_reconcile(trades):
    assert np.allclose(trades.cost_r, trades.fee_r + trades.slip_r + trades.funding_r)
    assert np.allclose(trades.r_net, trades.r_gross - trades.cost_r)
    assert (trades.fee_r > 0).all() and (trades.slip_r > 0).all()

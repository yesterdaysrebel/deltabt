"""The nine pre-registered look-ahead proofs for H-Pair, plus reconciliation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.costs import SymbolCosts
from deltabt.research import hpair as hp
from deltabt.research.hpair import build_panel, rolling_beta, signals

MET = dict(tick_size=0.01, contract_value=0.001, maker_fee=0.0001,
           taker_fee=0.0001, max_leverage=100.0, position_size_limit=52_000,
           funding_interval_seconds=14400, slippage_bps=1.0)
XAUT = SymbolCosts(symbol="XAUTUSD", **MET)
PAXG = SymbolCosts(symbol="PAXGUSD", **{**MET, "position_size_limit": 200_000})

HOURS = (hp.HEDGE_LOOKBACK_D + hp.Z_LOOKBACK_D) * 24 + 900


def make_pair(hours=HOURS, seed=0, base=4400.0, coint=0.9):
    """Two 1m series sharing a common factor plus a mean-reverting spread."""
    n = hours * 60
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.standard_normal(n) * 0.00008)
    # OU spread
    sp = np.empty(n); sp[0] = 0.0
    for i in range(1, n):
        sp[i] = coint * sp[i - 1] + rng.standard_normal() * 0.00004
    lx = np.log(base) + common + sp / 2
    lp = np.log(base) + common - sp / 2

    def frame(lg, s):
        c = np.exp(lg)
        w = np.abs(rng.standard_normal(n)) * 0.00005 * c
        return pd.DataFrame({
            "time": np.arange(n, dtype="int64") * 60,
            "open": np.concatenate(([c[0]], c[:-1])),
            "high": c + w, "low": c - w, "close": c,
            "volume": rng.random(n) * 50 + 5,
        })
    return frame(lx, 0), frame(lp, 1)


def make_funding(hours=HOURS, seed=0):
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(hours) * 0.01
    return pd.DataFrame({"time": np.arange(hours, dtype="int64") * 3600,
                         "open": v, "high": v, "low": v, "close": v,
                         "volume": np.zeros(hours)})


@pytest.fixture(scope="module")
def pair():
    return make_pair()


@pytest.fixture(scope="module")
def trades(pair):
    x, p = pair
    r = hp.run(x, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
               start=0, exec_model="taker/taker", **hp.PRIMARY)
    t = r.to_frame()
    if t.empty:
        pytest.skip("no pair trades on the synthetic series")
    return t


def test_generator_produces_trades(trades):
    assert len(trades) >= 5


# --- 1. hedge ratio is causal ------------------------------------------


class TestHedgeRatio:
    def test_uses_only_prior_data(self):
        y = np.arange(200, dtype="float64")
        x = np.arange(200, dtype="float64") * 2
        b = rolling_beta(y, x, 50)
        assert np.all(np.isnan(b[:50]))
        assert b[100] == pytest.approx(0.5, rel=1e-6)

    def test_future_mutation_cannot_change_past_beta(self):
        rng = np.random.default_rng(0)
        x = np.cumsum(rng.standard_normal(600))
        y = 0.9 * x + rng.standard_normal(600) * 0.1
        a = rolling_beta(y, x, 100)
        y2 = y.copy(); y2[400:] += 50
        b = rolling_beta(y2, x, 100)
        assert np.allclose(a[:400], b[:400], equal_nan=True)


# --- 2. rolling mean/std causal ----------------------------------------


def test_zscore_is_causal(pair):
    x, p = pair
    xh, ph = build_panel(x, p, 0, None)
    _, _, z = signals(xh, ph)
    xh2 = xh.copy()
    cut = len(xh2) * 2 // 3
    xh2.loc[cut:, ["open", "high", "low", "close"]] *= 1.3
    _, _, z2 = signals(xh2, ph)
    assert np.allclose(z[:cut], z2[:cut], equal_nan=True)


# --- 3, 4. entry after signal, exit after entry ------------------------


class TestOrdering:
    def test_entry_strictly_after_signal(self, trades):
        assert (trades.entry_time > trades.signal_time).all()

    def test_no_same_bar_entry(self, trades):
        assert (trades.entry_time - trades.signal_time >= 3600).all()

    def test_exit_after_entry(self, trades):
        assert (trades.exit_time > trades.entry_time).all()

    def test_hold_respects_the_cap(self, trades):
        assert (trades.hold_hours <= hp.PRIMARY["max_hold_h"]).all()


# --- 5, 6, 9. mutation cannot change earlier signals -------------------


def _entries(x, p, **kw):
    r = hp.run(x, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
               start=0, exec_model="taker/taker", **kw)
    t = r.to_frame()
    return [] if t.empty else sorted(t.entry_time.tolist())


class TestPerturbation:
    def test_future_xaut_cannot_change_earlier_signals(self, pair):
        x, p = pair
        a = _entries(x, p, **hp.PRIMARY)
        if not a:
            pytest.skip("no trades")
        cut = a[len(a) * 2 // 3]
        x2 = x.copy(); m = x2.time >= cut
        for c in ("open", "high", "low", "close"):
            x2.loc[m, c] *= 1.25
        b = _entries(x2, p, **hp.PRIMARY)
        assert [e for e in a if e < cut] == [e for e in b if e < cut]

    def test_future_paxg_cannot_change_earlier_signals(self, pair):
        x, p = pair
        a = _entries(x, p, **hp.PRIMARY)
        if not a:
            pytest.skip("no trades")
        cut = a[len(a) * 2 // 3]
        p2 = p.copy(); m = p2.time >= cut
        for c in ("open", "high", "low", "close"):
            p2.loc[m, c] *= 1.25
        b = _entries(x, p2, **hp.PRIMARY)
        assert [e for e in a if e < cut] == [e for e in b if e < cut]

    def test_mutating_everything_after_cutoff_leaves_prior_signals_identical(self, pair):
        x, p = pair
        a = _entries(x, p, **hp.PRIMARY)
        if not a:
            pytest.skip("no trades")
        cut = a[len(a) // 2]
        x2, p2 = x.copy(), p.copy()
        for d in (x2, p2):
            m = d.time >= cut
            for c in ("open", "high", "low", "close"):
                d.loc[m, c] *= 1.4
            d.loc[m, "volume"] *= 10
        b = _entries(x2, p2, **hp.PRIMARY)
        assert [e for e in a if e < cut] == [e for e in b if e < cut]


# --- 7. future funding cannot affect entry -----------------------------


def test_future_funding_cannot_change_entries(pair):
    x, p = pair
    f1, f2 = make_funding(), make_funding(seed=2)
    a = hp.run(x, p, f1, f2, XAUT, PAXG, start=0,
               exec_model="taker/taker", **hp.PRIMARY).to_frame()
    if a.empty:
        pytest.skip("no trades")
    cut = int(a.entry_time.quantile(0.5))
    g1 = f1.copy(); g1.loc[g1.time >= cut, ["open", "high", "low", "close"]] = 9.0
    b = hp.run(x, p, g1, f2, XAUT, PAXG, start=0,
               exec_model="taker/taker", **hp.PRIMARY).to_frame()
    assert (sorted(a[a.entry_time < cut].entry_time)
            == sorted(b[b.entry_time < cut].entry_time))


# --- 8. fill assumptions cannot use future liquidity -------------------


def test_maker_fill_uses_only_the_entry_bar(pair):
    """A maker fill must depend on the entry bar's own range, not later bars."""
    x, p = pair
    a = hp.run(x, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
               start=0, exec_model="maker/maker", **hp.PRIMARY).to_frame()
    if a.empty:
        pytest.skip("no maker trades")
    cut = int(a.entry_time.quantile(0.5))
    x2 = x.copy(); m = x2.time > cut + 7200
    x2.loc[m, "volume"] *= 100
    for c in ("high",):
        x2.loc[m, c] *= 1.2
    b = hp.run(x2, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
               start=0, exec_model="maker/maker", **hp.PRIMARY).to_frame()
    assert (sorted(a[a.entry_time <= cut].entry_time)
            == sorted(b[b.entry_time <= cut].entry_time))


# --- reconciliation -----------------------------------------------------


class TestReconciliation:
    def test_components_reconcile_exactly(self, trades):
        gross = trades.xaut_ret_bps + trades.paxg_ret_bps
        assert np.allclose(gross, trades.gross_bps)
        cost = (trades.xaut_fee_bps + trades.paxg_fee_bps
                + trades.xaut_slip_bps + trades.paxg_slip_bps + trades.legging_bps)
        assert np.allclose(cost, trades.total_cost_bps)
        net = (trades.gross_bps + trades.xaut_funding_bps
               + trades.paxg_funding_bps - trades.total_cost_bps)
        assert np.allclose(net, trades.net_bps)

    def test_costs_never_negative(self, trades):
        for c in ("xaut_fee_bps", "paxg_fee_bps", "xaut_slip_bps",
                  "paxg_slip_bps", "legging_bps", "total_cost_bps"):
            assert (trades[c] >= 0).all(), c

    def test_direction_matches_the_z_sign(self, trades):
        # z >= +entry means the spread is rich -> short XAUT (side -1)
        assert (trades.loc[trades.z_entry > 0, "side"] == -1).all()
        assert (trades.loc[trades.z_entry < 0, "side"] == +1).all()

    def test_taker_model_never_legs(self, pair):
        x, p = pair
        r = hp.run(x, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
                   start=0, exec_model="taker/taker", **hp.PRIMARY)
        assert r.legged == 0

    def test_lower_fill_probability_reduces_trades(self, pair):
        x, p = pair
        hi = hp.run(x, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
                    start=0, exec_model="maker/maker", maker_fill_prob=1.0,
                    seed=1, **hp.PRIMARY)
        lo = hp.run(x, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
                    start=0, exec_model="maker/maker", maker_fill_prob=0.3,
                    seed=1, **hp.PRIMARY)
        assert lo.legged >= hi.legged or len(lo.trades) <= len(hi.trades)


def test_end_boundary_locks_the_test_split(pair):
    x, p = pair
    full = hp.run(x, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
                  start=0, exec_model="taker/taker", **hp.PRIMARY).to_frame()
    if full.empty:
        pytest.skip("no trades")
    cut = int(full.entry_time.quantile(0.5))
    t = hp.run(x, p, make_funding(), make_funding(seed=2), XAUT, PAXG,
               start=0, end=cut, exec_model="taker/taker", **hp.PRIMARY).to_frame()
    if not t.empty:
        assert t.exit_time.max() <= cut

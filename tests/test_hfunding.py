"""Look-ahead and convention proofs for H-Funding.

The seven pre-registered leakage tests, plus sign-convention tests with
hand-calculated funding cash flows. If any of these fail, no H-Funding result
stands.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.costs import SymbolCosts
from deltabt.research import hfunding as hf
from deltabt.research.hfunding import (
    MIN_PCTL_OBS,
    expanding_percentile,
    funding_rank,
)

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)


def make_price(hours=4000, seed=0, base=60_000.0) -> pd.DataFrame:
    """1m bars spanning `hours` hours."""
    n = hours * 60
    rng = np.random.default_rng(seed)
    c = base * np.exp(np.cumsum(rng.standard_normal(n) * 0.0002))
    w = np.abs(rng.standard_normal(n)) * 0.0003 * c
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 60,
        "open": np.concatenate(([base], c[:-1])),
        "high": c + w, "low": c - w, "close": c,
        "volume": rng.random(n) * 100 + 10,
    })


def make_funding(hours=4000, seed=0, scale=0.02, phi=0.9) -> pd.DataFrame:
    """Persistent (AR(1)) funding, matching the measured property of real data.

    White-noise funding would make the carry tests meaningless: a rate that is
    extreme at the signal reverts to ~0 before the first settlement, so no
    position could ever collect the carry it was opened for. Measured lag-1h
    autocorrelation on Delta is 0.75-0.96, hence phi=0.9 here.
    """
    rng = np.random.default_rng(seed)
    v = np.empty(hours)
    v[0] = rng.standard_normal() * scale
    for i in range(1, hours):
        v[i] = phi * v[i - 1] + rng.standard_normal() * scale * np.sqrt(1 - phi ** 2)
    return pd.DataFrame({
        "time": np.arange(hours, dtype="int64") * 3600,
        "open": v, "high": v, "low": v, "close": v,
        "volume": np.zeros(hours),
    })


# =====================================================================
# SIGN CONVENTION — hand-calculated
# =====================================================================


class TestSignConvention:
    """funding > 0 -> longs pay shorts.  Cash to `side` = -side * rate."""

    def test_long_pays_when_funding_positive(self):
        side, rate = 1, 0.01          # +0.01% per interval
        cash_per_unit_notional = -side * (rate / 100.0)
        assert cash_per_unit_notional == pytest.approx(-0.0001)

    def test_short_receives_when_funding_positive(self):
        side, rate = -1, 0.01
        assert -side * (rate / 100.0) == pytest.approx(+0.0001)

    def test_long_receives_when_funding_negative(self):
        side, rate = 1, -0.05
        assert -side * (rate / 100.0) == pytest.approx(+0.0005)

    def test_short_pays_when_funding_negative(self):
        side, rate = -1, -0.05
        assert -side * (rate / 100.0) == pytest.approx(-0.0005)

    def test_engine_matches_hand_calculation(self):
        """With PERSISTENT funding, a position opened on an extreme must collect.

        This is the end-to-end check that the engine applies the convention in
        the same direction as the hand calculations above.
        """
        hours = MIN_PCTL_OBS + 400
        price = make_price(hours, seed=1)
        f = make_funding(hours, seed=1, scale=0.02)
        r = hf.run(price, f, BTC, start=0, arm="A", **hf.PRIMARY)
        t = r.to_frame()
        if t.empty:
            pytest.skip("no signals on this synthetic series")
        shorts = t[t.side < 0]
        if len(shorts):
            # a short opened on high funding should, on average, receive
            assert shorts.funding_bps.mean() > 0
        longs = t[t.side > 0]
        if len(longs):
            assert longs.funding_bps.mean() > 0, (
                "a long opened on LOW (negative) funding should receive"
            )


# =====================================================================
# LEAK TEST 1 — percentile at T uses no observation after T
# =====================================================================


class TestPercentileCausality:
    def test_expanding_percentile_excludes_current_and_future(self):
        v = pd.Series(np.arange(1000, dtype="float64"))
        q = expanding_percentile(v, 0.95, min_obs=100)
        # value at index i is computed from v[:i] only
        manual = float(np.quantile(np.arange(600), 0.95))
        assert q[600] == pytest.approx(manual, rel=1e-6)

    def test_future_mutation_cannot_change_past_percentiles(self):
        v = pd.Series(np.random.default_rng(0).standard_normal(2000))
        a = expanding_percentile(v, 0.95, min_obs=100)
        w = v.copy(); w.iloc[1200:] = 999.0
        b = expanding_percentile(w, 0.95, min_obs=100)
        assert np.allclose(a[:1200], b[:1200], equal_nan=True)

    def test_rank_is_causal(self):
        v = pd.Series(np.random.default_rng(3).standard_normal(1200))
        a = funding_rank(v, min_obs=100)
        w = v.copy(); w.iloc[800:] = -50.0
        b = funding_rank(w, min_obs=100)
        assert np.allclose(a[:800], b[:800], equal_nan=True)


# =====================================================================
# LEAK TESTS 2 & 3 — entry uses only funding known beforehand
# =====================================================================


@pytest.fixture(scope="module")
def trades():
    hours = MIN_PCTL_OBS + 800
    r = hf.run(make_price(hours, seed=5), make_funding(hours, seed=5),
               BTC, start=0, arm="A", **hf.PRIMARY)
    t = r.to_frame()
    if t.empty:
        pytest.skip("no trades")
    return t


class TestEntryTiming:
    def test_entry_strictly_after_the_funding_bar_closes(self, trades):
        # knowable at signal_time + 3600; entry bar must open at or after that
        assert (trades.entry_time >= trades.signal_time + 3600).all()

    def test_entry_never_precedes_the_signal(self, trades):
        assert (trades.entry_time > trades.signal_time).all()

    def test_exit_after_entry(self, trades):
        assert (trades.exit_time > trades.entry_time).all()


# =====================================================================
# LEAK TESTS 4, 5, 6 — mutation cannot change earlier signals
# =====================================================================


def _entries(price, funding, **kw):
    r = hf.run(price, funding, BTC, start=0, arm=kw.pop("arm", "A"), **kw)
    t = r.to_frame()
    return t if t.empty else t.sort_values("entry_time")


class TestPerturbation:
    def test_future_price_cannot_change_earlier_signals(self):
        hours = MIN_PCTL_OBS + 800
        price = make_price(hours, seed=7); fund = make_funding(hours, seed=7)
        a = _entries(price, fund, **hf.PRIMARY)
        if a.empty:
            pytest.skip("no trades")
        cut_t = int(a.entry_time.quantile(0.6))
        p2 = price.copy()
        m = p2.time >= cut_t
        for c in ("open", "high", "low", "close"):
            p2.loc[m, c] *= 1.5
        b = _entries(p2, fund, **hf.PRIMARY)
        ea = a[a.entry_time < cut_t].entry_time.tolist()
        eb = b[b.entry_time < cut_t].entry_time.tolist()
        assert ea == eb, "mutating future PRICE changed earlier entry timestamps"

    def test_future_funding_cannot_change_earlier_signals(self):
        hours = MIN_PCTL_OBS + 800
        price = make_price(hours, seed=9); fund = make_funding(hours, seed=9)
        a = _entries(price, fund, **hf.PRIMARY)
        if a.empty:
            pytest.skip("no trades")
        cut_t = int(a.entry_time.quantile(0.6))
        f2 = fund.copy()
        m = f2.time >= cut_t
        for c in ("open", "high", "low", "close"):
            f2.loc[m, c] = 5.0     # absurd future funding
        b = _entries(price, f2, **hf.PRIMARY)
        ea = a[a.entry_time < cut_t].entry_time.tolist()
        eb = b[b.entry_time < cut_t].entry_time.tolist()
        assert ea == eb, "mutating future FUNDING changed earlier entry timestamps"

    def test_future_price_cannot_change_earlier_signals_arm_b(self):
        hours = MIN_PCTL_OBS + 1200
        price = make_price(hours, seed=11); fund = make_funding(hours, seed=11)
        a = _entries(price, fund, arm="B", **hf.PRIMARY)
        if a.empty:
            pytest.skip("no arm B trades")
        cut_t = int(a.entry_time.quantile(0.6))
        p2 = price.copy()
        m = p2.time >= cut_t
        for c in ("open", "high", "low", "close"):
            p2.loc[m, c] *= 1.4
        b = _entries(p2, fund, arm="B", **hf.PRIMARY)
        ea = a[a.entry_time < cut_t].entry_time.tolist()
        eb = b[b.entry_time < cut_t].entry_time.tolist()
        assert ea == eb


# =====================================================================
# LEAK TEST 7 — exits and the study boundary
# =====================================================================


class TestExitAndBoundary:
    def test_end_boundary_is_respected(self):
        hours = MIN_PCTL_OBS + 900
        price = make_price(hours, seed=13); fund = make_funding(hours, seed=13)
        full = _entries(price, fund, **hf.PRIMARY)
        if full.empty:
            pytest.skip("no trades")
        cut = int(full.entry_time.quantile(0.5))
        r = hf.run(price, fund, BTC, start=0, end=cut, arm="A", **hf.PRIMARY)
        t = r.to_frame()
        if not t.empty:
            assert t.exit_time.max() <= cut

    def test_holding_period_is_bounded_by_the_pre_registered_value(self):
        hours = MIN_PCTL_OBS + 900
        r = hf.run(make_price(hours, seed=15), make_funding(hours, seed=15),
                   BTC, start=0, arm="A", **hf.PRIMARY)
        t = r.to_frame()
        if t.empty:
            pytest.skip("no trades")
        assert (t.hold_hours <= hf.PRIMARY["hold_h"]).all()


# =====================================================================
# Decomposition integrity and data policy
# =====================================================================


class TestDecomposition:
    def test_components_sum_to_net(self, trades):
        s = (trades.price_bps + trades.funding_bps
             - trades.fee_bps - trades.slippage_bps)
        assert np.allclose(s, trades.net_bps)

    def test_price_component_matches_raw_prices(self, trades):
        manual = trades.side * (trades.exit_price - trades.entry_price) / trades.entry_price * 1e4
        assert np.allclose(manual, trades.price_bps)

    def test_costs_are_never_negative(self, trades):
        assert (trades.fee_bps > 0).all() and (trades.slippage_bps > 0).all()

    def test_missing_funding_is_flagged_not_invented(self):
        hours = MIN_PCTL_OBS + 900
        fund = make_funding(hours, seed=23)
        fund.loc[fund.index[::7], "close"] = np.nan        # puncture the series
        r = hf.run(make_price(hours, seed=23), fund, BTC, start=0, arm="A",
                   **hf.PRIMARY)
        t = r.to_frame()
        if t.empty:
            pytest.skip("no trades")
        # a gap must be recorded, never silently filled
        assert t.funding_gap.dtype == bool
        assert r.skipped_nan > 0

    def test_leverage_cap_respected(self, trades):
        assert (trades.notional <= hf.START_EQUITY * hf.MAX_LEVERAGE * 1.5).all()


def test_arm_a_direction_follows_the_crowding_hypothesis():
    """High funding must produce SHORTS, low funding LONGS -- never reversed."""
    hours = MIN_PCTL_OBS + 900
    r = hf.run(make_price(hours, seed=31), make_funding(hours, seed=31),
               BTC, start=0, arm="A", **hf.PRIMARY)
    t = r.to_frame()
    if t.empty:
        pytest.skip("no trades")
    shorts = t[t.side < 0]; longs = t[t.side > 0]
    if len(shorts) and len(longs):
        assert shorts.funding_at_signal.min() > longs.funding_at_signal.max()

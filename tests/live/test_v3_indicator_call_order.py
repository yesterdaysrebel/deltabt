"""Proof that V3's runtime call passes (factor, atr_period) and not the reverse.

WHY THIS EXISTS. During the 2026-08-18 audit an analysis script called
``ind.supertrend(h, l, c, 10, 2.0)`` believing the ATR period came first. It
does not -- the signature is ``supertrend(high, low, close, factor,
atr_period)``, matching Pine. A factor-10 Supertrend on a 2-period ATR barely
ever flips, which inflated every stop width in that analysis by roughly 2-4x.

The SCRIPT was wrong; the strategy was not. This test pins that distinction so
it cannot be re-litigated by reading, and so a future edit that swaps the
arguments fails loudly instead of silently changing every stop in the system.

Inspecting the config is not sufficient and the brief said so: the config could
be right while the call site transposes it. These tests spy on the ACTUAL call
made during evaluation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config.variants import ALL
from app.strategy import rules
from deltabt import indicators as ind

V3 = ALL["V3"]


def _bars(n=400, seed=7):
    """Deterministic synthetic OHLC. No market data is read."""
    rng = np.random.default_rng(seed)
    close = 60_000 + np.cumsum(rng.normal(0, 40, n))
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 300 + 1_700_000_000,
        "open": close, "high": close + 25, "low": close - 25,
        "close": close, "volume": 1.0,
    })


class TestTheRuntimeCallOrder:

    def test_supertrend_receives_factor_first_then_atr_period(self, monkeypatch):
        seen = []
        real = ind.supertrend
        monkeypatch.setattr(
            rules.ind, "supertrend",
            lambda h, l, c, *a: seen.append(a) or real(h, l, c, *a))
        rules.IndicatorSnapshot(_bars(), V3)
        assert seen, "IndicatorSnapshot did not call supertrend"
        factor, atr_period = seen[0]
        assert (factor, atr_period) == (2.0, 10), (
            f"V3 must call supertrend(factor=2.0, atr_period=10); "
            f"got factor={factor}, atr_period={atr_period}")
        # The transposition that caused the bad analysis, named explicitly.
        assert (factor, atr_period) != (10, 2.0)

    def test_dmi_receives_di_length_first_then_adx_smoothing(self, monkeypatch):
        """Same class of error, same signature convention (Pine order)."""
        seen = []
        real = ind.dmi
        monkeypatch.setattr(
            rules.ind, "dmi",
            lambda h, l, c, *a: seen.append(a) or real(h, l, c, *a))
        rules.IndicatorSnapshot(_bars(), V3)
        assert seen[0] == (14, 28), (
            f"expected di_length=14, adx_smoothing=28; got {seen[0]}")

    def test_wpr_receives_the_configured_period(self, monkeypatch):
        seen = []
        real = ind.wpr
        monkeypatch.setattr(
            rules.ind, "wpr",
            lambda h, l, c, *a: seen.append(a) or real(h, l, c, *a))
        rules.IndicatorSnapshot(_bars(), V3)
        assert seen[0] == (140,)


class TestTheTwoOrderingsAreNotInterchangeable:
    """If these ever agree, the test above proves nothing."""

    def test_the_transposed_call_gives_a_different_series(self):
        df = _bars()
        h, l, c = (df[k].to_numpy("float64") for k in ("high", "low", "close"))
        right, d_right = ind.supertrend(h, l, c, 2.0, 10)
        wrong, d_wrong = ind.supertrend(h, l, c, 10, 2.0)
        assert not np.allclose(right, wrong, equal_nan=True)

    def test_the_transposed_call_flips_far_less_often(self):
        """The mechanism behind the inflated stop widths: a factor-10
        Supertrend almost never flips, so the leg extreme is measured over a
        far longer leg and sits much further from price."""
        df = _bars(n=2000)
        h, l, c = (df[k].to_numpy("float64") for k in ("high", "low", "close"))
        _, d_right = ind.supertrend(h, l, c, 2.0, 10)
        _, d_wrong = ind.supertrend(h, l, c, 10, 2.0)
        flips_right = int(np.sum(np.diff(d_right[~np.isnan(d_right)]) != 0))
        flips_wrong = int(np.sum(np.diff(d_wrong[~np.isnan(d_wrong)]) != 0))
        assert flips_right > flips_wrong

    def test_a_larger_factor_puts_the_band_further_from_price(self):
        """Direct statement of what `factor` means, so the argument's identity
        is asserted rather than assumed."""
        df = _bars()
        h, l, c = (df[k].to_numpy("float64") for k in ("high", "low", "close"))
        near = np.abs(c - ind.supertrend(h, l, c, 2.0, 10)[0])
        far = np.abs(c - ind.supertrend(h, l, c, 6.0, 10)[0])
        ok = np.isfinite(near) & np.isfinite(far)
        assert np.nanmedian(far[ok]) > np.nanmedian(near[ok])


class TestTheResolvedConfigMatchesTheFrozenSpec:

    def test_v3_indicator_parameters(self):
        d = V3.to_dict()
        assert d["supertrend"] == {"atr_period": 10, "multiplier": 2.0}
        assert d["adx"] == {"period": 28, "di_period": 14, "minimum": 25.0}
        assert d["williams_r"] == {"period": 140, "rule": "variant_a"}

    def test_v3_matches_the_frozen_research_constants(self):
        """The runner and the backtester must not drift apart."""
        from deltabt.research import hwpr
        assert (hwpr.ST_PERIOD, hwpr.ST_MULT) == (
            V3.supertrend.atr_period, V3.supertrend.multiplier)
        assert hwpr.ADX_PERIOD == V3.adx.period
        assert hwpr.DI_PERIOD == V3.adx.di_period
        assert hwpr.WPR_PERIOD == V3.williams_r.period
        assert hwpr.ADX_MIN == V3.adx.minimum

    def test_v3_execution_parameters(self):
        assert V3.target_r == 2.0
        assert V3.max_stop_pct == 0.10
        assert V3.fire_once is False
        assert V3.confirm_supertrend is True
        assert V3.confirm_adx_di is True
        assert V3.confirm_wpr is False, "V3 does NOT confirm WPR on 1m"
        assert V3.config_hash == "11461f2a11a96f8a"

"""Indicator tests against hand-computed values and structural invariants."""

from __future__ import annotations

import numpy as np
import pytest

from deltabt import indicators as ind


def test_rma_seeds_with_simple_mean():
    """Pine's ta.rma seeds on the mean of the first n values, then runs a=1/n.

    A plain pandas ewm(adjust=True) does not reproduce this, which is the most
    common source of ADX/ATR drift between a Pine strategy and its port.
    """
    x = np.arange(1.0, 11.0)
    r = ind.rma(x, 3)

    assert np.all(np.isnan(r[:2])), "no value before the seed window fills"
    assert r[2] == pytest.approx((1 + 2 + 3) / 3)
    assert r[3] == pytest.approx(4 / 3 + (2 / 3) * r[2])
    assert r[4] == pytest.approx(5 / 3 + (2 / 3) * r[3])


def test_rma_holds_through_nan():
    x = np.array([1.0, 2.0, 3.0, np.nan, 5.0])
    r = ind.rma(x, 3)
    assert r[3] == pytest.approx(r[2]), "a NaN input carries the previous value"
    assert np.isfinite(r[4])


def test_true_range_first_bar_has_no_previous_close():
    high = np.array([10.0, 12.0])
    low = np.array([8.0, 9.0])
    close = np.array([9.0, 11.0])
    tr = ind.true_range(high, low, close)
    assert tr[0] == pytest.approx(2.0)
    # max(12-9, |12-9|, |9-9|) = 3
    assert tr[1] == pytest.approx(3.0)


class TestWpr:
    def test_matches_definition(self):
        rng = np.random.default_rng(0)
        n, length = 200, 14
        close = 100 + np.cumsum(rng.standard_normal(n))
        high = close + np.abs(rng.standard_normal(n))
        low = close - np.abs(rng.standard_normal(n))

        w = ind.wpr(high, low, close, length)
        i = 100
        hh = high[i - length + 1 : i + 1].max()
        ll = low[i - length + 1 : i + 1].min()
        assert w[i] == pytest.approx(100.0 * (close[i] - hh) / (hh - ll))

    def test_uses_high_and_low_not_close(self):
        """A port that uses rolling close extremes silently disagrees."""
        high = np.array([10.0, 20.0, 12.0])
        low = np.array([1.0, 2.0, 3.0])
        close = np.array([5.0, 6.0, 7.0])
        w = ind.wpr(high, low, close, 3)
        assert w[2] == pytest.approx(100.0 * (7.0 - 20.0) / (20.0 - 1.0))

    def test_warmup_is_nan(self):
        rng = np.random.default_rng(1)
        c = 100 + np.cumsum(rng.standard_normal(50))
        w = ind.wpr(c + 1, c - 1, c, 14)
        assert np.all(np.isnan(w[:13]))
        assert np.isfinite(w[13])

    def test_degenerate_range_is_nan_not_inf(self):
        """Flat halted segments must not produce inf and poison downstream."""
        flat = np.full(30, 5.0)
        w = ind.wpr(flat, flat, flat, 14)
        assert np.all(np.isnan(w[14:]))
        assert not np.any(np.isinf(w))

    def test_bounded(self):
        rng = np.random.default_rng(2)
        c = 100 + np.cumsum(rng.standard_normal(500))
        w = ind.wpr(c + np.abs(rng.standard_normal(500)),
                    c - np.abs(rng.standard_normal(500)), c, 21)
        f = w[np.isfinite(w)]
        assert f.min() >= -100.0 - 1e-9
        assert f.max() <= 1e-9


class TestDmi:
    def test_bounded_and_ordered(self):
        rng = np.random.default_rng(3)
        c = 100 + np.cumsum(rng.standard_normal(2000) * 0.5)
        h = c + np.abs(rng.standard_normal(2000))
        low = c - np.abs(rng.standard_normal(2000))
        plus, minus, adx = ind.dmi(h, low, c, 14, 28)
        for arr in (plus, minus, adx):
            f = arr[np.isfinite(arr)]
            assert f.min() >= -1e-9
            assert f.max() <= 100.0 + 1e-9

    def test_pure_uptrend_has_plus_di_dominant(self):
        n = 300
        c = np.arange(n, dtype=float) + 100.0
        plus, minus, adx = ind.dmi(c + 0.5, c - 0.5, c, 14, 28)
        i = -1
        assert plus[i] > minus[i]
        assert adx[i] > 50.0, "a monotone ramp should read as a strong trend"


class TestSupertrend:
    def test_direction_is_only_plus_or_minus_one(self):
        rng = np.random.default_rng(4)
        c = 100 + np.cumsum(rng.standard_normal(1000) * 0.3)
        st, d = ind.supertrend(c + 0.4, c - 0.4, c, 2.0, 10)
        vals = set(np.unique(d[np.isfinite(d)]).tolist())
        assert vals <= {-1.0, 1.0}

    def test_bullish_direction_implies_close_above_line(self):
        """`close > supertrend` is implied by direction, so it filters nothing.

        The original strategy ANDed this in as a separate condition; measuring
        zero violations here is what justifies deleting it.
        """
        rng = np.random.default_rng(5)
        c = 100 + np.cumsum(rng.standard_normal(20_000) * 0.3)
        h = c + np.abs(rng.standard_normal(20_000)) * 0.2
        low = c - np.abs(rng.standard_normal(20_000)) * 0.2
        st, d = ind.supertrend(h, low, c, 2.0, 10)

        ok = np.isfinite(st) & np.isfinite(d)
        bull = ok & (d < 0)
        bear = ok & (d > 0)
        assert bull.sum() > 1000 and bear.sum() > 1000, "need both regimes sampled"
        assert int(np.sum(bull & ~(c > st))) == 0
        assert int(np.sum(bear & ~(c < st))) == 0

    def test_wider_factor_gives_wider_stop_distance(self):
        rng = np.random.default_rng(6)
        c = 100 + np.cumsum(rng.standard_normal(5000) * 0.3)
        h, low = c + 0.3, c - 0.3
        d2 = np.abs(c - ind.supertrend(h, low, c, 2.0, 10)[0])
        d4 = np.abs(c - ind.supertrend(h, low, c, 4.0, 10)[0])
        m = np.isfinite(d2) & np.isfinite(d4)
        assert np.nanmedian(d4[m]) > np.nanmedian(d2[m])

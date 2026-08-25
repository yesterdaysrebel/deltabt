"""Unit tests for the options research primitives.

Max pain, settlement recovery, realised vol and rank correlation are all pure
functions with checkable invariants. They are tested here rather than only
through their research scripts, because a subtle error in any of them would
produce a plausible-looking table that nothing else would catch.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.research.iv_ic import BARS_PER_YEAR_5M, _spearman, realised_vol
from deltabt.research.pin import max_pain, settlement_index


class TestMaxPain:
    def test_all_open_interest_at_one_strike_pins_there(self):
        """With OI only at 100, settling at 100 pays holders nothing."""
        k = np.array([90.0, 100.0, 110.0])
        assert max_pain(k, np.array([0.0, 10.0, 0.0]), np.array([0.0, 10.0, 0.0])) == 100.0

    def test_calls_only_pins_at_the_lowest_strike(self):
        """Every call is worthless at or below the lowest listed strike."""
        k = np.array([90.0, 100.0, 110.0])
        assert max_pain(k, np.array([5.0, 5.0, 5.0]), np.zeros(3)) == 90.0

    def test_puts_only_pins_at_the_highest_strike(self):
        k = np.array([90.0, 100.0, 110.0])
        assert max_pain(k, np.zeros(3), np.array([5.0, 5.0, 5.0])) == 110.0

    def test_pulled_toward_the_heavier_side(self):
        """Heavy call OI low in the chain drags the minimum down."""
        k = np.array([90.0, 100.0, 110.0])
        balanced = max_pain(k, np.array([1.0, 1.0, 1.0]), np.array([1.0, 1.0, 1.0]))
        skewed = max_pain(k, np.array([100.0, 1.0, 1.0]), np.array([1.0, 1.0, 1.0]))
        assert skewed <= balanced

    def test_returns_a_listed_strike_never_an_interpolation(self):
        """The strike grid has no resolution between its points."""
        k = np.array([90.0, 100.0, 110.0])
        out = max_pain(k, np.array([3.0, 7.0, 2.0]), np.array([4.0, 1.0, 8.0]))
        assert out in set(k.tolist())

    def test_zero_open_interest_everywhere_is_degenerate_but_finite(self):
        k = np.array([90.0, 100.0, 110.0])
        assert max_pain(k, np.zeros(3), np.zeros(3)) in set(k.tolist())

    def test_scale_invariant_in_open_interest(self):
        """Doubling every OI cannot move the minimising strike."""
        k = np.array([90.0, 100.0, 110.0])
        c, p = np.array([3.0, 7.0, 2.0]), np.array([4.0, 1.0, 8.0])
        assert max_pain(k, c, p) == max_pain(k, 2 * c, 2 * p)


def chain(rows):
    return pd.DataFrame(
        [{"strike": k, "is_call": c, "settlement_price": s} for k, c, s in rows]
    )


class TestSettlementIndex:
    def test_recovers_the_index_from_in_the_money_calls(self):
        """settlement_price = S - K for a call, so S = settlement + K."""
        s_e = 100_000.0
        level, disagree = settlement_index(
            chain([(90_000.0, True, s_e - 90_000.0), (95_000.0, True, s_e - 95_000.0)])
        )
        assert level == pytest.approx(s_e)
        assert disagree == 0.0

    def test_recovers_the_index_from_in_the_money_puts(self):
        s_e = 100_000.0
        level, _ = settlement_index(
            chain([(110_000.0, False, 110_000.0 - s_e), (105_000.0, False, 105_000.0 - s_e)])
        )
        assert level == pytest.approx(s_e)

    def test_calls_and_puts_agree_on_a_consistent_chain(self):
        s_e = 100_000.0
        level, disagree = settlement_index(
            chain([
                (90_000.0, True, s_e - 90_000.0),
                (110_000.0, False, 110_000.0 - s_e),
            ])
        )
        assert level == pytest.approx(s_e)
        assert disagree == pytest.approx(0.0)

    def test_disagreement_is_reported_not_averaged_away(self):
        """An inconsistent chain must be visible to the caller, not smoothed."""
        _, disagree = settlement_index(
            chain([(90_000.0, True, 10_000.0), (110_000.0, False, 15_000.0)])
        )
        assert disagree == pytest.approx(5_000.0)

    def test_out_of_the_money_contracts_are_ignored(self):
        """Zero-settling contracts carry no information about the level."""
        s_e = 100_000.0
        level, _ = settlement_index(
            chain([(90_000.0, True, s_e - 90_000.0), (120_000.0, True, 0.0)])
        )
        assert level == pytest.approx(s_e)

    def test_all_worthless_chain_is_nan(self):
        level, _ = settlement_index(chain([(120_000.0, True, 0.0), (80_000.0, False, 0.0)]))
        assert np.isnan(level)

    def test_median_resists_a_single_bad_print(self):
        s_e = 100_000.0
        rows = [(90_000.0 + 1000 * i, True, s_e - (90_000.0 + 1000 * i)) for i in range(5)]
        rows.append((85_000.0, True, 99_999.0))  # nonsense
        level, _ = settlement_index(chain(rows))
        assert level == pytest.approx(s_e)


def const_series(n, step=300, start=0, price=100.0):
    return pd.DataFrame({"time": np.arange(n) * step + start, "close": np.full(n, price)})


class TestRealisedVol:
    def test_flat_series_has_zero_vol(self):
        assert realised_vol(const_series(50), 0, 50 * 300) == pytest.approx(0.0)

    def test_matches_the_annualisation_definition(self):
        rng = np.random.default_rng(0)
        n = 2000
        sigma = 0.001
        lr = rng.standard_normal(n) * sigma
        px = 100 * np.exp(np.cumsum(lr))
        df = pd.DataFrame({"time": np.arange(n) * 300, "close": px})
        got = realised_vol(df, 0, n * 300)
        assert got == pytest.approx(sigma * np.sqrt(BARS_PER_YEAR_5M), rel=0.06)

    def test_too_few_bars_is_nan(self):
        assert np.isnan(realised_vol(const_series(5), 0, 5 * 300))

    def test_respects_the_window(self):
        """Bars outside [start, end] must not enter the estimate."""
        rng = np.random.default_rng(1)
        n = 400
        px = 100 * np.exp(np.cumsum(rng.standard_normal(n) * 0.002))
        px[:200] = 100.0  # first half dead flat
        df = pd.DataFrame({"time": np.arange(n) * 300, "close": px})
        quiet = realised_vol(df, 0, 199 * 300)
        loud = realised_vol(df, 200 * 300, n * 300)
        assert quiet == pytest.approx(0.0)
        assert loud > 0.1

    def test_nonpositive_price_is_nan(self):
        df = const_series(50)
        df.loc[10, "close"] = 0.0
        assert np.isnan(realised_vol(df, 0, 50 * 300))


class TestSpearman:
    def test_perfect_monotone_is_one(self):
        x = np.arange(50.0)
        assert _spearman(x, np.exp(x / 10)) == pytest.approx(1.0)

    def test_perfect_inverse_is_minus_one(self):
        x = np.arange(50.0)
        assert _spearman(x, -x) == pytest.approx(-1.0)

    def test_invariant_to_monotone_transform(self):
        """This is why rank correlation is used: a biased-but-monotone
        realised-vol estimator cannot change the answer."""
        rng = np.random.default_rng(3)
        x = rng.standard_normal(200)
        y = rng.standard_normal(200)
        assert _spearman(x, y) == pytest.approx(_spearman(x, np.exp(y)))

    def test_nan_pairs_are_dropped_not_imputed(self):
        x = np.array([1.0, 2.0, 3.0, np.nan] * 5)
        y = np.array([1.0, 2.0, 3.0, 99.0] * 5)
        assert _spearman(x, y) == pytest.approx(1.0)

    def test_too_few_finite_pairs_is_nan(self):
        assert np.isnan(_spearman(np.arange(5.0), np.arange(5.0)))

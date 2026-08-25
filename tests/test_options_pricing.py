"""Black-76 correctness and IV inversion invariants.

These gate the whole options research line: Delta serves no IV history, so
every volatility measurement on this venue comes from inverting the exchange's
mark price through this module. A silent error here would propagate into every
downstream result with no other check able to catch it.
"""

from __future__ import annotations

import numpy as np
import pytest

from deltabt.options_pricing import (
    SECONDS_PER_YEAR,
    black76_price,
    forward_price,
    implied_vol,
    year_fraction,
)

F, K, T, V = 80_000.0, 82_000.0, 7.0 / 365.0, 0.55


class TestBlack76:
    def test_put_call_parity(self):
        """C - P = F - K exactly, undiscounted. Non-negotiable."""
        call = float(black76_price(F, K, T, V, True))
        put = float(black76_price(F, K, T, V, False))
        assert call - put == pytest.approx(F - K, abs=1e-8)

    def test_zero_vol_is_intrinsic(self):
        assert float(black76_price(F, 70_000.0, T, 0.0, True)) == pytest.approx(10_000.0)
        assert float(black76_price(F, 70_000.0, T, 0.0, False)) == pytest.approx(0.0)

    def test_expired_is_intrinsic(self):
        """At T=0 the option is worth its payoff, whatever the vol argument."""
        assert float(black76_price(F, 70_000.0, 0.0, V, True)) == pytest.approx(10_000.0)
        assert float(black76_price(F, 90_000.0, 0.0, V, False)) == pytest.approx(10_000.0)

    def test_monotone_in_vol(self):
        vols = np.linspace(0.05, 3.0, 40)
        prices = black76_price(F, K, T, vols, True)
        assert np.all(np.diff(prices) > 0), "vega is strictly positive"

    def test_bounded_by_forward_and_strike(self):
        """No vol produces a call above F or a put above K."""
        call = black76_price(F, K, T, np.linspace(0.1, 9.0, 30), True)
        put = black76_price(F, K, T, np.linspace(0.1, 9.0, 30), False)
        assert np.all(call < F)
        assert np.all(put < K)

    def test_deep_itm_call_approaches_intrinsic_at_low_vol(self):
        p = float(black76_price(F, 10_000.0, T, 0.01, True))
        assert p == pytest.approx(F - 10_000.0, rel=1e-6)


class TestImpliedVol:
    @pytest.mark.parametrize("strike", [70_000.0, 79_000.0, 80_000.0, 95_000.0])
    @pytest.mark.parametrize("vol", [0.2, 0.55, 1.5])
    @pytest.mark.parametrize("is_call", [True, False])
    def test_round_trip(self, strike, vol, is_call):
        """Recoverable wherever the contract has resolvable time value."""
        price = black76_price(F, strike, T, vol, is_call)
        back = float(implied_vol(price, F, strike, T, is_call))
        assert back == pytest.approx(vol, abs=1e-6)

    @pytest.mark.parametrize(
        "strike,vol,is_call",
        [
            (40_000.0, 0.2, True),    # deep ITM call: time value underflows
            (40_000.0, 0.55, True),
            (120_000.0, 0.2, True),   # deep OTM call: price underflows to 0
            (40_000.0, 0.2, False),   # deep OTM put
        ],
    )
    def test_wings_refuse_rather_than_guess(self, strike, vol, is_call):
        """Where vega vanishes the inversion must return NaN, not a number.

        Before MIN_TIME_VALUE_FRAC existed, the deep-ITM cases here returned a
        confident 0.604 for contracts priced at 0.20 and 0.55 vol -- bisection
        converging on the middle of its own bracket across a numerically flat
        function. A reconstructed IV history containing those would be
        undetectably wrong.
        """
        price = black76_price(F, strike, T, vol, is_call)
        assert np.isnan(float(implied_vol(price, F, strike, T, is_call)))

    def test_time_value_floor_does_not_reject_real_contracts(self):
        """The floor must not eat ordinary ITM options that do carry vega."""
        price = black76_price(F, 60_000.0, 30.0 / 365.0, 0.6, True)
        assert float(implied_vol(price, F, 60_000.0, 30.0 / 365.0, True)) == pytest.approx(
            0.6, abs=1e-6
        )

    def test_round_trip_at_daily_tenor(self):
        """Daily expiries are most of this surface and have the least vega."""
        t = 4.0 / 24.0 / 365.0
        price = black76_price(F, 80_500.0, t, 0.9, True)
        assert float(implied_vol(price, F, 80_500.0, t, True)) == pytest.approx(0.9, abs=1e-5)

    def test_below_intrinsic_is_nan_not_clipped(self):
        """A mark below intrinsic is an exchange artifact, not a low vol.

        Returning zero or a floor here would let bad marks enter a VRP average
        as if they were real quotes.
        """
        assert np.isnan(float(implied_vol(1.0, F, 70_000.0, T, True)))

    def test_at_or_above_upper_bound_is_nan(self):
        assert np.isnan(float(implied_vol(F, F, K, T, True)))
        assert np.isnan(float(implied_vol(K * 1.01, F, K, T, False)))

    def test_zero_and_negative_price_is_nan(self):
        assert np.isnan(float(implied_vol(0.0, F, K, T, True)))
        assert np.isnan(float(implied_vol(-5.0, F, K, T, True)))

    def test_expired_is_nan(self):
        assert np.isnan(float(implied_vol(100.0, F, K, 0.0, True)))

    def test_vectorises_and_propagates_nan_elementwise(self):
        prices = np.array([black76_price(F, K, T, 0.5, True), 1.0, 0.0])
        out = implied_vol(prices, F, K, T, True)
        assert out.shape == (3,)
        assert out[0] == pytest.approx(0.5, abs=1e-6)
        assert np.isnan(out[1]) or out[1] > 0  # 1.0 is above intrinsic (OTM call)
        assert np.isnan(out[2])


class TestTimeAndForward:
    def test_year_fraction_is_calendar_time(self):
        assert float(year_fraction(0, SECONDS_PER_YEAR)) == pytest.approx(1.0)
        assert float(year_fraction(0, 86400)) == pytest.approx(1.0 / 365.0)

    def test_year_fraction_floors_at_zero_after_expiry(self):
        assert float(year_fraction(100, 50)) == 0.0

    def test_forward_at_zero_rate_is_spot(self):
        """Validated empirically: r=0 reproduces Delta's own mark_iv.

        See research/validate_iv.py -- the calibrated rate came out 0.000 with
        a median absolute IV error of 0.00036 vol points, and any nonzero rate
        made it monotonically worse.
        """
        assert float(forward_price(80_000.0, 0.25, 0.0)) == pytest.approx(80_000.0)

    def test_forward_grows_with_rate(self):
        assert float(forward_price(80_000.0, 1.0, 0.1)) == pytest.approx(80_000.0 * np.exp(0.1))

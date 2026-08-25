"""The options cost law, and the regimes it has that the perpetual one does not."""

from __future__ import annotations

import numpy as np
import pytest

from deltabt.config import GST_MULTIPLIER
from deltabt.data.options import parse_symbol
from deltabt.options_costs import (
    ALT_PREMIUM_FEE_CAP,
    DEFAULT_OPTION_FEE_RATE,
    OptionCosts,
)

SPOT = 79_567.0
BTC = OptionCosts(symbol="C-BTC-80000-250826", contract_value=0.001, tick_size=0.1)


class TestFeeBase:
    def test_fee_is_charged_on_notional_not_premium(self):
        """Two options on the same underlying pay the same fee, whatever they cost.

        This is the structural difference from the perpetual side and the
        reason the law has a different shape. Both premiums here sit above the
        cap threshold so the notional term is the binding one.
        """
        cheap = float(BTC.fee_per_contract(0.01 * SPOT, SPOT))
        rich = float(BTC.fee_per_contract(0.05 * SPOT, SPOT))
        assert cheap == pytest.approx(rich)
        expected = DEFAULT_OPTION_FEE_RATE * SPOT * 0.001 * GST_MULTIPLIER
        assert cheap == pytest.approx(expected)

    def test_fee_scales_with_spot(self):
        a = float(BTC.fee_per_contract(0.02 * SPOT, SPOT))
        b = float(BTC.fee_per_contract(0.02 * SPOT, 2 * SPOT))
        assert b == pytest.approx(2 * a)


class TestPremiumCap:
    def test_cap_threshold_is_rate_over_cap(self):
        assert BTC.cap_binds_below == pytest.approx(0.001)

    def test_below_threshold_fee_is_flat_share_of_premium(self):
        """In the capped regime the fee stops falling with the premium."""
        for p in (0.0005, 0.0002, 0.00001):
            frac = float(BTC.fee_frac_of_premium(p * SPOT, SPOT))
            assert frac == pytest.approx(BTC.premium_fee_cap * GST_MULTIPLIER)

    def test_above_threshold_fee_falls_as_premium_rises(self):
        fracs = [float(BTC.fee_frac_of_premium(p * SPOT, SPOT)) for p in (0.002, 0.01, 0.05)]
        assert fracs[0] > fracs[1] > fracs[2]

    def test_law_matches_closed_form(self):
        """fee/premium = min(fee_rate / p, cap) * gst, independent of lot size."""
        for p in (0.05, 0.01, 0.002, 0.0005):
            got = float(BTC.fee_frac_of_premium(p * SPOT, SPOT))
            want = min(DEFAULT_OPTION_FEE_RATE / p, BTC.premium_fee_cap) * GST_MULTIPLIER
            assert got == pytest.approx(want)

    def test_contract_size_does_not_change_the_relative_law(self):
        eth = OptionCosts(symbol="C-ETH", contract_value=0.01, tick_size=0.01)
        assert float(eth.fee_frac_of_premium(0.01 * 2600, 2600)) == pytest.approx(
            float(BTC.fee_frac_of_premium(0.01 * SPOT, SPOT))
        )

    def test_alternative_cap_is_cheaper(self):
        """The default cap is the pessimistic reading of an ambiguous source."""
        alt = OptionCosts(
            symbol="C-BTC", contract_value=0.001, tick_size=0.1,
            premium_fee_cap=ALT_PREMIUM_FEE_CAP,
        )
        deep_otm = 0.0002 * SPOT
        assert float(alt.fee_frac_of_premium(deep_otm, SPOT)) < float(
            BTC.fee_frac_of_premium(deep_otm, SPOT)
        )


class TestRoundTrip:
    def test_round_trip_is_two_sided(self):
        p = 0.01 * SPOT
        one_side = float(BTC.fee_frac_of_premium(p, SPOT)) + BTC.half_spread_frac
        assert float(BTC.round_trip_frac_of_premium(p, SPOT)) == pytest.approx(2 * one_side)

    def test_atm_round_trip_is_a_few_percent_of_premium(self):
        """The bar any options edge has to clear. ~1% of spot is ATM-daily-ish."""
        rt = float(BTC.round_trip_frac_of_premium(0.01 * SPOT, SPOT))
        assert 0.03 < rt < 0.07

    def test_cheap_otm_round_trip_is_ruinous(self):
        """Below the cap threshold friction exceeds a quarter of the premium."""
        rt = float(BTC.round_trip_frac_of_premium(0.0002 * SPOT, SPOT))
        assert rt > 0.25

    def test_round_trip_is_monotone_decreasing_in_premium(self):
        """Non-strict: inside the capped regime the cost is flat by design."""
        prem = np.array([0.0002, 0.001, 0.005, 0.02, 0.05]) * SPOT
        rt = BTC.round_trip_frac_of_premium(prem, SPOT)
        assert np.all(np.diff(rt) <= 0)
        # Strictly decreasing once the notional term is the binding one.
        above_cap = np.array([0.002, 0.005, 0.02, 0.05]) * SPOT
        assert np.all(np.diff(BTC.round_trip_frac_of_premium(above_cap, SPOT)) < 0)

    def test_zero_premium_is_nan_not_infinite(self):
        assert np.isnan(float(BTC.fee_frac_of_premium(0.0, SPOT)))


class TestRounding:
    def test_round_premium_directions(self):
        assert float(BTC.round_premium(10.04)) == pytest.approx(10.0)
        assert float(BTC.round_premium(10.04, direction=1)) == pytest.approx(10.1)
        assert float(BTC.round_premium(10.06, direction=-1)) == pytest.approx(10.0)


class TestSymbolParsing:
    def test_parses_call_and_put(self):
        c = parse_symbol("C-BTC-96000-301026")
        assert (c.is_call, c.underlying, c.strike) == (True, "BTC", 96000.0)
        assert c.expiry_date.isoformat() == "2026-10-30"
        p = parse_symbol("P-ETH-2620-270826")
        assert (p.is_call, p.underlying, p.strike) == (False, "ETH", 2620.0)

    def test_settlement_is_noon_utc(self):
        import datetime as dt
        c = parse_symbol("C-BTC-96000-301026")
        assert dt.datetime.fromtimestamp(c.expiry_ts, dt.timezone.utc).hour == 12

    @pytest.mark.parametrize(
        "sym", ["MOVE-BTC-1000-250826", "BTCUSD", "MARK:C-BTC-96000-301026", "C-BTC-96000-3010"]
    )
    def test_rejects_non_vanilla(self, sym):
        """Returns None rather than raising -- mixed product lists are normal."""
        assert parse_symbol(sym) is None

    def test_rejects_impossible_date(self):
        assert parse_symbol("C-BTC-96000-322026") is None

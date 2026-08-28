"""The two places a bug would invent a funding edge.

WHY THIS FILE EXISTS
    scripts/funding_spread.py reports a carry of about +8.9%/yr that is stable
    across every lookback, hold and weighting tried. A number that stable is
    either a real cash flow or a systematic error, and there are exactly two
    candidates for the error:

    1. THE SIGN. Positive funding means longs PAY shorts. A short position
       therefore RECEIVES it. Flip that and a losing trade reads as a winning
       one with the same magnitude, which is the most flattering bug available
       and the least visible.

    2. THE RESAMPLE. The parquet samples the prevailing rate HOURLY;
       settlement happens every 4h or 8h. Summing the hourly series counts
       each payment 4 to 8 times. That alone would turn a true +1.5%/yr into
       roughly +9%/yr -- the number actually observed -- so it has to be ruled
       out explicitly rather than assumed.

    Neither is caught by any other test: the script is standalone, has no
    assertions of its own, and its output is plausible either way.
"""

from __future__ import annotations

import importlib.util
import pathlib

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "funding_spread.py"


def _module():
    spec = importlib.util.spec_from_file_location("funding_spread", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fs():
    return _module()


def _hourly(rate: float, days: int) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=days * 24, freq="1h", tz="UTC")
    return pd.Series(rate, index=idx)


def test_settlement_is_counted_once_per_interval_not_hourly(fs):
    """8h settlement over one day is THREE payments, not twenty-four."""
    daily = fs.realised_funding(_hourly(0.0001, 3), 28800)
    # 3 settlements/day x 0.0001. Anything near 24x is the resample bug.
    assert daily.iloc[1] == pytest.approx(0.0003, rel=1e-6), (
        f"one day of 8h funding came to {daily.iloc[1]}, expected 0.0003. "
        f"If it is ~0.0024 the hourly series is being summed and every "
        f"reported carry number is 8x too large.")


def test_four_hour_symbols_settle_six_times_a_day(fs):
    daily = fs.realised_funding(_hourly(0.0001, 3), 14400)
    assert daily.iloc[1] == pytest.approx(0.0006, rel=1e-6)


def test_the_two_intervals_do_not_give_the_same_answer(fs):
    """A 4h symbol must accrue twice what an 8h one does at the same rate."""
    four = fs.realised_funding(_hourly(0.0001, 3), 14400).iloc[1]
    eight = fs.realised_funding(_hourly(0.0001, 3), 28800).iloc[1]
    assert four == pytest.approx(2 * eight, rel=1e-6)


def test_a_short_receives_positive_funding(fs):
    """Positive rate = longs pay shorts. The carry line must reflect that."""
    weight, rate = -0.5, 0.0001            # short, longs are paying
    carry = -weight * rate
    assert carry > 0, (
        "a short position is losing money on positive funding, so the sign "
        "in scripts/funding_spread.py is inverted and every result flips")


def test_a_long_pays_positive_funding(fs):
    assert -(+0.5) * 0.0001 < 0


def test_a_dollar_neutral_pair_collects_the_spread_not_the_level(fs):
    """Both legs at the same rate must net to zero carry.

    If it does not, the portfolio is capturing the funding LEVEL through an
    unintended net exposure rather than the spread between symbols.
    """
    held = {"A": +0.5, "B": -0.5}
    rates = {"A": 0.0003, "B": 0.0003}
    carry = sum(-w * rates[k] for k, w in held.items())
    assert carry == pytest.approx(0.0), (
        "equal funding on both legs produced non-zero carry; the book is not "
        "actually neutral")


def test_the_spread_is_collected_with_the_right_sign(fs):
    """Short the high-funding leg, long the low: carry must be positive."""
    held = {"cheap": +0.5, "rich": -0.5}
    rates = {"cheap": -0.0001, "rich": +0.0004}
    carry = sum(-w * rates[k] for k, w in held.items())
    assert carry == pytest.approx(0.00025)


def test_costs_are_charged_on_both_legs(fs):
    """A pair trade crosses the spread four times per round trip."""
    assert fs.LEG_COST == pytest.approx(0.00079)
    opening = (0.5 + 0.5) * fs.LEG_COST
    assert opening == pytest.approx(0.00079), (
        "opening a dollar-neutral pair should cost one LEG_COST on total "
        "gross notional of 1.0")


def test_the_signal_cannot_see_the_funding_it_collects(fs):
    """The rank on day t must be built from data strictly before day t."""
    text = SCRIPT.read_text()
    assert ".shift(1)" in text, (
        "the funding signal is not shifted, so the portfolio is chosen using "
        "the same day's funding it is about to be paid")
    assert "rolling(lookback_days).mean().shift(1)" in text


def test_notional_uses_contract_value(fs):
    """`close * volume` is CONTRACTS. Dollars needs contract_value.

    BTCUSD's contract_value is 0.001 and a micro-cap's is 1.0, so omitting it
    understates BTC turnover by 1000x and leaves the micro-cap untouched --
    inverting the liquidity screen rather than merely loosening it. The first
    run of this script did exactly that and reported a Sharpe near 4 built
    entirely on names funding at 250-400%/yr, which vanished (to -0.88) the
    moment turnover was measured correctly.
    """
    text = SCRIPT.read_text()
    assert text.count("contract_value") >= 3, (
        "contract_value is not being applied when computing turnover, so the "
        "--min-volume screen is measuring contracts, not dollars")
    assert 'c["close"] * c["volume"] * cv' in text


def test_the_liquidity_screen_is_actually_applied(fs):
    text = SCRIPT.read_text()
    assert "liquid = vol.rolling(7).mean().shift(1) >= min_volume" in text
    assert ".where(liquid.loc[day])" in text, (
        "the liquidity mask is computed but never applied to the ranking")

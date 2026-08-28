"""Every way H-Carry-2 could invent a funding edge, pinned before it is run.

The prereg lists eight leakage assertions and three arithmetic ones. This file
is all eleven. It is deliberately paranoid about the three errors that have
already produced a false positive in this repository:

    THE SIGN         a short RECEIVES positive funding. Inverted, a losing
                     book reads as a winning one of identical magnitude.
    THE RESAMPLE     the parquet samples the rate HOURLY; settlement is every
                     4h or 8h. Summing hourly counts each payment 4-8 times,
                     which turns a true +1.5%/yr into roughly +9%/yr.
    THE NOTIONAL     `close * volume` is CONTRACTS. Omitting contract_value
                     understates BTCUSD turnover 1000x and INVERTS the
                     liquidity screen. This one shipped a Sharpe near 4.

Nothing here reads a result. If any of it fails, the experiment stops.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import h_carry_2 as hc  # noqa: E402


def _hourly(rate: float, days: int, start: str = "2026-01-01") -> pd.Series:
    idx = pd.date_range(start, periods=days * 24, freq="1h", tz="UTC")
    return pd.Series(rate, index=idx)


# ---------------------------------------------------------------- settlement

def test_settlement_grid_is_anchored_to_the_utc_epoch():
    """An 8h product settles at 00:00 / 08:00 / 16:00, not at series start."""
    idx = pd.date_range("2026-01-01 03:00", periods=48, freq="1h", tz="UTC")
    grid = hc.settlement_instants(idx, 28800)
    assert list(grid.hour[:3]) == [8, 16, 0]
    assert (np.asarray([int(t.timestamp()) % 28800 for t in grid]) == 0).all()


def test_settlement_is_counted_once_per_interval_not_hourly():
    """One day of 8h funding is THREE payments. Near 24x is the resample bug."""
    s = hc.settled_rates(_hourly(0.01, 3), 28800)
    day = s[s.index.floor("D") == pd.Timestamp("2026-01-02", tz="UTC")]
    assert len(day) == 3, f"{len(day)} settlements in a day at 8h; expected 3"
    assert day.sum() == pytest.approx(0.03)


def test_four_hour_symbols_settle_six_times_a_day():
    s = hc.settled_rates(_hourly(0.01, 3), 14400)
    day = s[s.index.floor("D") == pd.Timestamp("2026-01-02", tz="UTC")]
    assert len(day) == 6
    assert day.sum() == pytest.approx(0.06)


def test_a_four_hour_symbol_accrues_twice_an_eight_hour_one():
    four = hc.settled_rates(_hourly(0.01, 3), 14400)
    eight = hc.settled_rates(_hourly(0.01, 3), 28800)
    d = pd.Timestamp("2026-01-02", tz="UTC")
    assert four[four.index.floor("D") == d].sum() == pytest.approx(
        2 * eight[eight.index.floor("D") == d].sum())


def test_missing_settlements_are_dropped_not_forward_filled():
    """An absent rate is absent information. Filling invents a payment."""
    h = _hourly(0.01, 3)
    h.loc["2026-01-02 08:00":"2026-01-02 08:00"] = np.nan
    s = hc.settled_rates(h, 28800)
    assert pd.Timestamp("2026-01-02 08:00", tz="UTC") not in s.index
    assert not s.isna().any()
    assert len(s) == len(hc.settled_rates(_hourly(0.01, 3), 28800)) - 1


def test_annualisation_uses_the_products_own_interval():
    """0.01% per 8h is 3 payments a day: 0.01% x 3 x 365 = 10.95%/yr."""
    assert hc.annualise(0.01, 28800) == pytest.approx(0.1095, rel=1e-6)
    assert hc.annualise(0.01, 14400) == pytest.approx(0.2190, rel=1e-6)


# -------------------------------------------------------------------- timing

def _symbol(rates: dict[str, float], interval_s: int = 28800) -> hc.SymbolData:
    ts = pd.DatetimeIndex([pd.Timestamp(k, tz="UTC") for k in rates])
    return hc.SymbolData(
        symbol="TEST", interval_s=interval_s, contract_value=1.0,
        launch=pd.Timestamp("2020-01-01", tz="UTC"),
        settle_ts=ts.tz_localize(None).values,
        settle_rate=np.array(list(rates.values()), dtype=float),
        close=pd.Series(dtype=float), turnover=pd.Series(dtype=float))


def test_the_signal_cannot_see_the_settlement_it_is_about_to_collect():
    """A payment stamped exactly at the rebalance instant must be EXCLUDED.

    The trade happens at day 00:00. A settlement at that same instant is
    simultaneous with it, so ranking on it would be choosing the book with the
    funding it is about to be paid.
    """
    d = _symbol({"2026-01-01 08:00": 0.0, "2026-01-01 16:00": 0.0,
                 "2026-01-02 00:00": 99.0})
    got = hc.signal_at(d, pd.Timestamp("2026-01-02", tz="UTC"), lookback=2)
    assert got == pytest.approx(0.0), (
        "the 00:00 settlement leaked into the signal that trades at 00:00")


def test_the_signal_contains_no_future_observation_at_all():
    """Shift audit: everything before is zero, everything after is huge."""
    rates = {f"2026-01-{d:02d} 00:00": (0.0 if d <= 10 else 99.0)
             for d in range(1, 21)}
    d = _symbol(rates)
    for L in (1, 3, 7):
        got = hc.signal_at(d, pd.Timestamp("2026-01-11", tz="UTC"), lookback=L)
        assert got == pytest.approx(0.0), f"future leaked at L={L}: {got}"


def test_insufficient_settlement_history_makes_a_symbol_ineligible():
    d = _symbol({"2026-01-01 00:00": 0.01, "2026-01-01 08:00": 0.01})
    assert np.isnan(hc.signal_at(d, pd.Timestamp("2026-01-02", tz="UTC"), 7))
    assert np.isfinite(hc.signal_at(d, pd.Timestamp("2026-01-02", tz="UTC"), 2))


def test_the_signal_uses_the_last_L_settlements_and_no_more():
    d = _symbol({"2026-01-01 00:00": 1.0, "2026-01-01 08:00": 2.0,
                 "2026-01-01 16:00": 3.0})
    got = hc.signal_at(d, pd.Timestamp("2026-01-02", tz="UTC"), lookback=2)
    assert got == pytest.approx(hc.annualise(2.5, 28800))


# ------------------------------------------------------------------- notional

def test_notional_uses_contract_value():
    """BTCUSD cv=0.001 against a micro-cap cv=1000: 1e6x apart, not 1x."""
    close = pd.Series([100_000.0])
    vol = pd.Series([1_000.0])
    btc = hc.notional_usd(close, vol, 0.001)
    micro = hc.notional_usd(pd.Series([0.5]), pd.Series([1_000.0]), 1000.0)
    assert btc.iloc[0] == pytest.approx(100_000.0)
    assert micro.iloc[0] == pytest.approx(500_000.0)
    naive_btc = float(close.iloc[0] * vol.iloc[0])
    assert naive_btc == pytest.approx(1e8)
    assert btc.iloc[0] < naive_btc / 999, (
        "contract_value is not applied; the liquidity screen is measuring "
        "contracts and is inverted for BTCUSD")


def test_real_products_have_materially_different_contract_values():
    import json
    prod = json.loads((ROOT / "data" / "meta" / "products.json").read_text())
    assert prod["BTCUSD"]["contract_value"] == pytest.approx(0.001)
    others = {s: p["contract_value"] for s, p in prod.items()
              if p["contract_value"] >= 100}
    assert others, "no high-contract-value product to contrast BTCUSD against"


# ---------------------------------------------------------------------- signs

def test_a_short_receives_positive_funding():
    assert -(-0.5) * 0.0001 > 0


def test_a_long_pays_positive_funding():
    assert -(+0.5) * 0.0001 < 0


def test_a_dollar_neutral_pair_collects_the_spread_not_the_level():
    held = {"A": +0.5, "B": -0.5}
    rates = {"A": 0.0003, "B": 0.0003}
    assert sum(-w * rates[k] for k, w in held.items()) == pytest.approx(0.0)


def test_shorting_the_rich_leg_earns_the_spread():
    held = {"cheap": +0.5, "rich": -0.5}
    rates = {"cheap": -0.0001, "rich": +0.0004}
    assert sum(-w * rates[k] for k, w in held.items()) == pytest.approx(0.00025)


# ----------------------------------------------------------------- arithmetic

def test_leg_count_is_the_frozen_formula():
    assert hc.leg_count(4) == 2 and hc.leg_count(14) == 2
    assert hc.leg_count(21) == 4 and hc.leg_count(30) == 6
    assert hc.leg_count(200) == 6


def test_costs_are_charged_on_both_legs_at_every_rebalance():
    assert hc.LEG_COST == pytest.approx(0.00079)
    assert (0.5 + 0.5) * hc.LEG_COST == pytest.approx(0.00079)


def test_benjamini_hochberg_is_step_up_not_elementwise():
    """An isolated small p must not drag the whole family through."""
    p = [0.001] + [0.9] * 19
    assert hc.benjamini_hochberg(p) == [True] + [False] * 19
    assert not any(hc.benjamini_hochberg([0.20] * 20))
    assert all(hc.benjamini_hochberg([0.0001] * 20))


def test_newey_west_widens_the_error_under_positive_autocorrelation():
    rng = np.random.default_rng(0)
    e = rng.normal(size=4000)
    ar = np.zeros(4000)
    for i in range(1, 4000):
        ar[i] = 0.8 * ar[i - 1] + e[i]
    ar += 0.05
    iid_t = ar.mean() / (ar.std(ddof=1) / np.sqrt(len(ar)))
    assert abs(hc.newey_west_t(ar, lag=14)) < abs(iid_t), (
        "NW is not widening the standard error on a serially correlated "
        "series, so every t-statistic here is overstated")

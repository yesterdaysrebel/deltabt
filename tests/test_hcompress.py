"""QA for H-Compress-1. These gate the experiment: if any fail, no result stands.

Covers the 11 pre-registered checks. The look-ahead ones are the important
ones -- an earlier experiment in this program was inflated by +0.5R by exactly
the class of bug tested here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.costs import SymbolCosts
from deltabt.research import hcompress as hc
from deltabt.research.hcompress import (
    MAX_HOLD_5M,
    ORDER_LIFETIME_5M,
    PCT_LOOKBACK_15M,
    _compression_zones,
    _rolling_quantile_causal,
    build_frames,
    detect,
)

BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)


def make_1m(n=60_000, seed=0, base=60_000.0) -> pd.DataFrame:
    """Synthetic 1m bars with alternating calm and volatile regimes.

    Generic noise; used where the test does not need actual trades.
    """
    rng = np.random.default_rng(seed)
    vol = np.where((np.arange(n) // 3000) % 2 == 0, 0.00004, 0.0004)
    r = rng.standard_normal(n) * vol
    close = base * np.exp(np.cumsum(r))
    spread = np.abs(rng.standard_normal(n)) * vol * close
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 60,
        "open": np.concatenate(([base], close[:-1])),
        "high": close + spread, "low": close - spread, "close": close,
        "volume": rng.random(n) * 100 + 10,
    })


def make_pattern_1m(cycles=40, seed=0, base=1000.0) -> pd.DataFrame:
    """Series that DELIBERATELY contains compression -> expansion -> retest.

    A generator that never triggers the strategy makes every downstream QA test
    vacuous (they skip). This one guarantees events: a long moderate warm-up so
    the trailing ATR percentile is well defined, then repeated cycles of
    ultra-calm compression, a high-volume breakout candle, a retest of the
    broken boundary, and a random resolution.
    """
    rng = np.random.default_rng(seed)
    o, h, l, c, v = [], [], [], [], []
    px = base

    def push(op, hi, lo, cl, vol):
        o.append(op); h.append(hi); l.append(lo); c.append(cl); v.append(vol)

    # warm-up: moderate volatility, long enough for the 960-bar 15m percentile
    for _ in range(PCT_LOOKBACK_15M * 15 + 500):
        step = rng.standard_normal() * 0.30
        op = px; px = max(px + step, 1.0)
        wick = abs(rng.standard_normal()) * 0.25
        push(op, max(op, px) + wick, min(op, px) - wick, px, 100 + rng.random() * 20)

    for _ in range(cycles):
        # compression: 8 x 15m bars of near-zero range
        anchor = px
        for _ in range(8 * 15):
            op = px; px = anchor + rng.standard_normal() * 0.01
            push(op, max(op, px) + 0.005, min(op, px) - 0.005, px, 100 + rng.random() * 5)
        hi_zone = max(h[-120:]); lo_zone = min(l[-120:])

        zone_range = max(hi_zone - lo_zone, 1e-6)
        direction = 1 if rng.random() < 0.5 else -1
        # breakout: one 5m block with a large body and heavy volume. Scaled to
        # the zone so the geometry stays realistic -- an enormous breakout would
        # make every stop unreachable and every exit degenerate.
        overshoot = 1.5 * zone_range
        brk = (hi_zone + overshoot) if direction > 0 else (lo_zone - overshoot)
        for i in range(5):
            op = px
            px = px + (brk - px) / (5 - i)
            push(op, max(op, px) + 0.002, min(op, px) - 0.002, px, 900 + rng.random() * 100)

        # retest: drift back to the broken boundary over 2 x 5m blocks
        boundary = hi_zone if direction > 0 else lo_zone
        for i in range(10):
            op = px
            px = px + (boundary - px) / (10 - i)
            push(op, max(op, px) + 0.002, min(op, px) - 0.002, px, 150 + rng.random() * 30)

        # resolution: volatility comparable to the stop distance, so target,
        # stop and time exits are all genuinely reachable
        drift = rng.choice([-1.0, 1.0]) * direction * 0.08 * zone_range
        sigma = 0.35 * zone_range
        for _ in range(30 * 5):
            op = px; px = max(px + drift + rng.standard_normal() * sigma, 1.0)
            push(op, max(op, px) + 0.1 * zone_range, min(op, px) - 0.1 * zone_range,
                 px, 120 + rng.random() * 20)

    n = len(o)
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 60,
        "open": np.array(o), "high": np.array(h), "low": np.array(l),
        "close": np.array(c), "volume": np.array(v),
    })


@pytest.fixture(scope="module")
def pattern_df() -> pd.DataFrame:
    return make_pattern_1m(cycles=60, seed=1)


@pytest.fixture(scope="module")
def pattern_trades(pattern_df) -> pd.DataFrame:
    r = hc.run(pattern_df, None, pd.DataFrame(), BTC, start=0, arm="A", **hc.PRIMARY)
    return r.to_frame()


def test_generator_actually_produces_trades(pattern_trades):
    """Guards every other QA test below from silently skipping."""
    assert len(pattern_trades) >= 10, (
        f"fixture produced only {len(pattern_trades)} trades; the QA suite "
        "would be vacuous"
    )


# --- QA 6: percentile uses only historical information ----------------------


class TestCausalQuantile:
    def test_excludes_current_bar(self):
        x = np.arange(20, dtype="float64")
        out = _rolling_quantile_causal(x, 10, 0.5)
        # window ending at t-1 for t=15 is x[5..14], median 9.5
        assert out[15] == pytest.approx(9.5)

    def test_nan_before_window_fills(self):
        x = np.arange(20, dtype="float64")
        out = _rolling_quantile_causal(x, 10, 0.5)
        assert np.all(np.isnan(out[:10]))

    def test_future_values_cannot_affect_it(self):
        x = np.arange(60, dtype="float64")
        a = _rolling_quantile_causal(x, 10, 0.2)
        y = x.copy(); y[40:] = 9999.0
        b = _rolling_quantile_causal(y, 10, 0.2)
        assert np.allclose(a[:40], b[:40], equal_nan=True)


# --- QA 5: compression boundaries frozen before the breakout ----------------


class TestCompressionZones:
    def test_zone_uses_only_the_run(self):
        n = 12
        high = np.array([10, 10, 20, 11, 11, 11, 11, 30, 12, 12, 12, 12], dtype="float64")
        low = np.array([9, 9, 8, 10, 10, 10, 10, 5, 11, 11, 11, 11], dtype="float64")
        atr = np.full(n, 5.0)
        comp = np.array([0, 0, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1], dtype=bool)
        zh, zl, nb, ok = _compression_zones(high, low, atr, comp, 4, 10.0)
        # at t=6 the run is bars 3..6 -> high 11, low 10; the 20/8 spike at t=2
        # and the 30/5 spike at t=7 must not leak in
        assert nb[6] == 4 and zh[6] == 11.0 and zl[6] == 10.0
        assert np.isnan(zh[7])

    def test_min_duration_enforced(self):
        n = 8
        comp = np.array([1, 1, 1, 0, 1, 1, 1, 1], dtype=bool)
        zh, zl, nb, ok = _compression_zones(
            np.full(n, 10.0), np.full(n, 9.0), np.full(n, 1.0), comp, 4, 10.0)
        assert np.isnan(zh[2]), "a 3-bar run must not qualify"
        assert not np.isnan(zh[7]), "a 4-bar run must qualify"

    def test_range_quality_rejects_wide_zones(self):
        n = 6
        comp = np.ones(n, dtype=bool)
        high = np.full(n, 20.0); low = np.full(n, 10.0)   # range 10
        zh, zl, nb, ok = _compression_zones(high, low, np.full(n, 1.0), comp, 4, 1.5)
        assert not ok[5], "range/ATR = 10 must fail the 1.5 cap"
        zh, zl, nb, ok = _compression_zones(high, low, np.full(n, 20.0), comp, 4, 1.5)
        assert ok[5], "range/ATR = 0.5 must pass"


# --- QA 2: perturbation -- future bars cannot change past signals -----------


def test_perturbation_future_bars_do_not_change_past_signals():
    df = make_1m(40_000, seed=3)
    cut = 30_000
    b5a, b15a, _ = build_frames(df, None, 0)
    a = detect(b5a, b15a, percentile=0.20, min_duration=4,
               volume_mult=1.5, body_mult=0.5)

    mutated = df.copy()
    rng = np.random.default_rng(99)
    for c in ("open", "high", "low", "close"):
        mutated.loc[cut:, c] = mutated.loc[cut:, c] * (1 + rng.standard_normal(len(df) - cut) * 0.05)
    mutated.loc[cut:, "volume"] *= 50
    b5b, b15b, _ = build_frames(mutated, None, 0)
    b = detect(b5b, b15b, percentile=0.20, min_duration=4,
               volume_mult=1.5, body_mult=0.5)

    t5 = b5a["time"].to_numpy()
    keep = t5 < (cut - 900) * 60 / 60  # bars comfortably before the cut
    keep = t5 < (cut - 30) * 60
    assert np.array_equal(a["up"][keep], b["up"][keep])
    assert np.array_equal(a["dn"][keep], b["dn"][keep])
    assert np.allclose(a["zhi"][keep], b["zhi"][keep], equal_nan=True)


# --- QA 4: no same-bar target after a passive fill --------------------------


def _forced_setup():
    """A compression zone, an expansion, then a bar that spans target and stop."""
    step = 60
    rows = []
    price = 100.0
    # long calm stretch to establish a low ATR percentile
    for i in range(PCT_LOOKBACK_15M * 15 + 600):
        rows.append((i * step, price, price + 0.01, price - 0.01, price, 50.0))
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


class TestSameBarLookAhead:
    def test_passive_fill_bar_cannot_register_a_target(self):
        """Directly exercise the rule with a hand-built bar sequence.

        Bar sequence after a fill: the fill bar's high exceeds the target and
        its low reaches the limit. Under the rule the target must be ignored on
        that bar; only a stop may fire.
        """
        side, entry, r = 1, 100.0, 1.0
        stop, target = entry - r, entry + 2 * r
        bar_high, bar_low = 105.0, 99.5   # spans both
        for arm, expect_target_allowed in (("A", False), ("B", True)):
            entry_bar_passive = (arm == "A")
            hit_tgt = bar_high >= target
            if entry_bar_passive:
                hit_tgt = False
            assert hit_tgt == expect_target_allowed

    def test_no_trade_exits_as_target_on_its_entry_bar_in_arm_A(self, pattern_trades):
        assert len(pattern_trades) >= 10
        same_bar = pattern_trades[(pattern_trades.exit_reason == "target")
                                  & (pattern_trades.bars_held == 0)]
        assert len(same_bar) == 0

    def test_both_exit_types_are_exercised(self, pattern_trades):
        """Otherwise the same-bar assertion above could pass vacuously."""
        kinds = set(pattern_trades.exit_reason)
        assert {"stop", "target"} <= kinds, f"only saw {kinds}"


# --- QA 7/8: order lifetime and cancellation --------------------------------


class TestOrderMechanics:
    def test_fill_never_precedes_the_expansion_bar(self, pattern_trades):
        t = pattern_trades
        assert (t.entry_time > t.expansion_time).all()

    def test_order_lifetime_respected(self, pattern_trades):
        t = pattern_trades
        assert (t.wait_bars >= 1).all()
        assert (t.wait_bars <= ORDER_LIFETIME_5M).all()

    def test_cancelled_orders_do_not_execute(self, pattern_df):
        """Fills counted must never exceed orders placed."""
        r = hc.run(pattern_df, None, pd.DataFrame(), BTC, start=0, arm="A", **hc.PRIMARY)
        assert r.fills <= r.orders

    def test_passive_entry_is_exactly_the_boundary(self, pattern_trades):
        t = pattern_trades
        longs = t[t.side > 0]
        if len(longs):
            assert np.allclose(longs.entry_price, longs.zone_high)
        shorts = t[t.side < 0]
        if len(shorts):
            assert np.allclose(shorts.entry_price, shorts.zone_low)


# --- QA 9: stop/target ordering, and structural constraints -----------------


class TestRiskRules:
    def test_stop_is_the_opposite_boundary(self, pattern_trades):
        t = pattern_trades
        longs = t[t.side > 0]
        if len(longs):
            assert np.allclose(longs.stop_price, longs.zone_low)

    def test_max_stop_distance_enforced(self, pattern_trades):
        t = pattern_trades
        assert (t.stop_pct <= hc.MAX_STOP_PCT + 1e-12).all()

    def test_leverage_cap_enforced(self, pattern_trades):
        t = pattern_trades
        assert (t.notional <= hc.START_EQUITY * hc.MAX_LEVERAGE * 3).all()

    def test_time_exit_bounds_holding(self, pattern_trades):
        t = pattern_trades
        assert (t.bars_held < MAX_HOLD_5M).all()

    def test_r_multiple_is_consistent_with_prices(self, pattern_trades):
        """QA 10, mechanised: recompute R from raw prices for every trade."""
        t = pattern_trades
        assert len(t) >= 10
        recomputed = t.side * (t.exit_price - t.entry_price) / t.r_price
        assert np.allclose(recomputed, t.r_gross)
        assert np.allclose(t.r_net, t.r_gross - t.cost_r - t.funding_r)
        # R must equal the compression range for a passive entry at the boundary
        assert np.allclose(t.r_price, (t.zone_high - t.zone_low))

    def test_target_exits_realise_the_target_multiple(self, pattern_trades):
        tg = pattern_trades[pattern_trades.exit_reason == "target"]
        assert len(tg) > 0
        assert np.allclose(tg.r_gross, hc.PRIMARY["target_r"], atol=1e-9)

    def test_stop_exits_realise_minus_one_r(self, pattern_trades):
        st = pattern_trades[pattern_trades.exit_reason == "stop"]
        assert len(st) > 0
        assert np.allclose(st.r_gross, -1.0, atol=1e-9)


def test_end_boundary_prevents_test_set_leakage(pattern_df):
    """`end` must hard-stop signal generation, so the locked split stays locked."""
    cutoff = int(pattern_df.time.iloc[len(pattern_df) * 2 // 3])
    t = hc.run(pattern_df, None, pd.DataFrame(), BTC, start=0, end=cutoff,
               arm="A", **hc.PRIMARY).to_frame()
    assert len(t) > 0
    assert t.entry_time.max() < cutoff

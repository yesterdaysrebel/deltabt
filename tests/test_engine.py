"""Engine and cost-model tests.

These pin the behaviours that separate this engine from the Pine original:
integer contract rounding, a hard leverage cap, mark-price stop triggering,
snapshot funding, and pessimistic same-bar resolution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.config import StrategyParams, WprLatch
from deltabt.costs import SymbolCosts, funding_timestamps
from deltabt.data.quality import halt_mask, synthetic_mask, tradable_mask
from deltabt.engine import run_backtest
from deltabt.strategy import Signals, resample_5m

BTC = SymbolCosts(
    symbol="BTCUSD",
    tick_size=0.5,
    contract_value=0.001,
    maker_fee=0.0002,
    taker_fee=0.0005,
    max_leverage=200.0,
    position_size_limit=125_000,
    funding_interval_seconds=28800,
    slippage_bps=2.0,
)


class TestCosts:
    def test_gst_is_applied(self):
        """0.05% taker bills as 0.059% for Indian users."""
        assert BTC.effective_taker == pytest.approx(0.00059)
        assert BTC.effective_maker == pytest.approx(0.000236)

    def test_contracts_round_down(self):
        # 1.9999 BTC / 0.001 per contract = 1999.9 -> 1999
        assert BTC.contracts_for(1.9999) == 1999
        assert BTC.contracts_for(0.0009) == 0, "below one lot is not tradable"
        assert BTC.contracts_for(-5.0) == 0

    def test_contracts_respect_position_limit(self):
        small = SymbolCosts(**{**BTC.__dict__, "position_size_limit": 10})
        assert small.contracts_for(1.0) == 10

    def test_price_rounds_to_tick_grid(self):
        assert BTC.round_price(63_617.3) == 63_617.5
        assert BTC.round_price(63_617.3, direction=-1) == 63_617.0
        assert BTC.round_price(63_617.1, direction=1) == 63_617.5

    def test_cost_per_r_scales_inversely_with_stop_distance(self):
        """The arithmetic that makes a 1m Supertrend stop unviable."""
        tight = BTC.cost_per_r(60_000.0, 50.0)
        wide = BTC.cost_per_r(60_000.0, 500.0)
        assert tight == pytest.approx(wide * 10, rel=1e-6)
        # 0.118% + 4bps slippage on $60k against a $50 stop
        assert tight > 1.0

    def test_maker_exit_is_cheaper_than_taker(self):
        assert BTC.round_trip_rate(maker_exit=True) < BTC.round_trip_rate(maker_exit=False)


class TestFundingSchedule:
    def test_aligned_to_utc_epoch_not_window_start(self):
        """Delta settles at 00:00/08:00/16:00 UTC regardless of when we look."""
        stamps = funding_timestamps(1_700_000_123, 1_700_000_123 + 86_400, 28_800)
        assert all(s % 28_800 == 0 for s in stamps)
        assert len(stamps) == 3

    def test_four_hour_symbols_get_more_settlements(self):
        eight = funding_timestamps(0, 86_400, 28_800)
        four = funding_timestamps(0, 86_400, 14_400)
        assert len(four) > len(eight)

    def test_empty_when_window_shorter_than_interval(self):
        assert funding_timestamps(28_801, 28_900, 28_800).size == 0


class TestQualityMasks:
    def test_synthetic_requires_both_flat_and_zero_volume(self):
        df = pd.DataFrame({
            "time": [0, 60, 120, 180],
            "open": [10.0, 10.0, 10.0, 10.0],
            "high": [10.0, 11.0, 10.0, 10.0],
            "low": [10.0, 9.0, 10.0, 10.0],
            "close": [10.0, 10.0, 10.0, 10.0],
            "volume": [0.0, 0.0, 5.0, 0.0],
        })
        m = synthetic_mask(df)
        assert list(m) == [True, False, False, True], (
            "a zero-volume bar with real range, or a traded flat bar, is not synthetic"
        )

    def test_halt_masks_run_and_reopen_bar(self):
        n = 40
        df = pd.DataFrame({
            "time": np.arange(n) * 60,
            "open": np.full(n, 10.0), "high": np.full(n, 10.0),
            "low": np.full(n, 10.0), "close": np.full(n, 10.0),
            "volume": np.concatenate([[5.0] * 5, [0.0] * 25, [5.0] * 10]),
        })
        m = halt_mask(df, min_run=20)
        assert not m[4]
        assert m[5] and m[29], "the flat run is masked"
        assert m[30], "the gap-open reopen bar is masked too"
        assert not m[31]

    def test_short_illiquid_run_is_not_a_halt(self):
        n = 20
        df = pd.DataFrame({
            "time": np.arange(n) * 60,
            "open": np.full(n, 10.0), "high": np.full(n, 10.0),
            "low": np.full(n, 10.0), "close": np.full(n, 10.0),
            "volume": np.concatenate([[5.0] * 5, [0.0] * 3, [5.0] * 12]),
        })
        assert halt_mask(df, min_run=20).sum() == 0
        # ...but the individual bars are still untradable
        assert tradable_mask(df).sum() == 17


def test_resample_5m_aggregates_exactly():
    rng = np.random.default_rng(0)
    n = 100
    df = pd.DataFrame({
        "time": np.arange(n) * 60,
        "open": rng.random(n) + 100,
        "high": rng.random(n) + 101,
        "low": rng.random(n) + 99,
        "close": rng.random(n) + 100,
        "volume": rng.random(n) * 10,
    })
    out = resample_5m(df)
    assert len(out) == 20
    assert out["open"].iloc[0] == df["open"].iloc[0]
    assert out["close"].iloc[0] == df["close"].iloc[4]
    assert out["high"].iloc[0] == df["high"].iloc[:5].max()
    assert out["low"].iloc[0] == df["low"].iloc[:5].min()
    assert out["volume"].iloc[0] == pytest.approx(df["volume"].iloc[:5].sum())


# --- engine ----------------------------------------------------------------


def _flat_signals(n, *, long_at=None, stop=None, target_dir=1):
    """Signals with a single long entry and a fixed stop."""
    z = np.zeros(n, dtype=bool)
    le = z.copy()
    if long_at is not None:
        le[long_at] = True
    return Signals(
        long_entry=le,
        short_entry=z.copy(),
        stop_long=np.full(n, stop if stop is not None else np.nan),
        stop_short=np.full(n, np.nan),
        supertrend=np.full(n, np.nan),
        direction=np.full(n, -1.0),
        atr=np.zeros(n),
        wpr=np.full(n, np.nan),
        adx_1m=np.full(n, np.nan),
        adx_5m=np.full(n, np.nan),
        bull_1m=np.ones(n, dtype=bool),
        bear_1m=z.copy(),
        long_base=le.copy(),
        short_base=z.copy(),
        warmup=0,
    )


def _bars(n, price=60_000.0, *, high=None, low=None):
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 60,
        "open": np.full(n, price),
        "high": np.full(n, price) if high is None else high,
        "low": np.full(n, price) if low is None else low,
        "close": np.full(n, price),
        "volume": np.full(n, 100.0),
    })


def _params(**kw):
    base = dict(
        mode="corrected", risk_percent=0.5, reward_risk=2.0, max_leverage=3.0,
        min_stop_atr_mult=0.0, min_stop_ticks=1, max_hold_bars=0,
        exit_on_trend_flip=False, edge_trigger=False, cooldown_bars=0,
        max_cost_per_r=None, wpr=WprLatch(enabled=False),
    )
    base.update(kw)
    return StrategyParams(**base)


class TestEngine:
    def test_leverage_cap_binds(self):
        """A one-tick stop must not produce an unbounded position.

        This is the failure the original could not survive: entry at close,
        stop at the Supertrend line, and no cap lets qty = risk/tiny explode.
        """
        n = 10
        df = _bars(n)
        sig = _flat_signals(n, long_at=2, stop=59_999.5)  # 0.5 away
        res = run_backtest(df, df, pd.DataFrame(), sig, _params(), BTC)

        assert res.n_trades <= 1
        notional = BTC.notional(
            res.trades[0].contracts if res.trades else 0, 60_000.0
        ) if res.trades else 0.0
        # 3x cap on $10k equity, allowing for the entry fee already deducted
        assert notional <= 10_000 * 3.0 * 1.001

    def test_uncapped_leverage_would_explode(self):
        """Documents the original behaviour the cap exists to prevent."""
        n = 10
        df = _bars(n)
        sig = _flat_signals(n, long_at=2, stop=59_999.5)
        res = run_backtest(
            df, df, pd.DataFrame(), sig, _params(max_leverage=float("inf")), BTC
        )
        entered = res.trades or []
        if entered:
            assert BTC.notional(entered[0].contracts, 60_000.0) > 10_000 * 50

    def test_stop_triggers_on_mark_not_ltp(self):
        """Delta triggers stops on mark price by default.

        LTP never reaches the stop here; mark does. An engine that tests the
        LTP low would hold the position and report the wrong exit.
        """
        n = 10
        ltp = _bars(n, low=np.full(n, 60_000.0))
        mark = _bars(n)
        mark.loc[5, "low"] = 59_000.0  # mark dips through the stop

        sig = _flat_signals(n, long_at=2, stop=59_500.0)
        res = run_backtest(ltp, mark, pd.DataFrame(), sig, _params(), BTC)

        assert res.n_trades == 1
        assert res.trades[0].exit_reason == "stop"
        assert res.trades[0].exit_time == 5 * 60

    def test_same_bar_conflict_resolves_pessimistically(self):
        n = 10
        ltp = _bars(n)
        mark = _bars(n)
        mark.loc[5, "low"] = 59_000.0    # stop at 59,500 hit
        mark.loc[5, "high"] = 62_000.0   # target at 61,000 also hit

        sig = _flat_signals(n, long_at=2, stop=59_500.0)
        res = run_backtest(ltp, mark, pd.DataFrame(), sig, _params(), BTC)

        assert res.n_trades == 1
        t = res.trades[0]
        assert t.exit_reason == "stop", "worst case wins, matching Pine"
        assert t.ambiguous is True
        assert res.optimistic_pnl > t.pnl, "optimistic bound assumes the target"

    def test_target_exit_pays_maker_fee(self):
        n = 10
        ltp = _bars(n)
        mark = _bars(n)
        mark.loc[5, "high"] = 62_000.0

        sig = _flat_signals(n, long_at=2, stop=59_500.0)
        res = run_backtest(ltp, mark, pd.DataFrame(), sig, _params(), BTC)
        t = res.trades[0]
        assert t.exit_reason == "target"
        entry_fee = BTC.entry_cost(t.contracts, t.entry_price)
        maker_exit = BTC.exit_cost(t.contracts, t.exit_price, maker=True)
        assert t.fees == pytest.approx(entry_fee + maker_exit)

    def test_zero_contracts_after_rounding_is_rejected(self):
        """A position smaller than one lot is not a trade."""
        n = 10
        df = _bars(n, price=60_000.0)
        sig = _flat_signals(n, long_at=2, stop=1.0)  # enormous risk per unit
        res = run_backtest(df, df, pd.DataFrame(), sig, _params(), BTC)
        assert res.n_trades == 0
        assert res.rejects["zero_contracts"] >= 1

    def test_cost_gate_rejects_tight_stops(self):
        n = 10
        df = _bars(n)
        sig = _flat_signals(n, long_at=2, stop=59_990.0)  # 10 away on 60k
        res = run_backtest(
            df, df, pd.DataFrame(), sig, _params(max_cost_per_r=0.15), BTC
        )
        assert res.n_trades == 0
        assert res.rejects["cost_per_r"] == 1

    def test_untradable_bars_are_skipped(self):
        n = 10
        df = _bars(n)
        sig = _flat_signals(n, long_at=2, stop=59_000.0)
        tradable = np.ones(n, dtype=bool)
        tradable[2] = False
        res = run_backtest(
            df, df, pd.DataFrame(), sig, _params(), BTC, tradable=tradable
        )
        assert res.n_trades == 0
        assert res.rejects["untradable_bar"] == 1

    def test_funding_charged_only_at_snapshot(self):
        """Snapshot-based, not pro-rata: open across the instant or pay nothing."""
        n = 600  # 10 hours of 1m bars crosses one 8h boundary
        df = _bars(n)
        funding = pd.DataFrame({
            "time": np.arange(n, dtype="int64") * 60,
            "open": np.zeros(n), "high": np.zeros(n), "low": np.zeros(n),
            "close": np.full(n, 0.01),  # 0.01% per interval
            "volume": np.zeros(n),
        })
        sig = _flat_signals(n, long_at=2, stop=59_000.0)
        res = run_backtest(
            df, df, funding, sig, _params(max_hold_bars=500), BTC
        )
        assert res.n_trades == 1
        assert res.trades[0].funding > 0, "a long pays when the rate is positive"

    def test_max_hold_forces_exit(self):
        n = 200
        df = _bars(n)
        # Price is flat at 60,000, so neither the 59,000 stop nor the 62,000
        # target can trigger; only the holding cap can close this.
        sig = _flat_signals(n, long_at=2, stop=59_000.0)
        res = run_backtest(df, df, pd.DataFrame(), sig, _params(max_hold_bars=50), BTC)
        assert res.n_trades == 1
        assert res.trades[0].exit_reason == "max_hold"
        assert res.trades[0].bars_held == 50

    def test_cooldown_blocks_immediate_reentry(self):
        n = 60
        df = _bars(n)
        sig = _flat_signals(n, long_at=None, stop=59_500.0)
        sig.long_entry[:] = True   # a plateau, as the original produced
        sig.long_base[:] = True
        mark = _bars(n)
        mark.loc[5, "low"] = 59_000.0

        no_cd = run_backtest(df, mark, pd.DataFrame(), sig, _params(cooldown_bars=0), BTC)
        with_cd = run_backtest(df, mark, pd.DataFrame(), sig, _params(cooldown_bars=20), BTC)
        assert with_cd.rejects["cooldown"] > 0
        assert with_cd.n_trades <= no_cd.n_trades

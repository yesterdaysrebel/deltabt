"""Strategy-layer tests: HTF alignment, gate behaviour, and mode differences."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.config import StrategyParams, WprLatch
from deltabt.strategy import _broadcast_confirmed, build_signals, resample_5m
from deltabt.sweep import GridSpec, _mirror_fire, describe


def _synthetic_bars(n=6000, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 60_000 * np.exp(np.cumsum(rng.standard_normal(n) * 0.0006))
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 60,
        "open": close,
        "high": close * (1 + np.abs(rng.standard_normal(n)) * 0.0004),
        "low": close * (1 - np.abs(rng.standard_normal(n)) * 0.0004),
        "close": close,
        "volume": rng.random(n) * 100 + 1,
    })


class TestHtfAlignment:
    def test_broadcast_uses_last_closed_bar(self):
        """Within a 5m period every 1m bar must see the PREVIOUS 5m value.

        This is what makes the higher-timeframe read identical between
        backtest and live. Using the current (developing) 5m bar is what makes
        a live bot take trades the backtest never saw.
        """
        htf_time = np.array([0, 300, 600])
        htf_vals = np.array([10.0, 20.0, 30.0])
        ltf_time = np.arange(0, 900, 60)

        out = _broadcast_confirmed(htf_vals, htf_time, ltf_time)

        assert np.isnan(out[0]), "nothing has closed yet at t=0"
        assert np.all(np.isnan(out[0:5]))
        assert np.all(out[5:10] == 10.0), "the 0-300 bar's value, seen from 300"
        assert np.all(out[10:15] == 20.0)

    def test_never_leaks_the_current_bar(self):
        htf_time = np.array([0, 300, 600])
        htf_vals = np.array([10.0, 20.0, 30.0])
        ltf_time = np.arange(0, 900, 60)
        out = _broadcast_confirmed(htf_vals, htf_time, ltf_time)
        # 30.0 belongs to the bar opening at 600 and must not appear anywhere
        # in a window that ends at 840.
        assert 30.0 not in set(out[np.isfinite(out)].tolist())

    def test_resample_boundaries_are_utc_aligned(self):
        df = _synthetic_bars(600)
        out = resample_5m(df)
        assert np.all(out["time"].to_numpy() % 300 == 0)


class TestSignalGates:
    def test_parity_uses_the_original_uptick_rule(self):
        df = _synthetic_bars()
        p = StrategyParams.parity()
        sig = build_signals(df, p)
        # WPR(140) plus the full trend stack is close to unsatisfiable; the
        # point of parity mode is to reproduce that, not to trade.
        assert sig.long_entry.sum() + sig.short_entry.sum() < 50

    def test_disabling_wpr_increases_signals(self):
        df = _synthetic_bars()
        on = StrategyParams(wpr=WprLatch(enabled=True, length=14))
        off = StrategyParams(wpr=WprLatch(enabled=False))
        n_on = build_signals(df, on).long_entry.sum()
        n_off = build_signals(df, off).long_entry.sum()
        assert n_off > n_on

    def test_shorter_wpr_fires_more_than_longer(self):
        """The length cliff: 140 is unreachable under a trend stack, 14 is not."""
        df = _synthetic_bars()
        short = StrategyParams(wpr=WprLatch(enabled=True, length=14))
        long_ = StrategyParams(wpr=WprLatch(enabled=True, length=140))
        n_short = build_signals(df, short).long_entry.sum()
        n_long = build_signals(df, long_).long_entry.sum()
        assert n_short > n_long

    def test_structure_condition_is_effectively_a_noop(self):
        """`close > supertrend` is implied by `direction < 0` and filters ~nothing.

        On clean synthetic bars it is exactly a no-op. On real data it excluded
        6 of 1.33M bars, which is a genuine Pine edge case (the band ratcheting
        above price without a direction flip), so the assertion allows a
        vanishing fraction rather than demanding exact equality.
        """
        df = _synthetic_bars()
        base = dict(wpr=WprLatch(enabled=False), edge_trigger=False)
        with_s = build_signals(df, StrategyParams(use_structure=True, **base))
        without = build_signals(df, StrategyParams(use_structure=False, **base))

        removed = int((without.long_entry & ~with_s.long_entry).sum())
        assert removed / max(int(without.long_entry.sum()), 1) < 0.001
        assert not np.any(with_s.long_entry & ~without.long_entry), (
            "the condition may only ever remove signals, never add them"
        )

    def test_edge_trigger_collapses_plateaus(self):
        df = _synthetic_bars()
        base = dict(wpr=WprLatch(enabled=False))
        level = build_signals(df, StrategyParams(edge_trigger=False, **base))
        edge = build_signals(df, StrategyParams(edge_trigger=True, **base))
        assert edge.long_entry.sum() < level.long_entry.sum()
        # every edge signal must also be a level signal
        assert np.all(level.long_entry[edge.long_entry])

    def test_warmup_is_blanked(self):
        df = _synthetic_bars()
        p = StrategyParams(wpr=WprLatch(enabled=False))
        sig = build_signals(df, p)
        assert sig.warmup > 0
        assert not sig.long_entry[: sig.warmup].any()
        assert not sig.short_entry[: sig.warmup].any()

    def test_long_and_short_never_fire_together(self):
        df = _synthetic_bars()
        sig = build_signals(df, StrategyParams(wpr=WprLatch(enabled=False)))
        assert not np.any(sig.long_entry & sig.short_entry)

    def test_percentile_thresholds_adapt_to_the_symbol(self):
        """An absolute ADX of 25 means different things on different series."""
        df = _synthetic_bars()
        loose = build_signals(df, StrategyParams(
            wpr=WprLatch(enabled=False), adx_percentile_1m=0.10))
        tight = build_signals(df, StrategyParams(
            wpr=WprLatch(enabled=False), adx_percentile_1m=0.95))
        assert loose.long_entry.sum() > tight.long_entry.sum()


class TestParams:
    def test_parity_preset_reproduces_the_original(self):
        p = StrategyParams.parity()
        assert p.wpr.length == 140
        assert p.adx_threshold_1m == 25.0
        assert p.use_structure is True
        assert p.edge_trigger is False
        assert p.max_cost_per_r is None
        assert p.max_leverage == float("inf"), "the original had no cap"

    def test_validation_rejects_inverted_wpr_bands(self):
        with pytest.raises(ValueError, match="fire level"):
            StrategyParams(wpr=WprLatch(arm_long=-20.0, fire_long=-80.0)).validate()

    def test_validation_rejects_bad_percentile(self):
        with pytest.raises(ValueError, match="adx_percentile_1m"):
            StrategyParams(adx_percentile_1m=1.5).validate()

    def test_warmup_accounts_for_adx_seeding(self):
        p = StrategyParams(di_length=14, adx_smoothing=28, wpr=WprLatch(length=14))
        assert p.warmup_bars >= 14 + 2 * 28


class TestGrid:
    def test_wpr_off_is_always_a_candidate(self):
        combos = GridSpec().combinations()
        assert any(not c.wpr.enabled for c in combos)

    def test_length_140_is_excluded(self):
        lengths = {c.wpr.length for c in GridSpec().combinations() if c.wpr.enabled}
        assert 140 not in lengths

    def test_wpr_off_cells_are_deduplicated(self):
        """Off cells must not be replayed once per irrelevant sub-parameter."""
        grid = GridSpec(timeframes=((1, 5),),
                        wpr_length=(0, 14), wpr_fire_long=(-50.0, -20.0),
                        wpr_expiry=(30, 60), st_factor=(2.0,), st_atr_period=(14,),
                        di_length=(14,), adx_smoothing=(14,),
                        adx_percentile_1m=(0.7,), adx_percentile_5m=(None,))
        combos = grid.combinations()
        off = [c for c in combos if not c.wpr.enabled]
        assert len(off) == 1, f"expected 1 off cell, got {len(off)}"

    def test_mirror_fire_is_symmetric_about_midrange(self):
        assert _mirror_fire(-20.0) == -80.0
        assert _mirror_fire(-50.0) == -50.0
        assert _mirror_fire(-80.0) == -20.0

    def test_describe_reports_zero_length_when_off(self):
        p = StrategyParams(wpr=WprLatch(enabled=False, length=14))
        assert describe(p)["wpr_len"] == 0

    def test_default_grid_is_small_enough_to_be_meaningful(self):
        """Guardrail against reintroducing a grid that guarantees a false winner."""
        assert len(GridSpec().combinations()) < 1000

    def test_timeframe_is_a_grid_axis(self):
        """Base timeframe dominates cost per R, so it must be searchable."""
        combos = GridSpec().combinations()
        assert len({c.base_minutes for c in combos}) > 1
        for c in combos:
            assert c.confirm_minutes % c.base_minutes == 0
            assert c.confirm_minutes > c.base_minutes

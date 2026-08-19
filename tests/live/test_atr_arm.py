"""The ATR arm: 2 x ATR(10) stop, a 2R target derived from it, no ADX gate.

The point of this file is the DIFFERENCES from V3, because the arm shares V3's
indicator family and it would be easy to ship something that only looks
different. Each test names the property that separates them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config.variants import ALL, resolve_strategy
from app.strategy.atr_arm import ATR_ARM, AtrArmConfig, evaluate_atr, warmup_bars
from app.strategy.explanation import LONG, Outcome
from deltabt import indicators as ind


def bars(n=800, seed=5, drift=0.0):
    """Deterministic synthetic OHLC. No market data is read."""
    rng = np.random.default_rng(seed)
    close = 60_000 + np.cumsum(rng.normal(drift, 45, n))
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * 300 + 1_700_000_000,
        "open": close, "high": close + 30, "low": close - 30,
        "close": close, "volume": 1.0,
    })


class TestIdentity:

    def test_it_does_not_move_v3s_hash(self):
        """The whole reason it is a separate config object. StrategyConfig
        hashes its entire dataclass, so a new field there would move V1, V2,
        V2_LEVEL and V3 -- and V3 is pinned in monitor.yml and deploy.yml."""
        assert ALL["V3"].config_hash == "11461f2a11a96f8a"
        assert ATR_ARM.config_hash != ALL["V3"].config_hash

    def test_it_is_reachable_by_variant(self):
        for name in ("V4", "ATR", "v4_atr"):
            assert resolve_strategy({"DELTABOT_VARIANT": name}) is ATR_ARM

    def test_an_unknown_variant_still_refuses(self):
        with pytest.raises(ValueError, match="not a known variant"):
            resolve_strategy({"DELTABOT_VARIANT": "V5"})

    def test_the_spec_is_what_was_asked_for(self):
        assert ATR_ARM.stop_atr_period == 10
        assert ATR_ARM.stop_atr_mult == 2.0
        assert ATR_ARM.target_r == 2.0
        assert ATR_ARM.require_primary_adx is False      # ADX threshold dropped
        assert ATR_ARM.confirm_wpr is True               # 1m WPR confirms
        assert ATR_ARM.confirm_adx_di is False           # 1m ADX/DI does not
        assert ATR_ARM.wpr_period == 140
        assert ATR_ARM.max_stop_pct == 0.10
        assert ATR_ARM.fire_once is False


class TestTheStopIsATR:

    @pytest.fixture(scope="class")
    def detected(self):
        """Scan for real DETECTED setups on synthetic data."""
        df = bars(n=1200)
        out = []
        for i in range(warmup_bars(ATR_ARM) + 5, len(df), 7):
            w = df.iloc[: i + 1]
            e = evaluate_atr(w, w, ATR_ARM, symbol="X")
            if e.outcome is Outcome.DETECTED:
                out.append((w, e))
        if not out:
            pytest.skip("no setup on this series")
        return out

    def test_stop_distance_equals_two_atr(self, detected):
        for w, e in detected:
            h, l, c = (w[k].to_numpy("float64") for k in ("high", "low", "close"))
            atr = ind.atr(h, l, c, ATR_ARM.stop_atr_period)[-1]
            want = ATR_ARM.stop_atr_mult * atr
            assert e.detail["risk_per_unit"] == pytest.approx(want, rel=1e-12)
            assert abs(e.entry_price - e.stop_price) == pytest.approx(want, rel=1e-12)

    def test_the_stop_never_uses_the_leg_extreme(self, detected):
        """V3's stop is min(leg_low, supertrend). This one must not consult
        either, which is also why leg truncation cannot suppress it."""
        for _, e in detected:
            assert e.detail["stop_basis"] == "2.0xATR(10)"

    def test_target_is_two_r_of_the_atr_stop(self, detected):
        for _, e in detected:
            rpu = e.detail["risk_per_unit"]
            want = (e.entry_price + 2.0 * rpu if e.direction == LONG
                    else e.entry_price - 2.0 * rpu)
            assert e.target_price == pytest.approx(want, rel=1e-12)

    def test_stop_and_target_straddle_entry_correctly(self, detected):
        for _, e in detected:
            if e.direction == LONG:
                assert e.stop_price < e.entry_price < e.target_price
            else:
                assert e.target_price < e.entry_price < e.stop_price

    def test_a_wide_atr_is_refused_with_a_reason(self):
        cfg = AtrArmConfig(max_stop_pct=1e-6)
        df = bars(n=900)
        seen = False
        for i in range(warmup_bars(cfg) + 5, len(df), 11):
            w = df.iloc[: i + 1]
            e = evaluate_atr(w, w, cfg, symbol="X")
            if e.outcome is Outcome.REJECTED and "max_stop_pct" in (e.rejection_reason or ""):
                assert e.stop_distance_pct > 0
                seen = True
                break
        assert seen, "an impossibly tight cap must reject something"


class TestTheAdxGateIsOff:

    def test_the_named_check_is_present_but_always_true(self):
        """Kept as a named entry so persisted condition lists stay comparable
        across arms, but it must not be able to refuse a setup."""
        from app.strategy.atr_arm import _checks
        p = dict(direction=-1.0, plus_di=30.0, minus_di=10.0, adx=0.0,
                 wpr=-50.0, wpr_prev=-60.0, close=1.0, supertrend=0.9, atr=0.01)
        c = dict(p)
        long_checks, _ = _checks(p, c, ATR_ARM)
        named = dict(long_checks)
        assert "primary_adx_ge_min" in named
        assert named["primary_adx_ge_min"] is True, "ADX=0 must not block this arm"

    def test_turning_it_on_restores_the_threshold(self):
        from app.strategy.atr_arm import _checks
        cfg = AtrArmConfig(require_primary_adx=True)
        p = dict(direction=-1.0, plus_di=30.0, minus_di=10.0, adx=0.0,
                 wpr=-50.0, wpr_prev=-60.0, close=1.0, supertrend=0.9, atr=0.01)
        long_checks, _ = _checks(p, dict(p), cfg)
        assert dict(long_checks)["primary_adx_ge_min"] is False


class TestGuards:

    def test_short_history_is_suppressed_not_guessed(self):
        df = bars(n=50)
        e = evaluate_atr(df, df, ATR_ARM, symbol="X")
        assert e.outcome is Outcome.SUPPRESSED
        assert "warm-up incomplete" in e.rejection_reason

    def test_warmup_accounts_for_the_stop_atr_period(self):
        assert warmup_bars(AtrArmConfig(stop_atr_period=10_000)) > 10_000

    def test_config_rejects_a_nonsense_stop(self):
        with pytest.raises(ValueError, match="stop_atr_mult"):
            AtrArmConfig(stop_atr_mult=0).validate()

    def test_config_rejects_a_swapped_timeframe_pair(self):
        with pytest.raises(ValueError, match="5m primary"):
            AtrArmConfig(primary_timeframe="1m", confirmation_timeframe="5m").validate()

    def test_supertrend_is_called_factor_first(self, monkeypatch):
        """The transposition that corrupted an earlier analysis. Asserted on
        the ACTUAL runtime call, not on the config."""
        import app.strategy.atr_arm as mod
        seen = []
        real = ind.supertrend
        monkeypatch.setattr(mod.ind, "supertrend",
                            lambda h, l, c, *a: seen.append(a) or real(h, l, c, *a))
        df = bars(n=600)
        evaluate_atr(df, df, ATR_ARM, symbol="X")
        assert seen[0] == (2.0, 10), f"expected (factor=2.0, period=10), got {seen[0]}"

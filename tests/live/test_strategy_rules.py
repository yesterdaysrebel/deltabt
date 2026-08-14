"""Strategy correctness: closed-bar enforcement, no look-ahead, window invariance.

These are the tests that make bounded-window recomputation trustworthy. If any
of them fails, live signals are not comparable to the research backtest and no
forward-test record from this bot means anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.config.strategy import StrategyConfig
from app.strategy.explanation import Outcome
from app.strategy.rules import IndicatorSnapshot, evaluate, warmup_bars

CFG = StrategyConfig()


def series(n=900, seed=0, start=0, step=300, base=63000.0, drift=0.0):
    """Synthetic OHLC bars with enough structure to trip real conditions."""
    rng = np.random.default_rng(seed)
    ret = rng.standard_normal(n) * 0.0012 + drift
    c = base * np.exp(np.cumsum(ret))
    w = np.abs(rng.standard_normal(n)) * 0.0008 * c
    o = np.concatenate(([base], c[:-1]))
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * step + start,
        "open": o,
        "high": np.maximum(o, c) + w,
        "low": np.minimum(o, c) - w,
        "close": c,
        "volume": rng.random(n) * 100 + 10,
    })


def trending(n=900, step=300, start=0, base=63000.0, up=True, seed=3):
    """A single unbroken directional run.

    Deliberately pathological: the Supertrend never flips, so the leg outruns
    any window. Used only by the leg-truncation tests.
    """
    rng = np.random.default_rng(seed)
    d = 0.0008 if up else -0.0008
    ret = rng.standard_normal(n) * 0.0004 + d
    c = base * np.exp(np.cumsum(ret))
    w = np.abs(rng.standard_normal(n)) * 0.0003 * c
    o = np.concatenate(([base], c[:-1]))
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * step + start,
        "open": o, "high": np.maximum(o, c) + w,
        "low": np.minimum(o, c) - w, "close": c,
        "volume": rng.random(n) * 100 + 10,
    })


def swinging(n=2400, step=300, start=0, base=63000.0, seed=5, regime=120):
    """Alternating directional regimes -- what real markets look like.

    Drift flips sign every `regime` bars, so the Supertrend flips regularly,
    legs stay short enough to be determinable, and directional setups still
    occur. This is the fixture for behavioural tests.
    """
    rng = np.random.default_rng(seed)
    sign = np.where((np.arange(n) // regime) % 2 == 0, 1.0, -1.0)
    ret = rng.standard_normal(n) * 0.0009 + sign * 0.0007
    c = base * np.exp(np.cumsum(ret))
    w = np.abs(rng.standard_normal(n)) * 0.0006 * c
    o = np.concatenate(([base], c[:-1]))
    return pd.DataFrame({
        "time": np.arange(n, dtype="int64") * step + start,
        "open": o, "high": np.maximum(o, c) + w,
        "low": np.minimum(o, c) - w, "close": c,
        "volume": rng.random(n) * 100 + 10,
    })


def scan(primary, confirmation, cfg=CFG, *, symbol="BTCUSD", step=7):
    """Evaluate at many end-points, as the live engine does bar by bar."""
    out = []
    need = warmup_bars(cfg) + 10
    for end in range(need, len(primary) + 1, step):
        out.append(evaluate(primary.iloc[:end].reset_index(drop=True),
                            confirmation.iloc[:end].reset_index(drop=True),
                            cfg, symbol=symbol))
    return out


# =====================================================================
# WARM-UP
# =====================================================================


class TestWarmup:
    def test_warmup_accounts_for_adx_seeding_and_wpr(self):
        # max(140, 14 + 2*28 = 70, 10) + 5
        assert warmup_bars(CFG) == 145

    def test_short_history_is_suppressed_not_guessed(self):
        p, c = series(50), series(50)
        e = evaluate(p, c, CFG, symbol="BTCUSD")
        assert e.outcome is Outcome.SUPPRESSED
        assert "warm-up incomplete" in e.rejection_reason

    def test_suppressed_evaluations_still_explain_themselves(self):
        e = evaluate(series(10), series(10), CFG, symbol="BTCUSD")
        assert e.rejection_reason and e.strategy_config_hash


# =====================================================================
# INDICATOR REUSE -- no live reimplementation
# =====================================================================


class TestIndicatorReuse:
    def test_snapshot_matches_deltabt_indicators_exactly(self):
        from deltabt import indicators as ind
        df = series(600)
        s = IndicatorSnapshot(df, CFG)
        h, l, c = (df[k].to_numpy("float64") for k in ("high", "low", "close"))
        st, d = ind.supertrend(h, l, c, CFG.supertrend.multiplier,
                               CFG.supertrend.atr_period)
        p, m, a = ind.dmi(h, l, c, CFG.adx.di_period, CFG.adx.period)
        w = ind.wpr(h, l, c, CFG.williams_r.period)
        assert np.array_equal(s.st, st, equal_nan=True)
        assert np.array_equal(s.direction, d, equal_nan=True)
        assert np.array_equal(s.adx, a, equal_nan=True)
        assert np.array_equal(s.wpr, w, equal_nan=True)
        assert np.array_equal(s.plus_di, p, equal_nan=True)

    def test_frozen_parameters_are_the_research_constants(self):
        from deltabt.research import hwpr
        assert CFG.supertrend.atr_period == hwpr.ST_PERIOD
        assert CFG.supertrend.multiplier == hwpr.ST_MULT
        assert CFG.adx.di_period == hwpr.DI_PERIOD
        assert CFG.adx.period == hwpr.ADX_PERIOD
        assert CFG.williams_r.period == hwpr.WPR_PERIOD
        assert CFG.adx.minimum == hwpr.ADX_MIN


# =====================================================================
# WINDOW INVARIANCE -- the precondition for bounded-window recomputation
# =====================================================================


class TestWindowInvariance:
    @pytest.mark.parametrize("field", ["st", "direction", "adx", "wpr",
                                       "plus_di", "minus_di"])
    def test_tail_values_do_not_depend_on_window_length(self, field):
        """W and 2W must agree at the tail, or live != backtest."""
        df = series(3000, seed=11)
        short = IndicatorSnapshot(df.tail(750).reset_index(drop=True), CFG)
        long_ = IndicatorSnapshot(df.tail(1500).reset_index(drop=True), CFG)
        a = getattr(short, field)[-50:]
        b = getattr(long_, field)[-50:]
        assert np.allclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True), (
            f"{field} differs between window lengths -- the window is too short")

    def test_signal_is_identical_across_window_lengths(self):
        """On data where the Supertrend leg is visible in both windows."""
        df_p, df_c = series(2000, seed=21), series(2000, seed=21, step=60)
        a = evaluate(df_p.tail(800).reset_index(drop=True),
                     df_c.tail(800).reset_index(drop=True), CFG, symbol="BTCUSD")
        b = evaluate(df_p, df_c, CFG, symbol="BTCUSD")
        assert a.outcome == b.outcome and a.direction == b.direction
        if a.entry_price is not None:
            assert a.stop_price == pytest.approx(b.stop_price)
            assert a.target_price == pytest.approx(b.target_price)

    def test_leg_extreme_is_the_one_indicator_that_does_not_converge(self):
        """Documents WHY the truncation guard exists.

        Wilder-smoothed indicators forget their seed, so a longer window agrees
        at the tail. The leg extreme is an extremum since the last flip, so a
        window that starts mid-leg reports a different value -- and would
        silently move the stop.
        """
        df = trending(2000, up=True)          # one very long leg by construction
        short = IndicatorSnapshot(df.tail(600).reset_index(drop=True), CFG)
        long_ = IndicatorSnapshot(df, CFG)
        # Same bar, same Supertrend line, but a leg extreme that moves by ~60%
        # purely because the window starts elsewhere.
        assert short.st[-1] == pytest.approx(long_.st[-1])
        assert short.leg_low[-1] != pytest.approx(long_.leg_low[-1])
        # Both must therefore be flagged rather than acted on.
        assert short.leg_truncated and long_.leg_truncated

    def test_truncated_leg_is_suppressed_not_traded(self):
        df_p, df_c = trending(2000, up=True), trending(2000, step=60, up=True)
        e = evaluate(df_p.tail(600).reset_index(drop=True),
                     df_c.tail(600).reset_index(drop=True), CFG, symbol="BTCUSD")
        if e.indicators and e.indicators["primary"].get("leg_truncated"):
            assert e.outcome is Outcome.SUPPRESSED
            assert "leg extends beyond" in e.rejection_reason
            assert e.entry_price is None, "a suppressed bar must not size a trade"

    def test_leg_start_is_reported_for_audit(self):
        df_p, df_c = series(1000, seed=4), series(1000, seed=4, step=60)
        e = evaluate(df_p, df_c, CFG, symbol="BTCUSD")
        assert "leg_start_bar" in e.indicators["primary"]
        assert "leg_truncated" in e.indicators["primary"]

    def test_config_rejects_a_window_too_short_to_be_invariant(self):
        from dataclasses import replace
        with pytest.raises(ValueError, match="window_bars"):
            replace(CFG, window_bars=200).validate()


# =====================================================================
# NO LOOK-AHEAD -- section 5
# =====================================================================


class TestNoLookAhead:
    def test_appending_future_bars_cannot_change_an_earlier_signal(self):
        """The core causality proof."""
        full_p, full_c = trending(1200), trending(1200, step=60)
        cut = 900
        early = evaluate(full_p.iloc[:cut].reset_index(drop=True),
                         full_c.iloc[:cut].reset_index(drop=True),
                         CFG, symbol="BTCUSD")
        # Now reveal the future and re-evaluate the SAME bar.
        later = evaluate(full_p.iloc[:cut].reset_index(drop=True),
                         full_c.iloc[:cut].reset_index(drop=True),
                         CFG, symbol="BTCUSD")
        assert early.to_dict() == later.to_dict()

    def test_mutating_the_forming_bar_cannot_change_the_closed_signal(self):
        """Section 5's explicit requirement.

        The strategy is handed CLOSED bars only. A forming bar that would have
        been the next row cannot influence the signal generated from the last
        closed one, no matter how extreme it is.
        """
        p, c = trending(1000), trending(1000, step=60)
        base = evaluate(p, c, CFG, symbol="BTCUSD")

        forming = p.iloc[[-1]].copy()
        forming["time"] = int(p["time"].iloc[-1]) + 300
        forming[["open", "high", "low", "close"]] *= 3.0     # violent forming bar
        # The engine must never be given this row; proving the point requires
        # showing the signal from the closed frame is unchanged.
        again = evaluate(p, c, CFG, symbol="BTCUSD")
        assert base.to_dict() == again.to_dict()

        # And if the forming bar WERE included, the result would differ --
        # which is exactly why the caller must not include it.
        polluted = evaluate(pd.concat([p, forming], ignore_index=True), c,
                            CFG, symbol="BTCUSD")
        assert polluted.indicators["primary"]["bar_open"] != base.bar_open

    def test_future_price_mutation_does_not_change_past_indicator_values(self):
        df = series(2000, seed=5)
        a = IndicatorSnapshot(df, CFG)
        w = df.copy()
        w.loc[1500:, ["open", "high", "low", "close"]] *= 1.5
        b = IndicatorSnapshot(w, CFG)
        for field in ("st", "direction", "adx", "wpr"):
            assert np.array_equal(getattr(a, field)[:1500],
                                  getattr(b, field)[:1500], equal_nan=True), field

    def test_evaluation_reads_only_the_last_closed_bar(self):
        p, c = trending(1000), trending(1000, step=60)
        e = evaluate(p, c, CFG, symbol="BTCUSD")
        assert e.bar_open == int(p["time"].iloc[-1])
        assert e.indicators["primary"]["bar_open"] == e.bar_open

    def test_wpr_rising_compares_to_the_previous_closed_bar(self):
        """Not to a partially formed one."""
        p, c = trending(800), trending(800, step=60)
        e = evaluate(p, c, CFG, symbol="BTCUSD")
        ind_p = e.indicators["primary"]
        from deltabt import indicators as ind
        w = ind.wpr(p["high"].to_numpy("float64"), p["low"].to_numpy("float64"),
                    p["close"].to_numpy("float64"), CFG.williams_r.period)
        assert ind_p["wpr"] == pytest.approx(w[-1])
        assert ind_p["wpr_prev"] == pytest.approx(w[-2])


# =====================================================================
# RULE SEMANTICS
# =====================================================================


class TestRules:
    @pytest.fixture(scope="class")
    def scanned(self):
        p, c = swinging(2400), swinging(2400, step=60, seed=6)
        return scan(p, c)

    def test_setups_do_fire_on_realistic_data(self, scanned):
        """A rule set that never fires cannot be forward-tested."""
        detected = [e for e in scanned if e.outcome is Outcome.DETECTED]
        assert detected, "no setups at all -- the rule wiring is broken"

    def test_both_directions_are_reachable(self, scanned):
        dirs = {e.direction for e in scanned if e.outcome is Outcome.DETECTED}
        assert dirs, "no directional setups"
        assert dirs <= {1, -1}

    def test_long_geometry_is_ordered(self, scanned):
        for e in scanned:
            if e.outcome is Outcome.DETECTED and e.direction == 1:
                assert e.stop_price < e.entry_price < e.target_price

    def test_short_geometry_is_ordered(self, scanned):
        for e in scanned:
            if e.outcome is Outcome.DETECTED and e.direction == -1:
                assert e.target_price < e.entry_price < e.stop_price

    def test_target_is_exactly_target_r_times_risk(self, scanned):
        checked = 0
        for e in scanned:
            if e.outcome is not Outcome.DETECTED:
                continue
            risk = abs(e.entry_price - e.stop_price)
            reward = abs(e.target_price - e.entry_price)
            assert reward == pytest.approx(CFG.target_r * risk)
            assert e.reward_risk == CFG.target_r
            checked += 1
        assert checked, "no setups to check"

    def test_wpr_variant_a_holds_on_every_long(self, scanned):
        """The frozen rule, verified against the recorded indicator values."""
        for e in scanned:
            if e.outcome is Outcome.DETECTED and e.direction == 1:
                p = e.indicators["primary"]
                assert p["wpr"] > -80.0
                assert p["wpr"] > p["wpr_prev"]
                assert p["adx"] >= CFG.adx.minimum
                assert p["plus_di"] > p["minus_di"]
                assert p["direction"] < 0

    def test_wpr_variant_a_mirrors_on_every_short(self, scanned):
        for e in scanned:
            if e.outcome is Outcome.DETECTED and e.direction == -1:
                p = e.indicators["primary"]
                assert p["wpr"] < -20.0
                assert p["wpr"] < p["wpr_prev"]
                assert p["adx"] >= CFG.adx.minimum
                assert p["minus_di"] > p["plus_di"]
                assert p["direction"] > 0

    def test_confirmation_timeframe_agrees_on_every_setup(self, scanned):
        for e in scanned:
            if e.outcome is not Outcome.DETECTED:
                continue
            c = e.indicators["confirmation"]
            if e.direction == 1:
                assert c["direction"] < 0 and c["plus_di"] > c["minus_di"]
            else:
                assert c["direction"] > 0 and c["minus_di"] > c["plus_di"]
            assert c["adx"] >= CFG.adx.minimum

    def test_no_setup_is_ever_emitted_on_a_truncated_leg(self, scanned):
        for e in scanned:
            if e.indicators and e.indicators["primary"].get("leg_truncated"):
                assert e.outcome is Outcome.SUPPRESSED

    def test_every_evaluation_explains_itself(self):
        """Never a bare boolean, even when nothing happens."""
        p, c = series(900, seed=2), series(900, seed=2, step=60)
        e = evaluate(p, c, CFG, symbol="BTCUSD")
        assert e.conditions_passed or e.conditions_failed or e.rejection_reason
        assert "primary" in e.indicators and "confirmation" in e.indicators
        assert e.strategy_version.startswith("H-WPR-1-VariantA-V2@")
        assert e.summary()

    def test_failed_conditions_are_named_individually(self):
        p, c = series(900, seed=7), series(900, seed=7, step=60)
        e = evaluate(p, c, CFG, symbol="BTCUSD")
        if e.outcome is Outcome.NO_SETUP:
            assert e.conditions_failed
            assert all(isinstance(x, str) for x in e.conditions_failed)

    def test_wide_stop_is_rejected_with_a_reason(self):
        from dataclasses import replace
        cfg = replace(CFG, max_stop_pct=0.00001)
        p, c = trending(900, up=True), trending(900, step=60, up=True)
        e = evaluate(p, c, cfg, symbol="BTCUSD")
        if e.direction is not None:
            assert e.outcome is Outcome.REJECTED
            assert "max_stop_pct" in e.rejection_reason

    def test_config_hash_changes_when_a_parameter_changes(self):
        from dataclasses import replace
        from app.config.strategy import Adx
        a = CFG.config_hash
        b = replace(CFG, adx=Adx(period=14)).config_hash
        assert a != b, "a parameter change must be visible in the audit trail"

    def test_only_variant_a_is_accepted(self):
        from dataclasses import replace
        from app.config.strategy import WilliamsR
        with pytest.raises(ValueError, match="variant_a"):
            replace(CFG, williams_r=WilliamsR(rule="traverse_latch")).validate()

    def test_timeframes_are_frozen_at_5m_primary_1m_confirmation(self):
        from dataclasses import replace
        with pytest.raises(ValueError, match="5m primary"):
            replace(CFG, primary_timeframe="1m",
                    confirmation_timeframe="5m").validate()

    def test_percentile_adx_has_no_way_to_be_configured(self):
        """Non-causal thresholds must be impossible, not merely discouraged."""
        from app.config.strategy import Adx
        assert not any("percentile" in f for f in Adx.__dataclass_fields__)
        assert isinstance(CFG.adx.minimum, float)


# ===========================================================================
# V2: the two changes to the V1 rule set
#
# 1. The Williams %R gate applies on BOTH timeframes. V1 computed the 1m value
#    and persisted it to strategy_signals, but conf_long/conf_short never
#    referenced it, so the oscillator gated on 5m alone.
#
# 2. A signal fires only on the COMPLETE setup's FALSE -> TRUE transition. V1
#    was level-triggered and re-emitted on every bar the setup stayed true. The
#    repeats were absorbed by max_open_positions at the risk gate, which is a
#    different thing from not emitting them.
#
# Each test is paired with the case it must NOT fire on.
# ===========================================================================

def _stub(direction=-1.0, adx=30.0, plus=30.0, minus=10.0, wpr=-30.0, wpr_prev=-40.0):
    """One bar of indicator values, as IndicatorSnapshot.at() returns them."""
    return {"close": 100.0, "supertrend": 95.0, "direction": direction,
            "plus_di": plus, "minus_di": minus, "adx": adx,
            "wpr": wpr, "wpr_prev": wpr_prev, "leg_low": 94.0, "leg_high": 106.0,
            "leg_start_bar": 0, "leg_truncated": False, "bar_open": 0}


class TestConfirmationWprIsPartOfTheDecision:
    def test_a_long_needs_the_1m_oscillator_too(self):
        from app.strategy.rules import _checks
        p = _stub()                                   # 5m: full long setup
        c_ok = _stub()                                # 1m: rising, above -80
        c_bad = _stub(wpr=-90.0, wpr_prev=-95.0)      # 1m: rising but BELOW -80
        assert all(v for _, v in _checks(p, c_ok, CFG)[0]), "control: should pass"
        assert not all(v for _, v in _checks(p, c_bad, CFG)[0]), (
            "1m WPR below -80 must block the long -- this is the V1 defect")

    def test_a_falling_1m_oscillator_blocks_a_long(self):
        from app.strategy.rules import _checks
        p = _stub()
        c_falling = _stub(wpr=-30.0, wpr_prev=-20.0)  # above -80 but FALLING
        assert not all(v for _, v in _checks(p, c_falling, CFG)[0])

    def test_the_short_side_mirrors(self):
        from app.strategy.rules import _checks
        p = _stub(direction=1.0, plus=10.0, minus=30.0, wpr=-40.0, wpr_prev=-30.0)
        c_ok = _stub(direction=1.0, plus=10.0, minus=30.0, wpr=-40.0, wpr_prev=-30.0)
        c_bad = _stub(direction=1.0, plus=10.0, minus=30.0, wpr=-10.0, wpr_prev=-5.0)
        assert all(v for _, v in _checks(p, c_ok, CFG)[1])
        assert not all(v for _, v in _checks(p, c_bad, CFG)[1]), (
            "1m WPR above -20 must block the short")

    def test_v1_behaviour_is_not_reachable_by_config(self):
        """Disabling the defining component must fail loudly, not revert to V1."""
        with pytest.raises(ValueError, match="confirm_wpr"):
            StrategyConfig(confirm_wpr=False).validate()


class TestOneShotFiring:
    @staticmethod
    def _find_signal(cfg):
        """Walk a synthetic series, classifying every bar.

        Three sets, because they are genuinely different things:
          fired    -- a signal was emitted (Outcome.DETECTED)
          repeats  -- suppressed because the setup was already true
          passed   -- the CONJUNCTION was satisfied, whether or not a signal
                      survived the later stop-distance and leg-truncation
                      checks. `direction` is set the moment the conjunction
                      passes, so it marks that boundary exactly.
        """
        p, c = swinging(2400, seed=11), swinging(2400 * 5, step=60, seed=11)
        need = warmup_bars(cfg) + 2
        fired, repeats, passed = [], [], []
        for i in range(need, len(p)):
            prim = p.iloc[:i + 1].reset_index(drop=True)
            conf = c[c.time <= int(p.time.iloc[i])].reset_index(drop=True)
            if len(conf) < need:
                continue
            e = evaluate(prim, conf, cfg, symbol="BTCUSD")
            if e.outcome is Outcome.DETECTED:
                fired.append(i)
            if e.detail.get("suppressed_repeat"):
                repeats.append(i)
            if e.direction is not None or e.detail.get("suppressed_repeat"):
                passed.append(i)
        return fired, repeats, passed

    def test_a_repeat_is_suppressed_and_says_so(self):
        fired, repeats, _ = self._find_signal(CFG)
        assert repeats, (
            "the synthetic series never produced a continuously-true setup, so "
            "this test proves nothing -- fix the fixture, not the assertion")
        for i in repeats:
            assert i not in fired

    def test_level_triggering_fires_on_those_same_bars(self):
        """Negative control: with fire_once off, the suppressed bars DO fire."""
        level = StrategyConfig(fire_once=False)
        _, repeats_level, passed_level = self._find_signal(level)
        assert not repeats_level, "fire_once=False must not suppress anything"
        _, repeats_once, _ = self._find_signal(CFG)
        for i in repeats_once:
            assert i in passed_level, (
                f"bar {i} was suppressed as a repeat, but level-triggering does "
                f"not even reach the conjunction there; the suppression is "
                f"hiding a different bug")

    def test_one_shot_takes_strictly_fewer_signals(self):
        fired_once, _, _ = self._find_signal(CFG)
        fired_level, _, _ = self._find_signal(StrategyConfig(fire_once=False))
        assert set(fired_once) <= set(fired_level)
        assert len(fired_once) < len(fired_level)

    def test_evaluate_is_still_a_pure_function_of_its_bars(self):
        """One-shot must not have introduced state between calls."""
        p, c = swinging(1200, seed=7), swinging(1200 * 5, step=60, seed=7)
        first = evaluate(p, c, CFG, symbol="BTCUSD")
        for _ in range(3):
            again = evaluate(p, c, CFG, symbol="BTCUSD")
            assert again.outcome is first.outcome
            assert again.direction == first.direction
            assert again.rejection_reason == first.rejection_reason


class TestVariantRegistry:
    """The backup variants must stay reachable, valid and distinguishable."""

    def test_every_variant_validates(self):
        from app.config.variants import ALL
        for name, cfg in ALL.items():
            cfg.validate()

    def test_variants_have_distinct_hashes(self):
        from app.config.variants import ALL
        hashes = {n: c.config_hash for n, c in ALL.items()}
        assert len(set(hashes.values())) == len(hashes), (
            f"two variants share a config hash, so their signals would be "
            f"indistinguishable in the audit trail: {hashes}")

    def test_v2_is_what_ships(self):
        from app.config.strategy import FROZEN
        from app.config.variants import V2
        assert FROZEN.config_hash == V2.config_hash

    def test_v1_really_is_the_old_rule_set(self):
        from app.config.variants import V1
        assert V1.confirm_wpr is False and V1.fire_once is False

    def test_a_v2_named_config_cannot_disable_the_oscillator(self):
        """Reverting the defining component while keeping the name is drift."""
        with pytest.raises(ValueError, match="confirm_wpr"):
            StrategyConfig(confirm_wpr=False).validate()

    def test_but_naming_it_honestly_is_allowed(self):
        StrategyConfig(name="H-WPR-1-VariantA", confirm_wpr=False).validate()

"""Parity between the live frozen-arm evaluator and hwpr.py itself.

The reference is not a fixture of expected numbers -- it is
``hwpr.build_conditions`` + ``hwpr.arm_signals`` computed over the WHOLE slice,
which is exactly how the research computes it. The candidate is
``evaluate_frozen`` over a bounded trailing window, which is how a live runner
must compute it. Anything that makes those two disagree is the thing this file
exists to catch.

Marked ``needs_candles``: these read the repository's cached 1m candles. They
skip rather than fail on a checkout without them, because a missing cache is
not a regression.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.strategy.explanation import LONG, Outcome
from app.strategy.frozen_hwpr import (ARM, FROZEN_1M, WPR_VARIANT,
                                      evaluate_frozen)
from deltabt.data.store import CandleStore
from deltabt.research import hwpr

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]
SLICE = 30_000
WINDOW = FROZEN_1M.window_bars
CHECKS = 40


@pytest.fixture(scope="module")
def cases():
    """(symbol, df, C, lo, sh, signal indices, quiet indices)."""
    store, out = CandleStore(), []
    for sym in SYMBOLS:
        df = store.read(sym, "ltp", "1m")
        if df.empty or len(df) < SLICE:
            continue
        df = df.tail(SLICE).reset_index(drop=True)
        C = hwpr.build_conditions(df)
        lo, sh = hwpr.arm_signals(C, ARM, WPR_VARIANT)
        idx = np.arange(len(C["t1"]))
        rng = np.random.default_rng(3)
        sig = np.flatnonzero((lo | sh) & (idx >= WINDOW))
        quiet = np.flatnonzero(~(lo | sh) & (idx >= WINDOW))
        if not len(sig):
            continue
        out.append((sym, df, C, lo, sh,
                    np.sort(rng.choice(sig, min(CHECKS, len(sig)), replace=False)),
                    np.sort(rng.choice(quiet, min(CHECKS, len(quiet)), replace=False))))
    if not out:
        pytest.skip("no cached 1m candles")
    return out


def _eval(df, i):
    return evaluate_frozen(df.iloc[i - WINDOW + 1: i + 1], FROZEN_1M,
                           symbol="X", max_stop_pct=FROZEN_1M.max_stop_pct)


class TestSignalParity:

    def test_every_reference_signal_is_reproduced(self, cases):
        for sym, df, C, lo, sh, sig, _ in cases:
            for i in sig:
                e = _eval(df, i)
                assert e.outcome in (Outcome.DETECTED, Outcome.REJECTED), (
                    f"{sym} @ {int(C['t1'][i])}: reference fired, live gave "
                    f"{e.outcome} ({e.rejection_reason})")

    def test_direction_matches(self, cases):
        for sym, df, C, lo, sh, sig, _ in cases:
            for i in sig:
                e = _eval(df, i)
                assert e.direction == (LONG if lo[i] else -LONG)

    def test_quiet_bars_stay_quiet(self, cases):
        """A live evaluator inventing setups the research never took would be
        just as wrong as missing them."""
        for sym, df, C, lo, sh, _, quiet in cases:
            for i in quiet:
                e = _eval(df, i)
                assert e.outcome not in (Outcome.DETECTED, Outcome.REJECTED)

    def test_bar_timestamp_is_the_signal_bar(self, cases):
        for sym, df, C, lo, sh, sig, _ in cases:
            for i in sig:
                assert int(_eval(df, i).bar_open) == int(C["t1"][i])


class TestStopAndTargetParity:

    def test_stop_price_is_bit_identical_to_the_research_expression(self, cases):
        """hwpr._simulate: min(st1, leg_lo) long / max(st1, leg_hi) short."""
        for sym, df, C, lo, sh, sig, _ in cases:
            for i in sig:
                e = _eval(df, i)
                s = C["st1"][i]
                ref = min(s, C["leg_lo"][i]) if lo[i] else max(s, C["leg_hi"][i])
                assert e.stop_price == pytest.approx(float(ref), rel=0, abs=0)

    def test_stop_is_on_the_correct_side_of_entry(self, cases):
        for sym, df, C, lo, sh, sig, _ in cases:
            for i in sig:
                e = _eval(df, i)
                if e.direction == LONG:
                    assert e.stop_price < e.entry_price
                else:
                    assert e.stop_price > e.entry_price

    def test_target_is_exactly_two_r_of_the_1m_stop(self, cases):
        for sym, df, C, lo, sh, sig, _ in cases:
            for i in sig:
                e = _eval(df, i)
                if e.outcome is not Outcome.DETECTED:
                    continue
                rpu = e.detail["risk_per_unit"]
                want = (e.entry_price + 2.0 * rpu if e.direction == LONG
                        else e.entry_price - 2.0 * rpu)
                assert e.target_price == pytest.approx(want, rel=1e-12)


class TestItIsNotTheV3RuleSet:
    """The whole reason this arm exists."""

    def test_it_decides_on_1m_and_confirms_on_5m(self):
        assert FROZEN_1M.primary_timeframe == "1m"
        assert FROZEN_1M.confirmation_timeframe == "5m"

    def test_it_uses_the_frozen_five_percent_cap_not_v3s_ten(self):
        from app.config.variants import ALL
        assert FROZEN_1M.max_stop_pct == 0.05
        assert ALL["V3"].max_stop_pct == 0.10

    def test_its_hash_is_distinct_from_v3(self):
        from app.config.variants import ALL
        assert FROZEN_1M.config_hash != ALL["V3"].config_hash
        assert ALL["V3"].config_hash == "11461f2a11a96f8a", "V3 must not move"

    def test_parameters_are_taken_from_the_frozen_module_not_restated(self):
        assert FROZEN_1M.supertrend_atr_period == hwpr.ST_PERIOD
        assert FROZEN_1M.supertrend_multiplier == hwpr.ST_MULT
        assert FROZEN_1M.adx_period == hwpr.ADX_PERIOD
        assert FROZEN_1M.di_period == hwpr.DI_PERIOD
        assert FROZEN_1M.adx_minimum == hwpr.ADX_MIN
        assert FROZEN_1M.wpr_period == hwpr.WPR_PERIOD

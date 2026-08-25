"""``deltabt.rulecore`` against the hand-written live arms, on real bars.

WHY THESE TESTS ARE THE POINT OF THE MODULE
    ``rulecore`` exists so a strategy is defined once and both the backtester
    and the bot execute that definition. That claim is only worth anything if
    the shared core reproduces what the hand-written arms actually did. These
    tests establish it on cached market data rather than on synthetic bars,
    because the interesting disagreements are all in warm-up, NaN handling and
    timeframe alignment -- none of which a toy fixture exercises.

    They also guard the confirmation-alignment defect the core exposed in
    ``app/strategy/rules.py``: the arm paired the previous PRIMARY bar with the
    previous CONFIRMATION bar, one minute back instead of one primary bar back.
    That is fixed; the test asserts the fix and that the old spelling would
    still be caught.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from deltabt import rulecore
from deltabt.spec import StrategySpec, TimeframeRules
from deltabt.strategy import resample_ohlcv

from app.config.strategy import StrategyConfig
from app.strategy.rules import (IndicatorSnapshot, _checks,
                                _confirm_bars_per_primary)

CANDLES = Path("data/candles/BTCUSD/ltp_1m.parquet")
#: Enough 1m bars for ~2,400 primary bars: several hundred Supertrend legs and
#: a few thousand warm-up bars, while staying fast enough for the suite.
BARS = 12_000


pytestmark = pytest.mark.skipif(
    not CANDLES.exists(), reason="BTCUSD 1m candles not cached")


@pytest.fixture(scope="module")
def bars() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = (pd.read_parquet(CANDLES).sort_values("time")
          .reset_index(drop=True).tail(BARS).reset_index(drop=True))
    return df, resample_ohlcv(df, 5)


@pytest.fixture(scope="module")
def hwpr_spec() -> StrategySpec:
    """The spec form of ``StrategyConfig()`` -- H-WPR-1 Variant A, V2."""
    gate = TimeframeRules(supertrend="aligned", di=True, adx_min=25.0,
                          wpr_rule="variant_a",
                          wpr_long_level=-80.0, wpr_short_level=-20.0)
    return StrategySpec(
        name="H-WPR-1-VariantA-V2", primary_minutes=5, confirm_minutes=1,
        st_multiplier=2.0, st_atr_period=10, di_period=14, adx_period=28,
        wpr_period=140, primary=gate, confirm=gate,
        trigger="edge", stop="leg_extreme", target_r=2.0, max_stop_pct=0.05)


def _live_setup_truth(one_min, five_min, cidx, cfg):
    """The live arm's conjunction at every primary bar, as boolean arrays."""
    P = IndicatorSnapshot(five_min, cfg)
    C = IndicatorSnapshot(one_min, cfg)
    n = len(five_min)
    long_ok = np.zeros(n, dtype=bool)
    short_ok = np.zeros(n, dtype=bool)
    for i in range(n):
        j = int(cidx[i])
        if j < 1 or i < 1:
            continue
        cl, cs = _checks(P.at(i), C.at(j), cfg)
        long_ok[i] = all(v for _, v in cl)
        short_ok[i] = all(v for _, v in cs)
    return long_ok, short_ok


def test_spec_mirrors_the_live_config(hwpr_spec):
    """Every constant the live arm freezes appears in the spec unchanged."""
    cfg = StrategyConfig()
    assert hwpr_spec.st_atr_period == cfg.supertrend.atr_period
    assert hwpr_spec.st_multiplier == cfg.supertrend.multiplier
    assert hwpr_spec.di_period == cfg.adx.di_period
    assert hwpr_spec.adx_period == cfg.adx.period
    assert hwpr_spec.primary.adx_min == cfg.adx.minimum
    assert hwpr_spec.wpr_period == cfg.williams_r.period
    assert hwpr_spec.target_r == cfg.target_r
    assert hwpr_spec.max_stop_pct == cfg.max_stop_pct
    # Same warm-up requirement, computed independently on each side.
    from app.strategy.rules import warmup_bars
    assert hwpr_spec.warmup_bars == warmup_bars(cfg)


def test_setup_truth_matches_the_live_arm_exactly(bars, hwpr_spec):
    """The conjunction itself agrees on every bar, with no exceptions.

    This is the load-bearing test. If the gates agree bar for bar, the core is
    evaluating the same strategy; everything after it is trigger and stop
    arithmetic, tested separately.
    """
    one_min, five_min = bars
    cfg = StrategyConfig()
    sig = rulecore.compute(five_min, one_min, hwpr_spec)
    live_long, live_short = _live_setup_truth(
        one_min, five_min, sig.confirm_index, cfg)

    start = hwpr_spec.warmup_bars + 5
    assert start < len(five_min), "sample too short to clear warm-up"
    core_long = sig.long_setup[start:]
    core_short = sig.short_setup[start:]

    # A test that passes because nothing ever fires proves nothing.
    assert core_long.sum() + core_short.sum() > 20, "no setups in sample"

    np.testing.assert_array_equal(core_long, live_long[start:])
    np.testing.assert_array_equal(core_short, live_short[start:])


def test_previous_bar_lookup_is_timeframe_aligned(bars, hwpr_spec):
    """Regression guard for the confirmation-alignment defect.

    ``app.strategy.rules.evaluate`` used to answer "was this setup already true
    on the previous closed bar?" with ``_checks(P.at(-2), C.at(-2), cfg)``. On
    a 5m primary with 1m confirmation, ``P.at(-2)`` is five minutes back but
    ``C.at(-2)`` is only ONE minute back: the aligned bar is ``C.at(-6)``. The
    suppression decision was taken against a pairing that was never itself an
    evaluation, and on this sample it changed the firing decision on 10 of 600
    bars -- both suppressing genuine FALSE->TRUE edges and admitting repeats.

    Both spellings are computed here. The aligned one must match the core, and
    the old one must NOT -- otherwise this test would still pass on a sample
    where the two happen to coincide and would stop guarding anything.
    """
    one_min, five_min = bars
    cfg = StrategyConfig()
    sig = rulecore.compute(five_min, one_min, hwpr_spec)
    cidx = sig.confirm_index
    step = _confirm_bars_per_primary(cfg)
    assert step == 5, "5m primary over 1m confirmation is five bars"

    P = IndicatorSnapshot(five_min, cfg)
    C = IndicatorSnapshot(one_min, cfg)
    start = hwpr_spec.warmup_bars + 5

    aligned_disagreements = misaligned_disagreements = considered = 0
    for i in range(start, len(five_min)):
        if not (sig.long_setup[i] or sig.short_setup[i]):
            continue
        j = int(cidx[i])
        if j < step:
            continue
        considered += 1
        core_was = bool(sig.long_setup[i - 1] if sig.long_setup[i]
                        else sig.short_setup[i - 1])
        for offset, bucket in ((step, "aligned"), (1, "misaligned")):
            cl, cs = _checks(P.at(i - 1), C.at(j - offset), cfg)
            was = (all(v for _, v in cl) if sig.long_setup[i]
                   else all(v for _, v in cs))
            if was != core_was:
                if bucket == "aligned":
                    aligned_disagreements += 1
                else:
                    misaligned_disagreements += 1

    assert considered > 20, "not enough setups to make this meaningful"
    assert aligned_disagreements == 0, (
        f"{aligned_disagreements}/{considered} previous-bar verdicts disagree "
        f"with the core even when the confirmation bar is correctly aligned")
    assert misaligned_disagreements > 0, (
        "the old C.at(-2) spelling no longer differs on this sample, so this "
        "test would not catch a regression -- widen the sample rather than "
        "deleting the assertion")


def test_edge_trigger_fires_only_on_false_to_true(bars, hwpr_spec):
    one_min, five_min = bars
    sig = rulecore.compute(five_min, one_min, hwpr_spec)
    fired = sig.long_entry | sig.short_entry
    setup = sig.long_setup | sig.short_setup
    idx = np.flatnonzero(fired)
    assert idx.size > 0
    # every fire is preceded by a bar where the same side's setup was false
    for i in idx:
        side = sig.long_setup if sig.long_entry[i] else sig.short_setup
        assert side[i] and not side[i - 1], f"bar {i} fired without a FALSE->TRUE edge"
    assert fired.sum() < setup.sum(), "edge trigger suppressed nothing"


def test_level_trigger_fires_on_every_true_bar(bars, hwpr_spec):
    one_min, five_min = bars
    from dataclasses import replace
    level = replace(hwpr_spec, trigger="level")
    edge_sig = rulecore.compute(five_min, one_min, hwpr_spec)
    lvl_sig = rulecore.compute(five_min, one_min, level)
    assert lvl_sig.long_entry.sum() > edge_sig.long_entry.sum()
    # the two agree on setup truth; only the trigger differs
    np.testing.assert_array_equal(lvl_sig.long_setup, edge_sig.long_setup)


def test_confirmation_alignment_picks_the_last_closed_bar(bars, hwpr_spec):
    """Each COMPLETE primary bar maps to the 1m bar closing at the same instant."""
    one_min, five_min = bars
    sig = rulecore.compute(five_min, one_min, hwpr_spec)
    t5 = five_min["time"].to_numpy("int64")
    t1 = one_min["time"].to_numpy("int64")
    # The final resampled bar may be partial -- see the test below.
    ok = (sig.confirm_index >= 0)
    ok[-1] = False
    expected_open = t5[ok] + 5 * 60 - 60      # opens 60s before the primary close
    np.testing.assert_array_equal(t1[sig.confirm_index[ok]], expected_open)


def test_a_partial_trailing_primary_bar_falls_back_to_the_last_closed_confirm(bars, hwpr_spec):
    """``resample_ohlcv`` emits a PARTIAL final bar, and that is a caller trap.

    Aggregating 1m bars into 5m produces a trailing bucket from however many 1m
    bars happen to exist, so the last row of a resampled frame is a forming bar
    unless the source length divides evenly. ``compute`` cannot detect that --
    its contract is closed bars only -- and it resolves the alignment by taking
    the most recent CLOSED confirmation bar, which is what the bot sees.

    Pinned as a test because the failure mode is silent: the trailing bar gets
    a stale confirmation and a truncated high/low, and nothing raises.
    """
    one_min, five_min = bars
    sig = rulecore.compute(five_min, one_min, hwpr_spec)
    t5 = five_min["time"].to_numpy("int64")
    t1 = one_min["time"].to_numpy("int64")

    last_primary_close = int(t5[-1]) + 5 * 60
    last_confirm_close = int(t1[-1]) + 60
    j = int(sig.confirm_index[-1])
    # whatever the case, the chosen bar is the newest one that had CLOSED
    assert int(t1[j]) + 60 <= min(last_primary_close, last_confirm_close)
    assert j == len(t1) - 1 or int(t1[j + 1]) + 60 > last_primary_close

    # and dropping the partial bar restores exact alignment
    trimmed = five_min.iloc[:-1].reset_index(drop=True)
    sig2 = rulecore.compute(trimmed, one_min, hwpr_spec)
    ok = sig2.confirm_index >= 0
    np.testing.assert_array_equal(
        t1[sig2.confirm_index[ok]],
        trimmed["time"].to_numpy("int64")[ok] + 5 * 60 - 60)


def test_no_signal_uses_a_future_bar(bars, hwpr_spec):
    """Truncating the series must not change any earlier bar's verdict.

    The cheapest possible look-ahead check, and the one that would have caught
    the same-bar target bug recorded in PROGRAM_SUMMARY.
    """
    one_min, five_min = bars
    full = rulecore.compute(five_min, one_min, hwpr_spec)
    cut5 = len(five_min) - 50
    cut1 = int(np.searchsorted(one_min["time"].to_numpy("int64"),
                               five_min["time"].to_numpy("int64")[cut5]))
    part = rulecore.compute(five_min.iloc[:cut5].reset_index(drop=True),
                            one_min.iloc[:cut1].reset_index(drop=True),
                            hwpr_spec)
    start = hwpr_spec.warmup_bars + 5
    np.testing.assert_array_equal(part.long_entry[start:], full.long_entry[start:cut5])
    np.testing.assert_array_equal(part.short_entry[start:], full.short_entry[start:cut5])

"""``evaluate_spec`` against the backtest signals it is meant to reproduce.

THE POINT OF THE MODULE IS THAT THESE AGREE
    The live arm and the backtest are the same code (``deltabt.rulecore``)
    reading the same spec, so agreement should be structural rather than
    coincidental. These tests check it anyway, on real bars, because the live
    path adds three things the backtest does not have: resampling from a 1m
    stream, dropping incomplete buckets, and reading only the last row.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.strategy.explanation import LONG, Outcome
from app.strategy.spec_arm import evaluate_spec, warmup_1m_bars
from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.strategy import resample_complete, resample_ohlcv

CANDLES = Path("data/candles/BTCUSD/ltp_1m.parquet")
#: The candidate. 240m primary, %R only, ATR stop, no confirmation gate.
FAMILY, MINUTES = "wpr_only", 240

pytestmark = pytest.mark.skipif(not CANDLES.exists(), reason="candles not cached")


@pytest.fixture(scope="module")
def one_min() -> pd.DataFrame:
    return (pd.read_parquet(CANDLES).sort_values("time")
            .reset_index(drop=True).tail(200_000).reset_index(drop=True))


@pytest.fixture(scope="module")
def spec():
    return build_spec(FAMILY, MINUTES)


def test_the_candidate_needs_24_days_of_history(spec):
    """Sizing the buffer and the backfill is the deployment constraint."""
    assert spec.confirm.enabled is False, "this arm has no confirmation gate"
    assert warmup_1m_bars(spec) == spec.warmup_bars * MINUTES
    assert warmup_1m_bars(spec) > 34_000, "warm-up shrank; re-check the buffer"


def test_it_reproduces_the_backtest_signal_bar_for_bar(spec, one_min):
    """Walk forward one primary bar at a time and compare to the full run."""
    primary = resample_complete(one_min, MINUTES)
    full = rulecore.compute(primary, None, spec)

    t1 = one_min["time"].to_numpy("int64")
    checked = fired = 0
    # last 60 primary bars: far past warm-up, cheap enough to loop
    for i in range(len(primary) - 60, len(primary)):
        close_ts = int(primary["time"].iloc[i]) + MINUTES * 60
        hist = one_min.iloc[: int(np.searchsorted(t1, close_ts))].reset_index(drop=True)
        exp = evaluate_spec(hist, spec, symbol="BTCUSD")

        want_long = bool(full.long_entry[i])
        want_short = bool(full.short_entry[i])
        got_long = exp.outcome is Outcome.DETECTED and exp.direction == LONG
        got_short = exp.outcome is Outcome.DETECTED and exp.direction is not None \
            and not got_long
        assert (got_long, got_short) == (want_long, want_short), (
            f"bar {i} ({pd.Timestamp(int(primary['time'].iloc[i]), unit='s')}): "
            f"live ({got_long},{got_short}) vs backtest ({want_long},{want_short}) "
            f"-- {exp.rejection_reason}")
        if want_long or want_short:
            fired += 1
            assert exp.entry_price == pytest.approx(float(full.close[i]))
            stop = full.stop_long[i] if want_long else full.stop_short[i]
            assert exp.stop_price == pytest.approx(float(stop))
        checked += 1

    assert checked == 60
    assert fired > 0, "no entries in the compared range -- this test is vacuous"


def test_it_reads_only_closed_bars(spec, one_min):
    """Appending minutes of a forming primary bar must not change the verdict."""
    primary = resample_complete(one_min, MINUTES)
    close_ts = int(primary["time"].iloc[-1]) + MINUTES * 60
    t1 = one_min["time"].to_numpy("int64")
    cut = int(np.searchsorted(t1, close_ts))

    base = evaluate_spec(one_min.iloc[:cut].reset_index(drop=True), spec, symbol="BTCUSD")
    for extra in (1, 7, 60, MINUTES - 1):
        forming = evaluate_spec(one_min.iloc[: cut + extra].reset_index(drop=True),
                                spec, symbol="BTCUSD")
        assert forming.outcome is base.outcome, f"+{extra}m changed the outcome"
        assert forming.direction == base.direction
        assert forming.bar_open == base.bar_open, (
            f"+{extra}m advanced the decision bar -- a forming bucket was used")


def test_insufficient_history_is_suppressed_not_guessed(spec, one_min):
    exp = evaluate_spec(one_min.tail(5_000).reset_index(drop=True), spec, symbol="BTCUSD")
    assert exp.outcome is Outcome.SUPPRESSED
    assert "warm-up incomplete" in exp.rejection_reason
    assert exp.direction is None


def test_an_empty_frame_is_suppressed(spec):
    exp = evaluate_spec(pd.DataFrame(columns=["time", "open", "high", "low",
                                              "close", "volume"]),
                        spec, symbol="BTCUSD")
    assert exp.outcome is Outcome.SUPPRESSED


def test_the_explanation_carries_the_spec_identity(spec, one_min):
    exp = evaluate_spec(one_min, spec, symbol="BTCUSD")
    assert exp.strategy_config_hash == spec.config_hash
    assert exp.strategy_version == spec.name
    assert exp.primary_timeframe == f"{MINUTES}m"
    # no confirmation gate on this arm, and the explanation must say so rather
    # than name a timeframe it never consults
    assert exp.confirmation_timeframe == ""


def test_bucket_completeness_threshold(spec, one_min):
    """One absent minute keeps a 240m bar; a badly holed one is dropped.

    Both extremes are wrong and the threshold is the point. Requiring every
    minute discards 4.8% of cached BTCUSD 240m buckets over a single missing
    minute AND punches holes that every smoothed value downstream is then
    computed across. Requiring nothing accepts a bar whose high and low are
    truncated, which moves the signal and the stop.
    """
    primary = resample_complete(one_min, MINUTES)
    close_ts = int(primary["time"].iloc[-1]) + MINUTES * 60
    t1 = one_min["time"].to_numpy("int64")
    cut = int(np.searchsorted(t1, close_ts))
    hist = one_min.iloc[:cut].reset_index(drop=True)
    base = evaluate_spec(hist, spec, symbol="BTCUSD")

    # 1 minute of 240 -> 239/240, above the 90% floor, bucket survives
    nicked = hist.drop(hist.index[-3]).reset_index(drop=True)
    assert evaluate_spec(nicked, spec, symbol="BTCUSD").bar_open == base.bar_open, (
        "a bar missing one minute of 240 was discarded")

    # 30 minutes of 240 -> 210/240, below the floor, bucket is dropped
    holed = hist.drop(hist.index[-30:]).reset_index(drop=True)
    assert evaluate_spec(holed, spec, symbol="BTCUSD").bar_open != base.bar_open, (
        "a bar missing 30 minutes of 240 was evaluated anyway")


def test_live_and_backtest_see_the_same_bars(spec, one_min):
    """The divergence that produced a different stop on 4.8% of bars.

    The live path dropped any incomplete bucket; the backtest kept them. Both
    now call ``resample_complete``, so the bar sets are identical rather than
    merely similar.
    """
    from deltabt.harness import _resampled
    live = resample_complete(one_min, MINUTES)
    data = {"symbol": "BTCUSD", "ltp": one_min, "mark": None,
            "tradable": np.ones(len(one_min), dtype=bool)}
    backtest, _, _ = _resampled(data, MINUTES, {})
    pd.testing.assert_frame_equal(live, backtest)

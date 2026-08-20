"""Parity between the live flip-arm evaluator and the research it reproduces.

The reference is not a fixture of expected numbers. It is the same confluence
`run_reversal_confluence.py` measured -- Pine-convention Supertrend flips and
%R level crossings, computed over the WHOLE slice the way research computes it.
The candidate is `evaluate_flip` over a bounded trailing window, which is how a
live runner must compute it. Anything that makes those two disagree is what
this file exists to catch, because a live arm that does not reproduce its own
backtest is measuring something nobody has a prior for.

The direction convention gets its own test. Pine's Supertrend returns -1 for an
UPTREND, so a bullish flip is direction going from >= 0 to < 0. Reading that
backwards inverts every trade the arm takes while leaving the signal COUNT
identical -- a bug no aggregate would reveal, which is why the sides are
asserted and not just the timing.

These read the repository's cached 1m candles and SKIP rather than fail
without them, since a missing cache is not a regression. The skip lives in the
fixture rather than in a mark: registering a new marker means editing
pyproject.toml, and pyproject.toml is in deploy.yml's allow-list, so it would
roll a live experiment to silence a warning.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.strategy.explanation import LONG, SHORT, Outcome
from app.strategy.flip_arm import FLIP_ARM, evaluate_flip, warmup_bars
from deltabt import indicators as ind
from deltabt.data.store import CandleStore
from deltabt.research import hwpr

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD"]
SLICE = 30_000
WINDOW = FLIP_ARM.window_bars
CHECKS = 40


def reference_signals(df):
    """The research computation, over the whole slice.

    Deliberately written from the same primitives run_reversal_confluence uses
    -- ind.supertrend and ind.wpr with hwpr's constants -- rather than by
    importing that module's helpers. If the live arm and this both drifted the
    same way through a shared helper, a parity test would pass and mean
    nothing.
    """
    h = df["high"].to_numpy("float64")
    l = df["low"].to_numpy("float64")
    c = df["close"].to_numpy("float64")
    _st, d1 = ind.supertrend(h, l, c, hwpr.ST_MULT, hwpr.ST_PERIOD)
    wpr = ind.wpr(h, l, c, hwpr.WPR_PERIOD)
    pd1 = np.concatenate(([d1[0]], d1[:-1]))
    pw = np.concatenate(([np.nan], wpr[:-1]))
    with np.errstate(invalid="ignore"):
        flip_long = (d1 < 0) & (pd1 >= 0)
        flip_short = (d1 > 0) & (pd1 <= 0)
        cross_up = (wpr > -80.0) & (pw <= -80.0)
        cross_dn = (wpr < -20.0) & (pw >= -20.0)
    return flip_long & cross_up, flip_short & cross_dn


@pytest.fixture(scope="module")
def cases():
    store, out = CandleStore(), []
    for sym in SYMBOLS:
        df = store.read(sym, "ltp", "1m")
        if df.empty or len(df) < SLICE:
            continue
        df = df.tail(SLICE).reset_index(drop=True)
        lo, sh = reference_signals(df)
        idx = np.arange(len(df))
        usable = idx >= WINDOW
        sig = np.flatnonzero((lo | sh) & usable)
        quiet = np.flatnonzero(~(lo | sh) & usable)
        rng = np.random.default_rng(7)
        if sig.size:
            sig = rng.choice(sig, size=min(CHECKS, sig.size), replace=False)
        quiet = rng.choice(quiet, size=min(CHECKS, quiet.size), replace=False)
        out.append((sym, df, lo, sh, sig, quiet))
    if not out:
        pytest.skip("no cached 1m candles in this checkout")
    return out


def _at(df, i):
    """The trailing window a live runner would hold at bar i, inclusive."""
    return df.iloc[max(0, i - WINDOW + 1):i + 1].reset_index(drop=True)


class TestParity:
    def test_the_reference_fires_at_all(self, cases):
        # A parity test against a reference that never fires passes vacuously.
        total = sum(int((lo | sh).sum()) for _, _, lo, sh, _, _ in cases)
        assert total > 50, f"reference produced only {total} signals across the slice"

    def test_every_reference_signal_is_detected_live(self, cases):
        for sym, df, lo, sh, sig, _ in cases:
            for i in sig:
                exp = evaluate_flip(_at(df, i), FLIP_ARM, symbol=sym)
                assert exp.outcome is Outcome.DETECTED, (
                    f"{sym}@{i}: research fires here, live says "
                    f"{exp.outcome.value} ({exp.rejection_reason})")

    def test_the_side_matches_not_merely_the_timing(self, cases):
        # Pine returns -1 for an uptrend. Inverting that leaves the signal
        # COUNT identical and every trade backwards.
        for sym, df, lo, sh, sig, _ in cases:
            for i in sig:
                exp = evaluate_flip(_at(df, i), FLIP_ARM, symbol=sym)
                want = LONG if lo[i] else SHORT
                assert exp.direction == want, (
                    f"{sym}@{i}: research says "
                    f"{'LONG' if lo[i] else 'SHORT'}, live says {exp.direction}")

    def test_quiet_bars_stay_quiet(self, cases):
        for sym, df, lo, sh, _, quiet in cases:
            for i in quiet:
                exp = evaluate_flip(_at(df, i), FLIP_ARM, symbol=sym)
                assert exp.outcome is not Outcome.DETECTED, (
                    f"{sym}@{i}: research is silent here, live detected "
                    f"{exp.direction}")


class TestGeometry:
    def test_the_stop_is_exactly_the_configured_fraction(self, cases):
        for sym, df, lo, sh, sig, _ in cases:
            for i in sig[:10]:
                exp = evaluate_flip(_at(df, i), FLIP_ARM, symbol=sym)
                risk = abs(exp.entry_price - exp.stop_price)
                assert risk == pytest.approx(exp.entry_price * FLIP_ARM.stop_pct)
                assert exp.stop_distance_pct == pytest.approx(FLIP_ARM.stop_pct)

    def test_the_target_is_exactly_target_r_of_the_stop(self, cases):
        for sym, df, lo, sh, sig, _ in cases:
            for i in sig[:10]:
                exp = evaluate_flip(_at(df, i), FLIP_ARM, symbol=sym)
                risk = abs(exp.entry_price - exp.stop_price)
                reward = abs(exp.target_price - exp.entry_price)
                assert reward == pytest.approx(FLIP_ARM.target_r * risk)

    def test_the_stop_sits_on_the_losing_side_of_entry(self, cases):
        for sym, df, lo, sh, sig, _ in cases:
            for i in sig[:20]:
                exp = evaluate_flip(_at(df, i), FLIP_ARM, symbol=sym)
                if exp.direction == LONG:
                    assert exp.stop_price < exp.entry_price < exp.target_price
                else:
                    assert exp.target_price < exp.entry_price < exp.stop_price


class TestWindowIndependence:
    def test_a_longer_window_does_not_change_the_verdict(self, cases):
        """Supertrend is recursive, so its value depends on where history starts.

        window_bars must be large enough that the band has converged; if it is
        not, the live arm's answer depends on how long the process has been up,
        which is unreproducible by construction.
        """
        sym, df, lo, sh, sig, _ = cases[0]
        for i in sig[:15]:
            short = evaluate_flip(_at(df, i), FLIP_ARM, symbol=sym)
            longer = evaluate_flip(
                df.iloc[max(0, i - 3 * WINDOW + 1):i + 1].reset_index(drop=True),
                FLIP_ARM, symbol=sym)
            assert short.outcome is longer.outcome
            assert short.direction == longer.direction
            assert short.entry_price == pytest.approx(longer.entry_price)


class TestWarmup:
    def test_too_little_history_is_suppressed_not_detected(self, cases):
        sym, df, *_ = cases[0]
        short = df.tail(warmup_bars(FLIP_ARM) - 1).reset_index(drop=True)
        exp = evaluate_flip(short, FLIP_ARM, symbol=sym)
        assert exp.outcome is Outcome.SUPPRESSED
        assert "warm-up" in (exp.rejection_reason or "")

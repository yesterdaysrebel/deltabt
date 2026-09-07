"""``rulecore`` must not depend on where the array starts or ends.

TWO DIFFERENT PROPERTIES, BOTH LOAD-BEARING
    *End* invariance is the look-ahead check: truncating the future must not
    change any earlier bar's verdict. It is what catches the same-bar target
    bug recorded in PROGRAM_SUMMARY.

    *Start* invariance is what makes the live path legitimate. The bot does not
    recompute from genesis on every bar; it recomputes over a bounded trailing
    window and reads the last row. That is only the same strategy if the tail
    of a windowed computation equals the tail of the full one -- Wilder
    smoothing forgets its seed, so it converges, but "converges" is a claim
    about a specific window length on specific data, not a theorem.

    The exception is deliberate and is asserted rather than tolerated: the
    Supertrend leg extreme does NOT converge, because it is an extremum since
    the last flip. ``leg_determinate`` marks where a bounded window cannot see
    the flip, and entries there are suppressed rather than sized off a stop
    that is an artifact of the window's start.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from deltabt import rulecore
from deltabt.strategy import resample_ohlcv

from deltabt.catalog import FAMILIES, build_spec

CANDLES = Path("data/candles/BTCUSD/ltp_1m.parquet")
BARS = 30_000
#: The live arm's trailing window (app.config.strategy.WINDOW_BARS).
WINDOW = 1_500
#: Bars at the tail compared between the two computations. The FIRST value
#: that actually fires is used, so a rare family gets a real comparison
#: instead of a vacuous one.
#:
#: 300 was fine while every family fired often. `manual_scalp_cross_both_t3`
#: requires %R to cross out of the band on the 5m AND the 1m on the same bar,
#: which happens EIGHT times in 6,001 BTCUSD 5m bars and not once in the last
#: 300 -- so the comparison below was all-false against all-false, which the
#: guard at the end of the test correctly refuses. Widening the tail is the
#: fix; deleting the guard would have been the bug.
TAILS = (300, 600, 900)
#: Bars of window that must remain AHEAD of the compared tail. Start
#: invariance is a claim that Wilder smoothing has forgotten its seed by the
#: time the comparison starts, so the tail may not eat the warm-up that makes
#: the claim true.
MIN_WARMUP = 600

pytestmark = pytest.mark.skipif(
    not CANDLES.exists(), reason="BTCUSD 1m candles not cached")


@pytest.fixture(scope="module")
def one_min() -> pd.DataFrame:
    return (pd.read_parquet(CANDLES).sort_values("time")
            .reset_index(drop=True).tail(BARS).reset_index(drop=True))


def _frames(one_min: pd.DataFrame, spec):
    primary = one_min if spec.primary_minutes == 1 else \
        resample_ohlcv(one_min, spec.primary_minutes).iloc[:-1].reset_index(drop=True)
    if not spec.confirm.enabled:
        return primary, None
    confirm = one_min if spec.confirm_minutes == 1 else \
        resample_ohlcv(one_min, spec.confirm_minutes).iloc[:-1].reset_index(drop=True)
    return primary, confirm


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_start_invariance_the_live_bounded_window(family, one_min):
    """A trailing window reproduces the full computation at the tail.

    This is the property the paper trader depends on. If it fails for a family,
    that family cannot be run live from a bounded window at this window length,
    whatever its backtest says.
    """
    spec = build_spec(family, 5)
    primary, confirm = _frames(one_min, spec)
    if len(primary) < WINDOW + max(TAILS):
        pytest.skip("not enough primary bars for a windowed comparison")

    full = rulecore.compute(primary, confirm, spec)

    win_primary = primary.tail(WINDOW).reset_index(drop=True)
    if confirm is not None:
        # the confirmation window must cover the same wall-clock span
        span = spec.primary_minutes // spec.confirm_minutes
        win_confirm = confirm.tail(WINDOW * span).reset_index(drop=True)
    else:
        win_confirm = None
    win = rulecore.compute(win_primary, win_confirm, spec)

    # Compare a tail that sits far past the window's warm-up, widening it
    # only as far as a rare family needs to fire at all. If nothing fires at
    # any width the largest is used and the guard below reports it, which is
    # the honest outcome: the family cannot be shown to be window-safe here.
    tail = max(t for t in TAILS if WINDOW - t >= MIN_WARMUP)
    for cand in TAILS:
        if WINDOW - cand < MIN_WARMUP:
            break
        c = slice(WINDOW - cand, WINDOW)
        if ((win.long_setup[c] | win.short_setup[c]).any()
                and (win.long_entry[c] | win.short_entry[c]).any()):
            tail = cand
            break
    f = slice(len(primary) - tail, len(primary))
    w = slice(WINDOW - tail, WINDOW)

    np.testing.assert_array_equal(
        win.long_setup[w], full.long_setup[f],
        err_msg=f"{family}: windowed long setup differs from full-series")
    np.testing.assert_array_equal(
        win.short_setup[w], full.short_setup[f],
        err_msg=f"{family}: windowed short setup differs from full-series")

    # An all-false tail would make every assertion above trivially true.
    setups = int((win.long_setup[w] | win.short_setup[w]).sum())
    entries = int((win.long_entry[w] | win.short_entry[w]).sum())
    assert setups > 0 and entries > 0, (
        f"{family}: nothing fired in the compared tail of {tail} bars "
        f"({setups} setups, {entries} entries) -- this test would pass on a "
        f"broken core")

    # Entries may legitimately differ only where the window cannot resolve the
    # Supertrend leg, and only in the direction of suppression.
    #
    # NOTE: on this sample ``leg_determinate`` is true for every compared bar,
    # so the suppression branch below is NOT exercised here. A 1500-bar window
    # at 5m is five days, and a Supertrend leg outrunning that is rare enough
    # not to appear in 30,000 bars. The assertions are kept because the branch
    # is reachable on real data, not because this test proves it works.
    det = win.primary.leg_determinate[w]
    np.testing.assert_array_equal(win.long_entry[w][det], full.long_entry[f][det])
    np.testing.assert_array_equal(win.short_entry[w][det], full.short_entry[f][det])
    suppressed = (~det) & (full.long_entry[f] | full.short_entry[f])
    assert not (win.long_entry[w] | win.short_entry[w])[~det].any(), (
        f"{family}: windowed run FIRED on a bar whose leg it cannot see")
    if spec.stop != "leg_extreme":
        assert not suppressed.any(), (
            f"{family}: a non-leg stop should never be window-suppressed")


def test_leg_suppression_actually_engages_on_a_short_window(one_min):
    """Exercise the branch the window test above cannot reach.

    Shrinking the window until the Supertrend leg outruns it forces
    ``leg_determinate`` false, which must suppress leg-extreme entries rather
    than sizing them off an extremum of an arbitrary truncation.
    """
    spec = build_spec("hwpr_v2", 5)
    assert spec.stop == "leg_extreme"
    primary, confirm = _frames(one_min, spec)

    short = spec.warmup_bars + 30           # barely past warm-up
    win = rulecore.compute(
        primary.tail(short).reset_index(drop=True),
        confirm.tail(short * 5).reset_index(drop=True) if confirm is not None else None,
        spec)
    indet = int((~win.primary.leg_determinate).sum())
    assert indet > 0, "window is not short enough to strand a leg"
    # nothing may fire on an indeterminate bar, whatever the setup says
    assert not (win.long_entry | win.short_entry)[~win.primary.leg_determinate].any()


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_end_invariance_no_look_ahead(family, one_min):
    """Truncating the future changes no earlier bar's verdict."""
    spec = build_spec(family, 5)
    primary, confirm = _frames(one_min, spec)
    cut = len(primary) - 100
    if cut < spec.warmup_bars * 3:
        pytest.skip("not enough bars")

    full = rulecore.compute(primary, confirm, spec)
    if confirm is not None:
        span = spec.primary_minutes // spec.confirm_minutes
        part_confirm = confirm.iloc[: cut * span].reset_index(drop=True)
    else:
        part_confirm = None
    part = rulecore.compute(primary.iloc[:cut].reset_index(drop=True),
                            part_confirm, spec)

    start = spec.warmup_bars + 5
    np.testing.assert_array_equal(part.long_entry[start:], full.long_entry[start:cut],
                                  err_msg=f"{family}: look-ahead in long entries")
    np.testing.assert_array_equal(part.short_entry[start:], full.short_entry[start:cut],
                                  err_msg=f"{family}: look-ahead in short entries")


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_deterministic(family, one_min):
    """Same inputs, same outputs -- no hidden state between calls."""
    spec = build_spec(family, 5)
    primary, confirm = _frames(one_min, spec)
    a = rulecore.compute(primary, confirm, spec)
    b = rulecore.compute(primary, confirm, spec)
    np.testing.assert_array_equal(a.long_entry, b.long_entry)
    np.testing.assert_array_equal(a.short_entry, b.short_entry)
    np.testing.assert_array_equal(a.stop_long, b.stop_long)


def test_every_family_actually_fires(one_min):
    """A grid of families that never trade would make every test above vacuous."""
    silent = []
    for family in sorted(FAMILIES):
        spec = build_spec(family, 5)
        primary, confirm = _frames(one_min, spec)
        sig = rulecore.compute(primary, confirm, spec)
        if int(sig.long_entry.sum() + sig.short_entry.sum()) == 0:
            silent.append(family)
    assert not silent, f"families produced no entries at 5m: {silent}"

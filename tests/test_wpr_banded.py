"""The `banded` %R rule: variant_a with a ceiling at the band midpoint.

WHAT IT IS FOR
    `variant_a` is `%R > -80 AND rising` -- a floor with nothing above it. So
    %R = -4, meaning price at the high of its 140-bar window, is a valid long.
    The live ATR arm entered longs at -4.3, -6.9, -8.6, -11.8 and -12.9 on
    2026-08-26/27 carrying a 2xATR stop worth 0.2-0.5% of price, and an
    ordinary pullback removed them.

    `banded` keeps every other part of the rule and refuses entries that have
    already run past the middle of the band: long in (-80, -50), short in
    (-50, -20) at the default levels.

WHY THE MIDPOINT IS DERIVED AND NOT A FIELD
    A band needs three numbers and TimeframeRules has two. Adding a third
    field would change `asdict()` and therefore move EVERY StrategySpec's
    config_hash, orphaning every result already recorded in out/sweep/ --
    exactly the trap app/config/variants.py records V1 falling into. The
    midpoint of (-80, -20) is -50, which is the wanted split, so it is
    computed. test_no_existing_spec_hash_moved pins that this stayed free.
"""

from __future__ import annotations

import numpy as np
import pytest

from deltabt.catalog import FAMILIES, build_spec
from deltabt.rulecore import TimeframeIndicators, gate
from deltabt.spec import WPR_RULES, TimeframeRules


def _ti(wpr: list[float]) -> TimeframeIndicators:
    """Indicators with only %R varying; every other gate left open."""
    n = len(wpr)
    ones = np.ones(n)
    return TimeframeIndicators(
        time=np.arange(n, dtype="int64") * 300,
        high=ones * 101.0, low=ones * 99.0, close=ones * 100.0,
        st=ones * 98.0,
        direction=-ones,          # bullish Supertrend (Pine: <0 is up)
        plus_di=ones * 30.0,
        minus_di=ones * 10.0,
        adx=ones * 50.0,
        wpr=np.array(wpr, dtype="float64"),
        leg_low=ones * 99.0, leg_high=ones * 101.0, atr=ones,
        leg_start=np.zeros(n, dtype="int64"),
        leg_determinate=np.ones(n, dtype=bool),
    )


def _rules(rule: str) -> TimeframeRules:
    return TimeframeRules(supertrend="off", di=False, adx_min=None,
                          wpr_rule=rule)


def test_banded_is_in_the_vocabulary():
    assert "banded" in WPR_RULES


@pytest.mark.parametrize("wpr,expected", [
    (-70.0, True),    # rising, below the -50 midpoint
    (-51.0, True),    # rising, just inside
    (-49.0, False),   # rising but ALREADY PAST the midpoint -- the whole point
    (-11.8, False),   # the live ETHUSD entry this rule exists to refuse
    (-4.3, False),    # the live SOLUSD entry
    (-85.0, False),   # below the floor: variant_a refuses this too
])
def test_long_admits_only_the_lower_half(wpr, expected):
    """The previous bar is one point lower, so `rising` always holds here."""
    long_ok, _ = gate(_ti([wpr - 1.0, wpr]), _rules("banded"))
    assert bool(long_ok[-1]) is expected


@pytest.mark.parametrize("wpr,expected", [
    (-30.0, True),    # falling, above the -50 midpoint
    (-49.0, True),
    (-51.0, False),   # already past the midpoint on the way down
    (-95.7, False),   # the live BEATUSD short, at the bottom of its range
    (-15.0, False),   # above the short ceiling: variant_a refuses this too
])
def test_short_admits_only_the_upper_half(wpr, expected):
    _, short_ok = gate(_ti([wpr + 1.0, wpr]), _rules("banded"))
    assert bool(short_ok[-1]) is expected


def test_direction_requirement_survives():
    """The ceiling narrows WHERE an entry may happen. It must not invert the
    rule into a fade: a long still has to be rising."""
    long_ok, _ = gate(_ti([-60.0, -70.0]), _rules("banded"))   # falling
    assert not long_ok[-1]
    _, short_ok = gate(_ti([-40.0, -30.0]), _rules("banded"))  # rising
    assert not short_ok[-1]


def test_banded_is_strictly_narrower_than_variant_a():
    """Every banded entry is a variant_a entry. Not the reverse."""
    seq = [-85.0, -78.0, -70.0, -62.0, -55.0, -48.0, -35.0, -20.0, -8.0, -3.0]
    ti = _ti(seq)
    va_long, va_short = gate(ti, _rules("variant_a"))
    bd_long, bd_short = gate(ti, _rules("banded"))
    assert (bd_long <= va_long).all(), "banded admitted a long variant_a refused"
    assert (bd_short <= va_short).all()
    assert bd_long.sum() < va_long.sum(), "the ceiling refused nothing at all"


def test_nan_never_passes():
    """Warm-up must fail the gate, not compare against NaN and pass."""
    long_ok, short_ok = gate(_ti([np.nan, -60.0]), _rules("banded"))
    assert not long_ok[-1] and not short_ok[-1]


def test_the_family_differs_from_atr_arm_by_one_field():
    """A difference measured between these two must be the ceiling and
    nothing else, so everything but the primary %R rule has to match."""
    a = FAMILIES["atr_arm"]
    b = FAMILIES["atr_banded"]
    assert a["over"] == b["over"], "stop/trigger differ; a comparison is confounded"
    assert a["confirm"] == b["confirm"], "confirmation gate differs"
    pa, pb = a["primary"], b["primary"]
    differing = [f for f in ("supertrend", "di", "adx_min", "wpr_rule",
                             "wpr_long_level", "wpr_short_level")
                 if getattr(pa, f) != getattr(pb, f)]
    assert differing == ["wpr_rule"], f"primary also differs in {differing}"


def test_no_existing_spec_hash_moved():
    """Adding the rule must not have cost the recorded sweeps their identity.

    out/sweep/*.csv carries spec_hash per row. A new FIELD on TimeframeRules
    would have moved all of them; a new VALUE for an existing field moves
    none.
    """
    assert build_spec("atr_arm", 5).config_hash[:12] == "5104b68fd7d7"


def test_the_midpoint_is_derived_from_the_levels_not_hardcoded():
    """Move the outer edges and the split must move with them."""
    narrow = TimeframeRules(supertrend="off", di=False, adx_min=None,
                            wpr_rule="banded", wpr_long_level=-80.0,
                            wpr_short_level=-40.0)          # midpoint -60
    long_ok, _ = gate(_ti([-66.0, -65.0]), narrow)
    assert long_ok[-1], "-65 is below the -60 midpoint and should pass"
    long_ok, _ = gate(_ti([-56.0, -55.0]), narrow)
    assert not long_ok[-1], "-55 is above the -60 midpoint and should not"

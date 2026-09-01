"""The deployed arm: Supertrend agrees AND %R banded. Nothing else.

WHY THIS EXISTS. The operator stated their rule directly on 2026-09-01 -- "if
wpr is banded and supertrend agrees I take trades" -- after three arms in a row
encoded something else:

    manual_scalp      %R variant_a alone. A floor with no ceiling, so it bought
                      at %R -9.35 and sold at %R -85.71 and -93.51.
    manual_scalp_st   added Supertrend as a filter but kept variant_a. It
                      opened FOUR shorts in one bar at %R -90.61 to -93.77 with
                      price a hair off the leg low.
    manual_flip       read Supertrend as a TRIGGER. It is a FILTER.

Evaluated against the operator's rule, manual_scalp_st and it agreed on
NOTHING: six signals taken by the live arm, six refused by theirs.

WHAT IS DELIBERATELY ABSENT, each measured on the thin 3 and each costing money:

    1m confirmation ON      -6.56% -> -13.46%   win 50% -> 44%
    max hold cut to 1h      -6.56% -> -18.38%
    tighter stop with it    -18.38% -> -34.41% at 2xATR

The short hold is not wrong; it is incompatible with a 4xATR stop, since a 1R
target on a stop that wide takes hours to reach. It cannot be rescued by
tightening the stop either -- cost_r = round_trip / stop_pct, so halving the
stop doubles cost per R. 4xATR at a 24h hold won a 15-cell grid.

These tests pin the arm's SHAPE and its deployed hash. They do not pin P&L:
every configuration measured is still negative.
"""

from __future__ import annotations

import pytest

from deltabt.catalog import FAMILIES, build_spec

DEPLOYED = "d6c319a387f656677a8614d66809a0c8af59a5c1c5e75d2fd9588e6026082df6"


def test_the_family_is_registered():
    assert "manual_scalp_st_banded" in FAMILIES


def test_supertrend_is_a_FILTER_not_a_trigger():
    """`flip` would make it a trigger. That was manual_flip, and it was wrong."""
    assert build_spec("manual_scalp_st_banded", 5).primary.supertrend == "aligned"


def test_the_wpr_rule_is_banded_not_variant_a():
    """variant_a is the bug: a floor with no ceiling, so %R -93 is a valid short."""
    assert build_spec("manual_scalp_st_banded", 5).primary.wpr_rule == "banded"


def test_no_adx_and_no_di():
    p = build_spec("manual_scalp_st_banded", 5).primary
    assert p.di is False
    assert p.adx_min is None


def test_the_confirmation_timeframe_is_off():
    """Requiring banded on 1m too cost 6.9 points and 6 points of win rate."""
    assert build_spec("manual_scalp_st_banded", 5).confirm.enabled is False


def test_the_stop_stays_wide_and_the_target_is_1R():
    s = build_spec("manual_scalp_st_banded", 5)
    assert s.stop_atr_multiplier == 4.0, "tightening this doubles cost per R"
    assert s.target_r == 1.0
    assert s.max_stop_pct == 0.10


def test_the_band_bounds_entries_on_BOTH_sides():
    """The whole point: a long may not chase the top, a short may not sell the
    bottom. Both failures were observed live before this family existed."""
    p = build_spec("manual_scalp_st_banded", 5).primary
    assert p.wpr_long_level == -80.0
    assert p.wpr_short_level == -20.0


def test_the_deployed_hash_is_what_the_workflows_pin():
    """variables.tf decides, deploy.yml labels, monitor.yml pins -- 64 chars,
    because a SPEC arm reports spec.config_hash unabridged. If this moves, the
    daily report calls a correct deployment drifted every morning."""
    assert build_spec("manual_scalp_st_banded", 5).config_hash == DEPLOYED


def test_adding_it_moved_no_other_family():
    for family, expected in (("atr_arm", "5104b68fd7d7"),
                             ("atr_banded", "ddd76a9fda2b"),
                             ("manual_scalp", "977909932064"),
                             ("manual_scalp_st", "218901c5946f")):
        assert build_spec(family, 5, 1).config_hash[:12] == expected, family


@pytest.mark.parametrize("minutes", [1, 5, 15, 60, 240])
def test_it_validates_at_every_timeframe(minutes):
    build_spec("manual_scalp_st_banded", minutes).validate()

"""`manual_scalp_st` is manual_scalp with Supertrend alignment, nothing else.

WHY THIS EXISTS. manual_scalp gates on %R alone, and it was built that way
partly on a FALSE reading of the operator's own trade history. The analysis
reported 61% of their entries as counter-Supertrend, computed with
``direction > 0`` as bullish. Pine returns direction -1 for an UPTREND
(deltabt/rulecore.py:142), so the column was inverted; the real figure is
62.4% ALIGNED, and aligned trades averaged -0.0455R against -0.1451R for
counter ones.

WHAT THIS DOES NOT CLAIM. Across 6,559 backtested trades the effect is
-0.0013R trade-weighted -- indistinguishable from nothing. Four symbols
improve, three worsen. This family corrects a mistaken premise; it does not
encode a discovered edge, and the tests below pin its SHAPE, never its P&L.

The nearest existing family is `atr_arm`, which also carries DI=True, so the
Supertrend contribution cannot be isolated there.
"""

from __future__ import annotations

import pytest

from deltabt.catalog import FAMILIES, build_spec


def test_the_family_is_registered():
    assert "manual_scalp_st" in FAMILIES


def test_supertrend_is_the_only_thing_added_to_manual_scalp():
    base, st = build_spec("manual_scalp", 5), build_spec("manual_scalp_st", 5)
    assert base.primary.supertrend == "off"
    assert st.primary.supertrend == "aligned"
    for field in ("di", "adx_min", "wpr_rule"):
        assert getattr(st.primary, field) == getattr(base.primary, field), field


def test_no_di_and_no_adx_so_supertrend_is_isolated():
    p = build_spec("manual_scalp_st", 5).primary
    assert p.di is False
    assert p.adx_min is None


def test_the_confirmation_timeframe_stays_ungated():
    """manual_scalp_both measured this: 25% of signals, 2.2% of trades."""
    assert build_spec("manual_scalp_st", 5).confirm.enabled is False


def test_it_keeps_manual_scalp_geometry():
    base, st = build_spec("manual_scalp", 5), build_spec("manual_scalp_st", 5)
    assert st.target_r == base.target_r == 1.0
    assert st.stop_atr_multiplier == base.stop_atr_multiplier == 4.0
    assert st.trigger == base.trigger == "edge"


def test_the_deployed_hash_is_the_one_the_workflows_pin():
    """infra/terraform, deploy.yml and monitor.yml all name this arm.

    monitor.yml pins the FULL 64-char hash because a SPEC arm reports
    spec.config_hash unabridged. If this moves, the daily report calls a
    correct deployment drifted every morning until the pin is updated.
    """
    assert build_spec("manual_scalp_st", 5).config_hash == (
        "218901c5946f6e4f9220513df491cdef83eebc6135452a8c4a9c2aba91621853")


def test_adding_it_did_not_move_any_deployed_hash():
    """Adding a catalog key must never change an existing family."""
    for family, expected in (("atr_arm", "5104b68fd7d7"),
                             ("atr_banded", "ddd76a9fda2b"),
                             ("manual_scalp", "977909932064")):
        assert build_spec(family, 5, 1).config_hash[:12] == expected, family


@pytest.mark.parametrize("minutes", [1, 5, 15, 60, 240])
def test_it_validates_at_every_timeframe(minutes):
    build_spec("manual_scalp_st", minutes).validate()

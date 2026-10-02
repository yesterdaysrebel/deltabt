"""deltabt/exits.py: the one stop rule every executor asks (2026-10-02)."""
from __future__ import annotations

import pytest

from deltabt.catalog import build_spec
from deltabt.exits import earned_stop_r, tightens

LADDER = ((0.5, 0.0), (1.0, 0.5), (1.5, 1.0), (2.0, 1.5))


@pytest.mark.parametrize("fav, want", [(0.4, None), (0.5, 0.0), (0.99, 0.0),
                                       (1.0, 0.5), (1.7, 1.0), (2.0, 1.5), (2.9, 1.5)])
def test_the_ladder(fav, want):
    assert earned_stop_r(fav, ladder_rungs=LADDER) == want


@pytest.mark.parametrize("fav, want", [(0.49, None), (0.5, 0.0), (1.2, 0.7), (2.8, 2.3)])
def test_the_trail(fav, want):
    got = earned_stop_r(fav, trail_after_r=0.5, trail_r=0.5)
    assert got == (None if want is None else pytest.approx(want))


def test_tighten_only_application_of_a_stateless_trail_is_a_peak_trail():
    """The module's contract: the running max of (excursion - trail) is
    (peak - trail), so callers need no peak state."""
    path = [0.2, 0.6, 1.4, 0.9, 1.1, 2.2, 1.0]
    stop = -1.0
    for fav in path:
        cand = earned_stop_r(fav, trail_after_r=0.5, trail_r=0.5)
        if cand is not None and tightens(1, cand, stop):
            stop = cand
    assert stop == pytest.approx(max(path) - 0.5)


def test_no_rule_earns_nothing():
    assert earned_stop_r(5.0) is None


def test_existing_arm_identities_are_unchanged_by_the_new_fields():
    """The trail fields must not move the recorded paper hashes."""
    assert build_spec("manual_scalp_both_t3", 5).config_hash.startswith("41e764beceaf")
    assert build_spec("manual_scalp_both_t3_ladder", 5).config_hash.startswith("2e8bd2529685")
    trail = build_spec("manual_scalp_both_t3_trail", 5)
    assert (trail.trail_after_r, trail.trail_r) == (0.5, 0.5)
    assert trail.config_hash[:12] not in ("41e764beceaf", "2e8bd2529685")


@pytest.mark.parametrize("over, msg", [
    (dict(trail_r=0.5), "set together"),
    (dict(trail_after_r=0.5, trail_r=0.0), "positive"),
    (dict(trail_after_r=0.5, trail_r=0.5, ladder_rungs=LADDER), "not both"),
])
def test_spec_validation(over, msg):
    from dataclasses import replace
    spec = replace(build_spec("manual_scalp_both_t3", 5), **over)
    with pytest.raises(ValueError, match=msg):
        spec.validate()

"""build_spec's confirmation override must not disturb the default.

WHY THE ARGUMENT EXISTS
    The catalog pins confirmation at a constant 5:1 ratio to the primary, so
    a 60m primary is confirmed by 12m bars. That answers "what does this
    family do at scale". It does not answer "what does a 60m primary confirmed
    by the 5m chart I actually watch do", which needs the confirmation held
    FIXED while the primary widens. Both are legitimate; only one was
    reachable.

WHAT MUST NOT MOVE
    Every spec built WITHOUT the argument has to keep its hash. atr_banded@5m
    is ddd76a9fda2b and that string is what the live bot reports as its
    strategy_version -- if this refactor moved it, the running experiment's
    bind_experiment() would refuse on config drift at the next deploy, and the
    run would end. The two pins below are the ones that would actually cost
    something.

WHY A NON-DIVIDING CONFIRMATION IS REFUSED RATHER THAN ROUNDED
    align_confirm() maps each primary bar to the last confirmation bar closing
    at or before it. If the confirmation does not tile the primary, that
    instant drifts: a 7m confirmation under a 15m primary lands 1m before the
    close on one bar and 6m before it on the next, so the rule is evaluated on
    a different amount of information each time. Silently rounding would hide
    that; raising names it.
"""

from __future__ import annotations

import pytest

from deltabt.catalog import FAMILIES, build_spec


#: The two that are pinned outside this repo: ddd76a9fda2b is what the live
#: bot reports, 5104b68fd7d7 is the base every ATR comparison is against.
PINNED = {("atr_banded", 5): "ddd76a9fda2b", ("atr_arm", 5): "5104b68fd7d7"}


@pytest.mark.parametrize("key,expected", sorted(PINNED.items()))
def test_default_hashes_are_unchanged(key, expected):
    family, minutes = key
    assert build_spec(family, minutes).config_hash[:12] == expected, (
        f"{family}@{minutes}m moved off {expected}. If this is the live spec, "
        f"the running experiment will refuse to rebind and the run ends.")


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_omitting_the_override_is_identical_to_passing_none(family):
    assert build_spec(family, 15).config_hash == build_spec(
        family, 15, None).config_hash


@pytest.mark.parametrize("minutes,confirm", [
    (5, 1), (5, 5), (15, 5), (30, 5), (45, 5), (60, 5), (60, 1),
])
def test_the_override_is_honoured(minutes, confirm):
    spec = build_spec("atr_arm", minutes, confirm)
    assert spec.confirm_minutes == confirm
    assert spec.primary_minutes == minutes


def test_the_ratio_is_still_the_default():
    """60m under the 5:1 ratio is confirmed by 12m, not by the override."""
    assert build_spec("atr_arm", 60).confirm_minutes == 12


@pytest.mark.parametrize("minutes,confirm", [(15, 7), (10, 3), (5, 2)])
def test_a_confirmation_that_does_not_tile_the_primary_is_refused(
        minutes, confirm):
    with pytest.raises(ValueError, match="divide"):
        build_spec("atr_arm", minutes, confirm)


def test_a_confirmation_wider_than_the_primary_is_refused():
    with pytest.raises(ValueError, match="divide|exceed"):
        build_spec("atr_arm", 5, 15)


def test_changing_the_confirmation_changes_the_hash():
    """Otherwise two different rules would share one identity in the CSV."""
    a = build_spec("atr_arm", 15, 1).config_hash
    b = build_spec("atr_arm", 15, 5).config_hash
    assert a != b

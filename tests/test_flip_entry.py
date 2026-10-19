"""The owner's flip setup (2026-10-19): windowed Supertrend flip + %R cross."""
import numpy as np
import pytest

from deltabt import rulecore
from deltabt.catalog import build_spec
from deltabt.spec import StrategySpec
from tests import signal_lab_fixture as fx


def _frames():
    coin = fx.coin(7, 1.25)
    l = coin["ltp"]
    from deltabt.strategy import resample_complete
    return resample_complete(l, 5), l


def test_window_one_reproduces_the_one_bar_modes():
    P, C = _frames()
    from dataclasses import replace
    one = replace(build_spec("flip_st", 5, 1), event_window_bars=1)
    # the same rules with the one-bar vocabulary (NOT TimeframeRules defaults,
    # which switch DI and ADX on)
    exact = replace(one, event_window_bars=None,
                    primary=replace(one.primary, supertrend="flip", wpr_rule="cross_levels"))
    a, b = rulecore.compute(P, C, one), rulecore.compute(P, C, exact)
    # From the first fully warmed bar on: the windowed mode also refuses a
    # flip inside the warm-up zone (leg_determinate), the one-bar mode does
    # not, and a live bot never evaluates those bars anyway.
    k = one.warmup_bars
    assert np.array_equal(a.long_entry[k:], b.long_entry[k:])
    assert np.array_equal(a.short_entry[k:], b.short_entry[k:])
    assert (b.long_entry[k:] | b.short_entry[k:]).sum() > 0


def test_wider_window_fires_at_least_as_often_and_is_causal():
    P, C = _frames()
    s3, s6 = build_spec("flip_st_tight", 5, 1), build_spec("flip_st", 5, 1)
    a, b = rulecore.compute(P, C, s3), rulecore.compute(P, C, s6)
    assert (a.long_entry | a.short_entry).sum() <= (b.long_entry | b.short_entry).sum()
    assert (b.long_entry | b.short_entry).sum() > 0, "the synthetic market never fires the setup"
    # causal: truncating the future does not change the past
    cut = len(P) - 40
    c = rulecore.compute(P.iloc[:cut].reset_index(drop=True), C[C.time < P.time.iloc[cut]].reset_index(drop=True), s6)
    assert np.array_equal(c.long_entry, b.long_entry[:cut]) and np.array_equal(c.short_entry, b.short_entry[:cut])


def test_recent_helper():
    ev = np.array([0, 1, 0, 0, 0, 1, 0], dtype=bool)
    assert rulecore._recent(ev, 1).tolist() == ev.tolist()
    assert rulecore._recent(ev, 3).tolist() == [0, 1, 1, 1, 0, 1, 1]


def test_existing_specs_keep_their_hash_and_the_window_needs_a_consumer():
    assert build_spec("manual_scalp_both_t3_st", 5).config_hash == \
        "fbbd2ab96ddcd2a6f8775437c38f4b344e622756dad8620dd3dadb876d5ea3be"   # the rule's own hash on master, checked 2026-10-19
    assert "event_window_bars" not in build_spec("manual_scalp_both_t3_st", 5)._hash_payload()
    assert build_spec("flip_st", 5).config_hash != build_spec("flip_st_tight", 5).config_hash
    with pytest.raises(ValueError):
        StrategySpec(**{**build_spec("flip_st", 5).__dict__, "event_window_bars": None}).validate()
    with pytest.raises(ValueError):
        StrategySpec(**{**build_spec("manual_scalp_both_t3_st", 5).__dict__, "event_window_bars": 6}).validate()

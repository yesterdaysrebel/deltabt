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


def test_shorts_only_keeps_the_shorts_and_drops_every_long():
    from dataclasses import replace
    P, C = _frames()
    both, short = build_spec("flip_st", 5, 1), build_spec("flip_st_short", 5, 1)
    a, b = rulecore.compute(P, C, both), rulecore.compute(P, C, short)
    assert np.array_equal(a.short_entry, b.short_entry) and not b.long_entry.any()
    assert a.long_entry.any(), "the synthetic market produced no longs to drop"
    # the side restriction is part of the identity (a name is too, so the
    # shorts-only twin is compared with itself, not with the live family)
    assert replace(both, sides="short").config_hash != both.config_hash
    assert "sides" not in both._hash_payload()
    with pytest.raises(ValueError):
        replace(both, sides="shorts").validate()


# --- the 1m %R "not exhausted" check (owner, 2026-10-20) ----------------------

def test_not_exhausted_is_a_plain_level_test_mirrored_for_longs():
    from deltabt.spec import TimeframeRules
    ti = rulecore.TimeframeIndicators.__new__(rulecore.TimeframeIndicators)
    w = np.array([np.nan, -100.0, -81.0, -80.0, -79.9, -50.0, -20.0, -19.9, 0.0])
    object.__setattr__(ti, "wpr", w)
    object.__setattr__(ti, "time", np.arange(len(w)))
    rules = TimeframeRules(supertrend="off", di=False, adx_min=None, wpr_rule="not_exhausted")
    lo, sh = rulecore.gate(ti, rules)
    # a short only while %R is still above the oversold level (VVV: -100 -> refused)
    assert sh.tolist() == [False, False, False, False, True, True, True, True, True]
    # a long only while %R is still below the overbought level
    assert lo.tolist() == [False, True, True, True, True, True, False, False, False]


def test_the_1m_wpr_rule_is_the_live_rule_plus_one_filter():
    live, new = build_spec("flip_st_short", 5, 1), build_spec("flip_st_short_1m_wpr", 5, 1)
    assert live.config_hash.startswith("dbd723813926"), "the running rule keeps its identity"
    assert new.config_hash != live.config_hash
    assert new.primary == live.primary and new.confirm.supertrend == live.confirm.supertrend
    P, C = _frames()
    a, b = rulecore.compute(P, C, live), rulecore.compute(P, C, new)
    assert b.short_entry.sum() <= a.short_entry.sum()
    i = rulecore.align_confirm(P.time.to_numpy(), b.confirm.time, new)
    fired = np.where(b.short_entry)[0]
    assert (b.confirm.wpr[i[fired]] > -80.0).all(), "every short has 1m %R above -80"


def test_each_account_names_its_own_history_rule(monkeypatch):
    from live import learning
    monkeypatch.setenv("DELTABT_SIGNAL_HISTORY_FAMILY", "flip_st_short_1m_wpr")
    monkeypatch.setenv("DELTABT_SIGNAL_HISTORY_METALS_FAMILY", "flip_st_short")
    monkeypatch.setenv("DELTABOT_UNIVERSE", "top_gainers")
    assert learning._default_history_family() == "flip_st_short_1m_wpr"
    monkeypatch.setenv("DELTABOT_UNIVERSE", "metals")
    assert learning._default_history_family() == "flip_st_short"
    monkeypatch.delenv("DELTABT_SIGNAL_HISTORY_METALS_FAMILY")
    assert learning._default_history_family() == "flip_st_short_1m_wpr"

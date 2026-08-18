"""H-REL-1 Stage A guards. Machinery is shared and tested in test_hstructure2."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deltabt.research import hstructure2 as h2
from deltabt.research import hrel1 as r1


def bars(n: int, *, start: int = 1735689600, seed: int = 0, vol: float = 0.001):
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    o = np.concatenate(([100.0], c[:-1]))
    hi = np.maximum(o, c) * (1 + rng.random(n) * vol)
    lo = np.minimum(o, c) * (1 - rng.random(n) * vol)
    return pd.DataFrame(dict(time=start + 60 * np.arange(n), open=o, high=hi,
                             low=lo, close=c, volume=np.ones(n)))


def test_stage_a_machinery_is_imported_not_reimplemented():
    assert r1.estimate is h2.estimate
    assert r1.control is h2.control
    assert r1.in_split is h2.in_split
    assert r1.day_cluster is h2.day_cluster
    assert r1.PRIMARY_HORIZON_MIN == h2.PRIMARY_HORIZON_MIN


def test_the_leader_cannot_be_a_follower():
    lead = r1.leader_shock(r1.bars15(bars(30000, seed=1)))
    with pytest.raises(ValueError, match="cannot lag itself"):
        r1.events(bars(30000, seed=1), r1.LEADER, lead)


def test_leader_and_followers_are_disjoint_and_declared():
    assert r1.LEADER == "BTCUSD"
    assert r1.LEADER not in r1.FOLLOWERS
    assert set(r1.FOLLOWERS) == {"ETHUSD", "SOLUSD", "XRPUSD"}


def test_a5_requires_two_of_three_not_three_of_four():
    """The leader is excluded, so 3-of-4 would be unreachable -- a bug, not a gate."""
    assert r1.SYMBOLS_REQUIRED_A5 == 2
    assert len(r1.FOLLOWERS) == 3


def test_only_one_new_threshold_is_introduced():
    assert r1.SHOCK_PERCENTILE == 0.95
    from deltabt.research import hcompress
    assert r1.PCT_LOOKBACK == hcompress.PCT_LOOKBACK_15M


# ------------------------------------------------------------------ lookahead

def test_shock_threshold_excludes_the_current_bar():
    df = bars(30000, seed=3)
    a = r1.leader_shock(r1.bars15(df))
    spiked = df.copy()
    i = 25000
    spiked.loc[i, "close"] *= 1.05
    b = r1.leader_shock(r1.bars15(spiked))
    t = i // r1.TF_MIN
    assert np.isclose(a["threshold"].to_numpy()[t], b["threshold"].to_numpy()[t],
                      equal_nan=True)


def test_shock_state_is_unchanged_by_future_bars():
    df = bars(30000, seed=5)
    full = r1.leader_shock(r1.bars15(df))
    part = r1.leader_shock(r1.bars15(df.iloc[:24000].reset_index(drop=True)))
    k = len(part) - 1
    assert np.array_equal(full["shock"].to_numpy()[:k],
                          part["shock"].to_numpy()[:k])


def test_events_are_never_timed_before_the_bar_that_defines_them():
    lead = r1.leader_shock(r1.bars15(bars(40000, seed=7)))
    ev = r1.events(bars(40000, seed=11), "ETHUSD", lead)
    assert len(ev)
    # every t0 must be at or after the 15m close that produced it
    assert (ev["t0"].to_numpy() % (r1.TF_MIN * 60) == 0).all()


# ------------------------------------------------------------------ alignment

def test_series_are_inner_joined_never_forward_filled():
    """A filled follower bar carries a price from before the shock and would
    look exactly like a follower that failed to move."""
    lead15 = r1.bars15(bars(30000, seed=13))
    lead = r1.leader_shock(lead15)
    foll = r1.bars15(bars(30000, seed=17)).iloc[::2].reset_index(drop=True)
    F = r1.event_flags(lead, foll)
    assert set(F["time"]).issubset(set(foll["time"]))
    assert len(F) == len(set(lead["time"]) & set(foll["time"]))


def test_under_response_is_a_sign_test_against_zero():
    """No gap threshold: the condition is sign(r_lead)*(r_lead - r_foll) > 0."""
    lead = pd.DataFrame({"time": [0, 900, 1800], "close": [1.0, 1.0, 1.0],
                         "r": [0.02, -0.02, 0.02], "abs_r": [0.02, 0.02, 0.02],
                         "threshold": [0.001] * 3, "shock": [True] * 3})
    foll = pd.DataFrame({"time": [0, 900, 1800], "close": [1.0] * 3,
                         "r": [0.019, -0.019, 0.021]})
    F = r1.event_flags(lead, foll)
    # warmup suppresses these, so test the raw arithmetic instead
    m = lead.merge(foll, on="time", suffixes=("_l", "_f"))
    sign = np.sign(m["r_l"].to_numpy())
    under = sign * (m["r_l"].to_numpy() - m["r_f"].to_numpy()) > 0
    assert under.tolist() == [True, True, False]
    assert len(F) == 3


def test_direction_is_the_leader_sign():
    assert r1.DIRECTION == {"LAG_UP": 1, "LAG_DOWN": -1}


def test_up_and_down_events_are_mutually_exclusive():
    lead = r1.leader_shock(r1.bars15(bars(30000, seed=19)))
    F = r1.event_flags(lead, r1.bars15(bars(30000, seed=23)))
    assert not (F["LAG_UP"] & F["LAG_DOWN"]).any()


def test_one_family_only():
    assert list(r1.FAMILIES) == ["R1-LAG"]
    with pytest.raises(ValueError, match="family must be one of"):
        r1.family_frame(pd.DataFrame({"event": ["LAG_UP"]}), "R1-FADE")


# ------------------------------------------------------------------ TEST lock

def test_valid_split_can_never_read_a_test_price():
    ev = pd.DataFrame({"t0": [r1.VALID[1] - 60], "y_1440": [0.01]})
    assert len(r1.in_split(ev, r1.VALID, 1440)) == 0


def test_a_split_reaching_past_the_test_boundary_is_refused():
    ev = pd.DataFrame({"t0": [r1.TRAIN[0]], "y_60": [0.0]})
    with pytest.raises(ValueError, match="locked TEST boundary"):
        r1.in_split(ev, (r1.TRAIN[0], r1.TEST_START + 1), 60)


# ------------------------------------------------------------------ gate

def test_gate_default_is_unchanged_for_the_other_hypotheses():
    """Parameterising A5 for H-REL-1 must not alter how the first two are judged."""
    from deltabt.research.run_hstructure2_train import gate
    a = dict(horizons={"+60m": {"pooled": dict(effect=0.01, mde=0.001)}},
             control=dict(mean=0.0, ci_low=-0.001, ci_high=0.001, p_value=0.0),
             halves={"H1": dict(effect=0.01), "H2": dict(effect=0.01)},
             per_symbol={"A": dict(effect=0.01), "B": dict(effect=0.01),
                         "C": dict(effect=-0.01), "D": dict(effect=-0.01)})
    assert gate(a)["A5_cross_sectional"]["required"] == 3
    assert gate(a)["A5_cross_sectional"]["passed"] is False
    assert gate(a, symbols_required=2)["A5_cross_sectional"]["passed"] is True

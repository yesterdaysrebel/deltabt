"""The entry window: a spec field that must not move any existing identity.

WHY THESE TESTS EXIST

    `entry_hours_utc` is the first field added to StrategySpec since the
    catalog started carrying deployed arms, and adding a field to a dataclass
    whose `config_hash` is `sha256(json(asdict(self)))` moves EVERY spec's
    hash. That is not a cosmetic break: `bind_experiment()` refuses a container
    whose hash differs from the RUNNING forward test, so the live arm would
    stop mid-run as a side effect of adding a feature it does not use, and
    every recorded sweep would be orphaned.

    So `config_hash` omits the key when it is None, and these tests pin that
    the deployed hashes are byte-identical to what production reports today.

    The second group pins the SEMANTICS, which are easy to get subtly wrong in
    a way no hash catches: the window gates the TRIGGER, not the setup. Fold it
    into the setup and a setup already true at 17:55 goes FALSE->TRUE at the
    window boundary and fires a stale signal at 18:00 -- a different rule from
    the one measured, with a perfectly valid-looking hash.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from deltabt import rulecore
from deltabt.catalog import FAMILIES, build_spec
from deltabt.spec import StrategySpec, TimeframeRules

#: What the running paper arm reports, pinned in .github/workflows/monitor.yml.
#: If this moves, the arm stops.
DEPLOYED = "0ae19f412d693456e53c043fec93994c550af50c4eef885f5c66925ab068c467"

WINDOWED = "manual_scalp_st_banded_h18_24"


# --------------------------------------------------------------- identity ---

def test_the_deployed_arms_hash_has_not_moved():
    assert build_spec("manual_scalp_banded_h1dir_t3", 5, 60).config_hash == DEPLOYED


@pytest.mark.parametrize("family,minutes,expected", [
    ("atr_arm", 5, "5104b68fd7d7"),
    ("atr_banded", 5, "ddd76a9fda2b"),
])
def test_the_other_pinned_hashes_have_not_moved(family, minutes, expected):
    assert build_spec(family, minutes).config_hash[:12] == expected


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_family_without_a_window_hashes_as_if_the_field_did_not_exist(family):
    """The compatibility claim, stated as an executable assertion.

    Reproduces the pre-change digest: the payload with the key removed
    entirely. Every family that sets no window must hash to exactly that.
    """
    import hashlib
    import json
    from dataclasses import asdict

    spec = build_spec(family, 5, 1)
    if spec.entry_hours_utc is not None:
        pytest.skip("this family carries a window")
    payload = asdict(spec)
    del payload["entry_hours_utc"]
    legacy = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert spec.config_hash == legacy


def test_a_window_changes_the_hash():
    """The other half: it must not be free to change the rule silently."""
    base = build_spec("manual_scalp_st_banded", 5, 1)
    assert replace(base, entry_hours_utc=(18, 24)).config_hash != base.config_hash


def test_the_windowed_family_is_its_parent_plus_the_window():
    """Nothing else may differ, or the comparison in the catalog is not one."""
    parent = build_spec("manual_scalp_st_banded", 5, 1,
                        stop_atr_multiplier=4.0, target_r=1.0)
    child = build_spec(WINDOWED, 5, 1, stop_atr_multiplier=4.0, target_r=1.0)
    assert child.entry_hours_utc == (18, 24)
    assert replace(child, entry_hours_utc=None, name=parent.name).config_hash \
        == parent.config_hash


def test_the_field_survives_a_json_round_trip_as_a_tuple():
    spec = build_spec(WINDOWED, 5, 1)
    back = StrategySpec.from_dict(spec.to_dict())
    assert back.entry_hours_utc == (18, 24)
    assert back.config_hash == spec.config_hash


# ------------------------------------------------------------- validation ---

@pytest.mark.parametrize("window", [
    (24, 6),        # start is not an hour of the day
    (18, 25),       # end past midnight
    (18, 0),        # 0 is not a legal end; midnight is 24
    (-1, 6),
    (18, 18),       # empty, or every hour, depending on how you read it
    (0, 24),        # every hour: use None, which is what the archive records
    (18,),
    "18-24",
])
def test_a_nonsensical_window_is_refused(window):
    spec = replace(build_spec("manual_scalp_st_banded", 5, 1),
                   entry_hours_utc=window)
    with pytest.raises(ValueError):
        spec.validate()


@pytest.mark.parametrize("window", [(18, 24), (20, 2), (0, 6), (23, 24)])
def test_a_sensible_window_validates(window):
    replace(build_spec("manual_scalp_st_banded", 5, 1),
            entry_hours_utc=window).validate()


# -------------------------------------------------------------- semantics ---

@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    """Six days of synthetic 5m bars with a trend that turns every few hours.

    Synthetic rather than cached so the test runs anywhere; the assertions are
    about WHICH BARS may fire, which does not need real prices.
    """
    n = 6 * 24 * 12
    t = np.arange(n, dtype="int64") * 300 + 1_767_225_600  # a UTC midnight
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    return pd.DataFrame({"time": t, "open": close, "high": high, "low": low,
                         "close": close, "volume": np.ones(n)})


def _hours(sig) -> np.ndarray:
    fired = sig.long_entry | sig.short_entry
    return ((sig.time[fired] % 86_400) // 3_600)


def test_every_entry_falls_inside_the_window(bars):
    spec = build_spec(WINDOWED, 5, 1)
    sig = rulecore.compute(bars, None, spec)
    hours = _hours(sig)
    assert len(hours), "fixture produced no entries; the test would be vacuous"
    assert hours.min() >= 18 and hours.max() <= 23


def test_a_wrapping_window_is_honoured(bars):
    spec = replace(build_spec("manual_scalp_st_banded", 5, 1),
                   entry_hours_utc=(22, 3))
    sig = rulecore.compute(bars, None, spec)
    hours = _hours(sig)
    assert len(hours)
    assert set(np.unique(hours)) <= {22, 23, 0, 1, 2}


def test_the_window_gates_the_trigger_and_not_the_setup(bars):
    """The property the measurement rests on.

    Every windowed entry must ALSO be an entry of the unwindowed spec on the
    same bar. If the window were folded into the setup, the boundary bar would
    manufacture a FALSE->TRUE edge and fire a setup that was already true --
    an entry the unwindowed spec does not have.
    """
    base = build_spec("manual_scalp_st_banded", 5, 1)
    win = replace(base, entry_hours_utc=(18, 24))
    b = rulecore.compute(bars, None, base)
    w = rulecore.compute(bars, None, win)

    assert not np.any(w.long_entry & ~b.long_entry), (
        "the windowed spec fires a long the unwindowed one does not; the "
        "window is creating edges at its own boundary")
    assert not np.any(w.short_entry & ~b.short_entry)

    inside = ((b.time % 86_400) // 3_600 >= 18)
    assert np.array_equal(w.long_entry, b.long_entry & inside)
    assert np.array_equal(w.short_entry, b.short_entry & inside)


def test_the_setup_itself_is_untouched(bars):
    """Only the trigger is gated, so the audit trail can still say 'a setup
    existed and the clock refused it' rather than 'there was no setup'."""
    base = build_spec("manual_scalp_st_banded", 5, 1)
    win = replace(base, entry_hours_utc=(18, 24))
    b = rulecore.compute(bars, None, base)
    w = rulecore.compute(bars, None, win)
    assert np.array_equal(b.long_setup, w.long_setup)
    assert np.array_equal(b.short_setup, w.short_setup)


def test_refusals_by_the_clock_are_recorded(bars):
    base = build_spec("manual_scalp_st_banded", 5, 1)
    win = replace(base, entry_hours_utc=(18, 24))
    b = rulecore.compute(bars, None, base)
    w = rulecore.compute(bars, None, win)

    refused = w.rejected_entry_hours
    assert refused.sum() > 0
    # Exactly the unwindowed entries that fall outside the window.
    outside = (b.long_entry | b.short_entry) & (((b.time % 86_400) // 3_600) < 18)
    assert np.array_equal(refused, outside)
    # And a spec with no window never claims one.
    assert not rulecore.compute(bars, None, base).rejected_entry_hours.any()


def test_a_windowless_spec_is_bit_identical_to_before(bars):
    """No window means no behaviour change anywhere, not merely the same hash."""
    spec = build_spec("manual_scalp_st_banded", 5, 1)
    a = rulecore.compute(bars, None, spec)
    b = rulecore.compute(bars, None, replace(spec, entry_hours_utc=None))
    assert np.array_equal(a.long_entry, b.long_entry)
    assert np.array_equal(a.short_entry, b.short_entry)


def test_the_live_evaluator_explains_a_clock_refusal(bars):
    """The bot must say why, or an operator sees a silent arm and no reason."""
    from app.strategy.explanation import Outcome
    from app.strategy.spec_arm import evaluate_spec

    base = build_spec("manual_scalp_st_banded", 5, 1)
    win = replace(base, entry_hours_utc=(18, 24), name="windowed")
    one_min = bars  # a 1m frame at 5m spacing is fine: the spec resamples

    b = rulecore.compute(bars, None, base)
    fired = np.flatnonzero(
        (b.long_entry | b.short_entry) & (((b.time % 86_400) // 3_600) < 18))
    assert len(fired), "fixture produced no out-of-window entry"

    spec5 = replace(win, primary_minutes=1)
    cut = int(fired[-1]) + 1
    exp = evaluate_spec(one_min.iloc[:cut], spec5, symbol="TESTUSD")
    assert exp.outcome is Outcome.REJECTED
    assert "entry window" in exp.rejection_reason
    assert exp.detail["entry_hours_utc"] == [18, 24]


# ----------------------------------------------------- the other new arm ---

def test_manual_scalp_t4_is_the_original_entry_with_a_4r_target():
    """Nothing but the target may differ, or 'the operator's original entry,
    held for the move' is not what the stack runs."""
    base = build_spec("manual_scalp", 5, 1)
    t4 = build_spec("manual_scalp_t4", 5, 1)
    assert t4.target_r == 4.0
    assert t4.entry_hours_utc is None
    assert replace(t4, target_r=base.target_r, name=base.name).config_hash == base.config_hash


def test_the_original_familys_hash_has_not_moved():
    """It ran live as MANUAL-SCALP-5M-PAPER-20260831-*; that record must stay
    comparable."""
    assert build_spec("manual_scalp", 5, 1).config_hash.startswith("977909932064d543")

"""The operator's hand-traded style, encoded as a spec.

WHY THIS EXISTS. `manual_scalp` was recovered from 165 hand-placed round trips
(out/manual/roundtrips_seven.csv), not invented. These tests pin the three
parameters the record actually justifies, so a later edit has to argue with the
evidence rather than drift silently:

    no Supertrend / no DI   they carried no information -- Supertrend agreed
                            with 39% of winners and 39% of losers, DI with 51%
                            and 54%, and 61% of trades were counter-Supertrend
    target_r = 1.0          winners cluster at 0.5-1.5R (77 of 83)
    stop 4x ATR             winners' stops 150 bps median, losers' 122 bps

Adding a FAMILIES key cannot move another spec's config_hash; the pin below is
here so that THIS family's hash is noticed when it changes.
"""
from __future__ import annotations

import pytest

from deltabt.catalog import FAMILIES, build_spec


def test_the_family_is_registered():
    assert "manual_scalp" in FAMILIES


def test_the_gates_the_record_found_inert_are_off():
    """Supertrend and DI did not separate the operator's winners from losers."""
    s = build_spec("manual_scalp", 5)
    assert s.primary.supertrend == "off"
    assert s.primary.di is False
    assert s.primary.adx_min is None, (
        "an ADX gate was NOT justified: the 20-25 band was the best of five "
        "bands on n=40, in-sample, and is a hypothesis rather than a finding")


def test_it_takes_profit_at_one_r_not_two():
    assert build_spec("manual_scalp", 5).target_r == 1.0


def test_the_stop_is_wide_because_tight_stops_lost():
    s = build_spec("manual_scalp", 5)
    assert s.stop == "atr" and s.stop_atr_multiplier == 4.0
    assert s.max_stop_pct == 0.10, (
        "the thin symbols carry 200-380 bps stops; the 5% default would refuse "
        "exactly the setups whose cost economics work")


def test_single_timeframe():
    """The operator sometimes trades 1m alone, and the confirm added nothing
    measurable -- 5m/1m and 1m/1m scored -0.0743 and -0.0806."""
    assert build_spec("manual_scalp", 5).confirm.enabled is False


def test_adding_it_did_not_move_the_running_arm():
    """The live experiment binds on strategy_hash; a new key must not shift it."""
    assert build_spec("atr_arm", 5).config_hash[:12] == "5104b68fd7d7"
    assert build_spec("atr_banded", 5).config_hash[:12] == "ddd76a9fda2b"


@pytest.mark.parametrize("minutes", [1, 5, 15, 60])
def test_it_builds_and_validates_at_every_timeframe(minutes):
    build_spec("manual_scalp", minutes).validate()

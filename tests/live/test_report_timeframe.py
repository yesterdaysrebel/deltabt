"""The report must escalate silence against the arm's OWN bar period.

WHAT THIS PINS
    ``scripts/daily_report.py`` hard-coded 5 minutes in two places: the prose
    that explains a young run, and the threshold at which zero evaluations in
    24h becomes a problem. Both were written when every arm decided on 5m bars.

    v5 decides on 240m bars. Its first evaluation can legitimately be four
    hours after start, so a one-hour threshold escalates a healthy run -- the
    cry-wolf failure the surrounding code already documents for feed
    reconnects, reintroduced for a different reason. The "should be in the
    hundreds" line is wrong by an order of magnitude too: 4 symbols x 6 bars a
    day is 24, not hundreds.

    The timeframe is read off the strategy version rather than added to
    /api/status, because scripts/ is not copied into the image and app/ is:
    changing app/ would trigger the deploy workflow, restart the container
    under a RUNNING experiment, and end the run to improve a sentence in its
    own report.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_REPORT = pathlib.Path(__file__).resolve().parents[2] / "scripts/daily_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("daily_report", _REPORT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def dr():
    return _load()


@pytest.mark.parametrize("version,minutes", [
    ("wpr_only@240m@110eede40f13", 240),          # the deployed arm
    ("hwpr_v2@60m@2c9d8833017f", 60),
    ("trend_pure@15m@0123456789ab", 15),
    ("some_family@1m@abcdef012345", 1),
])
def test_a_spec_names_its_timeframe(dr, version, minutes):
    assert dr.primary_minutes(version) == minutes


@pytest.mark.parametrize("version", [
    "H-WPR-1-VariantA@d7837e445bc74781",          # the 5m arms name no timeframe
    "H-WPR-1-VariantA",
    "",
    None,
])
def test_anything_without_one_falls_back_to_five(dr, version):
    """Every StrategyConfig arm decides on 5m; that is the only safe default."""
    assert dr.primary_minutes(version) == 5


def test_the_240m_arm_is_not_escalated_after_one_hour(dr):
    """The defect. A healthy 240m run can be silent for nearly four hours."""
    assert dr.silence_threshold("wpr_only@240m@110eede40f13") == 8 * 3600
    assert dr.silence_threshold("wpr_only@240m@110eede40f13") > 4 * 3600, (
        "one full bar period must fit inside the threshold, with room to spare")


def test_the_threshold_is_two_bars(dr):
    """One bar is not enough: a run starting just after a close waits a full
    period for its first, so a one-bar threshold escalates a healthy start."""
    for version, minutes in (("x@60m@aa", 60), ("x@240m@bb", 240)):
        assert dr.silence_threshold(version) >= 2 * 60 * minutes


def test_the_five_minute_floor_is_unchanged(dr):
    """The arms this was written for must keep the behaviour they had."""
    assert dr.silence_threshold("H-WPR-1-VariantA@d7837e445bc74781") == 3600.0
    assert dr.silence_threshold(None) == dr.MIN_RUN_AGE_FOR_SILENCE


def test_short_timeframes_do_not_shrink_below_the_floor(dr):
    """Two 1m bars is 120 seconds; escalating that would fire constantly."""
    assert dr.silence_threshold("x@1m@cc") == 3600.0
    assert dr.silence_threshold("x@5m@dd") == 3600.0

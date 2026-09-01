"""Exits for "the setup stopped being true", and the defaults that keep them off.

WHY THESE EXIST. On 2026-09-01 the live arm's two closed trades lost -1.187R
and -1.351R against a -1.0R stop. The decomposition:

    SOLUSD   price move 0.995R + fees 0.192R = -1.187R
    ETHUSD   price move 1.075R + fees 0.277R = -1.351R

The stop worked; fees landed on top of it. A full stop-out therefore costs
MORE than 1R, so cutting the loss early saves the remaining price move and
leaves a proportionally smaller fee. It also truncates winners that would have
recovered -- which is exactly why this is measured, not assumed.

WHAT IS NOT HERE. `exit_on_trend_flip` already existed and was measured on the
PRIMARY timeframe (bull_1m/bear_1m are legacy names built from
`pi.direction`): it takes -6.56% to -29.58% and the win rate from 50% to 33%,
because a 1R target on a 4xATR stop needs hours and Supertrend flips sooner.

BOTH NEW EXITS DEFAULT OFF. Every backtest number in this repository was
produced without them; a default that changed would silently invalidate all of
it.
"""

from __future__ import annotations

import dataclasses

import pytest

from deltabt.config import StrategyParams


def _p(**kw) -> StrategyParams:
    return dataclasses.replace(StrategyParams(), **kw)


class TestDefaultsAreOff:
    def test_adverse_r_is_off(self):
        assert StrategyParams().exit_at_adverse_r is None

    def test_wpr_band_exit_is_off(self):
        assert StrategyParams().exit_on_wpr_band_exit is False

    def test_the_band_levels_match_the_entry_band(self):
        p = StrategyParams()
        assert p.wpr_exit_long_level == -80.0
        assert p.wpr_exit_short_level == -20.0


class TestValidation:
    @pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.1])
    def test_adverse_r_outside_the_open_interval_is_refused(self, bad):
        with pytest.raises(ValueError, match="exit_at_adverse_r"):
            _p(exit_at_adverse_r=bad).validate()

    @pytest.mark.parametrize("good", [0.1, 0.3, 0.5, 0.7, 0.99])
    def test_sensible_thresholds_pass(self, good):
        _p(exit_at_adverse_r=good).validate()

    def test_none_is_valid_and_means_disabled(self):
        _p(exit_at_adverse_r=None).validate()

    def test_inverted_band_levels_are_refused(self):
        """A long exits UNDER the floor and a short OVER the ceiling. If the
        floor sits above the ceiling both fire immediately, on every bar."""
        with pytest.raises(ValueError, match="must be BELOW"):
            _p(wpr_exit_long_level=-10.0, wpr_exit_short_level=-90.0).validate()

    @pytest.mark.parametrize("bad", [-101.0, 1.0, 50.0])
    def test_levels_outside_the_wpr_range_are_refused(self, bad):
        with pytest.raises(ValueError, match="wpr_exit"):
            _p(wpr_exit_long_level=bad).validate()


class TestTheArithmeticOfAnEarlyExit:
    """Pins the intent: the threshold is a FRACTION OF THE STOP DISTANCE."""

    @pytest.mark.parametrize("frac,entry,stop,exit_px,fires", [
        (0.5, 100.0, 90.0, 95.0, True),    # -0.5R exactly
        (0.5, 100.0, 90.0, 94.0, True),    # -0.6R, past it
        (0.5, 100.0, 90.0, 96.0, False),   # -0.4R, not yet
        (0.3, 100.0, 90.0, 97.0, True),    # -0.3R exactly
        (0.7, 100.0, 90.0, 95.0, False),   # -0.5R against a 0.7 threshold
    ])
    def test_long_side(self, frac, entry, stop, exit_px, fires):
        assert ((entry - exit_px) >= frac * (entry - stop)) is fires

    @pytest.mark.parametrize("frac,entry,stop,exit_px,fires", [
        (0.5, 100.0, 110.0, 105.0, True),
        (0.5, 100.0, 110.0, 104.0, False),
        (0.5, 100.0, 110.0, 106.0, True),
    ])
    def test_short_side_mirrors_it(self, frac, entry, stop, exit_px, fires):
        assert ((exit_px - entry) >= frac * (stop - entry)) is fires


class TestTheBandExitFiresOnlyAdversely:
    """Leaving the band the FAVOURABLE way is the trade working.

    A long entered at %R -70 whose %R climbs to -40 has left the band upward --
    toward overbought -- which is the move it was taken for. Exiting there
    would cut precisely the trades that reach target.
    """

    @pytest.mark.parametrize("wpr,fires", [
        (-85.0, True),    # dropped out the bottom: the setup failed
        (-95.0, True),
        (-79.0, False),   # still inside
        (-40.0, False),   # left upward: winning, not failing
        (-5.0, False),
    ])
    def test_long(self, wpr, fires):
        assert (wpr < StrategyParams().wpr_exit_long_level) is fires

    @pytest.mark.parametrize("wpr,fires", [
        (-15.0, True),    # broke out the top: the setup failed
        (-5.0, True),
        (-25.0, False),   # still inside
        (-70.0, False),   # left downward: winning
    ])
    def test_short(self, wpr, fires):
        assert (wpr > StrategyParams().wpr_exit_short_level) is fires


class TestOrderingIsNotArbitrary:
    def test_stop_and_target_are_checked_before_the_new_exits(self):
        """If a bar reached the stop or the target, THAT is the real exit.

        The new conditions read the CLOSE, so on a bar that traded through the
        stop they would otherwise report a better price the position never got.
        """
        for path in ("deltabt/engine.py", "deltabt/portfolio.py"):
            src = open(path).read()
            i = src.index("_stop:")            # `elif hit_stop:`
            for later in ('"adverse_r"', '"wpr_band"'):
                assert src.index(later) > i, f"{later} precedes the stop in {path}"

    def test_both_engines_carry_the_same_exits(self):
        """gated_backtest uses portfolio.py; backtest_sweep uses engine.py.
        An exit in only one would make the two disagree silently."""
        for reason in ('"adverse_r"', '"wpr_band"'):
            assert reason in open("deltabt/engine.py").read()
            assert reason in open("deltabt/portfolio.py").read()

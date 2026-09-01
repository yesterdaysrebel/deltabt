"""The live band exit: close when %R leaves the entry band ADVERSELY.

WHY IT EXISTS. On 2026-09-01 the arm's two closed trades lost -1.187R and
-1.351R against a -1.0R stop, decomposing as a ~1.0R price move plus 0.19R and
0.28R of fees. A full stop-out costs more than 1R, so leaving earlier saves the
remainder of the move and a proportionally smaller fee.

WHAT MUST NOT HAPPEN. Leaving the band the FAVOURABLE way is the trade working.
A long whose %R climbs past the midpoint toward overbought is winning; closing
there would cut precisely the trades that reach target. Half of these tests
exist to pin that asymmetry.

MEASURED BEFORE BEING BUILT (ungated portfolio, 2026-09-01):
    thin 3    -6.56% -> -7.32%   max drawdown 14.85% -> 12.40%
    all 7    -60.43% -> -81.47%  max drawdown 63.67% -> 83.14%
"""

from __future__ import annotations

import dataclasses

import pytest

from app.config.settings import RiskConfig, _as_bool
from app.execution.paper_broker import ExitReason


class TestTheEnvFlagCannotBeSwitchedOnByWritingZero:
    """`if env.get(key)` skips empty strings, but "0" is a non-empty string and
    therefore truthy. A naive bool() would read DELTABOT_WPR_BAND_EXIT=0 as
    True and enable the very thing the operator wrote 0 to disable."""

    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off", " 0 "])
    def test_falsey_words_are_false(self, raw):
        assert _as_bool(raw) is False

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on"])
    def test_truthy_words_are_true(self, raw):
        assert _as_bool(raw) is True


class TestDefaultsAreOff:
    def test_the_exit_is_off_by_default(self):
        assert RiskConfig().exit_on_wpr_band_exit is False

    def test_levels_mirror_the_entry_band(self):
        r = RiskConfig()
        assert r.wpr_exit_long_level == -80.0
        assert r.wpr_exit_short_level == -20.0

    def test_it_is_a_risk_field_so_the_strategy_hash_is_untouched(self):
        """Like max_hold_seconds: a policy about carrying inventory, not a
        signal rule. It moves risk_hash, which ends the running experiment by
        design, and leaves the strategy identifiable."""
        assert "exit_on_wpr_band_exit" in {f.name for f in
                                          dataclasses.fields(RiskConfig)}


class TestTheExitReasonIsDistinct:
    def test_it_is_not_reported_as_a_stop_or_a_time_exit(self):
        """A setup invalidation is neither. Folding it into STOP_LOSS would
        make the daily report's exit mix lie about why trades ended."""
        assert ExitReason.SETUP_INVALIDATED.value == "SETUP_INVALIDATED"
        assert ExitReason.SETUP_INVALIDATED not in (
            ExitReason.STOP_LOSS, ExitReason.TIME_EXIT,
            ExitReason.TAKE_PROFIT, ExitReason.MANUAL_CLOSE)


class TestTheAdverseSideOnly:
    """LONG exits BELOW the floor. SHORT exits ABOVE the ceiling."""

    LONG, SHORT = 1, -1

    def _fires(self, side, wpr, r=None):
        r = r or RiskConfig()
        return (wpr < r.wpr_exit_long_level if side == self.LONG
                else wpr > r.wpr_exit_short_level)

    @pytest.mark.parametrize("wpr,fires", [
        (-85.0, True), (-95.0, True), (-100.0, True),
        (-80.0, False),                      # exactly on the floor is inside
        (-79.9, False), (-65.0, False),
        (-40.0, False), (-5.0, False),       # left UPWARD: winning, not failing
    ])
    def test_long(self, wpr, fires):
        assert self._fires(self.LONG, wpr) is fires

    @pytest.mark.parametrize("wpr,fires", [
        (-15.0, True), (-5.0, True), (0.0, True),
        (-20.0, False),                      # exactly on the ceiling is inside
        (-35.0, False), (-70.0, False),      # left DOWNWARD: winning
        (-95.0, False),
    ])
    def test_short(self, wpr, fires):
        assert self._fires(self.SHORT, wpr) is fires

    def test_the_two_sides_never_fire_on_the_same_value(self):
        """Otherwise every bar closes both books."""
        for w in (-100.0, -90.0, -50.0, -20.0, -10.0, 0.0):
            assert not (self._fires(self.LONG, w) and self._fires(self.SHORT, w))


class TestTheDeliveryChainIsComplete:
    """Three links, and it has broken four times: a variable, user_data
    writing it, and run.sh forwarding it with -e. Sourcing /opt/deltabt/env
    does NOT put a value in the container's environment."""

    @pytest.mark.parametrize("var", ["DELTABOT_WPR_BAND_EXIT",
                                     "DELTABOT_WPR_EXIT_LONG",
                                     "DELTABOT_WPR_EXIT_SHORT"])
    def test_user_data_writes_it_and_run_sh_forwards_it(self, var):
        assert var in open("infra/terraform/templates/user_data.sh.tftpl").read()
        assert var in open("deploy/aws/run.sh").read()

    @pytest.mark.parametrize("var", ["exit_on_wpr_band_exit",
                                     "wpr_exit_long_level",
                                     "wpr_exit_short_level"])
    def test_terraform_declares_it_and_passes_it_to_the_template(self, var):
        assert f'variable "{var}"' in open("infra/terraform/variables.tf").read()
        assert var in open("infra/terraform/ec2.tf").read()

    def test_the_bot_passes_them_to_the_broker(self):
        """Not via broker_params: that feeds EXECUTION_FIELDS, and a value
        there but not in the tuple is dropped from execution_hash silently."""
        src = open("app/runtime/bot.py").read()
        for kw in ("exit_on_wpr_band_exit=", "wpr_exit_long_level=",
                   "wpr_exit_short_level="):
            assert kw in src

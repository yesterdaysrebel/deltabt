"""The backtest's exits must be the exits the paper trader can actually take.

THE DEFECT THIS PINS
    `StrategyParams.exit_on_trend_flip` defaults to True and deltabt/harness.py
    did not override it, so EVERY sweep in out/sweep/ closed positions when the
    primary Supertrend flipped. The live bot cannot do that. Its exits are

        STOP_LOSS, TAKE_PROFIT, MANUAL_CLOSE, TIME_EXIT, SYSTEM_SAFETY,
        DATA_FAILURE

    and there is no TREND_FLIP among them. Separately, HOLD_HOURS was 48 here
    against max_hold_seconds = 86400 (24h) in Terraform, so a backtested
    position had twice as long to recover as a real one.

    Measured on atr_arm at 5m over 7 symbols, the flip exit is not cosmetic:
    stop-losses fell from 66% of exits to 33% and the money lost on stops fell
    45%, while total P&L got WORSE (-15,624 against -10,062). A sweep run that
    way is not a preview of the bot; it is a different strategy.

WHY IT WAS INVISIBLE
    StrategySpec carries entries, stops and targets -- and NO EXIT POLICY. So
    CLAUDE.md's "a strategy is defined ONCE and both sides execute that
    definition" simply does not reach exits: they live in StrategyParams on
    the backtest side and in the broker on the live side, with nothing
    comparing them. This file is that comparison until the spec grows one.

WHAT IS ASSERTED
    Not that the exits are correct -- that they MATCH. Same shape as
    tests/live/test_env_forwarding.py and tests/live/test_alarm_delivery.py.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from deltabt.catalog import build_spec
from deltabt.harness import EXIT_ON_TREND_FLIP, HOLD_HOURS, params_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
BROKER = ROOT / "app/execution/paper_broker.py"
VARIABLES = ROOT / "infra/terraform/variables.tf"


def _live_exit_reasons() -> set[str]:
    """The ExitReason enum the paper broker can actually emit."""
    text = BROKER.read_text()
    body = text[text.index("class ExitReason"):]
    body = body[: body.index("\n\n")]
    return set(re.findall(r"^\s+([A-Z_]+)\s*=", body, re.M))


def _live_max_hold_seconds() -> int:
    """The configured time stop, from the Terraform variable's default."""
    text = VARIABLES.read_text()
    block = text[text.index('variable "max_hold_seconds"'):]
    block = block[: block.index("\n}")]
    m = re.search(r"default\s*=\s*(\d+)", block)
    assert m, "max_hold_seconds has no default; parity cannot be checked"
    return int(m.group(1))


def test_the_broker_has_no_trend_flip_exit():
    """If this ever becomes false, the harness may enable the flip exit."""
    assert "TREND_FLIP" not in _live_exit_reasons()


def test_the_harness_does_not_use_an_exit_the_bot_lacks():
    has_live_flip = "TREND_FLIP" in _live_exit_reasons()
    assert EXIT_ON_TREND_FLIP == has_live_flip, (
        "deltabt/harness.py sets exit_on_trend_flip="
        f"{EXIT_ON_TREND_FLIP} but the paper broker's TREND_FLIP support is "
        f"{has_live_flip}. Every sweep would be measuring exits the bot "
        "cannot take.")


@pytest.mark.parametrize("minutes", [1, 5, 15, 30, 60, 240])
def test_every_cell_gets_the_parity_exit_settings(minutes):
    """Both StrategyParams builders in the harness, not just one."""
    p = params_for(build_spec("atr_arm", minutes), minutes)
    assert p.exit_on_trend_flip is False


def test_the_time_stop_matches_the_deployed_one():
    assert HOLD_HOURS * 3600 == _live_max_hold_seconds(), (
        f"backtests hold for {HOLD_HOURS}h but the bot is deployed with "
        f"max_hold_seconds={_live_max_hold_seconds()} "
        f"({_live_max_hold_seconds()/3600:.0f}h). A backtested position gets a "
        f"different amount of time to recover than a real one.")


@pytest.mark.parametrize("minutes,expected_bars", [
    (5, 288),      # 24h / 5m
    (60, 24),      # 24h / 1h
    (240, 20),     # floor of 20 bars, not 6
])
def test_the_hold_is_scaled_to_wall_clock_not_bars(minutes, expected_bars):
    """240 BARS is 4 hours at 1m and 40 days at 240m. Scaling is the point."""
    p = params_for(build_spec("atr_arm", minutes), minutes)
    assert p.max_hold_bars == expected_bars


def test_recorded_sweeps_are_not_silently_stale():
    """out/sweep/*.csv predates this fix unless it was regenerated.

    Not an assertion about the numbers -- a reminder that carries the reason,
    so a future reader does not compare a pre-fix CSV against a post-fix run
    and call the difference a strategy result.
    """
    readme = ROOT / "out/sweep/README.md"
    assert readme.exists(), "out/sweep/README.md is where that caveat lives"
    assert "exit_on_trend_flip" in readme.read_text(), (
        "out/sweep/README.md does not mention exit_on_trend_flip. Any CSV in "
        "that directory generated before 2026-08-27 was run with a Supertrend "
        "flip exit the bot does not have, and the README must say so.")

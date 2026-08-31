"""A 1R arm must be able to fill an order.

WHAT HAPPENED. On 2026-08-31 manual_scalp -- which takes profit at 1R, the
finding it encodes from 165 hand-placed trades -- went live, bound cleanly,
evaluated bars, approved four setups and sent four orders. Every fill was
refused:

    reward/risk at the actual fill is 1.00, below the 1.70 floor (planned 1.00)

Healthy, bound, and structurally unable to open a position. `/readyz` green,
`/healthz` green, the daily report said "All clear".

WHY 1.7 WAS THE WRONG SHAPE. It is 0.85 x 2.0: "reject a fill that gives away
more than 15% of the planned RR". Written as an absolute it silently means
"reject everything" for any target below 1.7R. As a ratio it is correct for any
target, and reproduces 1.7 exactly for the 2R arms that have always used it.

This is the third gate in one day that refused everything while reporting
healthy -- minimum_rr=2.0 in the risk engine, min_fill_rr=1.7 in the broker,
and before them DELTABOT_MAX_DAILY_LOSS reaching no container at all. The tests
below pin the arithmetic AND the wiring, because the wiring is what was missing:
PaperBroker always accepted the argument; bot.py never passed it.
"""
from __future__ import annotations

import pytest

from app.config.settings import RiskConfig
from app.execution.paper_broker import FILL_RR_RETENTION, PaperBroker


def test_the_ratio_reproduces_the_old_constant_for_a_2r_arm():
    """Every existing arm must keep the behaviour it has today."""
    assert FILL_RR_RETENTION * 2.0 == pytest.approx(1.7)


def test_a_1r_arm_gets_a_floor_below_its_own_target():
    """0.85 < 1.0, so a 1R fill is no longer refused for being 1R."""
    assert FILL_RR_RETENTION * 1.0 < 1.0


def test_the_brokers_own_default_is_unchanged():
    """Direct constructions -- including the backtest's -- are untouched."""
    b = PaperBroker({}, starting_equity=10_000.0)
    assert b.min_fill_rr == pytest.approx(1.7)


@pytest.mark.parametrize("minimum_rr", [1.0, 1.5, 2.0, 3.0])
def test_the_floor_is_always_below_the_approved_target(minimum_rr):
    """THE INVARIANT THAT WAS BROKEN. A fill floor at or above the RR the risk
    engine approves refuses every order the risk engine allows -- the two gates
    contradict each other and the bot silently does nothing."""
    assert FILL_RR_RETENTION * minimum_rr < minimum_rr


def test_the_bot_passes_a_derived_floor_not_the_default():
    """PaperBroker always accepted min_fill_rr. bot.py never passed it, which
    is the whole defect -- so this asserts the wiring, not the arithmetic.

    It checks for broker_params rather than the constant: deriving the value
    inline in bot.py was the FIRST version of this fix, and it left cli.py's
    literal behind. Going through one function is what stops that.
    """
    import inspect

    from app.runtime import bot as bot_module
    src = inspect.getsource(bot_module.TradingBot.__init__)
    assert "broker_params" in src, (
        "bot.py must build its execution settings from broker_params; a "
        "defaulted min_fill_rr is what refused every fill on the 1R arm, and "
        "an inline derivation is what let cli.py drift away from it")


def test_min_fill_rr_is_part_of_the_experiment_identity():
    """Changing it moves execution_hash, so a running experiment ends rather
    than silently changing what it measures."""
    from app.forwardtest.identity import EXECUTION_FIELDS
    assert "min_fill_rr" in EXECUTION_FIELDS


def test_the_risk_config_default_still_pairs_with_the_old_constant():
    """RiskConfig.minimum_rr defaults to 2.0; 0.85 x 2.0 is the historic 1.7,
    so an arm that configures nothing behaves exactly as before."""
    assert FILL_RR_RETENTION * RiskConfig().minimum_rr == pytest.approx(1.7)


# --------------------------------------------------------------------------
# ONE SOURCE. The first version of this fix changed the broker and left
# app/cli.py's EXEC_PARAMS literal at 1.7, so an experiment created by the CLI
# recorded an execution_hash the bot could not reproduce. The bot refused its
# own experiment and would not start. identity.execution_params() exists
# because these two had already diverged once; changing one side reintroduced
# it. These tests pin that neither side holds a literal.

def test_broker_params_is_the_only_statement_of_these_values():
    """cli.py must not carry its own copy again."""
    import inspect

    from app import cli
    src = inspect.getsource(cli)
    assert "broker_params" in src, "cli.py must build the dict from one source"
    assert '"min_fill_rr": 1.7' not in src and "'min_fill_rr': 1.7" not in src, (
        "cli.py is holding a literal min_fill_rr again -- that is the bug")


def test_the_bot_and_the_cli_would_agree(monkeypatch):
    """THE INVARIANT THAT BROKE. What the CLI records when an experiment is
    created must equal what the bot computes when it verifies itself, or the
    bot refuses to start on execution_hash drift."""
    from app.config.settings import RiskConfig
    from app.execution.paper_broker import PaperBroker, broker_params

    for minimum_rr in (1.0, 2.0):
        risk = RiskConfig(minimum_rr=minimum_rr)
        recorded = broker_params(risk)                     # what the CLI writes
        broker = PaperBroker({}, starting_equity=10_000.0,
                             **broker_params(risk))        # what the bot builds
        for field, value in recorded.items():
            assert getattr(broker, field) == value, (
                f"{field} recorded as {value} but the broker holds "
                f"{getattr(broker, field)}")


def test_every_execution_field_the_identity_hashes_is_covered():
    """A field in EXECUTION_FIELDS that broker_params does not set is a field
    the two sides can disagree about. slippage_bps comes from RiskConfig and is
    passed separately, so it is the one exception."""
    from app.execution.paper_broker import broker_params
    from app.forwardtest.identity import EXECUTION_FIELDS
    from app.config.settings import RiskConfig
    covered = set(broker_params(RiskConfig())) | {"slippage_bps"}
    assert set(EXECUTION_FIELDS) <= covered, (
        f"not covered by broker_params: {set(EXECUTION_FIELDS) - covered}")

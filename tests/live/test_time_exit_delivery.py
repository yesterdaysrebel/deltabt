"""The time stop, end to end -- and the delivery chain that never carried it.

WHY THIS FILE EXISTS SEPARATELY FROM TestTheTimeStop.

    tests/live/test_risk.py already proves `_timed_out` computes the right
    answer. It always did. The defect was that the VALUE never reached the
    process: b63e365 shipped the code and called itself "Apply 1 of 2", and
    apply 2 never landed. DELTABOT_MAX_HOLD existed in exactly one place --
    the settings override table -- and appeared in neither
    user_data.sh.tftpl nor the `-e` list in run.sh, so max_hold_seconds was 0
    in every container no matter what anyone configured.

    No behavioural test could have caught that, because the behaviour was
    correct at every value it was ever given. The gap was between the config
    and the container, so the guard has to live there too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.execution.intents import ApprovedOrderIntent
from app.execution.paper_broker import ExitReason, PaperBroker, PaperPosition
from app.market_data.normalize import Tick
from deltabt.costs import SymbolCosts

ROOT = Path(__file__).resolve().parents[2]
BTC = SymbolCosts(symbol="BTCUSD", tick_size=0.5, contract_value=0.001,
                  maker_fee=0.0002, taker_fee=0.0005, max_leverage=200.0,
                  position_size_limit=125_000, funding_interval_seconds=28800,
                  slippage_bps=2.0)
COSTS = {"BTCUSD": BTC}
US = 1_000_000
DAY = 86_400
BAR_CLOSE = 1786560300


def tick(ltp, mark=None, ts=BAR_CLOSE):
    return Tick("BTCUSD", ts * US, ltp, mark if mark is not None else ltp)


def intent(entry=63_000.0, stop=62_500.0, target=64_000.0, qty=100):
    return ApprovedOrderIntent(
        intent_id="i1", signal_key="sig1", risk_evaluation_id="risk1",
        symbol="BTCUSD", side=1, order_type="market", quantity=qty,
        limit_price=None, entry_reference=entry, stop_price=stop,
        target_price=target, risk_per_unit=abs(entry - stop),
        risk_amount=qty * BTC.contract_value * abs(entry - stop),
        notional=BTC.notional(qty, entry), equity_before=10_000.0,
        estimated_fee=1.0, estimated_slippage=0.5,
        strategy_version="H-WPR-1-VariantA@abc", bar_open=BAR_CLOSE,
        checks_passed=("minimum_rr",))


def opened(max_hold=DAY):
    """A broker holding one live position, opened at BAR_CLOSE."""
    b = PaperBroker(COSTS, starting_equity=10_000.0, slippage_bps=2.0,
                    max_hold_seconds=max_hold)
    order = b.submit_order(intent(), now=BAR_CLOSE)
    b.process_market_event(tick(63_000.0))
    pos = b.get_positions("BTCUSD")[0]
    assert pos.status == "OPEN", "fixture failed to open a position"
    return b, pos


# =====================================================================
# THE DELIVERY CHAIN -- the actual defect
# =====================================================================

class TestTheDeliveryChainIsConsistent:
    """The chain must be all-present or all-absent -- never half.

    THE ORIGINAL BUG WAS A HALF-SHIPPED CHAIN. b63e365 landed the code and
    called itself "Apply 1 of 2"; apply 2 never came, so DELTABOT_MAX_HOLD
    existed in the settings table and in neither delivery link, and the time
    stop was silently inert on every host.

    So the invariant is CONSISTENCY, not presence. Asserting presence would be
    wrong today: the plumbing is deliberately staged out of the tree while the
    v3 experiment runs, because run.sh and user_data.sh.tftpl both feed
    `user_data`, and `user_data_replace_on_change = true` means shipping them
    replaces the host and ends the run. They return with the frozen-arm deploy,
    when v3 restarts anyway.

    Written this way the test is true before that deploy and after it, and
    fails loudly the moment someone ships one half again -- in either
    direction, which presence-checking could not do.
    """

    VAR = "DELTABOT_MAX_HOLD"

    def _links(self):
        return {
            "user_data template": (
                ROOT / "infra/terraform/templates/user_data.sh.tftpl").read_text(),
            "run.sh -e list": (ROOT / "deploy/aws/run.sh").read_text(),
            "terraform variable": (
                ROOT / "infra/terraform/variables.tf").read_text(),
        }

    def test_settings_reads_the_env_var(self):
        """The app half is unconditional -- it ships regardless of plumbing."""
        src = (ROOT / "app/config/settings.py").read_text()
        assert '("DELTABOT_MAX_HOLD", "max_hold_seconds", int)' in src

    def test_no_link_carries_the_variable_without_the_others(self):
        present = {name: (self.VAR in text or "max_hold_seconds" in text)
                   for name, text in self._links().items()}
        assert len(set(present.values())) == 1, (
            f"the delivery chain is HALF-SHIPPED, which is the exact defect "
            f"this file exists to catch: {present}")

    def test_when_shipped_the_fallback_preserves_the_configured_value(self):
        """`:-0` and not `:-86400`.

        A host whose /opt/deltabt/env predates the variable must keep the
        behaviour it has. Acquiring a time stop by accident moves the risk
        hash, and the bot would then refuse to bind to its own experiment.
        """
        run_sh = (ROOT / "deploy/aws/run.sh").read_text()
        if self.VAR not in run_sh:
            pytest.skip("plumbing staged out; nothing to constrain yet")
        assert f"{self.VAR}:-0" in run_sh
        assert f"{self.VAR}:-86400" not in run_sh


class TestEnablingItIsANewExperiment:
    """max_hold_seconds is in RiskConfig, so it is in the risk hash."""

    def test_a_non_zero_value_moves_the_risk_hash(self):
        from dataclasses import replace

        from app.config.settings import RiskConfig
        from app.forwardtest.identity import _hash

        base = RiskConfig()
        assert base.max_hold_seconds == 0, "the configured value is still 0"
        assert _hash(base) != _hash(replace(base, max_hold_seconds=DAY)), (
            "if these ever match, enabling the time stop would slip into a "
            "running experiment without the drift check noticing")

    def test_the_strategy_hash_is_untouched_by_it(self):
        """It is a risk policy, not a signal rule. V3 must stay identifiable."""
        from app.config.variants import ALL
        assert ALL["V3"].config_hash == "11461f2a11a96f8a"
        assert "max_hold" not in ALL["V3"].to_dict()


# =====================================================================
# END TO END THROUGH THE PAPER BROKER
# =====================================================================

class TestTheTimeExitActuallyCloses:

    def test_a_position_below_the_limit_stays_open(self):
        b, pos = opened()
        b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + DAY - 60))
        assert pos.status == "OPEN"
        assert pos.exit_reason is None

    def test_reaching_the_limit_emits_time_exit(self):
        b, pos = opened()
        b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + DAY))
        assert pos.status == "CLOSED"
        assert pos.exit_reason == ExitReason.TIME_EXIT.value

    def test_it_reaches_paper_execution_as_a_real_order_and_fill(self):
        """Not a bookkeeping flag: the close creates an exit order and a fill,
        so the lifecycle is complete and the fee is attributable."""
        b, pos = opened()
        evs = b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + DAY))
        kinds = [e.kind for e in evs]
        assert "EXIT_ORDER_CREATED" in kinds
        assert "FILL" in kinds
        assert "POSITION_CLOSED" in kinds
        exits = b.fills_for_position(pos.position_uid, "exit")
        assert len(exits) == 1
        assert exits[0].liquidity == "taker", "a time exit leaves at market"

    def test_the_event_journal_records_the_reason(self):
        b, pos = opened()
        evs = b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + DAY))
        closed = [e for e in evs if e.kind == "POSITION_CLOSED"]
        assert len(closed) == 1
        assert closed[0].payload["reason"] == "TIME_EXIT"

    def test_pnl_and_fees_are_recorded(self):
        b, pos = opened()
        b.process_market_event(tick(63_500.0, ts=BAR_CLOSE + DAY))
        assert pos.realized_pnl is not None
        assert pos.gross_pnl is not None
        assert pos.exit_fee > 0
        assert pos.r_multiple is not None
        # net = gross less both fees and funding; stated so a sign or ordering
        # slip in _close cannot pass unnoticed.
        assert pos.realized_pnl == pytest.approx(
            pos.gross_pnl - pos.entry_fee - pos.exit_fee - pos.funding)

    def test_it_closes_exactly_once_however_many_ticks_follow(self):
        b, pos = opened()
        for i in range(5):
            b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + DAY + i))
        assert len(b.fills_for_position(pos.position_uid, "exit")) == 1
        closes = [e for e in b.events if e.kind == "POSITION_CLOSED"]
        assert len(closes) == 1

    def test_a_stop_on_the_same_tick_wins(self):
        """Ranked last deliberately: a real exit must not be rewritten as an
        administrative one just because the position is also old."""
        b, pos = opened()
        b.process_market_event(tick(62_400.0, mark=62_400.0,
                                    ts=BAR_CLOSE + DAY))
        assert pos.exit_reason == ExitReason.STOP_LOSS.value

    def test_a_recovered_position_keeps_its_age(self):
        """A restart must not reset the clock -- the 66.9h position had to
        close on the first tick of the next run, not survive the transition."""
        b = PaperBroker(COSTS, starting_equity=10_000.0, slippage_bps=2.0,
                        max_hold_seconds=DAY)
        recovered = PaperPosition(
            position_uid="p-old", signal_key="s-old", symbol="BTCUSD", side=1,
            quantity=100, entry_price=63_000.0, stop_price=62_500.0,
            target_price=64_000.0, risk_per_unit=500.0, initial_risk=50.0,
            notional=6_300.0, equity_before=10_000.0,
            opened_at=BAR_CLOSE - int(66.9 * 3600), strategy_version="v",
            status="OPEN")
        b.positions[recovered.position_uid] = recovered
        b.process_market_event(tick(63_010.0, ts=BAR_CLOSE))
        assert recovered.status == "CLOSED"
        assert recovered.exit_reason == ExitReason.TIME_EXIT.value

    def test_zero_still_disables_it_entirely(self):
        b, pos = opened(max_hold=0)
        b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + 400 * DAY))
        assert pos.status == "OPEN"


class TestTheTimeExitPlacesNoRealOrder:

    def test_the_close_path_imports_no_network_client(self):
        """`cancel_order` and friends DO exist on PaperBroker and are meant to
        -- they cancel an in-memory paper order. The name is not the hazard;
        reaching the exchange is. So this asserts on transports, not on
        vocabulary, and the AST scan in test_no_live_trading.py remains the
        authority for the repository as a whole."""
        src = (ROOT / "app/execution/paper_broker.py").read_text()
        for forbidden in ("import requests", "import httpx", "import aiohttp",
                          "import websockets", "urllib.request", "hmac",
                          "POST /orders"):
            assert forbidden not in src, forbidden

    def test_the_exit_is_a_paper_order_held_in_memory(self):
        b, pos = opened()
        b.process_market_event(tick(63_010.0, ts=BAR_CLOSE + DAY))
        exit_uid = [e for e in b.events
                    if e.kind == "POSITION_CLOSED"][0].payload["exit_order_uid"]
        assert exit_uid in b.orders, "the exit order must be a PaperOrder"

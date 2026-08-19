"""A new experiment starts from a fresh risk ledger.

WHY. risk_state lives in strategy_state under a single key and OUTLIVES the
experiment that produced it. Registering a new experiment in a database that
already ran one inherited its equity, peak, win/loss counters, streak and
today's trade count.

Observed on the ATR arm on 2026-08-19: it started at equity 10,250.77 instead
of 10,000, with a 10/9 record it had not earned and four of the day's six-trade
budget already spent. Position size is equity * risk_per_trade, so this sized
every position 2.5% large -- not a reporting artifact.

The composite config hash cannot catch it: that compares strategy, risk,
execution and symbols. The ledger is none of those.
"""

from __future__ import annotations

import pytest

from app.persistence.repository import InMemoryRepository
from app.risk.engine import RiskState
from app.runtime.bot import STATE_KEY

pytestmark = pytest.mark.asyncio

CONTAMINATED = {
    "equity": 10_250.771777232127, "peak_equity": 10_250.771777232127,
    "day_start_equity": 10_250.77, "wins": 10, "losses": 9,
    "consecutive_losses": 0, "trades_today": 4, "realized_pnl": 250.77,
}


class TestTheLedgerIsReset:

    async def test_a_carried_ledger_is_what_the_bug_looked_like(self):
        """The shape of the defect, pinned so the fix has something to be
        measured against."""
        carried = RiskState.from_dict(CONTAMINATED)
        assert carried.equity != 10_000.0
        assert (carried.wins, carried.losses) != (0, 0)
        assert carried.trades_today != 0

    async def test_fresh_zeroes_every_carried_field(self):
        fresh = RiskState.fresh(10_000.0)
        assert fresh.equity == 10_000.0
        assert fresh.peak_equity == 10_000.0
        assert fresh.day_start_equity == 10_000.0
        for field in ("wins", "losses", "consecutive_losses", "trades_today"):
            assert getattr(fresh, field) == 0, f"{field} survived the reset"

    async def test_writing_fresh_state_overwrites_the_inherited_one(self):
        store: dict = {}
        repo = InMemoryRepository(store)
        await repo.set_state(STATE_KEY, CONTAMINATED)
        assert (await repo.get_state(STATE_KEY))["equity"] != 10_000.0

        await repo.set_state(STATE_KEY, RiskState.fresh(10_000.0).to_dict())
        got = await repo.get_state(STATE_KEY)
        assert got["equity"] == 10_000.0
        assert got["peak_equity"] == 10_000.0
        assert got["wins"] == 0 and got["losses"] == 0
        assert got["trades_today"] == 0

    async def test_position_sizing_is_what_the_contamination_actually_moved(self):
        """2.5% larger positions, which is why this is not cosmetic."""
        risk_pct = 0.005
        stop_distance = 100.0
        carried = RiskState.from_dict(CONTAMINATED).equity * risk_pct / stop_distance
        clean = RiskState.fresh(10_000.0).equity * risk_pct / stop_distance
        assert carried > clean
        assert carried / clean == pytest.approx(1.025, abs=0.001)


class TestCmdStartDoesTheReset:

    def test_cmd_start_writes_a_fresh_ledger(self):
        """Asserted on the source: cmd_start needs a live preflight and a
        database, so the behaviour is pinned where it is expressed rather than
        by standing up the whole stack."""
        import inspect

        from app import cli
        src = inspect.getsource(cli.cmd_start)
        assert "RiskState.fresh(settings.risk.starting_equity)" in src
        assert "set_state(" in src and "STATE_KEY" in src
        # It must happen only when the experiment was actually created --
        # resetting on a REFUSED start would wipe a running experiment's ledger.
        assert src.index("if created:") < src.index("RiskState.fresh")

    def test_the_key_is_imported_not_restated(self):
        from app import cli
        assert cli.STATE_KEY == "risk_state"

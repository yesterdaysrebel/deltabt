"""Every recorded row belongs to exactly one run, and a refusal records nothing.

FOUND IN PRODUCTION, 2026-08-14, while verifying the V2 deployment. Three
defects, all of the same shape: the audit trail said something the code did not
actually do.

  1. ``positions.experiment_id`` and ``positions.config_hash`` were NULL for
     every trade ever recorded. ``_to_record`` took both as parameters, both
     callers passed them, and the record simply never assigned them. The
     column's own schema comment reads "stamped so a position can never be
     silently attributed to the wrong run".

  2. ``report_rows`` filtered only ``strategy_signals`` by experiment. Orders,
     fills, positions, funding, risk events and system events were read whole,
     so the V2 experiment's status showed "2 positions opened, 3 fills,
     -$58.37" when it was four evaluations old. All of it was V1's.

  3. ``stop()`` persisted the risk state unconditionally. A startup that
     REFUSES -- configuration drift, a failed reconciliation -- returns before
     ``recover()`` runs, leaving ``self.state`` a fresh ``RiskState``. Writing
     that on the way out reset equity, peak equity, trades_today, the
     consecutive-loss counter and the drawdown baseline. The deploy at 10:22:55
     did exactly this: it refused on drift, rolled itself back, and wiped the
     run's equity from 9941.63 to 10000.0 as it exited.

The third is the serious one. The fail-closed path exists to protect the
experiment, and it was the thing damaging it -- and it does its damage on
precisely the restarts nobody is watching closely, because "it refused, no harm
done" is the expected outcome.
"""

from __future__ import annotations

import pytest

from app.config.settings import RiskConfig
from app.config.strategy import FROZEN
from app.forwardtest.identity import build_identity
from app.persistence.models import (
    PositionRecord,
    RiskEventRecord,
    SystemEventRecord,
    utc,
)
from app.persistence.repository import InMemoryRepository
from app.risk.engine import RiskState
from app.runtime.bot import STATE_KEY
from tests.live.conftest import requires_pg
from tests.live.test_experiment_identity import EXEC, SYMS, _register
from tests.live.test_recovery import make_bot, open_a_position

pytestmark = pytest.mark.asyncio


# =====================================================================
# 1. A POSITION CARRIES THE RUN THAT OPENED IT
# =====================================================================


class TestThePositionKnowsItsExperiment:
    async def test_an_opened_position_is_stamped(self):
        store: dict = {}
        seed = make_bot(store)
        await _register(seed)
        bot = make_bot(store)
        await bot.start()
        await open_a_position(bot)

        rows = await bot.repo.load_open_positions()
        assert rows, "no position was recorded"
        assert rows[0].experiment_id == "H-WPR-1-PAPER-TEST"

    async def test_it_carries_the_composite_hash_not_the_strategy_hash(self):
        """The composite is what covers risk and execution parameters too."""
        store: dict = {}
        seed = make_bot(store)
        await _register(seed)
        bot = make_bot(store)
        await bot.start()
        await open_a_position(bot)

        row = (await bot.repo.load_open_positions())[0]
        assert row.config_hash == bot.identity.config_hash
        assert row.config_hash != FROZEN.config_hash, (
            "the composite must differ from the strategy-only hash, or it is "
            "not covering risk and execution")

    async def test_an_unbound_bot_stamps_nothing_rather_than_guessing(self):
        bot = make_bot({})
        await bot.start()
        assert bot.experiment_id is None
        await open_a_position(bot)
        assert (await bot.repo.load_open_positions())[0].experiment_id is None


# =====================================================================
# 2. A REPORT READS ONE EXPERIMENT
# =====================================================================


def _pos(uid, opened_at, experiment_id):
    return PositionRecord(
        position_uid=uid, signal_key=f"sig_{uid}", instance_uid="inst",
        symbol="BTCUSD", side=1, status="CLOSED", quantity=1,
        entry_price=100.0, stop_price=95.0, target_price=110.0,
        initial_risk=5.0, risk_per_unit=5.0, notional=100.0,
        equity_before=10_000.0, opened_at=opened_at,
        strategy_version=FROZEN.version, realized_pnl=-50.0,
        experiment_id=experiment_id)


async def _two_experiments():
    """Run A over [100, 200), run B over [200, ...). One position in each."""
    repo = InMemoryRepository({})
    await repo.connect()
    repo._s["experiments"]["A"] = {
        "experiment_id": "A", "status": "STOPPED",
        "started_at": 100, "stopped_at": 200}
    repo._s["experiments"]["B"] = {
        "experiment_id": "B", "status": "RUNNING",
        "started_at": 200, "stopped_at": None}

    await repo.open_position(_pos("pos_a", 150, "A"))
    await repo.open_position(_pos("pos_b", 250, "B"))
    await repo.record_risk_event(RiskEventRecord(
        event_id="re_a", instance_uid="inst", event_type="REJECTION",
        reason="max_open_positions", received_ts=150))
    await repo.record_risk_event(RiskEventRecord(
        event_id="re_b", instance_uid="inst", event_type="REJECTION",
        reason="cooldown after trade", received_ts=250))
    await repo.record_system_event(SystemEventRecord(
        event_id="se_a", instance_uid="inst", component="bot",
        event_type="STARTUP", received_ts=150))
    await repo.record_system_event(SystemEventRecord(
        event_id="se_b", instance_uid="inst", component="bot",
        event_type="STARTUP", received_ts=250))
    return repo


class TestTheReportReadsOneExperiment:
    async def test_positions_from_the_previous_run_are_excluded(self):
        repo = await _two_experiments()
        rows = await repo.report_rows("B")
        uids = {p["position_uid"] for p in rows["positions"]}
        assert uids == {"pos_b"}, (
            "the previous experiment's trades are in this experiment's report")

    async def test_the_previous_run_still_sees_its_own(self):
        """Scoping must not make the stopped run's record disappear."""
        repo = await _two_experiments()
        rows = await repo.report_rows("A")
        assert {p["position_uid"] for p in rows["positions"]} == {"pos_a"}

    async def test_risk_events_are_scoped(self):
        repo = await _two_experiments()
        rows = await repo.report_rows("B")
        assert {r["event_id"] for r in rows["risk_events"]} == {"re_b"}

    async def test_system_events_are_scoped(self):
        repo = await _two_experiments()
        rows = await repo.report_rows("B")
        assert {e["event_id"] for e in rows["system_events"]} == {"se_b"}

    async def test_a_position_spanning_the_boundary_belongs_to_the_opener(self):
        """Opened under A, still open when B starts. A chose the entry."""
        repo = await _two_experiments()
        await repo.open_position(_pos("pos_span", 199, "A"))
        assert "pos_span" in {
            p["position_uid"] for p in (await repo.report_rows("A"))["positions"]}
        assert "pos_span" not in {
            p["position_uid"] for p in (await repo.report_rows("B"))["positions"]}

    async def test_no_experiment_still_reads_everything(self):
        """Development and ad-hoc queries must not be narrowed."""
        repo = await _two_experiments()
        rows = await repo.report_rows(None)
        assert {p["position_uid"] for p in rows["positions"]} == {"pos_a", "pos_b"}


class TestTheClosedTradeTableIsScopedToo:
    """The daily report's closed-trade table comes from /api/trades, which
    calls load_recent_positions -- NOT report_rows. Scoping report_rows alone
    fixed the summary and left the table a reader actually reads."""

    async def test_the_previous_runs_trades_are_not_listed(self):
        repo = await _two_experiments()
        rows = await repo.load_recent_positions(50, "B")
        assert {p.position_uid for p in rows} == {"pos_b"}

    async def test_unbound_still_lists_everything(self):
        repo = await _two_experiments()
        rows = await repo.load_recent_positions(50)
        assert {p.position_uid for p in rows} == {"pos_a", "pos_b"}

    async def test_the_endpoint_passes_the_bound_experiment(self):
        """The filter is worthless if the caller never applies it."""
        import inspect

        from app.api import app as api_module
        src = inspect.getsource(api_module.create_app)
        assert "load_recent_positions(limit, bot.experiment_id)" in src, (
            "/api/trades must scope to the bound experiment")

    @requires_pg
    async def test_it_holds_in_postgres(self, pg_repo):
        await pg_repo.open_position(_pos("pos_a", 150, "A"))
        await pg_repo.open_position(_pos("pos_b", 250, "B"))
        assert {p.position_uid
                for p in await pg_repo.load_recent_positions(50, "B")} == {"pos_b"}
        assert len(await pg_repo.load_recent_positions(50)) == 2


# =====================================================================
# 2b. THE SAME RULE, AGAINST REAL SQL
# =====================================================================


@requires_pg
class TestTheScopingHoldsInPostgres:
    """The in-memory twin cannot check the SQL, and the SQL is the part that
    changed: eight queries, five bind parameters, and a different timestamp
    column per table because positions, risk_events and system_events have no
    created_at. That is exactly the shape of change that passes review and
    fails in production."""

    @staticmethod
    async def _seed(pg_repo):
        async with pg_repo._pool.acquire() as con:
            for name, lo, hi in (("A", 100, 200), ("B", 200, None)):
                await pg_repo.create_experiment(
                    build_identity(name, FROZEN, RiskConfig(), dict(EXEC), SYMS))
                await con.execute(
                    "UPDATE forward_test SET started_at=$2, stopped_at=$3, "
                    "status=$4 WHERE experiment_id=$1",
                    name, utc(lo), utc(hi) if hi else None,
                    "STOPPED" if hi else "RUNNING")

            await pg_repo.open_position(_pos("pos_a", 150, "A"))
            await pg_repo.open_position(_pos("pos_b", 250, "B"))
            for uid, when in (("re_a", 150), ("re_b", 250)):
                await pg_repo.record_risk_event(RiskEventRecord(
                    event_id=uid, instance_uid="inst", event_type="REJECTION",
                    reason="max_open_positions"))
                await con.execute(
                    "UPDATE risk_events SET occurred_at=$2 WHERE event_id=$1",
                    uid, utc(when))
            for uid, when in (("se_a", 150), ("se_b", 250)):
                await pg_repo.record_system_event(SystemEventRecord(
                    event_id=uid, instance_uid="inst", component="bot",
                    event_type="STARTUP"))
                await con.execute(
                    "UPDATE system_events SET occurred_at=$2 WHERE event_id=$1",
                    uid, utc(when))

    async def test_each_run_reports_only_its_own(self, pg_repo):
        await self._seed(pg_repo)
        a = await pg_repo.report_rows("A")
        b = await pg_repo.report_rows("B")
        assert {p["position_uid"] for p in a["positions"]} == {"pos_a"}
        assert {p["position_uid"] for p in b["positions"]} == {"pos_b"}
        assert {r["event_id"] for r in b["risk_events"]} == {"re_b"}
        assert {e["event_id"] for e in b["system_events"]} == {"se_b"}

    async def test_the_day_window_still_narrows_within_a_run(self, pg_repo):
        """The two windows are ANDed, not one replacing the other."""
        await self._seed(pg_repo)
        assert (await pg_repo.report_rows("B", 240, 260))["positions"]
        assert not (await pg_repo.report_rows("B", 260, 280))["positions"]

    async def test_every_key_is_present_and_queryable(self, pg_repo):
        """A typo in any of the eight statements would raise, not return []."""
        await self._seed(pg_repo)
        rows = await pg_repo.report_rows("B")
        for key in ("signals", "orders", "fills", "positions", "funding",
                    "risk_events", "system_events", "quarantined"):
            assert key in rows and isinstance(rows[key], list)


# =====================================================================
# 3. A REFUSING STARTUP LEAVES THE DATABASE ALONE
# =====================================================================


class TestARefusalDoesNotWipeRiskState:
    async def test_a_drift_refusal_preserves_the_persisted_state(self):
        """The exact production incident, in miniature."""
        store: dict = {}
        seed = make_bot(store)
        await _register(seed, risk_per_trade=0.0025)

        # The run so far: equity down, a losing trade behind it.
        repo = InMemoryRepository(store)
        await repo.connect()
        real = RiskState(equity=9941.63, peak_equity=10_000.0, day="2026-08-14",
                         day_start_equity=10_000.0, daily_pnl=-58.37,
                         trades_today=2, consecutive_losses=1, losses=1,
                         realized_pnl=-58.37)
        await repo.set_state(STATE_KEY, real.to_dict())

        bot = make_bot(store)                       # runs at 0.005 -> drift
        assert await bot.start() is False
        await bot.stop()

        after = RiskState.from_dict(await repo.get_state(STATE_KEY))
        assert after.equity == pytest.approx(9941.63), (
            "the refusing bot overwrote the run's risk state with a fresh one")
        assert after.trades_today == 2
        assert after.consecutive_losses == 1
        assert after.day == "2026-08-14"

    async def test_the_drawdown_baseline_survives_too(self):
        """peak_equity is what the 10% halt is measured against."""
        store: dict = {}
        seed = make_bot(store)
        await _register(seed, minimum_rr=3.0)
        repo = InMemoryRepository(store)
        await repo.connect()
        await repo.set_state(STATE_KEY, RiskState(
            equity=9_100.0, peak_equity=10_400.0).to_dict())

        bot = make_bot(store)
        await bot.start()
        await bot.stop()

        after = RiskState.from_dict(await repo.get_state(STATE_KEY))
        assert after.peak_equity == pytest.approx(10_400.0)
        assert after.drawdown_pct == pytest.approx(0.125, abs=1e-6)

    async def test_a_successful_run_still_saves_on_shutdown(self):
        """The negative control: the guard must not disable persistence."""
        store: dict = {}
        seed = make_bot(store)
        await _register(seed)
        bot = make_bot(store)
        await bot.start()
        bot.state.equity = 9_800.0
        bot.state.trades_today = 3
        await bot.stop()

        after = RiskState.from_dict(await bot.repo.get_state(STATE_KEY))
        assert after.equity == pytest.approx(9_800.0)
        assert after.trades_today == 3

    async def test_an_unbound_bot_saves_because_it_did_recover(self):
        """No experiment is a legitimate running state, not a refusal."""
        bot = make_bot({})
        assert await bot.start() is True
        bot.state.equity = 9_500.0
        await bot.stop()
        assert RiskState.from_dict(
            await bot.repo.get_state(STATE_KEY)).equity == pytest.approx(9_500.0)

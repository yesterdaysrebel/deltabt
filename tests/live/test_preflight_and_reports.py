"""F6 -- the preflight gate.  F7 -- forward-test reporting.

AUDIT FINDINGS. There was no gate at all: `python -m app` started the bot
unconditionally, so a 30-day run could begin with a stale schema, an unknown
commit, or a symbol whose warm-up never completed -- and the damage would only
be visible weeks later. And there was no report, so the run produced a database
nobody would read.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.config.settings import RiskConfig, Settings
from app.config.strategy import FROZEN
from app.clock import MarketClock
from app.forwardtest.preflight import (
    PreflightReport,
    Verdict,
    add_safety,
    run_preflight,
)
from app.reports.builder import (
    MIN_TRADES_FOR_RATIOS,
    build_report,
    pnl_breakdown,
)
from tests.live.conftest import requires_pg
from tests.live.test_fill_association import _close_at, _open
from tests.live.test_recovery import COSTS, make_bot

pytestmark = pytest.mark.asyncio
MKT = 1_600_000_000
S16 = 1_600_012_800


# =====================================================================
# F6 -- PREFLIGHT
# =====================================================================


async def _run(repo=None, **over):
    settings = over.pop("settings", Settings(symbols=("BTCUSD", "ETHUSD",
                                                      "SOLUSD", "XRPUSD")))
    clock = over.pop("clock", MarketClock(MKT))
    return await run_preflight(settings, FROZEN, repo=repo, costs=COSTS,
                               clock=clock, dsn=over.pop("dsn", None),
                               check_feed=False, **over)


class TestPreflightMechanics:
    async def test_it_reports_every_problem_not_just_the_first(self):
        """An operator fixing four things wants to see four things."""
        r = await _run(repo=None, clock=MarketClock())
        assert len(r.failures) > 1

    async def test_a_crashed_check_is_itself_a_failure(self):
        class Exploding:
            async def is_writable(self):
                raise RuntimeError("boom")
            async def set_state(self, *a): raise RuntimeError("boom")
            async def get_state(self, *a): raise RuntimeError("boom")
            async def active_experiment(self): raise RuntimeError("boom")
        r = await _run(repo=Exploding())
        assert not r.ok
        assert any("raised RuntimeError" in c.detail for c in r.failures)

    async def test_the_rendering_names_the_blocking_checks(self):
        r = await _run(repo=None, clock=MarketClock())
        out = r.render()
        assert "PREFLIGHT FAILED" in out
        assert "must NOT be started" in out
        for c in r.failures:
            assert c.name in out


class TestPreflightChecks:
    async def test_the_frozen_strategy_passes(self, mem_repo):
        r = await _run(repo=mem_repo)
        by = {c.name: c for c in r.checks}
        assert by["strategy config valid"].verdict is Verdict.PASS
        assert "d7837e445bc74781" in by["config hash computed"].detail

    async def test_an_invalid_risk_config_blocks(self, mem_repo):
        bad = Settings(risk=RiskConfig.__new__(RiskConfig))
        object.__setattr__(bad.risk, "risk_per_trade", 0.9)
        for f, v in RiskConfig().__dict__.items():
            if f != "risk_per_trade":
                object.__setattr__(bad.risk, f, v)
        r = await _run(repo=mem_repo, settings=bad)
        assert any(c.name == "risk config valid" and c.blocking for c in r.checks)

    async def test_an_uninitialised_clock_blocks(self, mem_repo):
        r = await _run(repo=mem_repo, clock=MarketClock())
        c = [x for x in r.checks if x.name == "market clock initialised"][0]
        assert c.blocking and "exchange time is unknown" in c.detail

    async def test_a_wrong_sized_universe_blocks(self, mem_repo):
        r = await _run(repo=mem_repo, settings=Settings(symbols=("BTCUSD",)))
        assert any(c.name == "four symbols configured" and c.blocking
                   for c in r.checks)

    async def test_an_unwritable_database_blocks(self, mem_repo):
        mem_repo.writable = False
        r = await _run(repo=mem_repo)
        assert any(c.name == "database writable" and c.blocking for c in r.checks)

    async def test_persistence_round_trip_is_verified(self, mem_repo):
        r = await _run(repo=mem_repo)
        c = [x for x in r.checks if x.name == "event persistence working"][0]
        assert c.verdict is Verdict.PASS and "round trip" in c.detail

    async def test_a_missing_dsn_blocks_single_instance_verification(self, mem_repo):
        r = await _run(repo=mem_repo)
        assert any(c.name == "advisory lock available" and c.blocking
                   for c in r.checks)

    async def test_an_unknown_git_sha_blocks(self, mem_repo, monkeypatch):
        """A result that cannot be tied to code is not reproducible."""
        import app.forwardtest.identity as ident
        monkeypatch.setattr(ident, "git_sha", lambda: ("unknown", True))
        import app.forwardtest.preflight as pf
        monkeypatch.setattr(pf, "git_sha", lambda: ("unknown", True))
        r = await _run(repo=mem_repo)
        c = [x for x in r.checks if x.name == "git SHA recorded"][0]
        assert c.blocking and "not reproducible" in c.detail

    async def test_a_dirty_tree_blocks(self, mem_repo, monkeypatch):
        import app.forwardtest.preflight as pf
        monkeypatch.setattr(pf, "git_sha", lambda: ("abc123", True))
        r = await _run(repo=mem_repo)
        assert any(c.name == "working tree clean" and c.blocking
                   for c in r.checks)

    async def test_a_clean_tree_does_not_add_that_check(self, mem_repo, monkeypatch):
        import app.forwardtest.preflight as pf
        monkeypatch.setattr(pf, "git_sha", lambda: ("abc123", False))
        r = await _run(repo=mem_repo)
        assert not any(c.name == "working tree clean" for c in r.checks)


class TestPreflightSafetyBoundary:
    async def test_the_paper_boundary_is_reasserted_at_the_gate(self):
        """CI checks the repo; preflight checks the deployed artifact."""
        r = PreflightReport()
        add_safety(r)
        names = {c.name: c for c in r.checks}
        assert names["no live order-placement code"].verdict is Verdict.PASS
        assert names["no exchange credentials"].verdict is Verdict.PASS

    async def test_it_detects_a_planted_violation(self, tmp_path, monkeypatch):
        """A gate that cannot fail is not a gate."""
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        tripwire = root / "app" / "forwardtest" / "_tripwire.py"
        tripwire.write_text("import hmac\n"
                            "def place_order(x, api_key=None):\n    return 1\n")
        try:
            r = PreflightReport()
            add_safety(r)
            failed = {c.name for c in r.checks if c.blocking}
            assert "no live order-placement code" in failed
            assert "no exchange credentials" in failed
        finally:
            tripwire.unlink()


@requires_pg
@pytest.mark.postgres
class TestPreflightAgainstPostgres:
    async def test_a_migrated_schema_passes(self, pg_repo):
        from tests.live.conftest import TEST_DSN
        r = await run_preflight(
            Settings(symbols=("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"),
                     database_url=TEST_DSN),
            FROZEN, repo=pg_repo, costs=COSTS, clock=MarketClock(MKT),
            dsn=TEST_DSN, check_feed=False)
        by = {c.name: c for c in r.checks}
        assert by["schema current"].verdict is Verdict.PASS
        assert by["advisory lock available"].verdict is Verdict.PASS

    async def test_a_missing_table_blocks(self, pg_repo):
        async with pg_repo._pool.acquire() as con:
            await con.execute("DROP TABLE IF EXISTS funding_events CASCADE")
        r = await _run(repo=pg_repo)
        c = [x for x in r.checks if x.name == "schema current"][0]
        assert c.blocking and "funding_events" in c.detail

    async def test_a_held_advisory_lock_blocks(self, pg_repo):
        from app.persistence.lock import SingleInstanceLock
        from tests.live.conftest import TEST_DSN
        holder = SingleInstanceLock(TEST_DSN)
        await holder.acquire()
        try:
            r = await _run(repo=pg_repo, dsn=TEST_DSN)
            c = [x for x in r.checks if x.name == "advisory lock available"][0]
            assert c.blocking and "another bot instance" in c.detail
        finally:
            await holder.release()


# =====================================================================
# F7 -- REPORTING
# =====================================================================


class TestPnlArithmetic:
    async def test_gross_minus_costs_equals_net(self):
        pos = [{"status": "CLOSED", "entry_fee": 3.0, "exit_fee": 1.5,
                "realized_pnl": 90.0, "r_multiple": 1.8,
                "entry_slippage": 1.2, "exit_slippage": 0.0}]
        fund = [{"funding_amount": 0.5}]
        b = pnl_breakdown(pos, fund)
        assert b["net_pnl"] == 90.0
        assert b["fees"] == 4.5 and b["funding"] == 0.5
        assert b["gross_pnl"] == pytest.approx(95.0)

    async def test_costs_are_reported_separately_from_gross(self):
        """The research turned on gross-versus-cost; one net number hides it."""
        b = pnl_breakdown([], [])
        assert {"gross_pnl", "fees", "funding", "slippage", "net_pnl"} <= set(b)

    async def test_win_rate_and_profit_factor(self):
        pos = [{"status": "CLOSED", "realized_pnl": 100.0, "r_multiple": 2.0},
               {"status": "CLOSED", "realized_pnl": -50.0, "r_multiple": -1.0},
               {"status": "CLOSED", "realized_pnl": -50.0, "r_multiple": -1.0}]
        b = pnl_breakdown(pos, [])
        assert b["win_rate"] == pytest.approx(1 / 3)
        assert b["profit_factor"] == pytest.approx(1.0)
        assert b["expectancy_r"] == pytest.approx(0.0)

    async def test_max_drawdown_in_r(self):
        pos = [{"status": "CLOSED", "r_multiple": r, "realized_pnl": r}
               for r in (2.0, -1.0, -1.0, 1.0)]
        assert pnl_breakdown(pos, [])["max_drawdown_r"] == pytest.approx(2.0)

    async def test_open_positions_are_excluded_from_realised(self):
        pos = [{"status": "OPEN", "realized_pnl": None, "r_multiple": None}]
        assert pnl_breakdown(pos, [])["trades"] == 0

    async def test_profit_factor_is_none_without_losses(self):
        pos = [{"status": "CLOSED", "realized_pnl": 10.0, "r_multiple": 2.0}]
        assert pnl_breakdown(pos, [])["profit_factor"] is None


class TestReportContent:
    async def _bot_with_a_trade(self):
        bot = make_bot({})
        await bot.start()
        await _open(bot, ts=MKT)
        await _close_at(bot, MKT + 600, 64_500.0)
        return bot

    async def test_a_report_builds_on_an_empty_database(self, mem_repo):
        rep = await build_report(mem_repo, None)
        assert "UNBOUND" in rep.render_daily()

    async def test_it_reconstructs_the_trade(self):
        bot = await self._bot_with_a_trade()
        rep = await build_report(bot.repo, None)
        out = rep.render_final()
        assert "POSITIONS" in out and "P&L" in out
        assert rep.data["pnl"]["trades"] == 1

    async def test_ratios_are_withheld_on_a_tiny_sample(self):
        """The strategy was classified NO ECONOMIC EDGE; ratios on one trade
        would be worse than no report."""
        bot = await self._bot_with_a_trade()
        out = (await build_report(bot.repo, None)).render_final()
        assert "WITHHELD" in out
        assert "Insufficient sample size for profitability inference." in out
        assert "win rate" not in out

    async def test_ratios_appear_once_the_sample_is_large_enough(self, mem_repo):
        pos = [{"status": "CLOSED", "realized_pnl": 1.0, "r_multiple": 0.1,
                "symbol": "BTCUSD", "side": 1, "opened_at": MKT,
                "closed_at": MKT + 60, "entry_fee": 0.0, "exit_fee": 0.0}
               for _ in range(MIN_TRADES_FOR_RATIOS)]
        b = pnl_breakdown(pos, [])
        from app.reports.builder import _pnl_section
        out = _pnl_section(b, ratios=True).render()
        assert "win rate" in out and "WITHHELD" not in out

    async def test_the_three_timestamps_are_distinguished(self):
        bot = await self._bot_with_a_trade()
        out = (await build_report(bot.repo, None)).render_final()
        assert "TIMING" in out
        assert "exchange_ts" in out and "received_ts" in out

    async def test_rejections_are_broken_down_by_reason(self):
        bot = make_bot({})
        await bot.start()
        from app.strategy.explanation import Explanation, Outcome
        from app.runtime.bot import idempotency_key
        for i, reason in enumerate(["cooldown after trade: 60s elapsed",
                                    "cooldown after trade: 90s elapsed",
                                    "minimum_rr 1.40 below 2.00"]):
            e = Explanation(symbol="BTCUSD", bar_open=MKT + i * 300,
                            primary_timeframe="5m", confirmation_timeframe="1m",
                            strategy_version=FROZEN.version,
                            strategy_config_hash=FROZEN.config_hash,
                            outcome=Outcome.REJECTED, direction=1)
            e.rejection_reason = reason
            await bot._record_signal(e, idempotency_key(
                "BTCUSD", MKT + i * 300, 1, FROZEN.config_hash), MKT + i * 300)
        out = (await build_report(bot.repo, None)).render_daily()
        assert "cooldown after trade" in out and "minimum_rr" in out

    async def test_data_quality_and_reliability_are_reported(self):
        bot = await self._bot_with_a_trade()
        out = (await build_report(bot.repo, None)).render_final()
        assert "DATA QUALITY AND RELIABILITY" in out
        assert "fills quarantined" in out and "startups" in out

    async def test_a_daily_report_windows_by_utc_date(self, mem_repo):
        rep = await build_report(mem_repo, None, day="2020-09-13")
        assert rep.day == "2020-09-13"
        assert "2020-09-13" in rep.render_daily()

    async def test_the_report_is_deterministic(self):
        bot = await self._bot_with_a_trade()
        a = (await build_report(bot.repo, None)).render_final()
        b = (await build_report(bot.repo, None)).render_final()
        assert a == b


@requires_pg
@pytest.mark.postgres
async def test_a_report_builds_from_postgres(pg_repo):
    from app.forwardtest.identity import build_identity
    from tests.live.test_experiment_identity import EXEC, SYMS
    ident = build_identity("H-WPR-1-PAPER-TEST", FROZEN, RiskConfig(),
                           dict(EXEC), SYMS)
    await pg_repo.create_experiment(ident)
    rep = await build_report(pg_repo, "H-WPR-1-PAPER-TEST")
    out = rep.render_final()
    assert "H-WPR-1-PAPER-TEST" in out
    assert ident.config_hash in out
    assert "d7837e445bc74781" in out, "the frozen strategy hash must appear"
    assert "ADX 28" in out and "WPR 140" in out

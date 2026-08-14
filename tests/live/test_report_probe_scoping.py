"""The daily report's headline numbers belong to the running experiment.

FOUND 2026-08-14, after four other scoping fixes had already shipped. The
report has THREE independent data paths, and fixing two of them left the third
wrong in the section a reader looks at first:

    forward-test status / report  ->  repository.report_rows   (fixed)
    the trade tables              ->  /api/trades              (fixed)
    "What it did" + sample size   ->  deploy/aws/db_probe.py   (this file)

The probe runs inside the container via the monitor SSM document and its
queries were bare counts over the whole database. The 11:12 report printed
"Evaluations in the last 24h: 735" with "NO_SETUP 701 / REJECTED 32 /
APPROVED 2" under experiment H-WPR-1-PAPER-AWS-V2-20260814-2, which was six
minutes old and had evaluated nothing. Every number belonged to the run before
it.

``closed_trades_total`` is the one that mattered. It drives MIN_CLOSED_TRADES
in scripts/daily_report.py, so left unscoped it accumulates across experiments
until the report stops printing INSUFFICIENT SAMPLE and starts publishing
performance ratios computed from a different strategy's trades -- under the
current strategy's name.
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib

import pytest

from tests.live.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, requires_pg]

_PROBE = pathlib.Path(__file__).resolve().parents[2] / "deploy/aws/db_probe.py"

#: asyncpg binds intervals as timedelta, not as a string.
HOURS5 = datetime.timedelta(hours=5)
MIN30 = datetime.timedelta(minutes=30)


def _load():
    """Import the probe by path. It is not in a package: it is base64'd into
    the SSM document and piped into `python -` on the host."""
    spec = importlib.util.spec_from_file_location("db_probe", _PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _seed(con):
    """A finished run A, then a running run B that has done almost nothing."""
    await con.execute(
        "INSERT INTO forward_test (experiment_id, status, config_hash, "
        "strategy_hash, risk_hash, execution_hash, git_sha, app_version, "
        "strategy_version, symbols, snapshot, started_at, stopped_at) VALUES "
        "('A','STOPPED','c','s','r','e','g','v','sv','{BTCUSD}','{}'::jsonb,"
        " now() - interval '6 hours', now() - interval '1 hour'),"
        "('B','RUNNING','c','s','r','e','g','v','sv','{BTCUSD}','{}'::jsonb,"
        " now() - interval '1 hour', null)")

    async def signal(key, exp, outcome, reason, ago):
        await con.execute(
            "INSERT INTO strategy_signals (idempotency_key, instance_uid, symbol,"
            " bar_open, primary_timeframe, confirmation_timeframe, outcome,"
            " strategy_version, strategy_config_hash, experiment_id,"
            " conditions_passed, conditions_failed, indicators, rejection_reason)"
            " VALUES ($1,'i','BTCUSD', now() - $5::interval, '5m','1m',$3,'sv','s',"
            " $2, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, $4)",
            key, exp, outcome, reason, ago)

    # Run A: plenty of activity, all inside the last 24h.
    for i in range(5):
        await signal(f"a{i}", "A", "NO_SETUP", None, HOURS5)
    await signal("a_rej", "A", "REJECTED", "max_open_positions 1 reached", HOURS5)
    # Run B: one evaluation.
    await signal("b0", "B", "NO_SETUP", None, MIN30)

    async def position(uid, ago, status="CLOSED"):
        await con.execute(
            "INSERT INTO positions (position_uid, signal_key, instance_uid, symbol,"
            " side, status, quantity, entry_price, stop_price, target_price,"
            " initial_risk, risk_per_unit, notional, equity_before, opened_at,"
            " strategy_version) VALUES ($1,$1,'i','BTCUSD',1,$3,1,"
            " 100,95,110,5,5,100,10000, now() - $2::interval, 'sv')",
            uid, ago, status)

    await position("pos_a", HOURS5)          # run A's trade
    await position("pos_b", MIN30)       # run B's trade


class TestTheProbeScopesToTheRunningExperiment:
    async def test_evaluations_exclude_the_previous_run(self, pg_repo):
        mod = _load()
        async with pg_repo._pool.acquire() as con:
            await _seed(con)
            out = await mod.collect(con)
        assert out["scoped_to"] == "B"
        assert out["evaluations_24h"] == 1, (
            "the previous run's evaluations are being counted as this run's")

    async def test_the_outcome_table_excludes_them_too(self, pg_repo):
        mod = _load()
        async with pg_repo._pool.acquire() as con:
            await _seed(con)
            out = await mod.collect(con)
        assert out["outcomes_24h"] == {"NO_SETUP": 1}

    async def test_the_rejection_breakdown_is_this_runs(self, pg_repo):
        """Run A's max_open_positions rejection must not be attributed to B."""
        mod = _load()
        async with pg_repo._pool.acquire() as con:
            await _seed(con)
            out = await mod.collect(con)
        assert out["rejections_24h"] == {}

    async def test_the_sample_gate_counts_only_this_runs_trades(self, pg_repo):
        """The one that would eventually publish another run's performance."""
        mod = _load()
        async with pg_repo._pool.acquire() as con:
            await _seed(con)
            out = await mod.collect(con)
        assert out["closed_trades_total"] == 1, (
            "MIN_CLOSED_TRADES would be satisfied by trades this experiment "
            "never took")

    async def test_all_time_totals_are_still_reported_separately(self, pg_repo):
        """The persistence section needs them; only the per-run figures moved."""
        mod = _load()
        async with pg_repo._pool.acquire() as con:
            await _seed(con)
            out = await mod.collect(con)
        assert out["positions"] == 2
        assert out["strategy_signals"] == 7

    async def test_with_no_experiment_running_it_reads_everything(self, pg_repo):
        """An unbound bot has no run to be wrong about, and a report that
        showed nothing would hide a live problem."""
        mod = _load()
        async with pg_repo._pool.acquire() as con:
            await _seed(con)
            await con.execute("UPDATE forward_test SET status='STOPPED'")
            out = await mod.collect(con)
        assert out["scoped_to"] is None
        assert out["evaluations_24h"] == 7
        assert out["closed_trades_total"] == 2

    async def test_the_probe_only_reads(self, pg_repo):
        """It runs inside the container against the live experiment database."""
        source = _PROBE.read_text().lower()
        for verb in ("insert", "update", "delete", "drop", "truncate", "alter"):
            assert f" {verb} " not in source, f"db_probe.py contains {verb}"

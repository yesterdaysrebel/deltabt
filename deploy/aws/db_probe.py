"""Read-only counts and the rejection breakdown, for the daily report.

SELECT only. Embedded (base64) into the monitor SSM document at plan time
rather than installed on the host: changing user-data would replace the EC2
instance, and replacing the instance would end a running 30-day experiment.

The rejection breakdown is the part that matters. "No trades today" is not an
observation on its own -- the strategy rejects for specific, recorded reasons,
and a day of silence should say WHICH gate was closed rather than leaving the
reader to guess.
"""
import asyncio
import json
import os

import asyncpg

COUNTS = ("forward_test", "strategy_signals", "paper_orders", "paper_fills",
          "positions", "quarantined_fills", "funding_events", "risk_events")


async def collect(con) -> dict:
    """Everything the report reads, given an open connection.

    Split out from main() so the scoping can be tested against a real
    database. Every query here is SELECT.
    """
    out = {t: await con.fetchval(f"select count(*) from {t}") for t in COUNTS}

    # WHOSE NUMBERS ARE THESE?
    #
    # Every per-run figure below was a bare count over the whole database, and
    # the report printed the results under the RUNNING experiment's name. On
    # 2026-08-14 that produced "Evaluations in the last 24h: 735 / NO_SETUP 701
    # / REJECTED 32 / APPROVED 2" and "Orders 3, fills 3, closed trades 1" for
    # an experiment that was six minutes old and had evaluated nothing. All of
    # it belonged to the previous run.
    #
    # closed_trades_total was the dangerous one: it drives the 30-trade sample
    # gate in scripts/daily_report.py. Left alone it accumulates across runs
    # until the report stops saying INSUFFICIENT SAMPLE and starts publishing
    # performance ratios computed from a different experiment's trades.
    #
    # The window comes from forward_test.started_at rather than a fixed 24h,
    # intersected with 24h so a long-running experiment still reports a DAY.
    # Positions carry experiment_id from 094bec4 onward but are NULL for
    # everything recorded before it, so they are scoped by time like the
    # tables that have no such column -- experiments are strictly sequential
    # (unique index on status='RUNNING'), which makes time a clean partition.
    run = await con.fetchrow(
        "select experiment_id, started_at from forward_test where status='RUNNING'")
    rid = run["experiment_id"] if run else None
    since = run["started_at"] if run else None

    out["scoped_to"] = rid

    out["signals_bound"] = await con.fetchval(
        "select count(*) from strategy_signals where experiment_id is not null")
    out["signals_unbound"] = await con.fetchval(
        "select count(*) from strategy_signals where experiment_id is null")
    out["dup_signal_keys"] = await con.fetchval(
        "select count(*) from (select idempotency_key from strategy_signals "
        "group by idempotency_key having count(*)>1) x")
    out["dup_candles"] = await con.fetchval(
        "select count(*) from (select symbol,timeframe,bar_open from market_candles "
        "group by symbol,timeframe,bar_open having count(*)>1) x")

    # $1 IS NULL means "no experiment is running", and then these fall back to
    # the whole database on purpose: an unbound bot has no run to be wrong
    # about, and a report that showed nothing would hide a live problem.
    out["outcomes_24h"] = {r["outcome"]: r["n"] for r in await con.fetch(
        "select outcome, count(*) n from strategy_signals "
        "where bar_open > now() - interval '24 hours' "
        "and ($1::text is null or experiment_id = $1) "
        "group by outcome order by n desc", rid)}

    out["rejections_24h"] = {(r["rejection_reason"] or "")[:80]: r["n"]
                             for r in await con.fetch(
        "select rejection_reason, count(*) n from strategy_signals "
        "where bar_open > now() - interval '24 hours' and rejection_reason is not null "
        "and ($1::text is null or experiment_id = $1) "
        "group by rejection_reason order by n desc limit 12", rid)}

    out["evaluations_24h"] = await con.fetchval(
        "select count(*) from strategy_signals "
        "where bar_open > now() - interval '24 hours' "
        "and ($1::text is null or experiment_id = $1)", rid)

    out["closed_trades_total"] = await con.fetchval(
        "select count(*) from positions where status = 'CLOSED' "
        "and ($1::timestamptz is null or opened_at >= $1)", since) or 0
    out["orders_run"] = await con.fetchval(
        "select count(*) from paper_orders "
        "where ($1::timestamptz is null or created_at >= $1)", since)
    out["fills_run"] = await con.fetchval(
        "select count(*) from paper_fills "
        "where ($1::timestamptz is null or created_at >= $1)", since)

    # strategy_hash and risk_hash come along so the report can verify the
    # RUNNING experiment is the one that was intended, not merely that some
    # experiment is running. With two experiments live at once, "a run exists"
    # stopped being enough to identify which run this is.
    # The risk snapshot comes along so the report can check limits against
    # what the experiment ACTUALLY recorded rather than a constant. The report
    # hardcoded max_open_positions=1 and called two open positions a control
    # failure on the first day the limit was raised to six.
    out["experiments"] = [dict(r) for r in await con.fetch(
        "select experiment_id, status, started_at, planned_days, "
        "strategy_hash, risk_hash, git_sha, snapshot->'risk' as risk "
        "from forward_test")]

    return out


async def main() -> None:
    con = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        print(json.dumps(await collect(con), default=str))
    finally:
        await con.close()


# Guarded so tests can import this. The monitor document pipes the file into
# `python -`, where __name__ is "__main__", so the probe still runs there.
if __name__ == "__main__":
    asyncio.run(main())

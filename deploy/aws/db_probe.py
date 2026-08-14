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


async def main() -> None:
    con = await asyncpg.connect(os.environ["DATABASE_URL"])
    out = {t: await con.fetchval(f"select count(*) from {t}") for t in COUNTS}

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

    out["outcomes_24h"] = {r["outcome"]: r["n"] for r in await con.fetch(
        "select outcome, count(*) n from strategy_signals "
        "where bar_open > now() - interval '24 hours' group by outcome order by n desc")}

    out["rejections_24h"] = {(r["rejection_reason"] or "")[:80]: r["n"]
                             for r in await con.fetch(
        "select rejection_reason, count(*) n from strategy_signals "
        "where bar_open > now() - interval '24 hours' and rejection_reason is not null "
        "group by rejection_reason order by n desc limit 12")}

    out["evaluations_24h"] = await con.fetchval(
        "select count(*) from strategy_signals where bar_open > now() - interval '24 hours'")
    out["closed_trades_total"] = await con.fetchval(
        "select count(*) from positions where status = 'CLOSED'") or 0
    out["experiments"] = [dict(r) for r in await con.fetch(
        "select experiment_id, status, started_at, planned_days from forward_test")]

    print(json.dumps(out, default=str))
    await con.close()


asyncio.run(main())

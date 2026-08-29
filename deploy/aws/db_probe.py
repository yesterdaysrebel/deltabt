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

    # THE TWO COUNTS ABOVE ARE ALL-TIME, AND THE REPORT PRINTED THEM BESIDE
    # `scoped_to`. On 2026-08-29, three minutes into a new experiment, that
    # read "2264 bound ... scoped to ATR-5M-UN-GATED-PAPER-20260829-1" -- 2264
    # being every signal every experiment had ever recorded, while the new run
    # had six. Nothing was wrong with the number; it answered a question nobody
    # was asking at that position on the page.
    out["signals_bound_run"] = await con.fetchval(
        "select count(*) from strategy_signals where experiment_id = $1", rid)

    # WHETHER THE UNBOUND SIGNALS PREDATE THE RUN, ASKED RATHER THAN ASSUMED.
    # The note called them "pre-binding ... they predate the run", which the
    # all-time count cannot establish. An unbound signal recorded AFTER
    # started_at is a different animal entirely: it means the bot is evaluating
    # and persisting without an experiment id while a run is nominally live, so
    # the dataset is silently losing rows. That must escalate, not reassure.
    out["signals_unbound_since"] = await con.fetchval(
        "select count(*) from strategy_signals "
        "where experiment_id is null and ($1::timestamptz is null "
        "or created_at >= $1)", since)
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

    # --- what the run is actually measuring ---------------------------------
    #
    # Every column below is recorded and none of it was reported. schema.sql
    # says why planned_r and fill_rr matter: "reporting only one hides the
    # degradation the forward test exists to measure" -- and the report showed
    # neither. Cost as a fraction of R is the panel's binding constraint and
    # had to be computed by hand.
    out["economics"] = [dict(r) for r in await con.fetch(
        """select symbol, side, r_multiple, planned_r, fill_rr, notional,
                  entry_fee, exit_fee, funding, entry_slippage, exit_slippage,
                  realized_pnl, exit_reason, hold_seconds
             from positions
            where status = 'CLOSED'
              and ($1::timestamptz is null or opened_at >= $1)
            order by closed_at""", since)]

    # PER SYMBOL. AKEUSD and BEATUSD had 15 of 15 setups refused and the
    # aggregates showed nothing, because a symbol that never trades is
    # invisible in a total. Found only by querying by hand.
    out["by_symbol"] = [dict(r) for r in await con.fetch(
        """select symbol,
                  count(*) filter (where outcome <> 'NO_SETUP')      setups,
                  count(*) filter (where outcome = 'APPROVED')       approved,
                  count(*) filter (where rejection_reason is not null) rejected,
                  min(stop_distance_pct)                             min_stop_pct,
                  max(stop_distance_pct)                             max_stop_pct
             from strategy_signals
            where ($1::text is null or experiment_id = $1)
            group by symbol order by symbol""", rid)]

    # Rejections for the WHOLE RUN, not just the last 24h, and reported even
    # when orders exist -- the section was gated on "no orders", which is how
    # a symbol refused on every single setup stayed hidden.
    out["rejections_run"] = {(r["rejection_reason"] or "")[:90]: r["n"]
                             for r in await con.fetch(
        """select rejection_reason, count(*) n from strategy_signals
            where rejection_reason is not null
              and ($1::text is null or experiment_id = $1)
            group by 1 order by 2 desc limit 15""", rid)}

    # The drawdown gate is DISABLED for these runs, so the number nobody is
    # enforcing is the one that most needs reporting.
    state = await con.fetchval(
        "select value from strategy_state where key = 'risk_state'")
    if state:
        st = json.loads(state) if isinstance(state, str) else state
        out["risk_state"] = {k: st.get(k) for k in
                             ("equity", "peak_equity", "day_start_equity",
                              "daily_pnl", "trades_today", "consecutive_losses",
                              "wins", "losses", "realized_pnl")}

    # Nothing but stop or target closes a position -- TIME_EXIT is declared and
    # never emitted -- so a wide-stop setup can sit indefinitely. V3 makes that
    # materially likelier.
    out["oldest_open_seconds"] = await con.fetchval(
        "select extract(epoch from (now() - min(opened_at)))::bigint "
        "from positions where status in ('OPENING','OPEN','SUSPENDED','CLOSING')")

    # Is BANKUSD's shortened halt threshold actually suppressing anything, and
    # how much of each symbol's price history is forward-filled?
    out["bar_quality"] = [dict(r) for r in await con.fetch(
        """select symbol, count(*) bars,
                  count(*) filter (where volume = 0 and open = close
                                     and high = low and open = high) synthetic
             from market_candles
            where timeframe = '1m' and bar_open > now() - interval '24 hours'
            group by symbol order by symbol""")]

    out["halt_events_24h"] = await con.fetchval(
        "select count(*) from system_events where component = 'halt' "
        "and received_ts > now() - interval '24 hours'") or 0

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

"""Funding-collection PAPER account (owner 2026-11-11: "compare them with ladder" -> "approved").

Every third UTC day at 00:20 it rebalances, on paper, a market-neutral book of Delta perpetuals:
SHORT the fifth with the highest funding over the last three days (shorts are paid positive funding) and
LONG the fifth with the most negative (longs are paid negative funding), equal weight, half the equity on
each side. Coins held in both the old and the new book are kept untouched; the rest are closed and opened.
The rule is the backtest's "funding collection, every 3 days, fifths" (scripts/backtest_survivors.py,
out/compare_ladder_funding.txt: $1,500 -> $4,910 over 2025-02 .. 2026-10, worst drop 23%). It was chosen as the
best of several variants on that data, so this paper record is its first clean evidence.

What the backtest only assumed is MEASURED here:
  fills    each position opens and closes at the real best ask (long) / bid (short) from /v2/tickers,
           plus Delta's taker fee as measured on the real account (0.0531% a side incl. GST).
  funding  every settlement while a position is open, from Delta's FUNDING history at its own interval.
  size     whole contracts (a coin too dear for one contract at its share is skipped).
Deviations, stated: the liquidity floor uses the runner's 40-day daily cache (30-day mean turnover >= $250k,
>= 35 days of history; the backtest asked 60 days).

Runs inside the gainer-fade runner's loop (live/fade.py) as a BACKGROUND task with its own Delta client and
database connection, so it can never hold up the real account. Tables `funding_positions`, `funding_periods`.
NO ORDER IS EVER SENT: public GET endpoints only, nothing here holds keys.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

DAY, MINUTE, HOUR = 86400, 60, 3600
START_EQUITY = 1500.0
PERIOD_DAYS = 3                                      # rebalance when (day // DAY) % PERIOD_DAYS == 0
REBALANCE_AFTER = 20 * MINUTE                        # after the fade entries (00:01:30) and the real account
GIVE_UP = 3 * HOUR                                   # a rebalance not done by 03:00 UTC is recorded as missed
FRACTION, MIN_K, MIN_COINS = 0.2, 3, 20
MIN_TURNOVER_30D, MIN_HISTORY_DAYS = 250_000.0, 35
SIGNAL_DAYS, SIGNAL_MIN_DAYS = 3, 2
FEE = 0.000531                                       # measured on the real account (CONTEXT.md, 2026-11-09)
TASK_TIMEOUT = 30 * MINUTE


# ============================================================ pure

def is_rebalance_day(day: int) -> bool:
    return (int(day) // DAY) % PERIOD_DAYS == 0


def turnover_30d(d: pd.DataFrame, cv: float, day: int) -> tuple[float, int]:
    """(mean daily USD turnover over the 30 days before `day`, days of history) from 1d candles."""
    d = d[d.time < day].sort_values("time")
    if d.empty:
        return 0.0, 0
    t = d.volume * cv * (d.high + d.low + d.close) / 3
    return float(t.tail(30).mean()), int(len(d))


def daily_funding(rows: list[dict], interval: int, days: list[int]) -> dict[int, float]:
    """Funding per UTC day (sum of the settlements stamped in it, as a fraction) from Delta's FUNDING 1h rows
    (close = the rate in % per settlement). A day with no row at any stamp is left out."""
    if not rows:
        return {}
    ft = np.array([int(x["time"]) for x in rows])
    fv = np.array([float(x["close"]) for x in rows])
    order = np.argsort(ft)
    ft, fv = ft[order], fv[order]
    out = {}
    for d in days:
        stamps = range(d, d + DAY, interval)
        vals = []
        for s in stamps:
            i = np.searchsorted(ft, s, side="right") - 1
            if i >= 0 and s - ft[i] <= 2 * HOUR and np.isfinite(fv[i]):
                vals.append(fv[i] / 100.0)
        if vals:
            out[d] = float(sum(vals))
    return out


def signal(by_day: dict[int, float]) -> float | None:
    """Mean daily funding over the signal days (the backtest's 3-day mean, min 2 days)."""
    return float(np.mean(list(by_day.values()))) if len(by_day) >= SIGNAL_MIN_DAYS else None


def choose(sig: dict[str, float]) -> tuple[list[str], list[str]]:
    """(longs, shorts): LONG the lowest-funding fifth, SHORT the highest-funding fifth."""
    if len(sig) < MIN_COINS:
        return [], []
    o = sorted(sig, key=lambda s: (sig[s], s))
    k = max(int(len(o) * FRACTION), MIN_K)
    return o[:k], o[-k:]


def contracts_for(notional: float, price: float, cv: float) -> int:
    return int(math.floor(notional / (price * cv) + 1e-9)) if price > 0 and cv > 0 and notional > 0 else 0


def funding_usd(side: int, qty: float, price: float, rates: list[float]) -> float:
    """Funding received by a position (side +1 long, -1 short) of `qty` units: longs PAY a positive rate."""
    return -side * sum(rates) / 100.0 * qty * price


def settlement_rates(rows: list[dict], interval: int, start: int, end: int) -> list[float]:
    """Rates (% per settlement) of the stamps strictly after `start` and before `end` (the runner's _funding)."""
    if not rows:
        return []
    ft = np.array([int(x["time"]) for x in rows])
    fv = np.array([float(x["close"]) for x in rows])
    order = np.argsort(ft)
    ft, fv = ft[order], fv[order]
    out = []
    for s in range((start // interval + 1) * interval, end, interval):
        i = np.searchsorted(ft, s, side="right") - 1
        if i >= 0 and np.isfinite(fv[i]):
            out.append(float(fv[i]))
    return out


def close_pnl(side: int, qty: float, entry: float, exit_: float, funding: float) -> dict:
    gross = side * (exit_ - entry) * qty
    fees = FEE * qty * (entry + exit_)
    return dict(gross=gross, fees=fees, funding=funding, pnl=gross - fees + funding)


# ============================================================ I/O (called from FadeRunner.tick)

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_positions (
    id             BIGSERIAL PRIMARY KEY,
    opened_period  TIMESTAMPTZ NOT NULL,
    symbol         TEXT NOT NULL,
    side           INTEGER NOT NULL,          -- +1 long (negative funding), -1 short (positive funding)
    signal         DOUBLE PRECISION,          -- 3-day mean daily funding at the open (fraction per day)
    contracts      INTEGER NOT NULL,
    contract_value DOUBLE PRECISION NOT NULL,
    entry_time     TIMESTAMPTZ NOT NULL,
    entry_price    DOUBLE PRECISION NOT NULL, -- the best ask (long) / bid (short)
    entry_mark     DOUBLE PRECISION,
    status         TEXT NOT NULL,             -- OPEN | CLOSED
    exit_time      TIMESTAMPTZ,
    exit_price     DOUBLE PRECISION,
    exit_mark      DOUBLE PRECISION,
    settlements    INTEGER,
    funding        DOUBLE PRECISION,          -- USD received (+) or paid (-)
    gross          DOUBLE PRECISION,
    fees           DOUBLE PRECISION,
    pnl            DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS funding_positions_open ON funding_positions (status) WHERE status = 'OPEN';
CREATE TABLE IF NOT EXISTS funding_periods (
    period   TIMESTAMPTZ PRIMARY KEY,
    status   TEXT NOT NULL,                   -- ENTERED | SKIPPED | MISSED
    equity   DOUBLE PRECISION,                -- marked to market at the rebalance, before its trades
    detail   JSONB,
    at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _ts(t: float):
    return pd.Timestamp(int(t), unit="s", tz="UTC").to_pydatetime()


async def ensure_schema(con) -> None:
    await con.execute(SCHEMA)


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


class _Rebalance:
    """One rebalance, run off the runner's loop. Blocking venue reads go through asyncio.to_thread."""

    def __init__(self, runner, day: int, now: float):
        from deltabt.data.client import DeltaClient
        self.runner, self.day, self.now = runner, day, now
        self.client = DeltaClient(per_second=2.0)        # its own rate limit: never slows the fade runner's client

    def interval(self, sym: str) -> int:
        try:
            return int(self.runner.catalog.get(sym)["funding_interval_seconds"])
        except Exception:                                # noqa: BLE001
            return 8 * HOUR

    def funding_rows(self, sym: str, start: int, end: int) -> list[dict]:
        return self.client.candles("FUNDING:" + sym, "1h", start - 2 * HOUR, end, drop_forming=False) or []

    def quotes(self) -> dict[str, dict]:
        out = {}
        for r in self.client.tickers("perpetual_futures"):
            if r.get("top_tag") != "crypto" or r.get("product_trading_status", "operational") != "operational":
                continue
            q = r.get("quotes") or {}
            out[r["symbol"]] = dict(bid=_f(q.get("best_bid")), ask=_f(q.get("best_ask")), mark=_f(r.get("mark_price")),
                                    cv=_f(r.get("contract_value")))
        return out

    def dailies(self, syms) -> dict[str, pd.DataFrame]:
        cache = getattr(self.runner, "_day_cache", None)
        if cache and cache[0] == self.day:
            return {s: d for s, d in cache[1].items() if s in syms}
        out = {}
        for s in syms:                                   # sequential: the client rate-limits itself
            rows = self.client.candles(s, "1d", self.day - 40 * DAY, self.day - DAY, drop_forming=False)
            if rows:
                d = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"]).astype(float)
                d["time"] = d.time.astype("int64")
                out[s] = d
        return out

    async def run(self) -> None:
        q = await asyncio.to_thread(self.quotes)
        daily = await asyncio.to_thread(self.dailies, set(q))
        liquid = []
        for s, d in daily.items():
            cv = q[s]["cv"]
            if not cv:
                continue
            t30, n = turnover_30d(d, cv, self.day)
            if t30 >= MIN_TURNOVER_30D and n >= MIN_HISTORY_DAYS:
                liquid.append(s)
        days = [self.day - k * DAY for k in range(SIGNAL_DAYS, 0, -1)]
        sig = {}
        for s in sorted(liquid):
            rows = await asyncio.to_thread(self.funding_rows, s, days[0], self.day)
            v = signal(daily_funding(rows, self.interval(s), days))
            if v is not None:
                sig[s] = v
        longs, shorts = choose(sig)
        pool = self.runner._pool
        async with pool.acquire() as con:
            open_rows = await con.fetch("SELECT * FROM funding_positions WHERE status = 'OPEN'")
            closed = float(await con.fetchval("SELECT COALESCE(sum(pnl), 0) FROM funding_positions WHERE status = 'CLOSED'"))
            # mark to market: price at the mark + funding settled so far
            mtm, accrued = 0.0, {}
            for r in open_rows:
                mark = (q.get(r["symbol"]) or {}).get("mark") or r["entry_price"]
                qty = r["contracts"] * r["contract_value"]
                rows = await asyncio.to_thread(self.funding_rows, r["symbol"], int(r["entry_time"].timestamp()), int(self.now))
                rates = settlement_rates(rows, self.interval(r["symbol"]), int(r["entry_time"].timestamp()), int(self.now))
                accrued[r["id"]] = (rates, mark)
                mtm += r["side"] * (mark - r["entry_price"]) * qty + funding_usd(r["side"], qty, r["entry_price"], rates)
            equity = START_EQUITY + closed + mtm
            detail = dict(coins=len(sig), liquid=len(liquid), longs=longs, shorts=shorts,
                          signal_long=None if not longs else float(np.mean([sig[s] for s in longs])),
                          signal_short=None if not shorts else float(np.mean([sig[s] for s in shorts])))
            if not longs:
                await con.execute("INSERT INTO funding_periods (period, status, equity, detail) VALUES ($1,'SKIPPED',$2,$3) "
                                  "ON CONFLICT DO NOTHING", _ts(self.day), equity,
                                  json.dumps(dict(detail, why=f"only {len(sig)} coins with a funding signal")))
                return
            want = {s: +1 for s in longs} | {s: -1 for s in shorts}
            k = len(longs)
            per_side = equity / 2.0
            async with con.transaction():
                if await con.fetchval("SELECT 1 FROM funding_periods WHERE period = $1", _ts(self.day)):
                    return                               # another pass already did this period
                held = {}
                for r in open_rows:
                    s, side = r["symbol"], r["side"]
                    if want.get(s) == side:
                        held[s] = side                   # in both books: kept untouched
                        continue
                    qq = q.get(s) or {}
                    px = qq.get("bid") if side > 0 else qq.get("ask")
                    px = px or qq.get("mark")
                    if not px:
                        continue                         # no quote at all: close it next period
                    rates, mark = accrued.get(r["id"], ([], px))
                    qty = r["contracts"] * r["contract_value"]
                    m = close_pnl(side, qty, r["entry_price"], px, funding_usd(side, qty, r["entry_price"], rates))
                    await con.execute(
                        "UPDATE funding_positions SET status='CLOSED', exit_time=$2, exit_price=$3, exit_mark=$4, "
                        "settlements=$5, funding=$6, gross=$7, fees=$8, pnl=$9 WHERE id=$1",
                        r["id"], _ts(self.now), px, qq.get("mark"), len(rates), m["funding"], m["gross"], m["fees"], m["pnl"])
                opened, skipped = [], []
                for s, side in want.items():
                    if s in held:
                        continue
                    qq = q.get(s) or {}
                    px = qq.get("ask") if side > 0 else qq.get("bid")
                    n = contracts_for(per_side / k, px or 0.0, qq.get("cv") or 0.0)
                    if not px or n < 1:
                        skipped.append(s)
                        continue
                    await con.execute(
                        "INSERT INTO funding_positions (opened_period, symbol, side, signal, contracts, contract_value, "
                        "entry_time, entry_price, entry_mark, status) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'OPEN')",
                        _ts(self.day), s, side, sig.get(s), n, qq["cv"], _ts(self.now), px, qq.get("mark"))
                    opened.append(s)
                await con.execute(
                    "INSERT INTO funding_periods (period, status, equity, detail) VALUES ($1,'ENTERED',$2,$3)",
                    _ts(self.day), equity, json.dumps(dict(detail, kept=sorted(held), opened=opened, skipped=skipped)))
        log.info("funding paper rebalanced %s: equity %.2f, long %s, short %s", pd.Timestamp(self.day, unit="s").date(),
                 equity, longs, shorts)


async def step(runner, con, now: float | None = None) -> None:
    """One pass; never raises into the fade runner's loop. Starts the rebalance as a background task."""
    now = time.time() if now is None else now
    try:
        day = int(now) // DAY * DAY
        if not is_rebalance_day(day) or now < day + REBALANCE_AFTER:
            return
        if await con.fetchval("SELECT status FROM funding_periods WHERE period = $1", _ts(day)) is not None:
            return
        if now >= day + GIVE_UP:
            await con.execute("INSERT INTO funding_periods (period, status, detail) VALUES ($1,'MISSED',$2) "
                              "ON CONFLICT DO NOTHING", _ts(day), json.dumps({"at": now}))
            return
        task = getattr(runner, "_funding_task", None)
        if task is not None and not task.done():
            return
        runner._funding_task = asyncio.create_task(_run(runner, day, now))
    except Exception:                                    # noqa: BLE001
        log.exception("funding paper step failed")


async def _run(runner, day: int, now: float) -> None:
    try:
        await asyncio.wait_for(_Rebalance(runner, day, now).run(), TASK_TIMEOUT)
    except Exception:                                    # noqa: BLE001 -- the next pass retries until GIVE_UP
        log.exception("funding paper rebalance failed")

"""Gainer-fade PAPER runner (owner, 2026-10-28: "get variant A running as paper; scrap crypto and
metals bot"; "both, side by side"; "2%").

Once a UTC day it shorts the two previous-UTC-day top gainers on two simulated $250 accounts:

  A      rule A as tested (docs/gainer_fade_stop_prereg.md): stop 2 x 14-day daily ATR% above the
         entry, exit at the day's last 1m close.
  QUICK  the same, plus a take-profit 0.2 x the stop distance below the entry (the one cell of the
         win-rate scan with 70-80% wins in both years; CONTEXT.md "WIN-RATE SCAN").

Picks, ATR, stop fill, fees and sizing are the research definitions (scripts/gainer_fade_lab.py,
gainer_fade_stop_lab.py, gainer_fade_2025_lab.py). Two differences, both deliberate and recorded on
every row: the entry price is the CLOSE of the first traded minute from 00:00 UTC (within 15 min;
a coin with no trade by then is skipped), not the daily open; and only coins Delta lists as tradable now are eligible.

NO ORDER IS EVER SENT. Nothing here holds keys or imports an order path; positions live in the
`fade_positions` table only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

DAY, MINUTE, HOUR = 86400, 60, 3600
TOP_N = 2
MIN_TURNOVER = 250_000.0
ATR_DAYS = 14
K_STOP = 2.0
QUICK_TP = 0.2
RISK_PCT = 0.02
MAX_LEVERAGE = 5.0
TAKER = 0.0005
START_EQUITY = 250.0
ACCOUNTS = {"A": None, "QUICK": QUICK_TP}            # account -> take-profit fraction of the stop
ENTRY_AFTER = 90                                     # s past midnight: the 00:00 minute has closed
GIVE_UP_ENTRY = 3 * HOUR                             # a day not entered by then is recorded as missed
FIRST_TRADE_WINDOW = 15 * MINUTE                     # enter at the first traded minute within this


# ============================================================ pure logic (tested)

def readings(daily: dict[str, pd.DataFrame], cv: dict[str, float], day: int) -> pd.DataFrame:
    """Per coin: day D-1's change and turnover, and the 14-day ATR% up to D-1.

    `daily[sym]` = Delta 1d ltp candles (time = bar open, UTC). The definitions are
    gainer_fade_2025_lab.panel(): change = close/open - 1, turnover = volume x contract value x
    typical price, TR = max(h-l, |h-prev close|, |l-prev close|), ATR = simple 14-bar mean.
    """
    rows = []
    for sym, d in daily.items():
        d = d[d.time < day].sort_values("time")
        if d.empty or int(d.time.iloc[-1]) != day - DAY:
            continue
        pc = d.close.shift(1)
        tr = np.maximum.reduce([(d.high - d.low).to_numpy(), (d.high - pc).abs().to_numpy(),
                                (d.low - pc).abs().to_numpy()])
        atr = pd.Series(tr).rolling(ATR_DAYS).mean().iloc[-1]
        last = d.iloc[-1]
        rows.append(dict(symbol=sym, prev_change=last.close / last.open - 1,
                         prev_turnover=last.volume * cv[sym] * (last.high + last.low + last.close) / 3,
                         atrp=atr / last.close if np.isfinite(atr) and last.close > 0 else np.nan))
    r = pd.DataFrame(rows, columns=["symbol", "prev_change", "prev_turnover", "atrp"])
    r["eligible"] = (r.prev_change > 0) & (r.prev_turnover >= MIN_TURNOVER) & r.atrp.notna() & (r.atrp > 0)
    return r


def picks(r: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    return r[r.eligible].sort_values("prev_change", ascending=False).head(n).reset_index(drop=True)


def size(equity: float, entry: float, stop: float, cv: float) -> int:
    """Contracts for RISK_PCT of equity between entry and stop, capped at MAX_LEVERAGE x equity."""
    if not (equity > 0 and stop > entry > 0 and cv > 0):
        return 0
    by_risk = RISK_PCT * equity / ((stop - entry) * cv)
    by_lev = MAX_LEVERAGE * equity / (entry * cv)
    return int(math.floor(min(by_risk, by_lev)))


@dataclass
class Position:
    account: str
    symbol: str
    day: int
    entry_time: int
    entry: float
    stop: float
    target: float | None
    contracts: int
    cv: float
    last_checked: int                                # bar-open time of the last 1m candle walked
    exit_time: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    @property
    def end(self) -> int:                            # exit at the close of the day's last minute
        return self.day + DAY


def walk(p: Position, bars: pd.DataFrame, day_complete: bool = False) -> bool:
    """Advance `p` over closed 1m bars (time, high, low, close); True once it has exited.

    A minute whose high reaches the stop exits at max(stop, close) -- the research `ltp_close`
    fill -- and is checked BEFORE the target, so a minute touching both counts as the stop.
    `day_complete`: the day is over and `bars` reach its end; a thin coin whose last minutes had
    no trade (REST serves no candle for them) exits at its last traded close.
    """
    last_close = None
    for b in bars.sort_values("time").itertuples():
        t = int(b.time)
        if t <= p.last_checked or t < p.entry_time or t >= p.end:
            continue
        p.last_checked = t
        if b.high >= p.stop:
            p.exit_time, p.exit_price, p.exit_reason = t + MINUTE, max(p.stop, float(b.close)), "STOP"
            return True
        if p.target is not None and b.low <= p.target:
            p.exit_time, p.exit_price, p.exit_reason = t + MINUTE, p.target, "TARGET"
            return True
        last_close = float(b.close)
        if t == p.end - MINUTE:
            p.exit_time, p.exit_price, p.exit_reason = p.end, last_close, "TIME"
            return True
    if day_complete and last_close is not None:
        p.exit_time, p.exit_price, p.exit_reason = p.end, last_close, "TIME"
        return True
    return False


def settle(p: Position, funding_rates: list[float] = ()) -> dict:
    """Money for an exited position. Funding: rates in percent per settlement; a short RECEIVES a
    positive rate. Fees: taker on entry and exit notional."""
    qty = p.contracts * p.cv
    gross = (p.entry - p.exit_price) * qty
    fees = TAKER * qty * (p.entry + p.exit_price)
    funding = sum(funding_rates) / 100.0 * qty * p.entry
    pnl = gross - fees + funding
    risk = (p.stop - p.entry) * qty
    return dict(gross=gross, fees=fees, funding=funding, pnl=pnl, risk=risk, r=pnl / risk if risk > 0 else None)


def first_trade(client, symbol: str, day: int, now: float):
    """(bar time, close) of the first TRADED minute in [00:00, 00:15) UTC; "wait" while that window
    is still open and nothing has traded; None once it has closed with no trade. REST serves no
    candle for a no-trade minute at the start of a window, so the first row IS the first trade."""
    rows = client.candles(symbol, "1m", day, day + FIRST_TRADE_WINDOW - MINUTE, drop_forming=True)
    rows = sorted((x for x in rows if day <= int(x["time"]) < day + FIRST_TRADE_WINDOW), key=lambda x: int(x["time"]))
    if rows:
        return int(rows[0]["time"]), float(rows[0]["close"])
    return "wait" if now < day + FIRST_TRADE_WINDOW + MINUTE else None


# ============================================================ I/O

SCHEMA = """
CREATE TABLE IF NOT EXISTS fade_positions (
    id            BIGSERIAL PRIMARY KEY,
    account       TEXT NOT NULL,
    day           TIMESTAMPTZ NOT NULL,
    symbol        TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    prev_change   DOUBLE PRECISION NOT NULL,
    prev_turnover DOUBLE PRECISION NOT NULL,
    atrp          DOUBLE PRECISION NOT NULL,
    entry_time    TIMESTAMPTZ NOT NULL,
    entry_price   DOUBLE PRECISION NOT NULL,
    stop_price    DOUBLE PRECISION NOT NULL,
    target_price  DOUBLE PRECISION,
    contracts     INTEGER NOT NULL,
    contract_value DOUBLE PRECISION NOT NULL,
    equity_before DOUBLE PRECISION NOT NULL,
    status        TEXT NOT NULL,             -- OPEN | CLOSED | SKIPPED
    skip_reason   TEXT,
    last_checked  TIMESTAMPTZ,
    exit_time     TIMESTAMPTZ,
    exit_price    DOUBLE PRECISION,
    exit_reason   TEXT,                      -- STOP | TARGET | TIME | NO_DATA
    gross         DOUBLE PRECISION,
    fees          DOUBLE PRECISION,
    funding       DOUBLE PRECISION,
    pnl           DOUBLE PRECISION,
    risk          DOUBLE PRECISION,
    r             DOUBLE PRECISION,
    UNIQUE (account, day, symbol)
);
CREATE TABLE IF NOT EXISTS fade_days (
    day      TIMESTAMPTZ PRIMARY KEY,
    status   TEXT NOT NULL,                  -- ENTERED | NO_PICKS | MISSED
    detail   JSONB,
    at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fade_heartbeat (
    id       INTEGER PRIMARY KEY DEFAULT 1,
    at       TIMESTAMPTZ NOT NULL,
    detail   JSONB
);
"""


def _ts(t: int):
    return pd.Timestamp(int(t), unit="s", tz="UTC").to_pydatetime()


@dataclass
class FadeRunner:
    database_url: str
    client: object = None                              # deltabt.data.client.DeltaClient
    catalog: object = None                             # deltabt.data.store.ProductCatalog
    started_at: float = field(default_factory=time.time)
    last_loop_at: float = 0.0
    last_error: str | None = None
    ready: bool = False

    # -- lifecycle (what live.__main__._serve expects) --------------------------------
    async def start(self) -> bool:
        import asyncpg
        from app.persistence.db_auth import connect_kwargs
        from deltabt.data.client import DeltaClient
        from deltabt.data.store import ProductCatalog
        self.client = self.client or DeltaClient(per_second=2.0)
        self.catalog = self.catalog or ProductCatalog()
        self._pool = await asyncpg.create_pool(min_size=1, max_size=3, **connect_kwargs(self.database_url))
        async with self._pool.acquire() as con:
            await con.execute(SCHEMA)
            from live import fade_learning
            await fade_learning.ensure_schema(con)
        self.ready = True
        log.info("fade paper runner started; accounts %s", list(ACCOUNTS))
        return True

    async def stop(self) -> None:
        self.ready = False
        pool = getattr(self, "_pool", None)
        if pool is not None:
            await pool.close()

    async def run(self, interval: float = 30.0, alive_every: float = 300.0) -> None:
        last_alive = 0.0
        while True:
            try:
                await self.tick()
                self.last_error = None
                self.db_ok = True
            except Exception as exc:                    # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                self.db_ok = "Postgres" not in type(exc).__name__ and "Connection" not in type(exc).__name__
                log.exception("fade tick failed")
            self.last_loop_at = time.time()
            # CloudWatch's `silent` alarm wants a structured line at least every 15 min.
            if self.last_loop_at - last_alive >= alive_every:
                last_alive = self.last_loop_at
                log.info("fade runner alive", extra={"error": self.last_error})
            await asyncio.sleep(interval)

    # -- one pass ---------------------------------------------------------------------
    async def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        day = int(now) // DAY * DAY
        async with self._pool.acquire() as con:
            done = await con.fetchval("SELECT status FROM fade_days WHERE day = $1", _ts(day))
            if done is None and now >= day + ENTRY_AFTER:
                if now >= day + GIVE_UP_ENTRY:
                    await con.execute("INSERT INTO fade_days (day, status, detail) VALUES ($1,'MISSED',$2) "
                                      "ON CONFLICT DO NOTHING", _ts(day), json.dumps({"at": now}))
                else:
                    await self._enter(con, day)
            await self._monitor(con, now)
            from live import fade_learning                # records and labels; never trades or grades
            await fade_learning.step(self, con, now)
            await con.execute(
                "INSERT INTO fade_heartbeat (id, at, detail) VALUES (1, now(), $1) "
                "ON CONFLICT (id) DO UPDATE SET at = now(), detail = EXCLUDED.detail",
                json.dumps({"day": day, "error": self.last_error}))

    async def _equity(self, con, account: str) -> float:
        s = await con.fetchval("SELECT COALESCE(sum(pnl), 0) FROM fade_positions WHERE account = $1 "
                               "AND status = 'CLOSED'", account)
        return START_EQUITY + float(s)

    def _universe(self) -> dict[str, float]:
        rows = self.client.tickers("perpetual_futures")
        out = {}
        for r in rows:
            if r.get("top_tag") == "crypto" and r.get("product_trading_status", "operational") == "operational":
                try:
                    out[r["symbol"]] = float(r["contract_value"])
                except (KeyError, TypeError, ValueError):
                    continue
        return out

    async def _enter(self, con, day: int) -> None:
        cv = await asyncio.to_thread(self._universe)

        def daily(sym):
            rows = self.client.candles(sym, "1d", day - 40 * DAY, day - DAY, drop_forming=False)  # 30-day volume base
            return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"]).astype(float) \
                if rows else pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        data = {}
        for sym in cv:                                   # sequential: the client rate-limits itself
            d = await asyncio.to_thread(daily, sym)
            if len(d):
                d["time"] = d.time.astype("int64")
                data[sym] = d
        r = readings(data, cv, day)
        self._day_cache = (day, data, cv, r)            # for live/fade_learning.record
        pk = picks(r)
        if pk.empty:
            await con.execute("INSERT INTO fade_days (day, status, detail) VALUES ($1,'NO_PICKS',$2) "
                              "ON CONFLICT DO NOTHING", _ts(day), json.dumps({"coins": len(r)}))
            return
        entered = []
        firsts = {}
        for p in pk.itertuples():
            f = await asyncio.to_thread(first_trade, self.client, p.symbol, day, time.time())
            if f == "wait":
                return                                   # its first minute is not served yet: next tick
            firsts[p.symbol] = f
        for rank, p in enumerate(pk.itertuples(), 1):
            if firsts[p.symbol] is None:
                # no trade at all in the first 15 minutes: nothing to fill against
                for account in ACCOUNTS:
                    await con.execute(
                        """INSERT INTO fade_positions (account, day, symbol, rank, prev_change, prev_turnover, atrp,
                           entry_time, entry_price, stop_price, contracts, contract_value, equity_before, status,
                           skip_reason) VALUES ($1,$2,$3,$4,$5,$6,$7,$2,0,0,0,$8,$9,'SKIPPED',$10)
                           ON CONFLICT (account, day, symbol) DO NOTHING""",
                        account, _ts(day), p.symbol, rank, float(p.prev_change), float(p.prev_turnover),
                        float(p.atrp), cv[p.symbol], await self._equity(con, account),
                        "no trade in the first 15 minutes")
                entered.append(dict(symbol=p.symbol, change=round(float(p.prev_change), 4), skipped="no trade"))
                continue
            entry_t, entry = firsts[p.symbol]
            dist = K_STOP * float(p.atrp)
            stop = entry * (1 + dist)
            for account, tp in ACCOUNTS.items():
                eq = await self._equity(con, account)
                n = size(eq, entry, stop, cv[p.symbol])
                target = entry * (1 - tp * dist) if tp else None
                status, why = ("OPEN", None) if n >= 1 else ("SKIPPED", "below one contract at 2% risk")
                await con.execute(
                    """INSERT INTO fade_positions (account, day, symbol, rank, prev_change, prev_turnover, atrp,
                       entry_time, entry_price, stop_price, target_price, contracts, contract_value, equity_before,
                       status, skip_reason, last_checked)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$8)
                       ON CONFLICT (account, day, symbol) DO NOTHING""",
                    account, _ts(day), p.symbol, rank, float(p.prev_change), float(p.prev_turnover), float(p.atrp),
                    _ts(entry_t), entry, stop, target, n, cv[p.symbol], eq, status, why)
            entered.append(dict(symbol=p.symbol, change=round(float(p.prev_change), 4), entry=entry,
                                stop=round(stop, 8), atrp=round(float(p.atrp), 4)))
        await con.execute("INSERT INTO fade_days (day, status, detail) VALUES ($1,'ENTERED',$2) "
                          "ON CONFLICT DO NOTHING", _ts(day), json.dumps({"picks": entered, "coins": len(r)}))
        log.info("fade entries for %s: %s", pd.Timestamp(day, unit="s").date(), entered)

    def _funding(self, p: Position) -> list[float]:
        try:
            iv = int(self.catalog.get(p.symbol)["funding_interval_seconds"])
        except Exception:                                # noqa: BLE001
            iv = 8 * HOUR
        rows = self.client.candles("FUNDING:" + p.symbol, "1h", p.entry_time - 2 * HOUR, p.exit_time, drop_forming=False)
        if not rows:
            return []
        ft = np.array([int(x["time"]) for x in rows])
        fv = np.array([float(x["close"]) for x in rows])
        order = np.argsort(ft)
        ft, fv = ft[order], fv[order]
        first = (p.entry_time // iv + 1) * iv
        stamps = range(first, p.exit_time, iv)
        out = []
        for s in stamps:
            i = np.searchsorted(ft, s, side="right") - 1
            if i >= 0 and np.isfinite(fv[i]):
                out.append(float(fv[i]))
        return out

    async def _monitor(self, con, now: float) -> None:
        rows = await con.fetch("SELECT * FROM fade_positions WHERE status = 'OPEN'")
        if not rows:
            return
        bars_by_sym = {}
        for sym in {r["symbol"] for r in rows}:
            lo = min(int(r["last_checked"].timestamp()) for r in rows if r["symbol"] == sym)
            hi = int(now) // MINUTE * MINUTE - MINUTE              # last CLOSED minute
            if hi <= lo:
                continue
            got = await asyncio.to_thread(self.client.candles, sym, "1m", lo, hi, drop_forming=True)
            bars_by_sym[sym] = pd.DataFrame(got, columns=["time", "open", "high", "low", "close", "volume"]).astype(
                {"time": "int64", "high": float, "low": float, "close": float}) if got else None
        for r in rows:
            p = Position(account=r["account"], symbol=r["symbol"], day=int(r["day"].timestamp()),
                         entry_time=int(r["entry_time"].timestamp()), entry=r["entry_price"], stop=r["stop_price"],
                         target=r["target_price"], contracts=r["contracts"], cv=r["contract_value"],
                         last_checked=int(r["last_checked"].timestamp()))
            bars = bars_by_sym.get(p.symbol)
            done = walk(p, bars, day_complete=now >= p.end + 2 * MINUTE) if bars is not None and len(bars) else False
            if not done and now >= p.end + 2 * MINUTE:
                # The day is over. `bars` start AT last_checked, so the last traded minute is in it
                # even when walk() had nothing new to read: close there (a thin coin's last minutes
                # may have no candle at all).
                prior = bars[bars.time < p.end] if bars is not None and len(bars) else None
                if prior is not None and len(prior):
                    p.exit_time, p.exit_reason = p.end, "TIME"
                    p.exit_price = float(prior.sort_values("time").close.iloc[-1])
                    done = True
                elif now >= p.end + HOUR:
                    # no candle at all (halt / delisting): close at the entry, recorded as such
                    p.exit_time, p.exit_price, p.exit_reason = p.end, p.entry, "NO_DATA"
                    done = True
            if done:
                fr = await asyncio.to_thread(self._funding, p)
                m = settle(p, fr)
                await con.execute(
                    """UPDATE fade_positions SET status='CLOSED', last_checked=$2, exit_time=$3, exit_price=$4,
                       exit_reason=$5, gross=$6, fees=$7, funding=$8, pnl=$9, risk=$10, r=$11 WHERE id=$1""",
                    r["id"], _ts(p.last_checked), _ts(p.exit_time), p.exit_price, p.exit_reason,
                    m["gross"], m["fees"], m["funding"], m["pnl"], m["risk"], m["r"])
                log.info("fade %s %s closed %s: %+.2fR $%+.2f", p.account, p.symbol, p.exit_reason, m["r"] or 0, m["pnl"])
            elif p.last_checked != int(r["last_checked"].timestamp()):
                await con.execute("UPDATE fade_positions SET last_checked=$2 WHERE id=$1", r["id"], _ts(p.last_checked))

    # -- read side for the API -----------------------------------------------------------
    db_ok: bool = True

    def health(self, stale_limit: float = 300.0) -> dict:
        """The shape scripts/brief_report.py reads: status, ready, named checks."""
        stale = time.time() - self.last_loop_at if self.last_loop_at else None
        checks = [
            {"name": "runner_loop_alive", "ok": stale is not None and stale < stale_limit,
             "detail": f"last pass {stale:.0f}s ago" if stale is not None else "no pass yet"},
            {"name": "database_writable", "ok": bool(self.db_ok), "detail": self.last_error or "ok"},
        ]
        ok = bool(self.ready) and all(c["ok"] for c in checks)
        return {"status": "healthy" if ok else "unhealthy", "ready": bool(self.ready), "checks": checks,
                "mode": "fade", "seconds_since_loop": stale, "error": self.last_error,
                "uptime_seconds": time.time() - self.started_at}


def create_app(runner: FadeRunner):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    app = FastAPI(title="deltabt gainer-fade paper runner", docs_url=None, redoc_url=None)
    limit = float(os.environ.get("FADE_STALE_SECONDS", "300"))

    @app.get("/healthz")
    async def healthz():
        h = runner.health(limit)
        return JSONResponse(h, status_code=200 if h["status"] == "healthy" else 503)

    @app.get("/readyz")
    async def readyz():
        # deploy.sh's rollback gate: 2xx once the first pass has run.
        h = runner.health(limit)
        return JSONResponse(h, status_code=200 if h["status"] == "healthy" else 503)

    return app

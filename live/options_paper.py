"""Option-selling PAPER account (owner, 2026-10-28: "let's start options now" -> "approved").

Every day at 12:00 UTC (5:30 PM IST) it sells, on paper, one BTC and one ETH strangle that expire
24 h later: a put ~1% below and a call ~1% above the perp's 1h close at 12:00, one contract each.
The rule is the one the sealed 2026 test passed (docs/option_selling_prereg.md,
scripts/option_selling_lab.py). Two things the backtest could only assume are MEASURED here:

  fill        each leg is sold at the option's real best bid at entry (the lab assumed 0.9 x mark);
              mark, bid, ask and bid size are stored, so fill quality is read every day.
  settlement  each leg settles at Delta's own settlement_price of the expired option (its payout per
              unit, from the index's 30-min average); the lab used the perp's close as a stand-in.

Fees are the lab's: min(0.01% of notional, 3.5% of premium) on the sale and on the settlement.
Runs inside the gainer-fade runner's loop (live/fade.py) on its database; tables `option_legs`
and `option_days`. NO ORDER IS EVER SENT: public GET endpoints only, nothing here holds keys.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import pandas as pd

log = logging.getLogger(__name__)

DAY, MINUTE, HOUR = 86400, 60, 3600
UNDERLYINGS = {"BTC": dict(perp="BTCUSD", cv=0.001, grid=200), "ETH": dict(perp="ETHUSD", cv=0.01, grid=10)}
M = 0.01                                             # strike distance chosen on 2025 (build), tested on 2026
CONTRACTS = 1
FEE_NOTIONAL, FEE_PREMIUM_CAP = 0.0001, 0.035
START_EQUITY = 250.0
SALE_HOUR = 12                                       # options expire 12:00 UTC; sell 24 h before
ENTRY_AFTER = SALE_HOUR * HOUR + MINUTE              # the 11:00 1h bar has closed
GIVE_UP_ENTRY = SALE_HOUR * HOUR + 30 * MINUTE       # a sale not made by 12:30 UTC is recorded as missed
SETTLE_AFTER = MINUTE                                # past expiry before asking for the settlement


# ============================================================ pure

def expiry_tag(expiry: int) -> str:
    return pd.Timestamp(int(expiry), unit="s", tz="UTC").strftime("%d%m%y")


def sale_expiry(now: float) -> int:
    """The expiry today's 12:00 UTC sale is for: tomorrow 12:00 UTC."""
    return int(now) // DAY * DAY + DAY + SALE_HOUR * HOUR


def strike_order(spot: float, kind: str, grid: int, m: float = M) -> list[int]:
    """Strikes to try, nearest first: the lab's grid strike for spot x (1 -/+ m), then 1-4 steps out
    and in, alternating (scripts/option_selling_lab._job)."""
    target = spot * (1 - m) if kind == "P" else spot * (1 + m)
    k0 = int(round(target / grid) * grid)
    return [k0 + n * grid for n in (0, 1, -1, 2, -2, 3, -3, 4, -4)]


def fee(notional: float, value: float) -> float:
    return min(FEE_NOTIONAL * notional, FEE_PREMIUM_CAP * value)


def leg_pnl(premium_per_unit: float, settle_per_unit: float, spot: float, cv: float,
            contracts: int = CONTRACTS) -> dict:
    """One short leg held to expiry, in USD (the lab's formula with the real fill and settlement)."""
    notional = spot * cv * contracts
    prem = premium_per_unit * cv * contracts
    paid = settle_per_unit * cv * contracts
    fee_in, fee_out = fee(notional, prem), fee(notional, paid)
    return dict(premium=prem, fee_in=fee_in, fee_out=fee_out, notional=notional,
                pnl=prem - paid - fee_in - fee_out)


def _f(x):
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


# ============================================================ I/O (called from FadeRunner.tick)

SCHEMA = """
CREATE TABLE IF NOT EXISTS option_legs (
    id             BIGSERIAL PRIMARY KEY,
    expiry         TIMESTAMPTZ NOT NULL,
    underlying     TEXT NOT NULL,
    kind           TEXT NOT NULL,             -- P | C
    symbol         TEXT,
    strike         DOUBLE PRECISION,
    spot           DOUBLE PRECISION NOT NULL, -- perp 1h close at 12:00 UTC (the lab's)
    entry_time     TIMESTAMPTZ NOT NULL,
    mark           DOUBLE PRECISION,
    best_bid       DOUBLE PRECISION,
    best_ask       DOUBLE PRECISION,
    bid_size       DOUBLE PRECISION,
    mark_iv        DOUBLE PRECISION,
    delta          DOUBLE PRECISION,
    contracts      INTEGER NOT NULL,
    contract_value DOUBLE PRECISION NOT NULL,
    notional       DOUBLE PRECISION,
    premium        DOUBLE PRECISION,          -- USD received at the best bid
    fee_in         DOUBLE PRECISION,
    status         TEXT NOT NULL,             -- OPEN | SETTLED | SKIPPED
    skip_reason    TEXT,                      -- NO_STRIKE | NO_BID
    settled_at     TIMESTAMPTZ,
    settle_value   DOUBLE PRECISION,          -- Delta's settlement_price (payout per unit)
    fee_out        DOUBLE PRECISION,
    pnl            DOUBLE PRECISION,
    UNIQUE (expiry, underlying, kind)
);
CREATE TABLE IF NOT EXISTS option_days (
    expiry   TIMESTAMPTZ PRIMARY KEY,
    status   TEXT NOT NULL,                   -- ENTERED | MISSED
    detail   JSONB,
    at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _ts(t: float):
    return pd.Timestamp(int(t), unit="s", tz="UTC").to_pydatetime()


def _result(resp):
    """DeltaClient._get returns the whole envelope ({"success", "result"})."""
    return resp.get("result") if isinstance(resp, dict) and "result" in resp else resp


async def ensure_schema(con) -> None:
    await con.execute(SCHEMA)


async def _get(client, path: str):
    """A public GET; None for a symbol Delta does not list (it answers non-200)."""
    try:
        return _result(await asyncio.to_thread(client._get, path))
    except RuntimeError:
        return None


async def _spot(client, perp: str, day: int):
    """Close of the perp's 1h bar 11:00-12:00 UTC, or None while it is not served yet."""
    t = day + (SALE_HOUR - 1) * HOUR
    rows = await asyncio.to_thread(client.candles, perp, "1h", t, t, drop_forming=True)
    for x in rows:
        if int(x["time"]) == t and x.get("close") is not None:
            return float(x["close"])
    return None


async def sell(runner, con, now: float) -> None:
    day = int(now) // DAY * DAY
    expiry = sale_expiry(now)
    if await con.fetchval("SELECT status FROM option_days WHERE expiry = $1", _ts(expiry)) is not None:
        return
    if now < day + ENTRY_AFTER:
        return
    if now >= day + GIVE_UP_ENTRY:
        await con.execute("INSERT INTO option_days (expiry, status, detail) VALUES ($1,'MISSED',$2) "
                          "ON CONFLICT DO NOTHING", _ts(expiry), json.dumps({"at": now}))
        return
    spots = {}
    for und, u in UNDERLYINGS.items():
        spots[und] = await _spot(runner.client, u["perp"], day)
        if spots[und] is None:
            return                                   # the 12:00 bar is not served yet; next pass
    legs = []
    for und, u in UNDERLYINGS.items():
        for kind in ("P", "C"):
            row = dict(underlying=und, kind=kind, spot=spots[und], cv=u["cv"])
            for k in strike_order(spots[und], kind, u["grid"]):
                sym = f"{kind}-{und}-{k}-{expiry_tag(expiry)}"
                t = await _get(runner.client, f"/v2/tickers/{sym}")
                if t:
                    q = t.get("quotes") or {}
                    row.update(symbol=sym, strike=float(k), mark=_f(t.get("mark_price")),
                               bid=_f(q.get("best_bid")), ask=_f(q.get("best_ask")),
                               bid_size=_f(q.get("bid_size")), iv=_f(q.get("mark_iv") or t.get("mark_vol")),
                               delta=_f((t.get("greeks") or {}).get("delta")))
                    break
            legs.append(row)
    async with con.transaction():
        for r in legs:
            skip = ("NO_STRIKE" if r.get("symbol") is None else
                    "NO_BID" if not r.get("bid") or (r.get("bid_size") or 0) < CONTRACTS else None)
            p = leg_pnl(r["bid"], 0.0, r["spot"], r["cv"]) if skip is None else {}
            await con.execute(
                "INSERT INTO option_legs (expiry, underlying, kind, symbol, strike, spot, entry_time, mark, "
                "best_bid, best_ask, bid_size, mark_iv, delta, contracts, contract_value, notional, premium, "
                "fee_in, status, skip_reason) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,"
                "$17,$18,$19,$20) ON CONFLICT DO NOTHING",
                _ts(expiry), r["underlying"], r["kind"], r.get("symbol"), r.get("strike"), r["spot"], _ts(now),
                r.get("mark"), r.get("bid"), r.get("ask"), r.get("bid_size"), r.get("iv"), r.get("delta"),
                CONTRACTS, r["cv"], r["spot"] * r["cv"] * CONTRACTS, p.get("premium"), p.get("fee_in"),
                "SKIPPED" if skip else "OPEN", skip)
        await con.execute(
            "INSERT INTO option_days (expiry, status, detail) VALUES ($1,'ENTERED',$2) ON CONFLICT DO NOTHING",
            _ts(expiry), json.dumps({"spots": spots, "legs": [
                {k: r.get(k) for k in ("symbol", "mark", "bid", "ask")} for r in legs]}))
    log.info("option strangles sold (paper) for expiry %s: %s", expiry_tag(expiry),
             ", ".join(f"{r.get('symbol')} @ {r.get('bid')}" for r in legs))


async def settle_due(runner, con, now: float) -> None:
    for r in await con.fetch("SELECT id, symbol, spot, contract_value, contracts, best_bid FROM option_legs "
                             "WHERE status = 'OPEN' AND expiry <= $1", _ts(now - SETTLE_AFTER)):
        prod = await _get(runner.client, f"/v2/products/{r['symbol']}")
        value = _f((prod or {}).get("settlement_price"))
        if not prod or prod.get("state") != "expired" or value is None:
            continue                                 # not settled yet; next pass
        p = leg_pnl(r["best_bid"], value, r["spot"], r["contract_value"], r["contracts"])
        await con.execute("UPDATE option_legs SET status='SETTLED', settled_at=$2, settle_value=$3, fee_out=$4, "
                          "pnl=$5 WHERE id=$1", r["id"], _ts(now), value, p["fee_out"], p["pnl"])
        log.info("option leg settled (paper): %s at %s, pnl %.2f", r["symbol"], value, p["pnl"])


async def step(runner, con, now: float | None = None) -> None:
    """One options pass; never raises into the fade runner's loop."""
    now = time.time() if now is None else now
    for name, fn in (("sell", sell), ("settle", settle_due)):
        try:
            await fn(runner, con, now)
        except Exception:                                         # noqa: BLE001
            log.exception("options paper step %s failed", name)

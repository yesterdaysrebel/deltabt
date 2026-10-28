"""What the gainer-fade runner learns from (owner, 2026-10-28: "how will model training work here?"
→ "approved"). Rules fixed in docs/fade_model_switch_on_rule.md before any of this ran.

Each morning, after the two trades are entered, the runner RECORDS every eligible gainer (up to
the top 20 by yesterday's gain), traded or not, with readings known at entry -- the daily ones
the research used and two new kinds no history has: the order book and the recent trade flow.
A day later each candidate is LABELLED as if shorted under both exits (A, QUICK). Every Monday
the model is GRADED on the week just ended: does its own daily top 2 beat the rule's top 2 and
two random candidates? It never chooses a trade; docs/fade_model_switch_on_rule.md says when it
might, and the owner decides.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math

import numpy as np
import pandas as pd

from live import fade as F

log = logging.getLogger(__name__)

N_CANDIDATES = 20
RECORD_UNTIL = 6 * F.HOUR          # a day's candidates are recorded within 6 h of midnight or not at all
LABEL_AFTER = F.DAY + 5 * F.MINUTE
GRADE_FROM_HOUR = 1                # Mondays, from 01:00 UTC
MIN_TRAIN, MIN_TEST = 400, 50
N_RANDOM = 2000
FEATURES = ["rank", "prev_change", "log_turnover", "atrp", "wick", "prev2_change", "abnormal_vol",
            "fund_8h", "oi_change", "btc_change", "spread_bps", "log_bid_depth", "log_ask_depth",
            "book_imbalance", "taker_buy_share", "log_trade_usd"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS fade_candidates (
    day            TIMESTAMPTZ NOT NULL,
    symbol         TEXT NOT NULL,
    rank           INTEGER NOT NULL,
    traded         BOOLEAN NOT NULL,
    entry_price    DOUBLE PRECISION,
    readings       JSONB NOT NULL,
    recorded_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    r_a            DOUBLE PRECISION,
    r_quick        DOUBLE PRECISION,
    exit_a         TEXT,
    exit_quick     TEXT,
    labelled_at    TIMESTAMPTZ,
    PRIMARY KEY (day, symbol)
);
CREATE TABLE IF NOT EXISTS fade_grades (
    week_start     TIMESTAMPTZ PRIMARY KEY,
    graded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    n_train        INTEGER,
    n_test         INTEGER,
    days           INTEGER,
    summary        TEXT,
    detail         JSONB
);
"""


# ============================================================ pure logic (tested)

def daily_extras(d: pd.DataFrame, day: int) -> dict:
    """Readings from one coin's daily candles (time < day): D-1's close position in its range,
    D-2's change and D-1's turnover against the mean of D-31 .. D-2 (volume only; contract value
    cancels)."""
    d = d[d.time < day].sort_values("time")
    out = {"wick": np.nan, "prev2_change": np.nan, "abnormal_vol": np.nan}
    if len(d) < 2 or int(d.time.iloc[-1]) != day - F.DAY:
        return out
    last = d.iloc[-1]
    rng = last.high - last.low
    out["wick"] = (last.high - last.close) / rng if rng > 0 else np.nan
    prev = d.iloc[-2]
    if int(prev.time) == day - 2 * F.DAY and prev.open > 0:
        out["prev2_change"] = prev.close / prev.open - 1
    tv = (d.volume * (d.high + d.low + d.close) / 3).to_numpy()
    base = tv[-31:-1]
    if len(base) >= 20 and np.nanmean(base) > 0:
        out["abnormal_vol"] = tv[-1] / np.nanmean(base)
    return out


def book_readings(book: dict, cv: float) -> dict:
    """Spread and USD depth within 1% of the mid, from Delta's l2orderbook (size in contracts)."""
    out = {"spread_bps": np.nan, "log_bid_depth": np.nan, "log_ask_depth": np.nan, "book_imbalance": np.nan}
    try:
        buy = [(float(x["price"]), float(x["size"])) for x in book.get("buy") or []]
        sell = [(float(x["price"]), float(x["size"])) for x in book.get("sell") or []]
    except (TypeError, ValueError, KeyError):
        return out
    if not buy or not sell:
        return out
    bid, ask = max(p for p, _ in buy), min(p for p, _ in sell)
    mid = (bid + ask) / 2
    if mid <= 0:
        return out
    bd = sum(p * s * cv for p, s in buy if p >= mid * 0.99)
    ad = sum(p * s * cv for p, s in sell if p <= mid * 1.01)
    out["spread_bps"] = 1e4 * (ask - bid) / mid
    out["log_bid_depth"] = math.log10(bd + 1)
    out["log_ask_depth"] = math.log10(ad + 1)
    out["book_imbalance"] = (bd - ad) / (bd + ad) if bd + ad > 0 else np.nan
    return out


def flow_readings(trades: list[dict], cv: float) -> dict:
    """Share of the last trades' USD bought by an aggressive (taker) buyer."""
    out = {"taker_buy_share": np.nan, "log_trade_usd": np.nan}
    tot = buy = 0.0
    for t in trades or []:
        try:
            usd = float(t["size"]) * cv * float(t["price"])
        except (TypeError, ValueError, KeyError):
            continue
        tot += usd
        if t.get("buyer_role") == "taker":
            buy += usd
    if tot > 0:
        out["taker_buy_share"] = buy / tot
        out["log_trade_usd"] = math.log10(tot + 1)
    return out


def label(entry: float, atrp: float, day: int, bars: pd.DataFrame, entry_time: int | None = None) -> dict:
    """As-if R under both exits for a short entered at `entry` at the 00:00 minute's close.
    R is size-free and before funding -- the research definition."""
    out = {}
    dist = F.K_STOP * atrp
    for name, tp in (("a", None), ("quick", F.QUICK_TP)):
        t0 = day if entry_time is None else entry_time
        p = F.Position(account=name, symbol="", day=day, entry_time=t0, entry=entry, stop=entry * (1 + dist),
                       target=entry * (1 - tp * dist) if tp else None, contracts=1, cv=1.0, last_checked=t0)
        if not F.walk(p, bars, day_complete=True):
            return {}
        out[f"r_{name}"] = F.settle(p)["r"]
        out[f"exit_{name}"] = p.exit_reason
    return out


def _model():
    from deltabt.signal_model import model
    return model()


def grade_week(rows: pd.DataFrame, week_start: int, seed: int = 0) -> dict:
    """Train on labelled candidates before week_start - 1 day, test on the week: each day the
    model's own top 2 vs the rule's top 2 (rank 1-2) vs 2 random candidates, by mean R_A and R_QUICK."""
    rows = rows.dropna(subset=["r_a", "r_quick"])
    train = rows[rows.day < week_start - F.DAY]
    test = rows[(rows.day >= week_start) & (rows.day < week_start + 7 * F.DAY)]
    res = {"n_train": len(train), "n_test": len(test), "days": int(test.day.nunique())}
    if len(train) < MIN_TRAIN or len(test) < MIN_TEST or train.r_a.gt(0).nunique() < 2:
        res["summary"] = (f"not graded: {len(train)} training rows (need {MIN_TRAIN}), "
                          f"{len(test)} this week (need {MIN_TEST})")
        return res
    # a reading with no value in any training row is left out (the model refuses an all-empty
    # input; the same trap stopped the metals grade until #141)
    use = [f for f in FEATURES if train[f].notna().any()]
    res["readings_left_out"] = [f for f in FEATURES if f not in use]
    m = _model().fit(train[use], (train.r_a > 0).astype(int))
    test = test.assign(score=m.predict_proba(test[use])[:, 1])
    model_pk = test.sort_values(["day", "score"], ascending=[True, False]).groupby("day").head(F.TOP_N)
    rule_pk = test[test["rank"] <= F.TOP_N]
    rng = np.random.default_rng(seed)
    by_day = {d: g for d, g in test.groupby("day")}
    rand = {"a": [], "quick": []}
    for _ in range(N_RANDOM):
        ra, rq = [], []
        for g in by_day.values():
            idx = rng.choice(len(g), min(F.TOP_N, len(g)), replace=False)
            ra += list(g.r_a.to_numpy()[idx])
            rq += list(g.r_quick.to_numpy()[idx])
        rand["a"].append(np.mean(ra))
        rand["quick"].append(np.mean(rq))
    for k in ("a", "quick"):
        col = f"r_{k}"
        res[f"model_{k}"] = float(model_pk[col].mean())
        res[f"rule_{k}"] = float(rule_pk[col].mean())
        res[f"random_{k}"] = float(np.mean(rand[k]))
        res[f"random_p_{k}"] = float((np.array(rand[k]) >= model_pk[col].mean()).mean())
    res["picks"] = [[int(r.day), r.symbol, round(float(r.score), 4), round(float(r.r_a), 4),
                     round(float(r.r_quick), 4), int(r["rank"])] for _, r in model_pk.iterrows()]
    res["summary"] = (f"model's daily top 2: A {res['model_a']:+.3f}R / quick {res['model_quick']:+.3f}R vs the "
                      f"rule's {res['rule_a']:+.3f}R / {res['rule_quick']:+.3f}R vs random "
                      f"{res['random_a']:+.3f}R / {res['random_quick']:+.3f}R; random did as well "
                      f"{100 * res['random_p_a']:.0f}% of the time (A). It does not choose trades.")
    return res


# ============================================================ I/O (called from FadeRunner.tick)

def _result(resp):
    """DeltaClient._get returns the whole envelope ({"success", "result"})."""
    return resp.get("result") if isinstance(resp, dict) and "result" in resp else resp


async def ensure_schema(con) -> None:
    await con.execute(SCHEMA)


async def record(runner, con, day: int, now: float) -> None:
    """Once per day, after the entries: every eligible gainer with its readings."""
    if now >= day + RECORD_UNTIL:
        return
    if await con.fetchval("SELECT count(*) FROM fade_candidates WHERE day = $1", F._ts(day)):
        return
    status = await con.fetchval("SELECT status FROM fade_days WHERE day = $1", F._ts(day))
    cache = getattr(runner, "_day_cache", None)
    if status not in ("ENTERED", "NO_PICKS") or not cache or cache[0] != day:
        return
    _, data, cv, r = cache
    traded = set(F.picks(r).symbol)
    cand = r[r.eligible].sort_values("prev_change", ascending=False).head(N_CANDIDATES).reset_index(drop=True)
    btc = data.get("BTCUSD")
    btc_change = np.nan
    if btc is not None:
        b = btc[btc.time == day - F.DAY]
        if len(b):
            btc_change = float(b.close.iloc[0] / b.open.iloc[0] - 1)
    cl = runner.client
    for rank, c in enumerate(cand.itertuples(), 1):
        sym = c.symbol
        rd = {"rank": rank, "prev_change": float(c.prev_change), "log_turnover": math.log10(float(c.prev_turnover) + 1),
              "atrp": float(c.atrp), "btc_change": btc_change, **daily_extras(data[sym], day)}
        entry = None
        try:
            f = await asyncio.to_thread(F.first_trade, cl, sym, day, day + F.DAY)
            if isinstance(f, tuple):
                rd["entry_time"], entry = f
            fr = await asyncio.to_thread(cl.candles, "FUNDING:" + sym, "1h", day - 3 * F.HOUR, day, drop_forming=False)
            try:
                iv = int(runner.catalog.get(sym)["funding_interval_seconds"])
            except Exception:                                     # noqa: BLE001
                iv = 8 * F.HOUR
            rate = float(sorted(fr, key=lambda x: int(x["time"]))[-1]["close"]) if fr else np.nan
            rd["fund_8h"] = rate * 8 * F.HOUR / iv if np.isfinite(rate) else np.nan
            oi = await asyncio.to_thread(cl.candles, "OI:" + sym, "1h", day - F.DAY - F.HOUR, day, drop_forming=False)
            oi = {int(x["time"]): float(x["close"]) for x in oi if x.get("close") is not None}
            a, b = oi.get(day - F.DAY - F.HOUR), oi.get(day - F.HOUR)
            rd["oi_change"] = b / a - 1 if a and b else np.nan
            book = _result(await asyncio.to_thread(cl._get, f"/v2/l2orderbook/{sym}", {"depth": 50}))
            rd.update(book_readings(book if isinstance(book, dict) else {}, cv[sym]))
            trades = _result(await asyncio.to_thread(cl._get, f"/v2/trades/{sym}"))
            trades = trades.get("trades", []) if isinstance(trades, dict) else (trades or [])
            rd.update(flow_readings(trades, cv[sym]))
        except Exception as exc:                                  # noqa: BLE001
            # a missing reading is a NaN the model tolerates, never a reason to drop the row
            log.warning("fade candidate %s readings incomplete: %s", sym, exc)
        rd = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in rd.items()}
        await con.execute(
            "INSERT INTO fade_candidates (day, symbol, rank, traded, entry_price, readings) VALUES ($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT (day, symbol) DO NOTHING", F._ts(day), sym, rank, sym in traded, entry, json.dumps(rd))
    log.info("fade candidates recorded for %s: %d", pd.Timestamp(day, unit="s").date(), len(cand))


LABEL_EVERY = 10 * F.MINUTE


async def label_due(runner, con, now: float) -> None:
    if now - getattr(runner, "_last_label", 0.0) < LABEL_EVERY:
        return
    runner._last_label = now
    rows = await con.fetch("SELECT day, symbol, entry_price, readings FROM fade_candidates "
                           "WHERE labelled_at IS NULL AND day <= $1 AND entry_price IS NOT NULL",
                           F._ts(int(now) - LABEL_AFTER))
    for r in rows:
        day = int(r["day"].timestamp())
        atrp = (json.loads(r["readings"]) if isinstance(r["readings"], str) else r["readings"]).get("atrp")
        got = await asyncio.to_thread(runner.client.candles, r["symbol"], "1m", day, day + F.DAY - F.MINUTE,
                                      drop_forming=True)
        bars = pd.DataFrame(got, columns=["time", "open", "high", "low", "close", "volume"]).astype(
            {"time": "int64", "high": float, "low": float, "close": float}) if got else None
        rdd = json.loads(r["readings"]) if isinstance(r["readings"], str) else r["readings"]
        lab = label(r["entry_price"], float(atrp), day, bars, entry_time=int(rdd.get("entry_time") or day)) \
            if bars is not None and atrp else {}
        if not lab:
            if now > day + 3 * F.DAY:                             # never closable: mark and move on
                await con.execute("UPDATE fade_candidates SET labelled_at = now() WHERE day=$1 AND symbol=$2",
                                  r["day"], r["symbol"])
            continue
        await con.execute("UPDATE fade_candidates SET r_a=$3, r_quick=$4, exit_a=$5, exit_quick=$6, labelled_at=now() "
                          "WHERE day=$1 AND symbol=$2", r["day"], r["symbol"], lab["r_a"], lab["r_quick"],
                          lab["exit_a"], lab["exit_quick"])


async def grade_due(con, now: float) -> None:
    ts = pd.Timestamp(int(now), unit="s", tz="UTC")
    if ts.weekday() != 0 or ts.hour < GRADE_FROM_HOUR:
        return
    week_start = int((ts.normalize() - pd.Timedelta(days=7)).timestamp())
    if await con.fetchval("SELECT 1 FROM fade_grades WHERE week_start = $1", F._ts(week_start)):
        return
    raw = await con.fetch("SELECT day, symbol, rank, readings, r_a, r_quick FROM fade_candidates WHERE r_a IS NOT NULL")
    rows = pd.DataFrame([{"day": int(x["day"].timestamp()), "symbol": x["symbol"], "r_a": x["r_a"],
                          "r_quick": x["r_quick"],
                          **(json.loads(x["readings"]) if isinstance(x["readings"], str) else x["readings"]),
                          "rank": x["rank"]} for x in raw])
    for f in FEATURES:
        if f not in rows:
            rows[f] = np.nan
    rows[FEATURES] = rows[FEATURES].astype(float) if len(rows) else rows[FEATURES]
    res = await asyncio.to_thread(grade_week, rows, week_start) if len(rows) else {
        "n_train": 0, "n_test": 0, "days": 0, "summary": "not graded: nothing labelled yet"}
    await con.execute("INSERT INTO fade_grades (week_start, n_train, n_test, days, summary, detail) "
                      "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING", F._ts(week_start), res["n_train"],
                      res["n_test"], res.get("days", 0), res["summary"], json.dumps(res, default=float))
    log.info("fade weekly grade %s: %s", ts.date(), res["summary"])


async def step(runner, con, now: float) -> None:
    """One learning pass; never raises into the trading loop."""
    day = int(now) // F.DAY * F.DAY
    for name, fn in (("record", lambda: record(runner, con, day, now)), ("label", lambda: label_due(runner, con, now)),
                     ("grade", lambda: grade_due(con, now))):
        try:
            await fn()
        except Exception:                                         # noqa: BLE001
            log.exception("fade learning step %s failed", name)

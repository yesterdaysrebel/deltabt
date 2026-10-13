"""Backtest of the bot as built (2026-10-13): top gainers, %R + Supertrend, 2 a day.

    PYTHONPATH=. .venv/bin/python scripts/top_gainers_backtest.py picks
    PYTHONPATH=. .venv/bin/python scripts/top_gainers_backtest.py fetch
    PYTHONPATH=. .venv/bin/python scripts/top_gainers_backtest.py run

WHY. The owner asked (2026-10-13): "can you test all this setup with exact rules
on higher time frames how does it go?" This replays the bot's rules on history
and repeats them on slower chart pairs. It describes; it does not gate the bot.

THE RULES, AS THE BOT RUNS THEM (live/universe.py, catalog manual_scalp_both_t3_st):
  * coins: each UTC day D, the top 10 crypto perps by the PREVIOUS UTC day's
    change (close/open of the 1d candle ending at D 00:00), rising, with that
    day's turnover >= $250k (volume x contract_value x (h+l+c)/3). A coin can
    only be ENTERED on its picked days; an open position runs to its exit.
  * entry: %R variant_a + Supertrend (10, 2) aligned on the primary AND the
    confirm chart; 4xATR stop, 3R target, 72h time stop.
  * account: $250, 2% risk, position <= 5x equity (owner, 2026-10-13), one position at a time, at most 2
    entries per UTC day, daily loss 10%, 8 losses in a row (daily), drawdown
    latch 50%, no resume; 4h pause on a coin after a losing trade.
  * fills: stop_fill "ltp_close" on 1m bars (calibrated on live fills); taker
    fees + slippage from SymbolCosts; funding charged.

CHART PAIRS (primary/confirm minutes): 5/1 (as built), 15/5, 60/15, 240/60.

KNOWN DIFFERENCES FROM THE LIVE BOT, stated before the run:
  * survivorship: only coins listed TODAY can be picked; coins delisted since
    (often past gainers) are missing.
  * the live bot may take ONE contract when 2% rounds to zero and one contract
    risks <= 3%; this engine skips those signals (counted as zero_contracts).
  * the live bot warms up from 7 days of 1m history; 240/60 needs ~23 days for
    %R(140) on 4h bars, so live it would need a longer warm-up.
  * zero-volume filler bars are dropped before resampling (as every lab here).
"""
from __future__ import annotations

import dataclasses
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, ".")
from deltabt import rulecore  # noqa: E402
from deltabt.catalog import build_spec  # noqa: E402
from deltabt.costs import SymbolCosts  # noqa: E402
from deltabt.data.quality import tradable_mask  # noqa: E402
from deltabt.data.store import CandleStore, ProductCatalog  # noqa: E402
from deltabt.harness import _resampled, load_symbol, params_for  # noqa: E402
from deltabt.portfolio import Book, RiskGates, run_portfolio  # noqa: E402

OUT = Path("out/top_gainers")
DAY = 86400
FIRST_DAY = pd.Timestamp("2026-01-01", tz="UTC").value // 10**9
LAST_DAY = pd.Timestamp("2026-10-12", tz="UTC").value // 10**9   # inclusive
WARMUP_START = FIRST_DAY - 32 * DAY
TOP_N, MIN_TURNOVER = 10, 250_000.0
PAIRS = [(5, 1), (15, 5), (60, 15), (240, 60)]
FAMILY = "manual_scalp_both_t3_st"
EQUITY, RISK_PCT, MAX_LEV = 250.0, 2.0, 5.0
GATES = replace(RiskGates(), max_open_positions=1, max_trades_per_day=2,
                max_daily_loss_pct=0.10, max_drawdown_pct=0.50,
                max_consecutive_losses=8, resume_after_days=0,
                pause_seconds=4 * 3600, pause_after=None)


def _crypto_perps() -> list[dict]:
    rows = requests.get("https://api.india.delta.exchange/v2/tickers",
                        params={"contract_types": "perpetual_futures"}, timeout=30).json()["result"]
    return [r for r in rows if r.get("top_tag") == "crypto"]


def cmd_picks() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = _crypto_perps()
    store = CandleStore()
    cv = {r["symbol"]: float(r["contract_value"]) for r in rows}

    def daily(sym):
        try:
            return sym, store.load(sym, "ltp", "1d", FIRST_DAY - 2 * DAY, LAST_DAY)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: {exc}")
            return sym, pd.DataFrame()

    frames = []
    with ThreadPoolExecutor(4) as ex:
        for sym, d in ex.map(daily, sorted(cv)):
            if d.empty:
                continue
            d = d.assign(symbol=sym,
                         change=(d.close / d.open - 1) * 100,
                         turnover=d.volume * cv[sym] * (d.high + d.low + d.close) / 3,
                         pick_day=d.time + DAY)
            frames.append(d)
    allc = pd.concat(frames, ignore_index=True)
    allc = allc[(allc.pick_day >= FIRST_DAY) & (allc.pick_day <= LAST_DAY)]
    ok = allc[(allc.change > 0) & (allc.turnover >= MIN_TURNOVER)]
    picks = (ok.sort_values(["pick_day", "change"], ascending=[True, False])
               .groupby("pick_day").head(TOP_N))
    picks[["pick_day", "symbol", "change", "turnover"]].to_csv(OUT / "picks.csv", index=False)
    days = picks.pick_day.nunique()
    print(f"{len(cv)} crypto perps listed today; {allc.symbol.nunique()} have daily history")
    print(f"{len(picks)} coin-days over {days} days; {picks.symbol.nunique()} distinct coins")
    print(f"coins per day: median {picks.groupby('pick_day').size().median():.0f}, "
          f"min {picks.groupby('pick_day').size().min()}")


def cmd_fetch() -> None:
    picks = pd.read_csv(OUT / "picks.csv")
    span = picks.groupby("symbol").pick_day.agg(["min", "max"])
    syms = sorted(span.index)
    store = CandleStore()
    now = int(time.time()) // 60 * 60 - 120

    def one(sym):
        t0 = time.time()
        # a month of warm-up before the first pick (%R 140 on 4h = 23 days),
        # and 4 days after the last for the 72h time stop
        lo = int(span.loc[sym, "min"]) - 32 * DAY
        hi = min(int(span.loc[sym, "max"]) + 5 * DAY, now)
        try:
            store.load_all_series(sym, "1m", lo, hi)
            return sym, None, time.time() - t0
        except Exception as exc:  # noqa: BLE001
            return sym, str(exc), time.time() - t0

    with ThreadPoolExecutor(4) as ex:
        for k, (sym, err, dt) in enumerate(ex.map(one, syms), 1):
            print(f"[{k}/{len(syms)}] {sym} {'ERROR ' + err if err else 'ok'} {dt:.0f}s", flush=True)


def _book(sym, primary, confirm, spec, allowed_days):
    d = load_symbol(sym)
    if d is None:
        return None
    # FLAT NO-TRADE MINUTES ARE KEPT (2026-10-13, first run). Dropping them
    # made the 72h time stop count candles instead of clock time (a position
    # sat open from 03-31 to the end of the data and blocked the only slot)
    # and threw away hourly buckets with any quiet minute (93 of 190 coins
    # vanished at 1h). Delta's own candles keep them; tradable_mask still
    # refuses entries on them.
    l = d["ltp"]
    d = dict(symbol=sym, ltp=l, mark=d["mark"], funding=d["funding"], tradable=tradable_mask(l))
    cache = {}
    P, mk, tr = _resampled(d, primary, cache)
    C = _resampled(d, confirm, cache)[0]
    if len(P) <= spec.warmup_bars or len(C) <= spec.warmup_bars:
        return sym, None, None   # too thin for complete bars at this chart pair
    sig = rulecore.to_engine_signals(rulecore.compute(P, C, spec))
    # the decision is taken at the bar's CLOSE; the day it belongs to is the
    # close's day, and the bot re-picks 30 s after midnight
    close_day = ((P["time"].to_numpy("int64") + primary * 60 - 1) // DAY) * DAY
    allow = np.isin(close_day, np.fromiter(allowed_days, dtype="int64"))
    sig = replace(sig, long_entry=sig.long_entry & allow, short_entry=sig.short_entry & allow)
    cat = ProductCatalog()
    return sym, Book(symbol=sym, bars=P, signals=sig, costs=SymbolCosts.from_spec(cat.get(sym)),
                     mark=mk, tradable=tr, fill_ltp=d["ltp"], fill_mark=d["mark"]), d["funding"]


def _run_pair(job):
    primary, confirm, lev = job
    picks = pd.read_csv(OUT / "picks.csv")
    spec = build_spec(FAMILY, primary, confirm)
    books, fund, skipped = {}, {}, []
    for sym, g in picks.groupby("symbol"):
        got = _book(sym, primary, confirm, spec, set(g.pick_day.astype("int64")))
        if got and got[1] is not None:
            books[got[0]], fund[got[0]] = got[1], got[2]
        else:
            skipped.append(sym)
    params = replace(params_for(spec, primary, 72), stop_fill="ltp_close",
                     risk_percent=RISK_PCT, max_leverage=lev, max_cost_per_r=None)
    gates = replace(GATES, pause_after=lambda t: t.r_multiple < 0)
    res = run_portfolio(books, params, gates, initial_capital=EQUITY, funding=fund)
    t = pd.DataFrame([dataclasses.asdict(x) for x in res.trades])
    t.to_csv(OUT / f"trades_{primary}_{confirm}_{lev:g}x.csv", index=False)
    lost = int(picks.symbol.isin(skipped).sum())
    return job, t, dict(res.rejects), (len(books), skipped, lost)


def _summ(t: pd.DataFrame) -> dict:
    if t.empty:
        return dict(n=0)
    r = t.r_multiple.to_numpy(float)
    pnl = t.pnl.to_numpy(float) if "pnl" in t else np.full(len(t), np.nan)
    eq = EQUITY + np.cumsum(pnl)
    dd = float(np.max(np.maximum.accumulate(np.r_[EQUITY, eq])[1:] - eq)) if len(eq) else 0.0
    se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else float("nan")
    return dict(n=len(r), win=float((r > 0).mean()), meanR=float(r.mean()), seR=float(se),
                pnl=float(np.nansum(pnl)), maxdd=dd,
                max_hold_h=float(((t.exit_time - t.entry_time) / 3600).max()),
                longest_idle_d=float(np.diff(np.r_[FIRST_DAY, np.sort(t.entry_time.to_numpy()), LAST_DAY + DAY]).max() / DAY),
                hold_h=float(((t.exit_time - t.entry_time) / 3600).median()))


def cmd_run() -> None:
    # 5x is the bot's cap; 10x answers the owner's "what does 10x sound like?"
    jobs = [(p, c, lev) for p, c in PAIRS for lev in (MAX_LEV, 10.0)]
    with ProcessPoolExecutor(4) as ex:
        results = list(ex.map(_run_pair, jobs))
    lines = []
    mid = (FIRST_DAY + LAST_DAY + DAY) // 2
    for (primary, confirm, lev), t, rej, (nb, skipped, lost) in results:
        s = _summ(t)
        lines.append(f"\n{primary}m/{confirm}m at {lev:g}x  ({nb} coins; {len(skipped)} too thin, "
                     f"{lost} of {len(pd.read_csv(OUT / 'picks.csv'))} coin-days lost: {' '.join(skipped[:8])})")
        if not s["n"]:
            lines.append("  no trades"); continue
        lines.append(f"  trades {s['n']}  win {100*s['win']:.0f}%  mean {s['meanR']:+.3f}R "
                     f"(+/- {s['seR']:.3f})  P&L ${s['pnl']:+.2f} on ${EQUITY:.0f}  "
                     f"max drawdown ${s['maxdd']:.2f}  median hold {s['hold_h']:.1f}h")
        lines.append(f"  checks: longest hold {s['max_hold_h']:.0f}h (limit 72h); longest stretch with no entry "
                     f"{s['longest_idle_d']:.0f} days")
        for name, part in (("Jan-May", t[t.entry_time < mid]), ("May-Oct", t[t.entry_time >= mid])):
            p = _summ(part)
            lines.append(f"  {name}: " + (f"{p['n']} trades, {p['meanR']:+.3f}R, ${p['pnl']:+.2f}"
                                          if p["n"] else "no trades"))
        if "exit_reason" in t:
            lines.append("  exits: " + ", ".join(f"{k} {v}" for k, v in t.exit_reason.value_counts().items()))
        capped = int((t.leverage >= lev - 0.05).sum())
        lines.append(f"  trades sized down by the {lev:g}x cap: {capped}")
        lines.append("  refused: " + ", ".join(f"{k} {v}" for k, v in rej.items() if v))
    text = "\n".join(lines)
    print(text)
    (OUT / "result.txt").write_text(text + "\n")


if __name__ == "__main__":
    {"picks": cmd_picks, "fetch": cmd_fetch, "run": cmd_run}[sys.argv[1]]()

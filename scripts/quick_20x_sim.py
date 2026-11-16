"""Owner 2026-11-15: "simulate the trades with this" -- QUICK at max leverage with a stop just before liquidation.

    PYTHONPATH=. python scripts/quick_20x_sim.py

RULE. Every pick the real account's rule takes (rule A top-2 gainers, ATR stop <= 32.5% away): short with 50% of the
balance as isolated margin at the coin's max leverage (20x on these coins; Delta product specs, verified 2026-11-15:
initial margin 5%, maintenance 2.5%, liquidation = entry x (1 + 1/L - 2.5%) -- matches the liquidation prices Delta
reported on the real 1x and 2x positions). Position = margin / 5.12% (Delta reserves the fees inside the order margin:
83,682 SOPH contracts on $750, as the owner saw). TAKE-PROFIT where the profit after fees is +$500. STOP at +2.25%
(just before the +2.5% liquidation); it always fires before liquidation and fills 0.1% past its price. Else out at
24 h. Fees 0.05% a side + 0.1% spread. Balance compounds; two picks on a day use 50% each.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import fade_ladder_5x as L5  # noqa: E402
import fade_profit_ladder as PL  # noqa: E402
import fade_quick_full_replay as QR  # noqa: E402
import fade_rr12 as R12  # noqa: E402

FEE, EX = PL.FEE, PL.EXTRA
START, TARGET, SL, SLIP, MARGIN = 1640.0, 500.0, 0.0225, 0.001, 0.50
TAB = json.load(open("out/delta_margin/perp_margins_2026-11-12.json"))


def plan(sym: str, bal: float, e: float):
    im = TAB.get(sym, [5, 2.5])[0] / 100
    pos = MARGIN * bal / (im + 0.0012)
    take = TARGET / pos + 2 * FEE + EX
    return pos, e * (1 - take), e * (1 + min(SL, 0.9 * (im - 0.025) if im > 0.025 else SL)), e * (1 + im - 0.025), take


def outcome(hi, lo, cl, e, pos, tp, sl, k0=0):
    for k in range(k0, len(cl)):
        if hi[k] >= sl:
            xp = sl * (1 + SLIP)
            return "STOP", pos * (-(xp / e - 1) - FEE - FEE * xp / e - EX), k
        if lo[k] <= tp * 0.999:
            return "TARGET", TARGET, k
    xp = cl[-1]
    return "24h", pos * (-(xp / e - 1) - FEE - FEE * xp / e - EX), len(cl) - 1


def recent() -> None:
    picks = [dict(day=x["day"], symbol=x["symbol"], entry=x["entry_price"], stop=x["stop_price"])
             for x in json.load(open("out/fade_quick_full/quick_paper.json")) if x.get("entry_price")]
    picks += [dict(day="2026-11-13T00:00:00+00:00", symbol="HANAUSD", entry=0.01743, stop=0.02216),
              dict(day="2026-11-14T00:00:00+00:00", symbol="SOPHUSD", entry=0.001792, stop=0.002297)]
    picks = sorted([p for p in picks if p["stop"] / p["entry"] - 1 <= 0.325], key=lambda x: x["day"])
    cv = {}
    print(f"A) THE REAL PICKS 2026-10-29..11-14, trade by trade, from ${START:,.0f} (Delta 1m prices)\n")
    print(f"{'day':6s} {'coin':9s} {'balance':>8s} {'contracts':>10s} {'position':>9s} {'entry':>10s} {'target':>10s} {'stop':>10s} {'liq.':>10s} "
          f"{'result':>7s} {'after':>6s} {'P&L':>8s}")
    bal = START
    days = {}
    for p in picks:
        days.setdefault(p["day"], []).append(p)
    wins = stops = 0
    for day, ps in days.items():
        pnl = 0.0
        for p in ps:
            t0 = QR.ts(p["day"])
            ltp = QR.candles(p["symbol"], t0)
            e = p["entry"]
            pos, tp, sl, liq, take = plan(p["symbol"], bal, e)
            if p["symbol"] not in cv:
                import explore_delta_basis as EB
                cv[p["symbol"]] = float(EB.ProductCatalog().get(p["symbol"])["contract_value"])
            how, x, k = outcome([c["high"] for c in ltp], [c["low"] for c in ltp], [c["close"] for c in ltp], e, pos, tp, sl)
            pnl += x
            wins += how == "TARGET"
            stops += how == "STOP"
            mins = (ltp[k]["time"] - t0) // 60
            print(f"{day[5:10]:6s} {p['symbol'][:-3]:9s} {bal:8,.0f} {pos / (e * cv[p['symbol']]):10,.0f} {pos:9,.0f} {e:10.6g} {tp:10.6g} {sl:10.6g} "
                  f"{liq:10.6g} {how:>7s} {mins // 60:02d}:{mins % 60:02d}m {x:+8.0f}")
        bal = max(bal + pnl, 0)
    print(f"\n   {wins} targets, {stops} stops -> ${START:,.0f} becomes ${bal:,.0f}\n")


def history() -> None:
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    bal, rows = START, []
    for t, idx in d.groupby("date").groups.items():
        pnl = 0.0
        for i in idx:
            a = ps[i]
            hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
            lo = np.where(np.isfinite(a[1]), a[1], np.inf)
            cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
            e = cl[2]
            if not (np.isfinite(e) and np.isfinite(cl[-1])) or bal < 20:
                continue
            pos, tp, sl, liq, take = plan(d.symbol.iat[i], bal, e)
            how, x, _ = outcome(hi, lo, cl, e, pos, tp, sl, 3)
            x = max(x, -MARGIN * bal)
            pnl += x
            rows.append(dict(month=t.strftime("%Y-%m"), how=how, pnl=x, take=take))
        bal = max(bal + pnl, 0)
    df = pd.DataFrame(rows)
    print(f"B) THE FULL HISTORY, Delta picks 2025-01..2026-10, entry at the 00:03 close, from ${START:,.0f}, compounding\n")
    print(f"{'month':8s} {'start':>8s} {'trades':>6s} {'target':>6s} {'stop':>5s} {'24h':>4s} {'fall needed':>11s} {'P&L':>9s} {'end':>8s}")
    b = START
    for m in pd.period_range("2025-01", "2026-10", freq="M").strftime("%Y-%m"):
        g = df[df.month == m]
        p = g.pnl.sum() if len(g) else 0.0
        print(f"{m:8s} {b:8,.0f} {len(g):6d} {(g.how == 'TARGET').sum():6d} {(g.how == 'STOP').sum():5d} {(g.how == '24h').sum():4d} "
              f"{(100 * g['take'].median() if len(g) else 0):10.1f}% {p:+9.0f} {b + p:8,.0f}")
        b += p
    print(f"\n   all {len(df)} trades: target {100 * (df.how == 'TARGET').mean():.0f}%, stop {100 * (df.how == 'STOP').mean():.0f}% -> ${START:,.0f} becomes ${b:,.0f}")


if __name__ == "__main__":
    recent()
    history()

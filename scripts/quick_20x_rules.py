"""Owner 2026-11-15: QUICK at 20x with HARD BOT STOPS -- "when we win 3 trades in a row we stop for the month or
week; if we hit 2 SL we stop for the month or week; you have not run it on real trades yet; confirm numbers".

    PYTHONPATH=. python scripts/quick_20x_rules.py

RULE. Every pick of the real account's rule (rule A top-2, ATR stop <= 32.5%): short, 50% of the balance as isolated
margin at 20x (position = margin / 5.12%, at most $50,000 = Delta's limit for 20x), TAKE-PROFIT at +$TARGET after
fees, STOP at +2.25% (liquidation is +2.5%; the stop fires first, fills 0.1% past), else out at 24 h. Fees 0.05% a
side + 0.1% spread. The balance compounds.
HARD STOPS, per calendar week (Mon-Sun UTC) or month: after 3 TARGETS IN A ROW, or after 2 STOPS in the period, no
new trades until the next period. Trades already open on that day still finish.
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
import explore_delta_basis as EB  # noqa: E402
import fade_ladder_5x as L5  # noqa: E402
import fade_profit_ladder as PL  # noqa: E402
import fade_quick_full_replay as QR  # noqa: E402
import fade_rr12 as R12  # noqa: E402

FEE, EX = PL.FEE, PL.EXTRA
SL, SLIP = 0.0225, 0.001


def levels(bal, e, target):
    pos = min(0.5 * bal / 0.0512, 50000.0)
    take = target / pos + 2 * FEE + EX
    return pos, take, e * (1 - take), e * (1 + SL), e * 1.025


def play(hi, lo, cl, e, pos, tp, sl, target, k0):
    kt = next((k for k in range(k0, len(cl)) if lo[k] <= tp * 0.999), None)
    ks = next((k for k in range(k0, len(cl)) if hi[k] >= sl), None)
    if ks is not None and (kt is None or ks <= kt):
        xp = sl * (1 + SLIP)
        return "STOP", pos * (-(xp / e - 1) - FEE - FEE * xp / e - EX), ks, kt, ks
    if kt is not None:
        return "TARGET", float(target), kt, kt, ks
    xp = cl[-1]
    return "24h", pos * (-(xp / e - 1) - FEE - FEE * xp / e - EX), len(cl) - 1, kt, ks


def period(day: pd.Timestamp, kind: str):
    return day.strftime("%Y-%m") if kind == "month" else f"{day.isocalendar()[0]}-W{day.isocalendar()[1]:02d}"


def run(trades_by_day, start, target, kind, verbose=None):
    """trades_by_day: list of (day Timestamp, [(symbol, e, hi, lo, cl, k0, cv)]). kind: week / month / none."""
    bal, per, wins_row, stops, halted, low = start, None, 0, 0, None, start
    log = []
    for day, ts in trades_by_day:
        p = period(day, kind) if kind != "none" else "all"
        if p != per:
            per, wins_row, stops, halted = p, 0, 0, None
        if halted or bal < 100:
            for sym, *_ in ts:
                log.append((day, sym, bal, None, None, None, None, None, None, None, None, f"skipped ({halted or 'balance under $100'})", 0.0))
            continue
        res = []
        for sym, e, hi, lo, cl, k0, cv in ts:
            pos, take, tp, sl, liq = levels(bal, e, target)
            how, x, k, kt, ks = play(hi, lo, cl, e, pos, tp, sl, target, k0)
            res.append((k, sym, pos / (e * cv), pos, e, take, tp, sl, kt, ks, how, max(x, -0.5 * bal)))
        pnl = 0.0
        for k, sym, n, pos, e, take, tp, sl, kt, ks, how, x in sorted(res):
            pnl += x
            log.append((day, sym, bal, n, pos, e, take, tp, sl, kt, ks, how, x))
            if how == "TARGET":
                wins_row += 1
            else:
                wins_row = 0
            if how == "STOP":
                stops += 1
            if kind != "none" and halted is None:
                if wins_row >= 3:
                    halted = f"3 wins in a row: off until next {kind}"
                elif stops >= 2:
                    halted = f"2 stops: off until next {kind}"
        bal = max(bal + pnl, 0.0)
        low = min(low, bal)
    return bal, low, log


def real_picks():
    picks = [dict(day=x["day"], symbol=x["symbol"], entry=x["entry_price"], stop=x["stop_price"])
             for x in json.load(open("out/fade_quick_full/quick_paper.json")) if x.get("entry_price")]
    picks += [dict(day="2026-11-13T00:00:00+00:00", symbol="HANAUSD", entry=0.01743, stop=0.02216),
              dict(day="2026-11-14T00:00:00+00:00", symbol="SOPHUSD", entry=0.001792, stop=0.002297)]
    picks = sorted([p for p in picks if p["stop"] / p["entry"] - 1 <= 0.325], key=lambda x: x["day"])
    cat = EB.ProductCatalog()
    out = {}
    for p in picks:
        ltp = QR.candles(p["symbol"], QR.ts(p["day"]))
        out.setdefault(pd.Timestamp(p["day"][:10]), []).append(
            (p["symbol"], p["entry"], [c["high"] for c in ltp], [c["low"] for c in ltp], [c["close"] for c in ltp], 0,
             float(cat.get(p["symbol"])["contract_value"])))
    return sorted(out.items())


def history():
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    out = {}
    for i in range(len(d)):
        a = ps[i]
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if np.isfinite(cl[2]) and np.isfinite(cl[-1]):
            out.setdefault(d.date.iat[i], []).append((d.symbol.iat[i], cl[2], hi, lo, cl, 3, 1.0))
    return sorted(out.items())


def hm(k):
    return "  -  " if k is None else f"{k // 60:02d}:{k % 60:02d}"


def main() -> None:
    start = 3184.0
    rp = real_picks()
    print(f"A) THE REAL PICKS 2026-10-29..11-14 FROM ${start:,.0f}, WITH THE HARD STOPS (Delta 1m prices, entry at the bots' 00:00 price)\n")
    for target in (1000, 500):
        for kind in ("week", "month", "none"):
            bal, low, log = run(rp, start, target, kind)
            took = [r for r in log if r[3] is not None]
            print(f"  target ${target:,}, stops per {kind:5s}: {sum(r[11] == 'TARGET' for r in took)} targets, {sum(r[11] == 'STOP' for r in took)} stops, "
                  f"{len(log) - len(took)} skipped -> ${start:,.0f} becomes ${bal:,.0f} (lowest ${low:,.0f})")
    for target, kind in ((1000, "week"), (1000, "month")):
        bal, low, log = run(rp, start, target, kind)
        print(f"\n  TRADE BY TRADE: target ${target:,}, hard stops per {kind}")
        print(f"  {'day':10s} {'coin':9s} {'balance':>7s} {'contracts':>9s} {'position':>8s} {'entry':>10s} {'fall%':>5s} {'target':>10s} {'stop':>10s} "
              f"{'tgt hit':>7s} {'stop hit':>8s} {'result':>8s} {'P&L':>7s}")
        for day, sym, b, n, pos, e, take, tp, sl, kt, ks, how, x in log:
            if n is None:
                print(f"  {day.strftime('%a %m-%d'):10s} {sym[:-3]:9s} {b:7,.0f} {'':>9s} {'':>8s} {'':>10s} {'':>5s} {'':>10s} {'':>10s} {'':>7s} {'':>8s}   {how}")
            else:
                print(f"  {day.strftime('%a %m-%d'):10s} {sym[:-3]:9s} {b:7,.0f} {n:9,.0f} {pos:8,.0f} {e:10.6g} {100 * take:5.2f} {tp:10.6g} {sl:10.6g} "
                      f"{hm(kt):>7s} {hm(ks):>8s} {how:>8s} {x:+7.0f}")
        print(f"  -> ${bal:,.0f}")
    hist = history()
    print(f"\nB) THE FULL HISTORY (Delta picks 2025-01..2026-10, entry at the 00:03 close) FROM ${start:,.0f}, compounding\n")
    print(f"  {'target':>7s} {'hard stops':>10s} | {'from Jan 2025: end':>18s} {'lowest':>7s} {'trades':>6s} {'hit':>4s} | started each month (21 starts): "
          f"{'account under $500':>18s} {'above start after 3 mo':>22s} {'median after 3 mo':>17s}")
    for target in (500, 1000, 1500):
        for kind in ("none", "week", "month"):
            bal, low, log = run(hist, start, target, kind)
            took = [r for r in log if r[3] is not None]
            hit = np.mean([r[11] == "TARGET" for r in took]) if took else 0
            gone = up3 = 0
            b3s = []
            for m in pd.period_range("2025-02", "2026-10", freq="M"):
                s = m.to_timestamp()
                sub = [x for x in hist if x[0] >= s]
                b_all, low_all, _ = run(sub, start, target, kind)
                b3, low3, _ = run([x for x in sub if x[0] < s + pd.Timedelta(days=90)], start, target, kind)
                gone += low_all < 500
                up3 += b3 > start
                b3s.append(b3)
            print(f"  {target:7,d} {kind:>10s} | {bal:18,.0f} {low:7,.0f} {len(took):6d} {100 * hit:3.0f}% | {'':31s} {gone:8d} of 21 {up3:15d} of 21 {np.median(b3s):17,.0f}")
    for target, kind in ((1000, "month"),):
        bal, low, log = run(hist, start, target, kind)
        df = pd.DataFrame([(r[0].strftime("%Y-%m"), r[11], r[12], r[3] is not None) for r in log], columns=["m", "how", "x", "took"])
        print(f"\n  MONTH BY MONTH: target ${target:,}, hard stops per {kind}, from Jan 2025")
        print(f"  {'month':8s} {'start':>7s} {'taken':>5s} {'targets':>7s} {'stops':>5s} {'skipped':>7s} {'P&L':>8s} {'end':>7s}")
        b = start
        for m in sorted(df.m.unique()):
            g = df[df.m == m]
            t = g[g.took]
            print(f"  {m:8s} {b:7,.0f} {len(t):5d} {(t.how == 'TARGET').sum():7d} {(t.how == 'STOP').sum():5d} {len(g) - len(t):7d} {t.x.sum():+8,.0f} {b + t.x.sum():7,.0f}")
            b += t.x.sum()


if __name__ == "__main__":
    main()

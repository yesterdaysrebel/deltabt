"""Spike Sell AS BUILT, but the exchange stop (and Delta's liquidation) triggered on the MARK price instead of the
last traded price (owner 2026-11-15, the stop-loss problem: 86% of stops are back under the stop within 30 min --
are they wicks on Delta's own book that the mark price never sees?). Delta's own MARK 1m candles (out/careful/mark.npz).
  touch-LTP  as built: a 1m LTP high >= the stop
  touch-MARK a 1m MARK high >= the stop -> market buy, filled on the LTP: worse of the stop and that minute's LTP close
             (+0.1%) STRICT; the stop +0.1% LENIENT. Liquidation when the MARK high >= the liquidation price.
The bot-managed lock / trail are unchanged (LTP lows and closes); after the lock the stop order is also on MARK.
Sanity: with the LTP arrays passed as 'mark' the function must reproduce implementation B exactly (asserted).

    PYTHONPATH=. python scripts/careful_mark_stop.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402


def trade(sym, H, L, Cl, MH, le):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    cap = margin + extra
    pl = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    T, K, Q = e * (1 - (500 / n + 0.0012)), e * (1 - (200 / n + 0.0012)), e * (1 + ld)
    stop, locked, low = e * 1.0225, False, e
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if MH[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            if not locked and (x >= Q if MH is H else MH[j] >= Q):
                return "LIQ", -cap, k, j
            return ("LOCKED" if locked else "STOP"), pl(x), k, j
        if L[j] <= T * 0.999:
            return "TARGET", pl(T), k, j
        low = min(low, L[j])
        if not locked and L[j] <= 0.99 * e:
            locked, stop = True, min(stop, K)
            if Cl[j] >= stop:
                return "LOCKED", pl(Cl[j] * 1.001), k, j
        if locked:
            stop = min(stop, 1.008 * low)
            if Cl[j] >= stop:
                return "LOCKED", pl(Cl[j] * 1.001), k, j
    return "24h", pl(Cl[last] * 1.001), k, last


def main():
    P = pd.read_csv("out/careful/picks.csv", parse_dates=["day"])
    z, mk = np.load("out/careful/dataset.npz"), np.load("out/careful/mark.npz")
    rows = {False: [], True: []}
    nomark = 0
    for i, x in enumerate(P.itertuples()):
        H, L, Cl = z["hi"][i], z["lo"][i], z["cl"][i]
        MH = pd.Series(mk["hi"][i]).ffill().to_numpy()
        if not np.isfinite(mk["cl"][i]).any():
            nomark += 1
            continue
        MH = np.where(np.isfinite(MH), MH, -np.inf)
        for le in (False, True):
            b = B.first_trade(x.symbol, H, L, Cl, D.START, le)
            a = trade(x.symbol, H, L, Cl, H, le)
            assert (a is None) == (b is None) and (a is None or (a[0] == b["how"].replace("TIME", "24h") and abs(a[1] - b["pnl"]) < 0.01)), (x.day, x.symbol)
            if a is None:
                continue
            m = trade(x.symbol, H, L, Cl, MH, le)
            rows[le].append((x.day, a[0], a[1], m[0], m[1]))
    print(f"SANITY PASSED: with LTP as the trigger the function equals implementation B on every trade. Picks without mark candles: {nomark}\n")
    print("Spike Sell as built, per pick at a fixed $3,184: stop on LAST TRADED price vs on MARK price")
    for le in (False, True):
        d = pd.DataFrame(rows[le], columns=["day", "how_l", "pnl_l", "how_m", "pnl_m"])
        for nm, g in (("2025", d[d.day < "2026"]), ("2026 + real", d[d.day >= "2026"]), ("all", d)):
            sl, sm = g.how_l.isin(["STOP", "LIQ"]), g.how_m.isin(["STOP", "LIQ"])
            print(f"  {'LENIENT' if le else 'STRICT '} {nm:11s} {len(g):3d} trades | LTP: avg {g.pnl_l.mean():+6.0f}, stops {sl.mean():4.0%} (liq {(g.how_l == 'LIQ').sum()}), avg stop {g.pnl_l[sl].mean():+6.0f} | "
                  f"MARK: avg {g.pnl_m.mean():+6.0f}, stops {sm.mean():4.0%} (liq {(g.how_m == 'LIQ').sum()}), avg stop {g.pnl_m[sm].mean():+6.0f} | "
                  f"targets {int((g.how_l == 'TARGET').sum())} -> {int((g.how_m == 'TARGET').sum())}")
    d = pd.DataFrame(rows[False], columns=["day", "how_l", "pnl_l", "how_m", "pnl_m"])
    sv = d[d.how_l.isin(["STOP", "LIQ"]) & ~d.how_m.isin(["STOP", "LIQ"])]
    print(f"\n  LTP stops that the MARK trigger avoided (strict): {len(sv)} of {int(d.how_l.isin(['STOP', 'LIQ']).sum())}; "
          f"they became {dict(sv.how_m.value_counts())}, P&L {sv.pnl_l.sum():+,.0f} -> {sv.pnl_m.sum():+,.0f}")
    diff = (d.pnl_m - d.pnl_l)
    print(f"  paired difference MARK - LTP per trade: mean {diff.mean():+.0f}, t {diff.mean() / (diff.std() / np.sqrt(len(diff))):+.1f}; "
          f"2025 {diff[d.day < '2026'].mean():+.0f}, 2026 {diff[d.day >= '2026'].mean():+.0f}")


if __name__ == "__main__":
    main()

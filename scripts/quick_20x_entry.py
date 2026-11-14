"""Owner 2026-11-15: "you see the pattern, find a solution, the margin and leverage won't change". 20x, 50% of the
balance as margin, stop +2.25% from the fill (before the +2.5% liquidation), take +$TARGET after fees -- ONLY THE
ENTRY changes, because the losers are the picks that spike 2.25% before falling:
  SELL THE SPIKE  a limit sell X% above the 00:03 price, valid until 24 h after 00:00; filled when the high trades
                  0.05% through it (maker fill: fee 0.02%, no spread on entry). Not filled = no trade.
  LATER ENTRY     sell at the close H hours after 00:00 (taker), the opening spike already behind.
Each trade alone on a fixed $3,184 balance (Delta per-coin limits), stop and target from the fill; out 24 h after
the fill at the latest. A stop and a target in the same minute = the stop; the fill minute counts for the stop.

    PYTHONPATH=. python scripts/quick_20x_entry.py
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
import fade_rr12 as R12  # noqa: E402
import quick_20x_rules as Q  # noqa: E402

FEE, EX, MAKER = PL.FEE, PL.EXTRA, 0.0002
LIM = json.load(open("out/delta_margin/product_limits_2026-11-15.json"))
DEF = dict(cv=1.0, limit=10 ** 12, max_lev_notional=50000.0, im=5.0, mm=2.5, ims=1e-05, mms=5e-06)
BAL, MG, SL = 3184.0, 0.50, 0.0225


def size(sym, margin, e):
    p = LIM.get(sym, DEF)
    lo_, hi_ = 0.0, margin / ((p["im"] + 0.12) / 100)
    for _ in range(50):
        n = (lo_ + hi_) / 2
        im = p["im"] + p["ims"] * max(0.0, n - p["max_lev_notional"])
        lo_, hi_ = (n, hi_) if n * (im + 0.12) / 100 <= margin else (lo_, n)
    return min(lo_, p["limit"] * p["cv"] * e)


def trade(sym, hi, lo, cl, k0, target, mode, x):
    """mode 'limit': sell x above cl[k0-1]; 'later': sell at the close of minute x*60. Returns (how, pnl, fill_min)."""
    n = len(cl)
    if mode == "limit":
        ref = cl[k0 - 1] if k0 > 0 else cl[0]
        lim = ref * (1 + x)
        kf = next((k for k in range(k0, min(n, 1440)) if hi[k] >= lim * 1.0005), None)
        if kf is None:
            return "none", 0.0, None
        e, fee_in, slip, k1 = lim, MAKER, EX / 2, kf
    else:
        kf = int(x * 60)
        if kf >= n or not np.isfinite(cl[kf]):
            return "none", 0.0, None
        e, fee_in, slip, k1 = cl[kf], FEE, EX, kf + 1
    pos = size(sym, MG * BAL, e)
    take = target / pos + fee_in + FEE + slip
    tp, sp = e * (1 - take), e * (1 + SL)
    end = min(n - 1, kf + 1440)
    for k in range(k1 if mode == "later" else kf, end + 1):
        if hi[k] >= sp and (k > kf or mode == "limit" and hi[k] >= sp):
            if mode == "limit" and k == kf and hi[k] < sp:
                continue
            xp = sp * 1.001
            return "STOP", pos * (-(xp / e - 1) - fee_in - FEE * xp / e - slip), kf
        if k > kf and lo[k] <= tp * 0.999:
            return "TARGET", float(target), kf
    xp = cl[end]
    return "24h", pos * (-(xp / e - 1) - fee_in - FEE * xp / e - slip), kf


def history_paths():
    d = L5.trades().reset_index(drop=True)
    opens, ps = R12.paths(d)
    out = []
    for i in range(len(d)):
        a = ps[i]
        hi = np.where(np.isfinite(a[0]), a[0], -np.inf)
        lo = np.where(np.isfinite(a[1]), a[1], np.inf)
        cl = pd.Series(a[2].astype(float)).ffill().to_numpy()
        if np.isfinite(cl[2]) and np.isfinite(cl[-1]):
            out.append((d.date.iat[i], d.symbol.iat[i], hi, lo, cl, 3))
    return out


def real_paths():
    out = []
    for day, ts in Q.real_picks():
        for sym, e, hi, lo, cl, k0, cv in ts:
            out.append((day, sym, np.array(hi, float), np.array(lo, float), np.array(cl, float), 3))
    return out


def table(paths, label):
    print(label)
    print(f"  {'entry':34s} {'target':>6s} | {'filled':>6s} {'target':>6s} {'stop':>5s} {'24h':>4s} | {'win':>4s} {'avg/trade':>9s} {'total':>9s} "
          f"| {'2025':>8s} {'2026':>8s} {'green mo':>8s}")
    rows = [("as today: sell at 00:03", "later", 0.05)]
    rows += [(f"sell the spike: limit +{int(100 * x)}%", "limit", x) for x in (0.01, 0.02, 0.03, 0.05, 0.08)]
    rows += [(f"later entry: sell at {h:02d}:00", "later", h) for h in (1, 2, 4, 8)]
    for name, mode, x in rows:
        for target in (300, 500):
            res = [(day, trade(sym, hi, lo, cl, k0, target, mode, x)) for day, sym, hi, lo, cl, k0 in paths]
            tk = [(d, r) for d, r in res if r[0] != "none"]
            if not tk:
                continue
            v = np.array([r[1] for _, r in tk])
            h = np.array([r[0] for _, r in tk])
            y = np.array([d.year for d, _ in tk])
            m = pd.Series(v).groupby([d.strftime("%Y-%m") for d, _ in tk]).sum()
            print(f"  {name:34s} {target:6d} | {len(tk):6d} {100 * (h == 'TARGET').mean():5.0f}% {100 * (h == 'STOP').mean():4.0f}% "
                  f"{100 * (h == '24h').mean():3.0f}% | {100 * (v > 0).mean():3.0f}% {v.mean():+9.1f} {v.sum():+9,.0f} | "
                  f"{v[y == 2025].sum():+8,.0f} {v[y == 2026].sum():+8,.0f} {(m > 0).sum():>4d}/{len(m):<3d}")
    print()


def main() -> None:
    print(f"ONLY THE ENTRY CHANGES: 20x, {int(100 * MG)}% of ${BAL:,.0f} as margin, stop +{100 * SL:.2f}% from the fill, take +$300 / +$500\n")
    table(real_paths(), "A) THE 20 REAL PICKS 2026-10-29..11-14 (Delta 1m)")
    table(history_paths(), "B) FULL HISTORY: 1,022 Delta picks 2025-01..2026-10")


if __name__ == "__main__":
    main()

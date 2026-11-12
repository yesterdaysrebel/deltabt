"""QUICK with a TIGHT fixed stop at full leverage (owner 2026-11-12: "running quick with full leverage and big
position and stop at 2%, 4%").

    PYTHONPATH=. python scripts/fade_tight_stops.py

Rule A picks on Binance 2021-26 (scripts/fade_owner_exits.load). Each day's two picks each get HALF the balance as
margin; notional = leverage x margin. Per-coin max leverage and liquidation (1/L - maintenance) from Delta's table
(scripts/fade_quick_leverage.margins). Stop 2% or 4% above entry; target QUICK-style 0.2 x the stop, or 0.5x, 1x,
2x the stop; else out at 24 h. Liquidation first in a tie, then stop, then target. Liquidation loses the margin.
Two fill sets:
  ideal      enter at the 00:00 open, target fills on a touch, no spread (what made the 2% result look good)
  realistic  enter at the 00:03 close (the bot's real time), target must trade 0.1% through, stop fills at the
             worse of the stop and that minute's close, 0.1% spread round trip, fee 0.05% a side
Open exploration; 2 stops x 4 targets x 4 leverage levels.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import fade_owner_exits as X  # noqa: E402
import fade_quick_leverage as Q  # noqa: E402

T, FEE = X.T, X.FEE


def run(p, hi, lo, cl, im, mm, lev, stop, tk, through, extra):
    n = len(p)
    entry = p.open.to_numpy()
    L = 1 / im if lev == "full" else np.minimum(float(lev), 1 / im)
    liq = 1 / L - mm
    cand = [(X.first(hi >= (entry * (1 + liq))[:, None]), entry * (1 + liq), 0)]
    sp = entry * (1 + stop)
    ms = X.first(hi >= sp[:, None])
    cand.append((ms, np.maximum(sp, cl[np.arange(n), np.minimum(ms, T - 1)]), 1))
    tp = entry * (1 - tk * stop)
    cand.append((X.first(lo <= (tp * (1 - through))[:, None]), tp, 2))
    cand.append((np.full(n, T - 1), cl[:, T - 1], 3))
    M = np.stack([c[0] for c in cand]) * 10 + np.array([c[2] for c in cand])[:, None]
    k = M.argmin(axis=0)
    xp = np.stack([c[1] for c in cand])[k, np.arange(n)]
    ret = -(xp / entry - 1) - FEE - FEE * xp / entry - extra
    rb = np.where(k == 0, -0.5, np.maximum(0.5 * L * ret, -0.5))
    return rb, k, L


def main() -> None:
    p0, hi0, lo0, cl = X.load()
    im0, mm0, _ = Q.margins(p0)
    for setname, e, thr, ex in (("IDEAL fills", 0, 0.0, 0.0), ("REALISTIC fills", 3, 0.001, 0.001)):
        if e:
            p = p0.copy()
            p["open"] = cl[:, e - 1]
            hi, lo = hi0.copy(), lo0.copy()
            hi[:, :e], lo[:, :e] = -np.inf, np.inf
        else:
            p, hi, lo = p0, hi0, lo0
        ok = np.isfinite(p.open.to_numpy()) & (p.open.to_numpy() > 0)
        p, hi, lo, c, im, mm = p[ok].reset_index(drop=True), hi[ok], lo[ok], cl[ok], im0[ok], mm0[ok]
        print(f"\n{setname}: $1,500 -> end | worst drop | how trades end (liquidated / stopped / target / 24 h) | win | mean "
              f"per trade as % of balance | years up of 6")
        for stop in (0.02, 0.04):
            for tk in (0.2, 0.5, 1.0, 2.0):
                print(f"  stop {100 * stop:.0f}%  target {100 * tk * stop:.1f}% ({tk}x the stop)")
                for lev in (2, 5, 10, "full"):
                    rb, k, L = run(p, hi, lo, c, im, mm, lev, stop, tk, thr, ex)
                    s, dd = Q.account(p, rb)
                    ye = s.groupby(s.index.year).last()
                    up = int((ye.to_numpy() > np.r_[1500.0, ye.to_numpy()[:-1]]).sum())
                    sh = [100 * (k == i).mean() for i in range(4)]
                    lv = f"{lev}x" if lev != "full" else f"full ({int(np.median(L))}x)"
                    print(f"    {lv:>11s} -> ${s.iloc[-1]:>10,.0f}  drop {100 * dd:3.0f}%  "
                          f"{sh[0]:4.1f}/{sh[1]:4.1f}/{sh[2]:4.1f}/{sh[3]:4.1f}%  win {100 * (rb > 0).mean():3.0f}%  "
                          f"mean {100 * rb.mean():+6.2f}%  up {up}/6  (stop costs {100 * 0.5 * float(np.median(L)) * stop:.0f}% of balance)")


if __name__ == "__main__":
    main()

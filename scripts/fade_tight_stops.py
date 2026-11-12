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


def run(p, hi, lo, cl, im, mm, lev, stop, tk, through, extra, margin=0.5, safe=False):
    n = len(p)
    entry = p.open.to_numpy()
    L = 1 / im if lev == "full" else np.minimum(float(lev), 1 / im)
    if safe:                                                         # liquidation >= 2x the stop away
        L = np.maximum(1, np.minimum(1 / im, np.floor(1 / (2 * stop + mm))))
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
    rb = np.where(k == 0, -margin, np.maximum(margin * L * ret, -margin))
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


def safe25() -> None:
    """Owner 2026-11-12 "we can use tight SL? avoid liquidations": fixed stops, the most leverage that keeps
    liquidation >= 2x the stop away, 25% of capital as margin per pick; the cells of the paper-pick replay
    (scripts/fade_quick_full_replay.py --tight) on six years."""
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
        yrs = p.year.to_numpy()
        print(f"\n{setname}: 25% margin per pick, liquidation >= 2x the stop away, from $1,500")
        for stop in (0.01, 0.02, 0.04, 0.08):
            for tk in (0.2, 1.0):
                rb, k, L = run(p, hi, lo, c, im, mm, 1, stop, tk, thr, ex, margin=0.25, safe=True)
                s, dd = Q.account(p, rb)
                ye = s.groupby(s.index.year).last()
                up = int((ye.to_numpy() > np.r_[1500.0, ye.to_numpy()[:-1]]).sum())
                yr = " ".join(f"{y[2:]} {100 * rb[yrs == y].mean():+.2f}%" for y in X.YEARS)
                print(f"  stop {100 * stop:3.0f}% target {100 * tk * stop:4.1f}% ({int(np.median(L)):2d}x) -> ${s.iloc[-1]:>9,.0f}  "
                      f"drop {100 * dd:3.0f}%  liquidated {100 * (k == 0).mean():4.1f}%  win {100 * (rb > 0).mean():3.0f}%  "
                      f"years up {up}/6 | mean per trade by year (% of balance): {yr}")


def dollar_target() -> None:
    """Owner 2026-11-12: "more leverage will allow more contracts and target will reach quickly, like scalping maybe
    300-400$ target profit", "cut losses and no liquidations". On a ~$1,640 wallet with 25% ($410) as margin: target =
    a fixed share of the balance ($100/$200/$300/$400 = 6/12/18/24%), leverage L (capped at the coin's max), stop = the
    WIDEST that keeps liquidation 2x away: (1/L - maintenance) / 2. Compounds, so the $ target grows with the wallet."""
    p0, hi0, lo0, cl = X.load()
    im0, mm0, _ = Q.margins(p0)
    wallet = 1640.0
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
        print(f"\n{setname}: $ target per trade on a $1,640 wallet, 25% margin, widest safe stop, from $1,500 (six years)")
        for lev in (5, 10, 20):
            Lc = np.minimum(float(lev), 1 / im)
            stop = (1 / Lc - mm) / 2
            for usd in (100, 200, 300, 400):
                tgt = (usd / wallet) / (0.25 * Lc)                          # price move that earns $usd
                rb, k, L = run(p, hi, lo, c, im, mm, lev, stop, tgt / stop, thr, ex, margin=0.25)
                s, dd = Q.account(p, rb)
                sh = [100 * (k == i).mean() for i in range(4)]
                ye = s.groupby(s.index.year).last()
                up = int((ye.to_numpy() > np.r_[1500.0, ye.to_numpy()[:-1]]).sum())
                print(f"  {lev:2d}x  target ${usd} = {100 * np.median(tgt):4.1f}% move, stop {100 * np.median(stop):4.2f}% "
                      f"(-${wallet * 0.25 * lev * np.median(stop):.0f}) -> ${s.iloc[-1]:>8,.0f}  drop {100 * dd:3.0f}%  "
                      f"liq/stop/target/24h {sh[0]:.1f}/{sh[1]:.0f}/{sh[2]:.0f}/{sh[3]:.0f}%  years up {up}/6")


if __name__ == "__main__":
    dollar_target() if "--usd" in sys.argv else safe25() if "--safe25" in sys.argv else main()

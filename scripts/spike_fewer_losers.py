"""Owner 2026-11-17 after the first live stop (CROSS filled 05:02:51, stopped 27 s later, then fell): "70% losers is a
huge number, this is what I was asking to fix". Same picks, same 5% trail and 24 h clock as live, STRICT fills (stop at
the worse of stop and that minute's close, +0.1%), before fees. Two ways to fewer losers:
  WIDE   live entry (limit 5% above the 00:02 close), stop 2.25 / 3 / 4 / 5 / 7 / 10% above the entry
  TURN   wait for +5%, then sell at a 1m close that is X below the highest high since; stop S above that high
Per trade in % of the position, and in % of the account at the live risk: every stop costs 9% of the account
(live: 2.25% stop x 4x notional), so a wider stop means a smaller position.
    PYTHONPATH=. python scripts/spike_fewer_losers.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

TRAIL, RISK = 0.05, 0.09


def manage(H, L, Cl, k, e, sp):
    """short from the close of minute k at e, stop sp; trail TRAIL above the lowest low; out 24 h later"""
    last, low = min(len(Cl) - 1, k + 1440), np.inf
    for j in range(k + 1, last + 1):
        if not np.isfinite(H[j]):
            continue
        if H[j] >= sp:
            return -(max(sp, Cl[j]) * 1.001 / e - 1), sp
        low = min(low, L[j])
        sp = min(sp, low * (1 + TRAIL))
    c = Cl[last] if np.isfinite(Cl[last]) else Cl[np.isfinite(Cl)][-1]
    return -(c * 1.0005 / e - 1), sp


def wide(H, L, Cl, stop):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if not f.size:
        return None
    k = 3 + int(f[0])
    sp = e * (1 + stop)
    if H[k] >= sp:                                   # the fill minute itself ran through the stop
        return -(max(sp, Cl[k]) * 1.001 / e - 1), stop
    return manage(H, L, Cl, k, e, sp)[0], stop


def turn(H, L, Cl, x, s):
    trig = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= trig)
    if not f.size:
        return None
    hi = 0.0
    for k in range(3 + int(f[0]), 1440):
        if not np.isfinite(H[k]):
            continue
        hi = max(hi, H[k])
        if Cl[k] <= hi * (1 - x):
            e = Cl[k] * 0.9995
            sp = hi * (1 + s)
            return manage(H, L, Cl, k, e, sp)[0], sp / e - 1
    return None


def line(nm, rows):
    r = np.array([a for a, _ in rows]); dist = np.array([b for _, b in rows])
    acct = r * RISK / np.maximum(dist, 1e-9)                       # % of account at 9% per full stop
    w = r > 0
    return (f"{nm:34} {len(r):4d}  win {100 * w.mean():3.0f}%  avg win {100 * r[w].mean():+6.2f}%  avg loss {100 * r[~w].mean():+6.2f}%  "
            f"mean {100 * r.mean():+6.2f}%  | account/trade {100 * acct.mean():+5.2f}%  (stop {100 * np.median(dist):.1f}% median)")


def main():
    picks = [p for _, ps in D.load() for p in ps]
    out = [f"FEWER LOSERS? {len(picks)} picks 2025-01..2026-11, STRICT, before fees. % of the position; then % of the account if every full stop costs 9%."]
    for stop in (0.0225, 0.03, 0.04, 0.05, 0.07, 0.10):
        out.append(line(f"WIDE stop {stop:.2%}" + ("  (LIVE)" if stop == 0.0225 else ""), [t for p in picks if (t := wide(p[2], p[3], p[4], stop))]))
    for x in (0.005, 0.01, 0.02, 0.03):
        for s in (0.01, 0.0225, 0.05):
            out.append(line(f"TURN {x:.1%} off high, stop {s:.2%} > high", [t for p in picks if (t := turn(p[2], p[3], p[4], x, s))]))
    txt = "\n".join(out); print(txt); Path("out/careful/spike_fewer_losers.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()

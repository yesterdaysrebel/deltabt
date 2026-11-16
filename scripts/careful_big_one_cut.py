"""Owner 2026-11-15: "How to cut them before they hit stop or liquidation" -- the 75% margin / +$1,000 trade
(scripts/careful_big_one.py) with an EARLY CUT, per top pick at a fixed $3,184; 2025 vs 2026 + real.
  stop s        a tighter exchange stop (1.0 / 1.5 / 2.25%)
  cut-close x   the exchange stop stays at 2.25%; the bot buys back at the first 1m CLOSE x or more above the entry
  cut-time u    the exchange stop stays at 2.25%; the bot buys back at the first 1m close above the entry after u minutes
Fills: a stop STRICT (worse of it and the close, +0.1%) / LENIENT (the stop +0.1%); a cut at the close +0.1%.
Two implementations (loop / vectorised) must agree.

    PYTHONPATH=. python scripts/careful_big_one_cut.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_big_one as BO  # noqa: E402
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402
import careful_sim_c as Cc  # noqa: E402


def c1(sym, H, L, Cl, le, s=0.0225, x=None, u=None):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    m, xm = 0.75 * D.START, 0.20 * D.START
    n, ld = B.sizing(sym, m, e, xm)
    cap, Q = m + xm, e * (1 + ld)
    pl = lambda p: max(n * (-(p / e - 1) - 0.0007 - 0.0005 * p / e), -cap)  # noqa: E731
    tp, sp = e * (1 - (1000 / n + 0.0012)), e * (1 + s)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= sp:
            p = sp * 1.001 if le else max(sp, Cl[j]) * 1.001
            return ("LIQ", -cap) if p >= Q else ("STOP", pl(p))
        if L[j] <= tp * 0.999:
            return "TARGET", pl(tp)
        if x is not None and Cl[j] >= e * (1 + x):
            return "CUT", pl(Cl[j] * 1.001)
        if u is not None and j - k >= u and Cl[j] > e:
            return "CUT", pl(Cl[j] * 1.001)
    return "24h", pl(Cl[last] * 1.001)


def c2(sym, H, L, Cl, le, s=0.0225, x=None, u=None):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    m, xm = 0.75 * D.START, 0.20 * D.START
    n, liq = Cc.notional_for(sym, m, e)
    Q = e * (1 + liq + xm / n)
    last = min(2880, k + 1440)
    idx = np.arange(k + 1, last + 1)
    h, l, c = H[idx], L[idx], Cl[idx]
    tp, sp = e * (1 - (1000 / n + 0.0012)), e * (1 + s)
    ev = {"STOP": h >= sp, "TARGET": l <= tp * 0.999,
          "CUT": (c >= e * (1 + x) if x is not None else np.zeros(len(c), bool)) | ((idx - k >= u) & (c > e) if u is not None else np.zeros(len(c), bool))}
    first = {kk: (int(np.flatnonzero(v)[0]) if v.any() else 10 ** 9) for kk, v in ev.items()}
    i = min(first.values())
    net = lambda p: max(n * ((e - p) / e - 0.0007 - 0.0005 * p / e), -(m + xm))  # noqa: E731
    if i == 10 ** 9:
        return "24h", net(Cl[last] * 1.001)
    if first["STOP"] == i:
        p = sp * 1.001 if le else max(sp, c[i]) * 1.001
        return ("LIQ", -(m + xm)) if p >= Q else ("STOP", net(p))
    if first["TARGET"] == i:
        return "TARGET", net(tp)
    return "CUT", net(c[i] * 1.001)


GRID = [("stop 2.25% (as tested)", dict())] + [(f"stop {s:.1%}", dict(s=s)) for s in (0.01, 0.015)] + \
       [(f"cut on a close {x:.1%} against", dict(x=x)) for x in (0.005, 0.01, 0.015)] + \
       [(f"cut if losing after {u} min", dict(u=u)) for u in (2, 5, 10, 30)]


def main():
    picks = [(d, p[0][1], p[0][2], p[0][3], p[0][4]) for d, p in D.load()]
    print("75% margin, +$1,000 target, top pick only. Average $ per trade at a fixed $3,184, STRICT .. LENIENT.")
    print(f"   {'early cut':34s} | {'2025':>15s} | {'2026 + real':>15s} | all strict: +$1,000 reached, cut/stopped, liquidated | avg loss")
    for name, kw in GRID:
        out = {}
        for le in (False, True):
            rows = []
            for d, s, H, L, Cl in picks:
                a, b = c1(s, H, L, Cl, le, **kw), c2(s, H, L, Cl, le, **kw)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and abs(a[1] - b[1]) < 0.01)), (name, d, s, a, b)
                if a:
                    rows.append((d, a[0], a[1]))
            out[le] = pd.DataFrame(rows, columns=["day", "how", "pnl"])
        s_, l_ = out[False], out[True]
        bad = s_[s_.pnl <= 0]
        print(f"   {name:34s} | {s_[s_.day < '2026'].pnl.mean():+6.0f} .. {l_[l_.day < '2026'].pnl.mean():+5.0f} | "
              f"{s_[s_.day >= '2026'].pnl.mean():+6.0f} .. {l_[l_.day >= '2026'].pnl.mean():+5.0f} | "
              f"{(s_.how == 'TARGET').mean():5.0%}, {s_.how.isin(['STOP', 'CUT']).mean():5.0%}, {(s_.how == 'LIQ').sum():3d}                   | ${bad.pnl.mean():+,.0f}")
    print("\n   two implementations agreed on every trade of every version.")


if __name__ == "__main__":
    main()

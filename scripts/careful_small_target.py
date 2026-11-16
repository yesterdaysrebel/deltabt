"""Owner 2026-11-15: "did you check if before turning against us the trade goes in favour? if yes, can we book small
profit at that point instead of the $500 hard target -- on each trade". Spike Sell entry and size as built (limit
+5%, 49% at up to 20x = ~$30.5k, +25% extra margin); careful dataset (Delta 1m, 48 h).
  1. How far does the trade go IN FAVOUR before the stop (+2.25%) is hit, within 24 h?
  2. Book a small profit: take-profit netting $T after costs (T = 25..500), stop S above the entry, nothing else
     (no lock, no trail), out after 24 h. Fill models:
       STRICT   target fills when a 1m low trades 0.1% THROUGH it; stop at the worse of its price and the close +0.1%
       LENIENT  target fills 0.1% through;                          stop at its price +0.1%
       BEST     target fills when a 1m low only TOUCHES it;         stop at its price +0.1%   (the most generous)
     Stop and target in the same minute = stop. Per pick at a fixed $3,184; 2025 vs 2026 + real.
  Two implementations (loop / vectorised) must agree.

    PYTHONPATH=. python scripts/careful_small_target.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402
import careful_sim_b as B  # noqa: E402

MODES = {"STRICT": (0.999, False), "LENIENT": (0.999, True), "BEST": (1.0, True)}


def s1(sym, H, L, Cl, mode, T, S):
    thru, le = MODES[mode]
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    if k is None:
        return None
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    cap = margin + extra
    pl = lambda x: max(n * (-(x / e - 1) - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    tp, stop, Q = e * (1 - (T / n + 0.0012)), e * (1 + S), e * (1 + ld)
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if H[j] >= stop:
            x = stop * 1.001 if le else max(stop, Cl[j]) * 1.001
            return ("LIQ", -cap, k, j) if x >= Q else ("STOP", pl(x), k, j)
        if L[j] <= tp * thru:
            return "TARGET", pl(tp), k, j
    return "24h", pl(Cl[last] * 1.001), k, last


def s2(sym, H, L, Cl, mode, T, S):
    thru, le = MODES[mode]
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    margin, extra = 0.49 * D.START, 0.25 * D.START
    n, ld = B.sizing(sym, margin, e, extra)
    cap = margin + extra
    tp, stop = e * (1 - (T / n + 0.0012)), e * (1 + S)
    last = min(2880, k + 1440)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    si, ti = np.flatnonzero(h >= stop), np.flatnonzero(l <= tp * thru)
    a = int(si[0]) if si.size else 10 ** 9
    b = int(ti[0]) if ti.size else 10 ** 9
    net = lambda x: max(n * ((e - x) / e - 0.0007 - 0.0005 * x / e), -cap)  # noqa: E731
    if a <= b and a < 10 ** 9:
        x = stop * 1.001 if le else max(stop, c[a]) * 1.001
        return ("LIQ", -cap, k, k + 1 + a) if x >= e * (1 + ld) else ("STOP", net(x), k, k + 1 + a)
    if b < 10 ** 9:
        return "TARGET", net(tp), k, k + 1 + b
    return "24h", net(Cl[last] * 1.001), k, last


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    # 1. favourable move before the stop
    mfe, stopped, mins = [], [], []
    for d, s, H, L, Cl in picks:
        e = Cl[2] * 1.05
        k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
        if k is None:
            continue
        last = min(2880, k + 1440)
        h, l = H[k + 1:last + 1], L[k + 1:last + 1]
        si = np.flatnonzero(h >= e * 1.0225)
        end = int(si[0]) if si.size else len(h)          # the stop minute itself is excluded (order unknown -> stop first)
        lows = l[:end]
        best = 1 - lows[np.isfinite(lows)].min() / e if np.isfinite(lows).any() else 0.0
        mfe.append((d, max(best, 0.0), bool(si.size), end))
    m = pd.DataFrame(mfe, columns=["day", "best", "stopped", "mins"])
    n30k = 30472
    print(f"1. BEFORE THE +2.25% STOP IS HIT (or 24 h), how far did the price go in our favour? ({len(m)} trades)")
    print(f"   {'fall of at least':>17s} | {'all trades':>10s} | {'2025':>5s} | {'2026':>5s} | {'trades that later STOPPED':>25s} | net $ if booked there (~$30.5k position)")
    for x in (0.001, 0.002, 0.003, 0.005, 0.0075, 0.01, 0.015, 0.018):
        st = m[m.stopped]
        print(f"   {x:17.2%} | {(m.best >= x).mean():10.0%} | {(m[m.day < '2026'].best >= x).mean():5.0%} | {(m[m.day >= '2026'].best >= x).mean():5.0%} | "
              f"{(st.best >= x).mean():25.0%} | ${(x - 0.0012) * n30k:+5.0f}")
    print(f"   trades that hit the 2.25% stop within 24 h: {m.stopped.mean():.0%}; of those, never 0.1% in favour first: {(m[m.stopped].best < 0.001).mean():.0%}\n")
    grid = [(T, S) for S in (0.015, 0.0225, 0.035, 0.045) for T in (25, 50, 100, 150, 200, 300, 500)]
    res = {}
    for cfg in grid:
        for mode in MODES:
            out = []
            for d, s, H, L, Cl in picks:
                a, b = s1(s, H, L, Cl, mode, *cfg), s2(s, H, L, Cl, mode, *cfg)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 0.01)), (cfg, mode, d, s, a, b)
                if a:
                    out.append((d, a[0], a[1]))
            res[(cfg, mode)] = pd.DataFrame(out, columns=["day", "how", "pnl"])
    print(f"   CHECK PASSED: two implementations agree on every trade of all {len(grid)} designs, all three fill models.\n")
    print("2. BOOK A SMALL PROFIT ON EVERY TRADE. Average $ per trade: STRICT .. LENIENT .. BEST (touch fills).")
    print(f"   {'design':30s} | {'2025':>22s} | {'2026 + real':>22s} | 2026 win rate strict/best | needed to break even | avg loss when stopped (strict)")
    npos = 0
    for cfg in grid:
        T, S = cfg
        r = {mo: res[(cfg, mo)] for mo in MODES}
        c25 = [r[mo][r[mo].day < "2026"].pnl.mean() for mo in MODES]
        c26 = [r[mo][r[mo].day >= "2026"].pnl.mean() for mo in MODES]
        te, tb = r["STRICT"][r["STRICT"].day >= "2026"], r["BEST"][r["BEST"].day >= "2026"]
        loss = -te[te.how.isin(["STOP", "LIQ"])].pnl.mean()
        npos += (c25[0] > 0) and (c26[0] > 0)
        print(f"   net +${T:3d}, stop +{S:.2%}        | {c25[0]:+6.0f} .. {c25[1]:+5.0f} .. {c25[2]:+5.0f} | {c26[0]:+6.0f} .. {c26[1]:+5.0f} .. {c26[2]:+5.0f} | "
              f"{(te.how == 'TARGET').mean():5.0%} / {(tb.how == 'TARGET').mean():5.0%}            | {loss / (loss + T):6.0%}               | ${-loss:+,.0f}")
    best_any = sum((res[(c, 'BEST')][res[(c, 'BEST')].day < '2026'].pnl.mean() > 0) and (res[(c, 'BEST')][res[(c, 'BEST')].day >= '2026'].pnl.mean() > 0) for c in grid)
    print(f"\n   designs profitable in BOTH 2025 and 2026: STRICT {npos} of {len(grid)}; even with the most generous fills (BEST) {best_any} of {len(grid)}")


if __name__ == "__main__":
    main()

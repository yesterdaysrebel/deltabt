"""Owner 2026-11-15: "the price goes against the short by more than 2.25% in 82% of trades ... can we not buy here
instead of sell?" Careful dataset (Delta 1m, 48 h). At the moment the +5% sell limit fills (level e):
  1. both directions over the next 24 h: how often the price is at least x ABOVE e, at least x BELOW e, and which of
     +x / -x comes FIRST.
  2. a LONG instead: buy at the fill (stop-buy = market: e x 1.001 LENIENT, worse of e and that minute's close x 1.001
     STRICT; taker 0.05%); target +T (fills 0.1% through, taker); stop -S (STRICT: worse of the stop and the close,
     -0.1%; LENIENT: the stop -0.1%); out at the close 24 h after (-0.1%).
     Shown as % of the position per trade and in $ at Spike Sell's size ($30,472) where the stop is <= 2.25%.
  TRAIN 2025, TEST 2026 + real. Two implementations (loop / vectorised) must agree.

    PYTHONPATH=. python scripts/careful_long_instead.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import careful_data as D  # noqa: E402

FEE = 0.0005


def fill(H, Cl):
    e = Cl[2] * 1.05
    k = next((j for j in range(3, 1440) if H[j] >= e * 1.0005), None)
    return e, k


def long1(H, L, Cl, le, T, S):
    e, k = fill(H, Cl)
    if k is None:
        return None
    b = (e if le else max(e, Cl[k])) * 1.001
    tp, sp = (None if T is None else b * (1 + T)), b * (1 - S)
    ret = lambda x: (x / b - 1) - FEE - FEE * x / b   # noqa: E731
    last = min(2880, k + 1440)
    for j in range(k + 1, last + 1):
        if L[j] <= sp:
            return "STOP", ret((sp if le else min(sp, Cl[j])) * 0.999), k, j
        if tp is not None and H[j] >= tp * 1.001:
            return "TARGET", ret(tp), k, j
    return "TIME", ret(Cl[last] * 0.999), k, last


def long2(H, L, Cl, le, T, S):
    e = Cl[2] * 1.05
    f = np.flatnonzero(H[3:1440] >= e * 1.0005)
    if f.size == 0:
        return None
    k = 3 + int(f[0])
    b = (e if le else max(e, Cl[k])) * 1.001
    last = min(2880, k + 1440)
    h, l, c = H[k + 1:last + 1], L[k + 1:last + 1], Cl[k + 1:last + 1]
    si = np.flatnonzero(l <= b * (1 - S))
    ti = np.flatnonzero(h >= b * (1 + T) * 1.001) if T is not None else np.array([], int)
    s_at = int(si[0]) if si.size else 10 ** 9
    t_at = int(ti[0]) if ti.size else 10 ** 9
    if s_at <= t_at and s_at < 10 ** 9:
        x = (b * (1 - S) if le else min(b * (1 - S), c[s_at])) * 0.999
        how, j = "STOP", k + 1 + s_at
    elif t_at < 10 ** 9:
        x, how, j = b * (1 + T), "TARGET", k + 1 + t_at
    else:
        x, how, j = Cl[last] * 0.999, "TIME", last
    return how, (x - b) / b - FEE - FEE * x / b, k, j


def main():
    picks = [(d, s, H, L, Cl) for d, ps in D.load() for r, s, H, L, Cl in ps]
    print("1. AFTER THE +5% LEVEL FILLS (617 trades), the next 24 h, measured from that level:")
    print(f"   {'move x':>7s} | {'price goes >= x ABOVE':>21s} | {'price goes >= x BELOW':>21s} | {'+x comes first':>14s} | {'-x comes first':>14s} | neither")
    for x in (0.01, 0.018, 0.0225, 0.05, 0.10):
        up = dn = uf = df = nn = n = 0
        for d, s, H, L, Cl in picks:
            e, k = fill(H, Cl)
            if k is None:
                continue
            n += 1
            last = min(2880, k + 1440)
            h, l = H[k + 1:last + 1], L[k + 1:last + 1]
            iu, idn = np.flatnonzero(h >= e * (1 + x)), np.flatnonzero(l <= e * (1 - x))
            up += iu.size > 0
            dn += idn.size > 0
            a, b = (iu[0] if iu.size else 10 ** 9), (idn[0] if idn.size else 10 ** 9)
            if a == b == 10 ** 9:
                nn += 1
            elif a < b:
                uf += 1
            elif b < a:
                df += 1
            else:
                nn += 0
                uf += 0.5
                df += 0.5
        print(f"   {x:7.2%} | {up / n:21.0%} | {dn / n:21.0%} | {uf / n:14.0%} | {df / n:14.0%} | {nn / n:.0%}")
    grid = [(T, S) for S in (0.01, 0.0225, 0.05, 0.10) for T in (0.01, 0.0225, 0.05, 0.10, None)]
    res = {}
    for cfg in grid:
        for le in (False, True):
            out = []
            for d, s, H, L, Cl in picks:
                a, b = long1(H, L, Cl, le, *cfg), long2(H, L, Cl, le, *cfg)
                assert (a is None) == (b is None) and (a is None or (a[0] == b[0] and a[2:] == b[2:] and abs(a[1] - b[1]) < 1e-9)), (cfg, d, s, a, b)
                if a:
                    out.append((d, a[0], a[1]))
            res[(cfg, le)] = pd.DataFrame(out, columns=["day", "how", "ret"])
    print(f"\n   CHECK PASSED: two implementations agree on every trade of all {len(grid)} long designs, both fills.\n")
    print("2. BUY INSTEAD OF SELL at the same moment. Average per trade as % of the position, STRICT .. LENIENT:")
    print(f"   {'long design':34s} | {'2025':>17s} | {'2026 + real':>17s} | 2026: stopped, target hit | $ per trade at $30,472 (2026)")
    pos = 0
    for cfg in grid:
        T, S = cfg
        s, l_ = res[(cfg, False)], res[(cfg, True)]
        a, b, c, d2 = s[s.day < "2026"].ret.mean(), l_[l_.day < "2026"].ret.mean(), s[s.day >= "2026"].ret.mean(), l_[l_.day >= "2026"].ret.mean()
        te = s[s.day >= "2026"]
        pos += (a > 0) and (c > 0)
        usd = f"${c * 30472:+6,.0f} .. ${d2 * 30472:+6,.0f}" if S <= 0.0225 else "(stop past 20x liquidation)"
        nm = f"target {'+' + format(T, '.2%') if T else 'none (24 h)':11s}, stop -{S:.2%}"
        print(f"   {nm:34s} | {a:+.2%} .. {b:+.2%} | {c:+.2%} .. {d2:+.2%} | {(te.how == 'STOP').mean():4.0%}, {(te.how == 'TARGET').mean():4.0%}            | {usd}")
    print(f"\n   long designs positive on BOTH 2025 and 2026 (strict): {pos} of {len(grid)}")


if __name__ == "__main__":
    main()
